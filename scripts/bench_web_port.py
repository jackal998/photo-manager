"""Scan-throughput A/B bench: Qt arm vs web arm — the web-port cutover gate (C1/C5).

Phase 0 of the web port extracted the scan pipeline into a Qt-free
``core.app_service.scan_runner.run_pipeline`` (scan_runner.py:520).  Both the
desktop ``ScanWorker`` (QThread) and the web backend
(``app/web/routes/scan.py`` → ``threading.Thread``) drive that *same*
pipeline; the only difference between the two arms is the event-dispatch
mechanism — ``Signal.emit`` (Qt) vs ``queue.put``/SSE (web) — plus, in a
future phase, the cross-process IPC for scan-in-a-dedicated-worker.

This harness measures both arms on the same source roots and reports the
``web_files_per_s / qt_files_per_s`` ratio so a catastrophic web-arm
throughput regression is caught before the cutover flips the eval gates
to blocking.  It is the concrete deliverable behind:

* **Phase-0 exit gate (b)** — ``bench_web_port.py --pairs 1 --limit 50``
  runs without error on the qa sandbox and emits valid JSON with
  ``files_per_s > 0`` for the **web** arm.
* **T6 binding fix** — the Qt arm bootstraps a headless Qt application and
  the sanity job asserts the **qt** arm's ``files_per_s > 0`` (so the
  baseline isn't a silent no-op that ``continue-on-error`` swallows).

Methodology guardrails (mirrored from ``bench_autotune_604.py``):

1. **Pre-scan ``probe_device`` assertion** — every run prints the
   ``device_key`` / ``is_remote_drive`` / ``hash_workers_for_root`` for
   each source volume BEFORE the scan, the #604/#605 confound lesson.
2. **Alternating arms + ``statistics.median``** — ``--backend both``
   alternates qt/web per pair so monotonic drift hits both arms equally.
3. **Bounded per-scan timeout** — every scan has a hard deadline.
4. **exiftool reap smoke test** — snapshot-diff before/after each scan
   surfaces any ``exiftool.exe`` orphan that survived teardown (T7).

The script is the bench *mechanism*; the authoritative A/B baseline on the
real rig (NAS / spinning HDD) is a local manual checkpoint per the
"dev rig = checkpoint" principle — the 5% ratio gate (``--require-ratio``)
is too tight to assert on a tiny synthetic sandbox.  ``scripts/*`` is
excluded from coverage by design; this is a developer / CI validation tool.

CLI usage
---------

    # Both arms on the qa sandbox (the Phase-0 sanity invocation):
    python scripts/bench_web_port.py \\
        --sources qa/sandbox/near-duplicates --pairs 1 --limit 50

    # Web arm only (no Qt stack needed):
    python scripts/bench_web_port.py --backend web \\
        --sources qa/sandbox/near-duplicates --pairs 1 --limit 50

    # Real-rig A/B with the strict 5% ratio gate enforced:
    python scripts/bench_web_port.py --backend both --require-ratio \\
        --sources "D:\\Takeout-0508" "J:\\圖片" \\
        --pairs 3 --limit 2000 --output bench_web_port.json
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Running ``python scripts/bench_web_port.py`` puts scripts/ on sys.path[0];
# bootstrap the repo root so ``scanner`` / ``core`` / ``app`` resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DEFAULT_PER_SCAN_TIMEOUT = 1800.0  # seconds — 30 min hard ceiling per scan
_INTERRUPT_GRACE = 8.0  # seconds to wait after requesting interruption
_RATIO_FLOOR = 0.95  # web_files_per_s / qt_files_per_s pass threshold (design §1.4)
_BENCH_ARTIFACT_DIR = (
    Path(__file__).resolve().parent.parent / ".bench_web_port_artifacts"
)


@dataclass
class ScanBenchResult:
    """One scan's timing + load-bearing probes, for one backend arm."""

    backend: str               # "qt" | "web"
    pair_idx: int
    sources: list[str]
    wall_s: float
    n_files_walked: int
    n_files_hashed: int
    files_per_s: float         # n_files_hashed / wall_s (0.0 when wall_s==0)
    per_device_readers: dict[str, int]
    final_status: str = ""
    cancelled: bool = False
    interrupted_on_timeout: bool = False
    exiftool_orphans_post_scan: int = -1  # -1 = check failed; 0 = clean; >0 = T7 hit
    error: str = ""
    progress_lines: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared probes — duplicated (not imported) from bench_autotune_604.py on
