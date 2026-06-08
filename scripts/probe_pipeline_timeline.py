"""Per-second timeline probe — find the actual stalls in a real scan.

Instruments the scan pipeline at every choke point that could explain
"NAS idle + CPU not full":

* ``scanner.hasher.read_for_record`` — wraps each read with start/end
  timestamps so we can compute per-second bytes-read-per-device.
* ``scanner.hasher.compute_from_bytes`` — wraps each compute call with
  start/end so we can compute per-second compute completions and the
  in-flight compute count.
* ``scanner.byte_budget.per_device_budgets`` — records ``_inflight`` after
  every acquire/release (reused from probe_byte_budget_598.py).

Output is a per-second JSON timeline so you can SEE, second by second, what
each stage was doing — and where the stall is. If NAS bytes/s drops to 0
for N consecutive seconds while compute is busy → compute is the binder.
If NAS bytes/s drops while compute is idle AND byte_budget is empty → SMB
latency / server is the binder. Etc.

Use the user's real settings — ``hash_pool="auto"`` (which falls through to
the #554 thread shortcut on multi-device+NAS), ``autotune_read_knee=True``,
``exif_workers=2``, ``workers=8`` for NAS — to match what they observe.

CLI:
    python scripts/probe_pipeline_timeline.py \\
        --sources "D:\\Takeout-0508" "J:\\圖片" --limit 1700 \\
        --output probe_timeline_dj.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402


_INTERRUPT_GRACE = 8.0


def probe_device(source: str) -> dict:
    from scanner.workers import device_key, hash_workers_for_root, is_remote_drive, disk_incurs_seek_penalty
    dk = device_key(source)
    return {
        "source": source,
        "device_key": dk,
        "is_remote_drive": is_remote_drive(dk),
        "seek_penalty": disk_incurs_seek_penalty(dk),
        "hash_workers_for_root": hash_workers_for_root(dk),
    }


def snapshot_exiftool_pids() -> set[int]:
    if sys.platform != "win32":
        return set()
    try:
        # Match either casing — Windows is case-insensitive but tasklist /FI
        # is case-sensitive on the value side; querying with both catches
        # ExifTool.exe variants.
        pids: set[int] = set()
        for name in ("exiftool.exe", "ExifTool.exe"):
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=5,
            )
            if "INFO:" in out.stdout:
                continue
            for line in out.stdout.splitlines():
                cols = [c.strip(' "') for c in line.split(",")]
                if len(cols) >= 2 and cols[0].lower() == "exiftool.exe":
                    try:
                        pids.add(int(cols[1]))
                    except ValueError:
                        continue
        return pids
    except Exception:
        return set()


def install_instrumentation():
    """Wrap read_for_record + compute_from_bytes + per_device_budgets to
    capture timestamped events. Returns (events_lock, events) — the caller
    seeds events as the shared structure.
    """
    import scanner.hasher as _hasher
    import scanner.byte_budget as _bb
    from scanner.workers import device_key as _device_key

    lock = threading.Lock()
    events = {
        "reads": [],     # (t_end, device_key, bytes, duration_s)
        "computes": [],  # (t_end, device_key, duration_s)
        "budget":  [],   # (t, device_key, inflight_bytes, budget_bytes, op)
        "starts":  {"reads": 0, "computes": 0},  # running counters
        "in_flight": {"reads": 0, "computes": 0},
    }
    t0 = [None]  # Set on first read; relative timestamps from there.

    real_read = _hasher.read_for_record
    real_compute = _hasher.compute_from_bytes
    real_factory = _bb.per_device_budgets

    def timed_read(idx, record):
        ts = time.monotonic()
        with lock:
            if t0[0] is None:
                t0[0] = ts
            events["in_flight"]["reads"] += 1
        try:
            return real_read(idx, record)
        finally:
            te = time.monotonic()
            try:
                dk = _device_key(getattr(record, "path", ""))
            except Exception:
                dk = ""
            # Find bytes if possible from the result; safer: probe record file size.
            try:
                nbytes = record.path.stat().st_size if hasattr(record, "path") else 0
            except Exception:
                nbytes = 0
            with lock:
                events["in_flight"]["reads"] -= 1
                events["reads"].append((te - t0[0], dk, nbytes, te - ts))

    def timed_compute(idx, record, data):
        ts = time.monotonic()
        with lock:
            events["in_flight"]["computes"] += 1
        try:
            return real_compute(idx, record, data)
        finally:
            te = time.monotonic()
            try:
                dk = _device_key(getattr(record, "path", ""))
            except Exception:
                dk = ""
            with lock:
                events["in_flight"]["computes"] -= 1
                if t0[0] is not None:
                    events["computes"].append((te - t0[0], dk, te - ts))

    def wrap_budget(budget, dev_key: str):
        real_acquire = budget.acquire
        real_release = budget.release

        def aq(n_bytes, timeout=0.05):
            r = real_acquire(n_bytes, timeout)
            if r:
                ts = time.monotonic()
                with lock:
                    if t0[0] is not None:
                        events["budget"].append((ts - t0[0], dev_key, budget._inflight, budget._budget, "acquire"))
            return r

        def rl(n_bytes):
            real_release(n_bytes)
            ts = time.monotonic()
            with lock:
                if t0[0] is not None:
                    events["budget"].append((ts - t0[0], dev_key, budget._inflight, budget._budget, "release"))

        budget.acquire = aq        # type: ignore[method-assign]
        budget.release = rl        # type: ignore[method-assign]

    def probing_factory(total_bytes, device_keys, cancel_check):
        budgets = real_factory(total_bytes, device_keys, cancel_check)
        for dk, b in budgets.items():
            wrap_budget(b, dk)
        return budgets

    _hasher.read_for_record = timed_read
    _hasher.compute_from_bytes = timed_compute
    _bb.per_device_budgets = probing_factory
    # scan_worker imports these symbols by name at module load time; re-bind
    # them on the worker module too if already imported.
    try:
        import app.views.workers.scan_worker as _sw
        if hasattr(_sw, "read_for_record"):
            _sw.read_for_record = timed_read
        if hasattr(_sw, "compute_from_bytes"):
            _sw.compute_from_bytes = timed_compute
        if hasattr(_sw, "per_device_budgets"):
            _sw.per_device_budgets = probing_factory
    except Exception:
        pass
    return lock, events, t0


def bucket_per_second(events: dict, t0_set: bool, total_wall_s: float) -> list[dict]:
    """Bucket raw events into per-second time series."""
    if not t0_set:
        return []
    sec_max = int(total_wall_s) + 1
    # Per-second bucket: bytes-per-device, completed-reads-per-device,
    # completed-computes (any device), running in-flight (sampled by end events)
    buckets = [
        {
            "t": s, "nas_mb": 0.0, "hdd_mb": 0.0,
            "local_mb": 0.0, "other_mb": 0.0,
            "reads_done": 0, "computes_done": 0,
            "read_per_dev": defaultdict(int),
            "bytes_per_dev": defaultdict(float),
        }
        for s in range(sec_max)
    ]
    # First read-knee like classification: device_key starting with \\ = NAS,
    # 'D:' or other letter = local; we just bucket by exact key here.
    for t, dk, nb, dur in events["reads"]:
        s = int(t)
        if s >= sec_max:
            continue
        bk = buckets[s]
        bk["reads_done"] += 1
        bk["read_per_dev"][dk] += 1
        mb = nb / (1024 * 1024)
        bk["bytes_per_dev"][dk] += mb
        if dk.startswith("\\\\"):
            bk["nas_mb"] += mb
        elif len(dk) == 2 and dk[1] == ":":
            bk["hdd_mb"] += mb  # imprecise label; "D:"-style local key
        else:
            bk["other_mb"] += mb
    for t, dk, dur in events["computes"]:
        s = int(t)
        if s >= sec_max:
            continue
        buckets[s]["computes_done"] += 1
    # Snapshot byte-budget fill at the end of each second.
    # Latest sample for each device within the second.
    by_sec_dev = defaultdict(dict)  # {sec: {dev: (fill_pct, inflight_bytes, budget_bytes)}}
    for t, dk, inflight, budget, op in events["budget"]:
        s = int(t)
        if s >= sec_max or budget == 0:
            continue
        by_sec_dev[s][dk] = (inflight / budget, inflight, budget)
    # Forward-fill so seconds with no budget event inherit the last known fill
    last_by_dev: dict[str, tuple] = {}
    for s in range(sec_max):
        for dk, val in by_sec_dev.get(s, {}).items():
            last_by_dev[dk] = val
        buckets[s]["budget_fill_per_dev"] = {
            dk: {"pct": val[0], "inflight": val[1], "budget": val[2]}
            for dk, val in last_by_dev.items()
        }
        # Convert defaultdicts to plain dicts so JSON output is clean
        buckets[s]["read_per_dev"] = dict(buckets[s]["read_per_dev"])
        buckets[s]["bytes_per_dev"] = dict(buckets[s]["bytes_per_dev"])
    return buckets


def run_scan(*, sources: list[str], limit: int, hash_pool: str,
             autotune_read_knee: bool, exif_workers: int, workers: int,
             per_scan_timeout: float):
    """One ScanWorker.run() with full timeline instrumentation."""
    from app.views.workers.scan_worker import ScanWorker

    probes = [probe_device(s) for s in sources]
    print(f"\n=== PROBE pre-run ===")
    for p in probes:
        print(f"  {p}")
    print(f"  hash_pool={hash_pool}  autotune_read_knee={autotune_read_knee}  "
          f"exif_workers={exif_workers}  workers={workers}")

    out_dir = Path(__file__).resolve().parent.parent / ".probe_timeline_artifacts"
    out_dir.mkdir(exist_ok=True)
    manifest_path = out_dir / "manifest.sqlite"
    if manifest_path.exists():
        manifest_path.unlink()

    sources_dict = {f"src{i}": str(s) for i, s in enumerate(sources)}
    recursive_map = {f"src{i}": True for i in range(len(sources))}

    # Install instrumentation BEFORE constructing ScanWorker (so it sees
    # the wrapped read_for_record etc. via the module rebinding above).
    lock, events, t0_box = install_instrumentation()

    worker = ScanWorker(
        sources=sources_dict,
        output_path=str(manifest_path),
        recursive_map=recursive_map,
        limit=limit,
        workers=workers,
        hash_pool=hash_pool,
        exif_workers=exif_workers,
        autotune_read_knee=autotune_read_knee,
    )

    progress_lines: list[str] = []
    final_status: list[str] = [""]
    measured_knees: list[dict] = []
    sig_lock = threading.Lock()

    def on_progress(msg):
        with sig_lock:
            progress_lines.append(msg)
    def on_knee(summary):
        with sig_lock:
            measured_knees.append(dict(summary))
    def on_finished(_path):
        with sig_lock:
            final_status[0] = "Done."
    def on_failed(msg):
        with sig_lock:
            final_status[0] = msg
    def on_empty():
        with sig_lock:
            final_status[0] = "Done. (empty)"

    worker.progress.connect(on_progress, Qt.ConnectionType.DirectConnection)
    worker.read_knee_measured.connect(on_knee, Qt.ConnectionType.DirectConnection)
    worker.finished.connect(on_finished, Qt.ConnectionType.DirectConnection)
    worker.failed.connect(on_failed, Qt.ConnectionType.DirectConnection)
    worker.completed_empty.connect(on_empty, Qt.ConnectionType.DirectConnection)

    pre_pids = snapshot_exiftool_pids()
    t0 = time.monotonic()
    worker.start()
    deadline = t0 + per_scan_timeout
    while not worker.wait(1000):
        if time.monotonic() > deadline:
            print(f"  TIMEOUT after {per_scan_timeout:.0f}s — interrupting")
            worker.requestInterruption()
            if not worker.wait(int(_INTERRUPT_GRACE * 1000)):
                print(f"  WARN: didn't terminate after interrupt grace")
            break
    wall_s = time.monotonic() - t0
    time.sleep(0.3)
    post_pids = snapshot_exiftool_pids()
    new_orphans = sorted(post_pids - pre_pids)

    with lock:
        events_copy = {
            "reads": list(events["reads"]),
            "computes": list(events["computes"]),
            "budget": list(events["budget"]),
        }
    with sig_lock:
        status = final_status[0]

    timeline = bucket_per_second(events_copy, t0_box[0] is not None, wall_s)

    # ===== Print summary on stdout =====
    print(f"\n=== SUMMARY wall_s={wall_s:.2f} status={status!r} ===")
    print(f"  reads done: {len(events_copy['reads'])}")
    print(f"  computes done: {len(events_copy['computes'])}")
    print(f"  budget events: {len(events_copy['budget'])}")
    print(f"  new exiftool orphans: {new_orphans}")
    print(f"  measured knees: {measured_knees}")
    print()
    print(f"=== TIMELINE (every second; truncated to non-trivial seconds) ===")
    print(f"{'t':>4} {'NAS MB':>8} {'HDD MB':>8} {'reads':>6} {'comp':>5} {'budget_fill'}")
    for bk in timeline:
        # Skip empty seconds at the very start/end
        if bk["reads_done"] == 0 and bk["computes_done"] == 0 and not bk.get("budget_fill_per_dev"):
            continue
        fills = ""
        for dk, info in bk.get("budget_fill_per_dev", {}).items():
            short_dk = (dk[-12:] if dk.startswith("\\\\") else dk)
            fills += f"{short_dk}={info['pct']:.0%} "
        print(f"{bk['t']:>4} {bk['nas_mb']:>8.1f} {bk['hdd_mb']:>8.1f} "
              f"{bk['reads_done']:>6} {bk['computes_done']:>5}  {fills}")

    return {
        "wall_s": wall_s,
        "status": status,
        "sources": sources,
        "config": {
            "hash_pool": hash_pool,
            "autotune_read_knee": autotune_read_knee,
            "exif_workers": exif_workers,
            "workers": workers,
            "limit": limit,
            "per_scan_timeout": per_scan_timeout,
        },
        "probes": probes,
        "n_reads_done": len(events_copy["reads"]),
        "n_computes_done": len(events_copy["computes"]),
        "exiftool_new_orphans": new_orphans,
        "measured_knees": measured_knees,
        "timeline": timeline,
        "progress_lines": progress_lines,
    }


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sources", nargs="+", required=True)
    p.add_argument("--limit", type=int, default=1700)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--exif-workers", type=int, default=2)
    p.add_argument("--hash-pool", choices=("thread", "process", "auto"),
                   default="auto")
    p.add_argument("--autotune-read-knee", action="store_true",
                   default=True,
                   help="Use the shipped default-ON autotune (set by default; pass --no-autotune-read-knee to disable)")
    p.add_argument("--no-autotune-read-knee", dest="autotune_read_knee",
                   action="store_false")
    p.add_argument("--per-scan-timeout", type=float, default=600.0)
    p.add_argument("--output", required=True)
    args = p.parse_args(argv[1:])

    app = QCoreApplication.instance() or QCoreApplication([])
    _ = app

    result = run_scan(
        sources=args.sources, limit=args.limit,
        hash_pool=args.hash_pool,
        autotune_read_knee=args.autotune_read_knee,
        exif_workers=args.exif_workers,
        workers=args.workers,
        per_scan_timeout=args.per_scan_timeout,
    )
    Path(args.output).write_text(json.dumps(result, indent=2,
                                            ensure_ascii=False, default=str),
                                 encoding="utf-8")
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    raise SystemExit(main(sys.argv))
