"""Memory probe for photo-manager — tracks Python + Qt heap across manifest loads.

Activated via environment variable so the no-op path has near-zero overhead:

    PHOTO_MANAGER_MEMORY_PROBE=1
    PHOTO_MANAGER_MEMORY_PROBE_TAG=<label>          (default: 'untagged')
    PHOTO_MANAGER_MEMORY_PROBE_TRIM=1               (optional: call SetProcessWorkingSetSize after Point 5)
    PHOTO_MANAGER_MEMORY_PROBE_REFERRERS=<type,...> (optional comma-separated Stage-2 referrer dump)

Five measurement points are wired in production source:
    1 — end of MainWindow.__init__
    2 — ManifestLoadWorker._load after list(repo.load())
    3 — FileOperationsHandler._on_manifest_loaded after vm.groups = groups
    4 — same, after ui_updater.refresh_tree returns
    5 — 5-second QTimer after Point 4 (idle snapshot)
    6 — optional, only when PHOTO_MANAGER_MEMORY_PROBE_TRIM=1; after SetProcessWorkingSetSize

Qt-heap counters (tracemalloc is blind to C++ Qt heap):
    _qt_alloc / _qt_dealloc — net live objects per type.
    track_qt_alloc(type_name, obj) — call at construction; connects obj.destroyed for dealloc.

Artifact:
    ~/AppData/Local/PhotoManager/logs/memory_probe_<RUN_ID>.jsonl
    ~/AppData/Local/PhotoManager/logs/referrers_<type>_<RUN_ID>.jsonl  (Stage 2 only)

No external packages required — uses ctypes for Windows memory stats.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as _wintypes
import os
import threading
import uuid
from pathlib import Path

_ENABLED: bool = os.environ.get("PHOTO_MANAGER_MEMORY_PROBE") == "1"
_RUN_ID: str = uuid.uuid4().hex
_TAG: str = os.environ.get("PHOTO_MANAGER_MEMORY_PROBE_TAG", "untagged")
_TRIM: bool = os.environ.get("PHOTO_MANAGER_MEMORY_PROBE_TRIM") == "1"
_REFERRERS: str = os.environ.get("PHOTO_MANAGER_MEMORY_PROBE_REFERRERS", "")

# Qt allocation counters — incremented without heavy imports on the hot path.
_qt_alloc: dict[str, int] = {"QStandardItem": 0, "QImage": 0}
_qt_dealloc: dict[str, int] = {"QStandardItem": 0, "QImage": 0}

_lock = threading.Lock()
_tm_started = False

# Hold QTimer refs so they aren't GC'd before firing.
_active_timers: list = []

_ARTIFACT_DIR = Path.home() / "AppData" / "Local" / "PhotoManager" / "logs"


# --- Windows memory query via ctypes (no psutil needed) ---

class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", _wintypes.DWORD),
        ("PageFaultCount", _wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", _wintypes.DWORD),
        ("dwMemoryLoad", _wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010


def _get_process_memory() -> tuple[int, int, int]:
    """Return (rss_bytes, vms_bytes, private_bytes) via Windows ctypes.

    rss = WorkingSetSize (current RSS)
    vms = PagefileUsage (commit charge / virtual memory used)
    private = PrivateUsage (private committed pages)
    Falls back to zeros on error or non-Windows.
    """
    import sys
    if sys.platform != "win32":
        return _get_process_memory_posix()
    try:
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        pid = os.getpid()
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid
        )
        if not handle:
            return 0, 0, 0
        pmc = _PROCESS_MEMORY_COUNTERS_EX()
        pmc.cb = ctypes.sizeof(pmc)
        ok = psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb)
        kernel32.CloseHandle(handle)
        if ok:
            return pmc.WorkingSetSize, pmc.PagefileUsage, pmc.PrivateUsage
        return 0, 0, 0
    except Exception:
        return 0, 0, 0


def _get_process_memory_posix() -> tuple[int, int, int]:
    """Fallback for non-Windows: read /proc/self/status for VmRSS."""
    try:
        rss = 0
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
                    break
        return rss, 0, 0
    except Exception:
        return 0, 0, 0


def _get_system_avail() -> int:
    """Return available physical memory in bytes via GlobalMemoryStatusEx."""
    import sys
    if sys.platform != "win32":
        return 0
    try:
        kernel32 = ctypes.windll.kernel32
        mst = _MEMORYSTATUSEX()
        mst.dwLength = ctypes.sizeof(mst)
        kernel32.GlobalMemoryStatusEx(ctypes.byref(mst))
        return int(mst.ullAvailPhys)
    except Exception:
        return 0


def _ensure_artifact_dir() -> None:
    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def _start_tracemalloc() -> None:
    global _tm_started
    if not _tm_started:
        import tracemalloc
        tracemalloc.start(25)
        _tm_started = True


def track_qt_alloc(type_name: str, obj: object) -> None:
    """Increment Qt alloc counter for *type_name* and wire obj.destroyed for dealloc."""
    if not _ENABLED:
        return
    if type_name not in _qt_alloc:
        return
    with _lock:
        _qt_alloc[type_name] += 1
    # Connect destroyed signal so dealloc counter increments when Qt frees the object.
    try:
        def _on_destroyed(type_name: str = type_name) -> None:
            with _lock:
                _qt_dealloc[type_name] += 1

        obj.destroyed.connect(_on_destroyed)  # type: ignore[attr-defined]
    except Exception:
        pass


def snapshot(label: str, point: int, **extras: object) -> None:
    """Capture a memory snapshot and append a JSONL row to the artifact.

    Returns immediately when not enabled.
    Do NOT call gc.collect() before snapshot — that would mask retention bugs.
    """
    if not _ENABLED:
        return

    import datetime
    import gc
    import json
    import tracemalloc

    _start_tracemalloc()

    now = datetime.datetime.now(tz=datetime.timezone.utc)
    ts = now.timestamp()
    iso = now.isoformat()
    thread_name = threading.current_thread().name

    # --- tracemalloc ---
    tm_total, tm_peak = tracemalloc.get_traced_memory()
    tm_snap = tracemalloc.take_snapshot()
    stats = tm_snap.statistics("lineno")
    top30 = [
        {
            "file": str(s.traceback[0].filename),
            "lineno": s.traceback[0].lineno,
            "size_bytes": s.size,
            "count": s.count,
        }
        for s in stats[:30]
    ]
    tm_overhead = sum(s.size for s in stats if "tracemalloc" in str(s.traceback))

    # --- Windows process memory (ctypes, no psutil) ---
    rss, vms, private = _get_process_memory()
    system_avail = _get_system_avail()

    # --- gc typed object counts ---
    gc_count = gc.get_count()
    _TRACK_TYPES = {
        "QStandardItem", "PhotoRecord", "PhotoGroup",
        "QImage", "QPixmap", "QThread", "ManifestLoadWorker",
    }
    typed_counts: dict[str, int] = {name: 0 for name in _TRACK_TYPES}
    for obj in gc.get_objects():
        tname = type(obj).__name__
        if tname in typed_counts:
            typed_counts[tname] += 1

    # --- Qt counters (net live = alloc - dealloc) ---
    with _lock:
        qt_qi = _qt_alloc["QStandardItem"] - _qt_dealloc["QStandardItem"]
        qt_qimg = _qt_alloc["QImage"] - _qt_dealloc["QImage"]

    row = {
        "ts": ts,
        "iso": iso,
        "run_id": _RUN_ID,
        "tag": _TAG,
        "point": point,
        "label": label,
        "thread": thread_name,
        "tracemalloc_total_bytes": tm_total,
        "tracemalloc_peak_bytes": tm_peak,
        "tracemalloc_overhead_bytes": tm_overhead,
        "top30": top30,
        "rss_bytes": rss,
        "vms_bytes": vms,
        "private_bytes": private,
        "system_avail_bytes": system_avail,
        "gc_count": list(gc_count),
        "typed_counts": typed_counts,
        "qt_counter_qstandarditem": qt_qi,
        "qt_counter_qimage": qt_qimg,
        "extras": {str(k): str(v) for k, v in extras.items()},
    }

    _ensure_artifact_dir()
    artifact = _ARTIFACT_DIR / f"memory_probe_{_RUN_ID}.jsonl"
    with _lock:
        with artifact.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Stage 2: referrer dump at Point 5 when requested.
    if _REFERRERS and point == 5:
        for type_name in _REFERRERS.split(","):
            type_name = type_name.strip()
            if type_name:
                _dump_referrers(type_name)

    # Optional working-set trim at Point 5 on Windows.
    if _TRIM and point == 5:
        import sys
        if sys.platform == "win32":
            rss_before = rss
            try:
                ctypes.windll.kernel32.SetProcessWorkingSetSize(-1, -1)
            except Exception:
                pass
            rss_after, vms_after, private_after = _get_process_memory()
            snapshot("after_trim", 6, rss_before_trim=rss_before, rss_after_trim=rss_after,
                     delta=rss_after - rss_before)


def _dump_referrers(type_name: str, limit: int = 5) -> None:
    """Walk gc objects, pick up to *limit* instances of *type_name*, dump referrers."""
    import gc
    import json
    import random

    candidates = [o for o in gc.get_objects() if type(o).__name__ == type_name]
    samples = random.sample(candidates, min(limit, len(candidates))) if candidates else []

    rows = []
    for obj in samples:
        referrers = gc.get_referrers(obj)
        chain = []
        for r in referrers[:10]:
            rtype = type(r).__name__
            rid = id(r)
            try:
                rrepr = repr(r)[:120]
            except Exception:
                rrepr = "<repr-error>"
            attr_names: list[str] = []
            if isinstance(r, dict):
                try:
                    attr_names = [k for k, v in r.items() if v is obj][:5]
                except Exception:
                    pass
            chain.append({
                "referrer_type": rtype,
                "referrer_id": rid,
                "referrer_repr": rrepr,
                "attr_names": attr_names,
            })
        rows.append({
            "run_id": _RUN_ID,
            "target_type": type_name,
            "target_id": id(obj),
            "referrer_count": len(referrers),
            "chain": chain,
        })

    _ensure_artifact_dir()
    artifact = _ARTIFACT_DIR / f"referrers_{type_name}_{_RUN_ID}.jsonl"
    with artifact.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