# purpose: that module imports PySide6 at top level, which would drag Qt into
# the web arm and defeat the "web arm runs Qt-free" property this bench exists
# to validate.  These two helpers are pure and small; the duplication keeps
# the web arm importable without a Qt stack.
# ---------------------------------------------------------------------------


def probe_device(source: str) -> dict:
    """Return device_key / is_remote_drive / hash_workers_for_root for a source.

    The load-bearing assertion the original #604 run skipped — printed BEFORE
    every scan so a misclassified volume (the #605 ``device_key`` confound)
    is visible in the bench output, not hidden in the ratio.
    """
    from scanner.workers import (
        device_key,
        disk_incurs_seek_penalty,
        hash_workers_for_root,
        is_remote_drive,
    )

    dk = device_key(source)
    return {
        "source": source,
        "device_key": dk,
        "is_remote_drive": is_remote_drive(dk),
        "seek_penalty": disk_incurs_seek_penalty(dk),
        "hash_workers_for_root": hash_workers_for_root(dk),
    }


def snapshot_exiftool_pids() -> set[int]:
    """Return the set of running ``exiftool.exe`` PIDs (Windows), else ``set()``.

    Bracketing a scan with before/after snapshots makes the orphan check
    count only PIDs that appeared during THIS scan and survived teardown, so a
    concurrent exiftool (galbum, a parallel bench) can't false-positive it.
    """
    if sys.platform != "win32":
        return set()
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq exiftool.exe", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=5,
        )
        if "INFO:" in out.stdout:
            return set()
        pids: set[int] = set()
        for line in out.stdout.splitlines():
            cols = [c.strip(' "') for c in line.split(",")]
            if len(cols) >= 2 and cols[0].lower() == "exiftool.exe":
                try:
                    pids.add(int(cols[1]))
                except ValueError:
                    continue
        return pids
    except Exception:  # pylint: disable=broad-exception-caught
        return set()


def _parse_hashed_counts(lines: list[str]) -> tuple[int, int]:
    """Extract ``(n_walked, n_hashed)`` from the pipeline's progress log lines.

    Both arms route through ``run_pipeline`` → ``bus.log`` and emit the same
    "Hashing N files …" / "  Hashed N/M …" strings, so one parser serves both.
    """
    n_walked = 0
    n_hashed = 0
    for line in lines:
        if line.startswith("Hashing "):
            try:
                n_walked = int(line.split()[1].replace(",", ""))
            except (ValueError, IndexError):
                pass
        if line.startswith("  Hashed "):
            try:
                n_hashed = int(line.split()[1].split("/")[0].replace(",", ""))
            except (ValueError, IndexError):
                pass
    return n_walked, n_hashed


def _manifest_path(backend: str, pair_idx: int) -> Path:
    """Per-arm tmp manifest path so the bench never pollutes the real run db."""
    _BENCH_ARTIFACT_DIR.mkdir(exist_ok=True)
    p = _BENCH_ARTIFACT_DIR / f"manifest_{backend}_p{pair_idx}.sqlite"
    if p.exists():
        p.unlink()
    return p


# ---------------------------------------------------------------------------
# Web arm — drive the Qt-free run_pipeline directly through a capturing bus.
# ---------------------------------------------------------------------------


class _CapturingBus:
    """A ``ScanProgressBus`` implementation that buffers events in memory.

    Stands in for ``SseScanBus`` (app/web/routes/scan.py) — same Protocol, no
    SSE fan-out / event loop.  The web arm's throughput is the pipeline cost
    plus this bus's (negligible) ``list.append`` per event, which is the
    faithful lower bound for the ``queue.put`` the real SSE bus pays.
    """

    def __init__(self) -> None:
        self.log_lines: list[str] = []
        self.final_status: str = ""
        self.measured_knees: list[dict] = []

    def log(self, msg: str) -> None:
        self.log_lines.append(msg)

    def stage(self, stage_name: str, completed: int, total: int,
              files_per_sec: float) -> None:
        # The bench derives files_per_s from n_hashed/wall_s for parity across
        # arms; the typed stage event is buffered but not used for timing.
        pass

    def failed(self, msg: str) -> None:
        self.final_status = msg

    def finished(self, output_path: str) -> None:
        self.final_status = "Done."

    def completed_empty(self) -> None:
        self.final_status = "Done. (empty)"

    def hash_pool_measured(self, rates: dict) -> None:
        pass

    def read_knee_measured(self, summary: dict) -> None:
        self.measured_knees.append(dict(summary))


def run_web_scan(
    *, pair_idx: int, sources: list[str], limit: int | None,
    workers: int, hash_pool: str, per_scan_timeout: float,
) -> ScanBenchResult:
    """Run one scan through the web arm: ``run_pipeline`` + a capturing bus."""
    from core.app_service.cancel_token import _CancelToken
    from core.app_service.dtos import ScanConfig
    from core.app_service.scan_runner import run_pipeline

    probes = [probe_device(src) for src in sources]
    _print_probes("web", pair_idx, probes)
    per_device_readers = {
        p["device_key"]: p["hash_workers_for_root"] for p in probes
    }

    config = ScanConfig(
        sources={f"src{i}": Path(s) for i, s in enumerate(sources)},
        output_path=_manifest_path("web", pair_idx),
        recursive_map={f"src{i}": True for i in range(len(sources))},
        limit=limit,
        workers=workers,
        hash_pool=hash_pool,
    )
    cancel_token = _CancelToken()
    bus = _CapturingBus()

    pre_pids = snapshot_exiftool_pids()
    error = ""
    interrupted = False
    # The web backend runs run_pipeline in a daemon thread (scan.py:244); mirror
    # that so a hung pipeline can't wedge the bench, and so the timeout path is
    # exercised the same way the real server's is.
    done = threading.Event()

    def _target() -> None:
        nonlocal error
        try:
            run_pipeline(config, cancel_token, bus)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            error = f"{type(exc).__name__}: {exc}"
            bus.failed(error)
        finally:
            done.set()

    t0 = time.monotonic()
    thread = threading.Thread(target=_target, name=f"web-bench-{pair_idx}", daemon=True)
    thread.start()
    if not done.wait(per_scan_timeout):
        interrupted = True
        print(f"  TIMEOUT after {per_scan_timeout:.0f}s — requesting cancel")
        cancel_token.request()
        done.wait(_INTERRUPT_GRACE)
    wall_s = time.monotonic() - t0

    return _finalize(
        backend="web", pair_idx=pair_idx, sources=sources, wall_s=wall_s,
        lines=bus.log_lines, status=bus.final_status, error=error,
        interrupted=interrupted, per_device_readers=per_device_readers,
        pre_pids=pre_pids,
    )


# ---------------------------------------------------------------------------
# Qt arm — drive the real ScanWorker (QThread) exactly as the desktop does.
# ---------------------------------------------------------------------------


def _ensure_qt_app():
    """Bootstrap a headless Qt application for the Qt arm, once.

    The T6 contract says "QApplication (offscreen)".  The scan path itself is
    QtCore-only (proven by bench_autotune_604.py, which runs it under a bare
    QCoreApplication), so QApplication is not strictly required — but we honour
    the contract literally and try ``QApplication`` under the offscreen QPA
    platform first (forward-safe if any imported module incidentally touches
    QtGui), falling back to ``QCoreApplication`` where the GUI stack is absent.
    Either way the event loop QThread+signal dispatch needs is live.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])
    except Exception:  # pylint: disable=broad-exception-caught
        from PySide6.QtCore import QCoreApplication
        return QCoreApplication.instance() or QCoreApplication([])


def run_qt_scan(
    *, pair_idx: int, sources: list[str], limit: int | None,
    workers: int, hash_pool: str, per_scan_timeout: float,
) -> ScanBenchResult:
    """Run one scan through the Qt arm: a real ``ScanWorker`` QThread."""
    from PySide6.QtCore import Qt
    from app.views.workers.scan_worker import ScanWorker

    probes = [probe_device(src) for src in sources]
    _print_probes("qt", pair_idx, probes)
    per_device_readers = {
        p["device_key"]: p["hash_workers_for_root"] for p in probes
    }

    worker = ScanWorker(
        sources={f"src{i}": str(s) for i, s in enumerate(sources)},
        output_path=str(_manifest_path("qt", pair_idx)),
        recursive_map={f"src{i}": True for i in range(len(sources))},
        limit=limit,
        workers=workers,
        hash_pool=hash_pool,
    )

    sig_lock = threading.Lock()
    lines: list[str] = []
    status: list[str] = [""]

    def on_progress(msg: str) -> None:
        with sig_lock:
            lines.append(msg)

    def on_finished(_p: str) -> None:
        with sig_lock:
            status[0] = "Done."

    def on_failed(msg: str) -> None:
        with sig_lock:
            status[0] = msg

    def on_empty() -> None:
        with sig_lock:
            status[0] = "Done. (empty)"

    # DirectConnection so slots fire on the worker thread synchronously — the
    # main thread blocks in worker.wait() so a queued connection's event-loop
    # dispatch would never run and payloads would be lost. sig_lock guards the
    # post-join cross-thread read.
    worker.progress.connect(on_progress, Qt.ConnectionType.DirectConnection)  # type: ignore[arg-type]
    worker.finished.connect(on_finished, Qt.ConnectionType.DirectConnection)  # type: ignore[arg-type]
    worker.failed.connect(on_failed, Qt.ConnectionType.DirectConnection)  # type: ignore[arg-type]
    worker.completed_empty.connect(on_empty, Qt.ConnectionType.DirectConnection)  # type: ignore[arg-type]

    pre_pids = snapshot_exiftool_pids()
    interrupted = False
    t0 = time.monotonic()
    worker.start()
    deadline = t0 + per_scan_timeout
    while not worker.wait(1000):
        if time.monotonic() > deadline:
            interrupted = True
            print(f"  TIMEOUT after {per_scan_timeout:.0f}s — requesting interruption")
            worker.requestInterruption()
            if not worker.wait(int(_INTERRUPT_GRACE * 1000)):
                print("  WARN: worker.wait timed out AFTER interrupt — orphaned QThread")
            break
    wall_s = time.monotonic() - t0

    with sig_lock:
        final_status = status[0]
        captured = list(lines)

    return _finalize(
        backend="qt", pair_idx=pair_idx, sources=sources, wall_s=wall_s,
        lines=captured, status=final_status, error="",
        interrupted=interrupted, per_device_readers=per_device_readers,
        pre_pids=pre_pids,
    )


# ---------------------------------------------------------------------------
# Shared finalize + reporting
# ---------------------------------------------------------------------------


def _print_probes(backend: str, pair_idx: int, probes: list[dict]) -> None:
    print(f"\n=== {backend} pair#{pair_idx} ===")
    for p in probes:
        print(f"  PROBE source={p['source']!r}")
        print(f"        device_key={p['device_key']!r}  "
              f"is_remote_drive={p['is_remote_drive']}  "
              f"seek_penalty={p['seek_penalty']}  "
              f"hash_workers_for_root={p['hash_workers_for_root']}")


def _finalize(
    *, backend: str, pair_idx: int, sources: list[str], wall_s: float,
    lines: list[str], status: str, error: str, interrupted: bool,
    per_device_readers: dict[str, int], pre_pids: set[int],
) -> ScanBenchResult:
    """Common post-scan bookkeeping: orphan diff, count parse, files/s, log."""
    time.sleep(0.3)
    post_pids = snapshot_exiftool_pids()
    orphans = post_pids - pre_pids
    n_orphans = len(orphans)
    if n_orphans > 0:
        print(f"  T7-HIT: {n_orphans} new exiftool.exe PIDs survived: {sorted(orphans)}")

    n_walked, n_hashed = _parse_hashed_counts(lines)
    files_per_s = (n_hashed / wall_s) if wall_s > 0 else 0.0
    cancelled = status == "Scan cancelled."

    print(f"  RESULT backend={backend} pair#{pair_idx} wall_s={wall_s:.2f}  "
          f"n_walked={n_walked} n_hashed={n_hashed} files_per_s={files_per_s:.1f}  "
          f"status={status!r} orphans={n_orphans} error={error!r}")

    return ScanBenchResult(
        backend=backend, pair_idx=pair_idx, sources=list(sources),
        wall_s=wall_s, n_files_walked=n_walked, n_files_hashed=n_hashed,
        files_per_s=files_per_s, per_device_readers=per_device_readers,
        final_status=status or "(no signal)", cancelled=cancelled,
        interrupted_on_timeout=interrupted, exiftool_orphans_post_scan=n_orphans,
        error=error, progress_lines=list(lines),
    )


def _median_fps(results: list[ScanBenchResult], backend: str) -> float | None:
    """Median files/s over a backend's non-cancelled, positive-throughput runs."""
    vals = [
        r.files_per_s for r in results
        if r.backend == backend and not r.cancelled and r.files_per_s > 0
    ]
    return statistics.median(vals) if vals else None


def _build_summary(
    results: list[ScanBenchResult], backends: list[str], require_ratio: bool,
) -> dict:
    summary: dict = {"backends": backends}
    qt_fps = _median_fps(results, "qt")
    web_fps = _median_fps(results, "web")
    if "qt" in backends:
        summary["qt_files_per_s_median"] = qt_fps
    if "web" in backends:
        summary["web_files_per_s_median"] = web_fps

    if qt_fps and web_fps:
        ratio = web_fps / qt_fps
        summary["backend_comparison"] = {
            "ratio": ratio,
            "threshold": _RATIO_FLOOR,
            "pass": ratio >= _RATIO_FLOOR,
            # When require_ratio is off the ratio is reported but not gated —
            # on a tiny corpus it is dominated by per-scan noise, not parity.
            "advisory": not require_ratio,
        }
    if any(r.exiftool_orphans_post_scan > 0 for r in results):
        summary["t7_regression_hit"] = True

    # Liveness gate (Phase-0 (b) + T6): every requested arm must produce a
    # positive throughput; require_ratio additionally enforces the 5% floor.
    live = {b: (_median_fps(results, b) or 0.0) > 0 for b in backends}
    summary["liveness"] = live
    liveness_ok = all(live.values())
    ratio_ok = True
    if require_ratio and "backend_comparison" in summary:
        ratio_ok = summary["backend_comparison"]["pass"]
    summary["gate_pass"] = liveness_ok and ratio_ok
    return summary


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sources", nargs="+", required=True,
                   help="Source roots, e.g. qa/sandbox/near-duplicates")
    p.add_argument("--backend", choices=("qt", "web", "both"), default="both",
                   help="Which arm(s) to measure (default both)")
    p.add_argument("--pairs", type=int, default=3,
                   help="Repeat count; with --backend both, alternates qt/web per pair")
    p.add_argument("--limit", type=int, default=2000,
                   help="Per-source file cap (bounds wall-time)")
    p.add_argument("--workers", type=int, default=4,
                   help="Hash-stage worker count (ScanConfig.workers)")
    p.add_argument("--hash-pool", choices=("thread", "process", "auto"),
                   default="thread", help="Hash-stage executor (default thread)")
    p.add_argument("--per-scan-timeout", type=float, default=_DEFAULT_PER_SCAN_TIMEOUT,
                   help=f"Per-scan hard timeout (default {_DEFAULT_PER_SCAN_TIMEOUT:.0f}s)")
    p.add_argument("--require-ratio", action="store_true",
                   help="Also fail when web/qt files-per-s ratio < %.2f "
                        "(real-corpus gate; too tight for the tiny sandbox)" % _RATIO_FLOOR)
    p.add_argument("--no-warmup", action="store_true",
                   help="Skip the discarded warm-up scan per arm. The warm-up "
                        "controls the one-time import / file-cache cold-start that "
                        "otherwise inflates whichever arm runs first; without it the "
                        "reported ratio on a tiny corpus is a cold-start artifact.")
    p.add_argument("--output", default=None,
                   help="Optional JSON artifact path (JSON is always printed to stdout)")
    args = p.parse_args(argv[1:])

    backends = ["qt", "web"] if args.backend == "both" else [args.backend]

    # Bootstrap Qt only when the qt arm is requested, so the web arm runs on a
    # box without a working Qt GUI stack.
    if "qt" in backends:
        _ensure_qt_app()

    print("=== bench_web_port ===")
    print(f"sources={args.sources}  backends={backends}  pairs={args.pairs}  "
          f"limit={args.limit}  workers={args.workers}  hash_pool={args.hash_pool}")

    runners = {"qt": run_qt_scan, "web": run_web_scan}
    scan_kwargs = dict(
        sources=args.sources, limit=args.limit, workers=args.workers,
        hash_pool=args.hash_pool, per_scan_timeout=args.per_scan_timeout,
    )

    warmup = not args.no_warmup
    if warmup:
        # One discarded scan per arm: warms module imports (PIL/rawpy/scanner.*)
        # and the OS file cache so the first MEASURED run of each arm isn't the
        # process's cold-start outlier. exiftool spawn + reap is paid per-scan by
        # every run, so it stays a constant, not a cross-arm confound.
        print("\n--- warmup (discarded; controls import / file-cache cold-start) ---")
        for backend in backends:
            runners[backend](pair_idx=-1, **scan_kwargs)

    results: list[ScanBenchResult] = []
    for pair_idx in range(args.pairs):
        for backend in backends:
            results.append(runners[backend](pair_idx=pair_idx, **scan_kwargs))

    summary = _build_summary(results, backends, args.require_ratio)
    summary["warmup"] = warmup
    out = {"summary": summary, "results": [asdict(r) for r in results]}
    payload = json.dumps(out, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")

    print("\n=== summary ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("\n--- JSON ---")
    print(payload)
    if args.output:
        print(f"\nwrote {args.output}")

    return 0 if summary["gate_pass"] else 1


if __name__ == "__main__":
    # ProcessPoolExecutor (`--hash-pool process`) re-imports this module in each
    # spawn worker on Windows; freeze_support prevents recursive worker spawning.
    from multiprocessing import freeze_support
    freeze_support()
    raise SystemExit(main(sys.argv))
