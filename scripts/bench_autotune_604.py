"""Clean-rig re-run of the #604 read-knee autotune A/B (post-#606 revert).

The original #604 run conclusion ("autotune neutral, ratio 1.005") was
**confounded** by PR #605 / #583: that PR changed local-drive ``device_key``
from ``"D:"`` to ``"{GUID}"``, which silently bypassed
``disk_incurs_seek_penalty``'s 2-char drive-letter guard
(``scanner/workers.py:130``), so ``hash_workers_for_root`` fell back to the
SSD default of 4 readers on the D: spinning HDD — seek-thrash. Every #604
scan thrashed the HDD; the "ratio 1.005, autotune has no value" reading is
NOT trustworthy. PR #606 reverted #583/#605 on 2026-06-07.

This harness re-runs the A/B on **clean post-#606 code** with the
methodology guardrails baked in:

1. **Assert the load-bearing quantity at scan-start** — every run prints the
   actual ``device_key``, ``is_remote_drive``, and
   ``hash_workers_for_root`` for every distinct source volume BEFORE
   launching the scan, plus the warm-cache ``autotune_knees`` it passed.
2. **NAS-only control** — ``--nas-only`` drops the local sources and runs
   the NAS arm alone, so the autotune signal can be isolated from D:
   contention. Compare ON / OFF wall-times for both the multi-device
   real workload and the NAS-only control.
3. **Bounded per-scan timeout** — every scan has a hard deadline; on
   timeout the harness calls ``worker.requestInterruption()`` and waits
   another 8s. Without this, a 39k-file NAS scan could pile up across
   alternating pairs.
4. **Reap exiftool on exit** — best-effort post-scan check via
   ``tasklist`` on Windows; if a stray ``exiftool.exe`` survives the
   scan's own teardown we surface it as a T7 regression hit (the gap
   that motivated this clean re-run in the first place).
5. **No mocking** — drives the real ``ScanWorker.run()`` against the real
   source roots. The synthetic cliff lives in
   ``tests/integration/test_autotune_ab.py``; this script is the
   real-rig analogue.

CLI usage
---------

    python scripts/bench_autotune_604.py \\
        --sources "D:\\Takeout-0508" "J:\\圖片" \\
        --pairs 3 --limit 2000 --output bench_604.json

    # NAS-only control
    python scripts/bench_autotune_604.py \\
        --sources "J:\\圖片" --nas-only \\
        --pairs 3 --limit 2000 --output bench_604_nas_only.json

The output JSON is the artifact the user pastes back into the conversation;
it contains every probe, every wall-time, the per-device knee, and the
final ratio + median summary. See ``docs/audits/`` for the audit template
this feeds into.

Why this lives in ``scripts/`` (not ``tests/integration/``)
----------------------------------------------------------

This drives the **real disks** (your NAS, your spinning HDD). Pytest
fixtures + the existing GATE-2 integration test cover the *synthetic* A/B;
they cannot reach the real hardware. ``scripts/*`` is excluded from
coverage by design — this is a developer / user-run validation tool.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Running ``python scripts/bench_autotune_604.py`` puts scripts/ on sys.path[0];
# bootstrap the repo root so ``scanner`` + ``app`` resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402


_DEFAULT_PER_SCAN_TIMEOUT = 1800  # seconds — 30 min hard ceiling per scan
_INTERRUPT_GRACE = 8.0  # seconds to wait after requestInterruption()


@dataclass
class ScanResult:
    arm: str               # "OFF" | "ON" | "OFF-nas-only" | "ON-nas-only"
    pair_idx: int
    sources: list[str]
    wall_s: float
    n_files_walked: int    # files the walker found (pre-limit)
    n_files_hashed: int    # files actually hashed (post-limit)
    per_device_readers: dict[str, int]
    per_device_knee_at_start: dict[str, dict | None]
    measured_knees: list[dict] = field(default_factory=list)
    progress_lines: list[str] = field(default_factory=list)
    cancelled: bool = False
    interrupted_on_timeout: bool = False
    final_status: str = ""    # "Done." | "Scan cancelled." | "(no signal)"
    exiftool_orphans_post_scan: int = -1  # -1 = check failed; 0 = clean; >0 = T7 regression hit


def probe_device(source: str) -> dict:
    """Return ``device_key``, ``is_remote_drive``, and ``hash_workers_for_root``
    for one source path. This is the load-bearing assertion the prior #604
    run skipped — print these BEFORE every scan and verify they match what
    the rig is supposed to be (D: HDD → 1, J: NAS → 8).
    """
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
    """Return the set of currently-running ``exiftool.exe`` PIDs, or an
    empty set on any failure. Used to bracket a scan with before/after
    snapshots so the orphan check only counts PIDs that appeared during
    THIS scan and survived past its teardown (a clean T7 fix → zero).

    Without the snapshot diff, a concurrent process (parallel harness,
    user's other exiftool, galbum) would false-positive the smoke check.

    Windows-only — on POSIX returns ``set()`` (no exiftool zombie pattern
    observed there).
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
            # CSV row: "exiftool.exe","1234","Console","1","12,345 K"
            cols = [c.strip(' "') for c in line.split(",")]
            if len(cols) >= 2 and cols[0].lower() == "exiftool.exe":
                try:
                    pids.add(int(cols[1]))
                except ValueError:
                    continue
        return pids
    except Exception:
        return set()


def run_one_scan(
    *,
    arm: str,
    pair_idx: int,
    sources: list[str],
    limit: int | None,
    autotune_read_knee: bool,
    autotune_knees: dict | None,
    per_scan_timeout: float,
    workers: int,
    hash_pool: str = "thread",
) -> ScanResult:
    """One full ScanWorker.run() on the given sources. Returns a ScanResult
    with timing + every load-bearing probe captured at scan-start."""
    # Imports inside the function so module load doesn't drag QApplication.
    from app.views.workers.scan_worker import ScanWorker

    # 1) Pre-scan probe — print and capture the load-bearing per-device numbers.
    probes = [probe_device(src) for src in sources]
    print(f"\n=== {arm} pair#{pair_idx} ===")
    for p in probes:
        print(f"  PROBE source={p['source']!r}")
        print(f"        device_key={p['device_key']!r}  "
              f"is_remote_drive={p['is_remote_drive']}  "
              f"seek_penalty={p['seek_penalty']}  "
              f"hash_workers_for_root={p['hash_workers_for_root']}")
    print(f"  PROBE autotune_read_knee={autotune_read_knee}  "
          f"autotune_knees={autotune_knees!r}")

    per_device_readers = {p["device_key"]: p["hash_workers_for_root"] for p in probes}
    per_device_knee_at_start = {
        p["device_key"]: (autotune_knees or {}).get(p["device_key"]) for p in probes
    }

    # 2) Wire the ScanWorker. Output manifest to a tmp path so we don't pollute
    # the user's real run-manifest.sqlite.
    out_dir = Path(__file__).resolve().parent.parent / ".bench_604_artifacts"
    out_dir.mkdir(exist_ok=True)
    manifest_path = out_dir / f"manifest_{arm}_p{pair_idx}.sqlite"
    if manifest_path.exists():
        manifest_path.unlink()

    sources_dict = {f"src{i}": str(s) for i, s in enumerate(sources)}
    recursive_map = {f"src{i}": True for i in range(len(sources))}

    worker = ScanWorker(
        sources=sources_dict,
        output_path=str(manifest_path),
        recursive_map=recursive_map,
        limit=limit,
        workers=workers,
        hash_pool=hash_pool,
        autotune_read_knee=autotune_read_knee,
        autotune_knees=autotune_knees or {},
    )

    # 3) Signal capture — direct connection (called on worker thread); lock
    # the lists so the main thread can read post-join cleanly.
    sig_lock = threading.Lock()
    progress_lines: list[str] = []
    measured_knees: list[dict] = []

    def on_progress(msg: str) -> None:
        with sig_lock:
            progress_lines.append(msg)

    def on_knee(summary: dict) -> None:
        with sig_lock:
            measured_knees.append(dict(summary))

    final_status: list[str] = [""]

    def on_finished(_path: str) -> None:
        with sig_lock:
            final_status[0] = "Done."

    def on_failed(msg: str) -> None:
        with sig_lock:
            final_status[0] = msg

    def on_empty() -> None:
        with sig_lock:
            final_status[0] = "Done. (empty)"

    # DirectConnection so slots fire on the worker thread synchronously —
    # the main thread blocks in worker.wait() so the queued (default)
    # connection's event-loop dispatch never runs and signal payloads are
    # lost. sig_lock guards the cross-thread reads on completion.
    worker.progress.connect(on_progress, Qt.ConnectionType.DirectConnection)  # type: ignore[arg-type]
    worker.read_knee_measured.connect(on_knee, Qt.ConnectionType.DirectConnection)  # type: ignore[arg-type]
    worker.finished.connect(on_finished, Qt.ConnectionType.DirectConnection)  # type: ignore[arg-type]
    worker.failed.connect(on_failed, Qt.ConnectionType.DirectConnection)  # type: ignore[arg-type]
    worker.completed_empty.connect(on_empty, Qt.ConnectionType.DirectConnection)  # type: ignore[arg-type]

    # 4) Run + bound. We use QThread.start() so requestInterruption() works.
    cancelled = False
    interrupted_on_timeout = False

    # Snapshot exiftool PIDs BEFORE the scan starts so we only count THIS
    # scan's children in the post-scan orphan check. A concurrent process
    # (parallel harness, galbum, user's manual exiftool) won't pollute.
    pre_pids = snapshot_exiftool_pids()

    t0 = time.monotonic()
    worker.start()
    deadline = t0 + per_scan_timeout
    # Poll wait() at 1s so we can break early on the timeout.
    while not worker.wait(1000):
        if time.monotonic() > deadline:
            interrupted_on_timeout = True
            print(f"  TIMEOUT after {per_scan_timeout:.0f}s — requesting interruption")
            worker.requestInterruption()
            # Generous grace — the in-loop cancel branch tears down within
            # ~5s if the post-#606 reap chain works.
            if not worker.wait(int(_INTERRUPT_GRACE * 1000)):
                print(f"  WARN: worker.wait({_INTERRUPT_GRACE:.0f}s) timed out "
                      f"AFTER interrupt — orphaned QThread (T7 hit?)")
            break
    wall_s = time.monotonic() - t0

    with sig_lock:
        status = final_status[0]
    if status == "Scan cancelled.":
        cancelled = True

    # 5) Post-scan exiftool reap smoke test — snapshot-diff so concurrent
    # processes don't false-positive. Orphans = PIDs that appeared during
    # this scan AND are still alive 300 ms after the worker thread exited.
    time.sleep(0.3)
    post_pids = snapshot_exiftool_pids()
    this_scan_orphans = (post_pids - pre_pids)
    n_orphans = len(this_scan_orphans)
    if n_orphans > 0:
        print(f"  T7-HIT: {n_orphans} new exiftool.exe PIDs survived this scan: "
              f"{sorted(this_scan_orphans)}")

    # 6) Hashed-file count from the last "Hashing N,NNN files" / "Hashed" line.
    n_hashed = 0
    n_walked = 0
    with sig_lock:
        for line in progress_lines:
            if line.startswith("Hashing "):
                # "Hashing 12,345 files across 2 device(s): ..."
                try:
                    n_walked = int(line.split()[1].replace(",", ""))
                except (ValueError, IndexError):
                    pass
            if line.startswith("  Hashed "):
                try:
                    n_hashed = int(line.split()[1].split("/")[0].replace(",", ""))
                except (ValueError, IndexError):
                    pass

    print(f"  RESULT arm={arm} pair#{pair_idx} wall_s={wall_s:.2f}  "
          f"n_walked={n_walked} n_hashed={n_hashed}  status={status!r}  "
          f"orphans={n_orphans}  knees={measured_knees}")

    return ScanResult(
        arm=arm, pair_idx=pair_idx, sources=list(sources),
        wall_s=wall_s,
        n_files_walked=n_walked, n_files_hashed=n_hashed,
        per_device_readers=per_device_readers,
        per_device_knee_at_start=per_device_knee_at_start,
        measured_knees=measured_knees,
        progress_lines=list(progress_lines),
        cancelled=cancelled,
        interrupted_on_timeout=interrupted_on_timeout,
        final_status=status or "(no signal)",
        exiftool_orphans_post_scan=n_orphans,
    )


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sources", nargs="+", required=True,
                   help="Source roots, e.g. D:\\Takeout-0508 J:\\圖片")
    p.add_argument("--pairs", type=int, default=3,
                   help="Alternating OFF/ON pairs (median-of-N)")
    p.add_argument("--limit", type=int, default=2000,
                   help="Per-scan file limit (bounds wall-time)")
    p.add_argument("--workers", type=int, default=8,
                   help="Hash-stage worker count (ScanWorker(workers=N))")
    p.add_argument("--output", required=True,
                   help="JSON output path")
    p.add_argument("--per-scan-timeout", type=float, default=_DEFAULT_PER_SCAN_TIMEOUT,
                   help=f"Per-scan hard timeout (default {_DEFAULT_PER_SCAN_TIMEOUT}s)")
    p.add_argument("--warm-cache", action="store_true",
                   help="Pre-seed autotune_knees with a measured knee (skips ramp on ON arm)")
    p.add_argument("--nas-only", action="store_true",
                   help="Run only the NAS sources (drop local-disk sources)")
    p.add_argument("--hash-pool", choices=("thread", "process", "auto"),
                   default="thread",
                   help="Hash-stage executor: thread (GIL-bound), process (escapes GIL), or auto (calibrated). Default thread for back-compat.")
    args = p.parse_args(argv[1:])

    # QCoreApplication is required for Qt signal/slot dispatch.
    app = QCoreApplication.instance() or QCoreApplication([])
    _ = app

    # Filter sources for --nas-only.
    from scanner.workers import is_remote_drive, device_key
    sources = list(args.sources)
    if args.nas_only:
        sources = [s for s in sources if is_remote_drive(device_key(s))]
        if not sources:
            print("ERROR: --nas-only filtered out every source", file=sys.stderr)
            return 2

    # Warm-cache prep — passed as autotune_knees on the ON arm so the ramp
    # is skipped after the first measured ON pair. The first ON pair runs
    # with empty cache (live ramp); subsequent ON pairs reuse the previous
    # pair's measured knee. This mirrors what real users see after their
    # first cached scan.
    from scanner.autotune import AUTOTUNE_RECIPE_VERSION
    warm_cache: dict[str, dict] = {}

    print(f"=== bench_autotune_604 ===")
    print(f"sources={sources}  pairs={args.pairs}  limit={args.limit}  workers={args.workers}")
    print(f"per_scan_timeout={args.per_scan_timeout:.0f}s  warm_cache_init={args.warm_cache}")
    print(f"AUTOTUNE_RECIPE_VERSION={AUTOTUNE_RECIPE_VERSION}")

    all_results: list[ScanResult] = []
    for pair_idx in range(args.pairs):
        # Alternate OFF then ON so monotonic drift hits both arms equally.
        r_off = run_one_scan(
            arm="OFF", pair_idx=pair_idx, sources=sources, limit=args.limit,
            autotune_read_knee=False, autotune_knees=None,
            per_scan_timeout=args.per_scan_timeout, workers=args.workers,
            hash_pool=args.hash_pool,
        )
        all_results.append(r_off)
        r_on = run_one_scan(
            arm="ON", pair_idx=pair_idx, sources=sources, limit=args.limit,
            autotune_read_knee=True,
            autotune_knees=dict(warm_cache) if warm_cache else None,
            per_scan_timeout=args.per_scan_timeout, workers=args.workers,
            hash_pool=args.hash_pool,
        )
        all_results.append(r_on)
        # Harvest measured knees into the warm cache for subsequent pairs.
        for knee in r_on.measured_knees:
            dev = knee.get("device")
            k = knee.get("knee")
            if dev and isinstance(k, int) and k > 0:
                warm_cache[dev] = {"knee": k, "recipe": AUTOTUNE_RECIPE_VERSION}

    # --- Summary ---
    offs = [r.wall_s for r in all_results if r.arm == "OFF" and not r.cancelled]
    ons = [r.wall_s for r in all_results if r.arm == "ON" and not r.cancelled]
    summary: dict = {
        "sources": sources,
        "pairs": args.pairs,
        "limit": args.limit,
        "workers": args.workers,
        "off_walls_s": offs,
        "on_walls_s": ons,
    }
    if offs and ons:
        summary["off_median_s"] = statistics.median(offs)
        summary["on_median_s"] = statistics.median(ons)
        summary["on_over_off_ratio"] = summary["on_median_s"] / summary["off_median_s"]
    if any(r.exiftool_orphans_post_scan > 0 for r in all_results):
        summary["t7_regression_hit"] = True

    out = {
        "summary": summary,
        "warm_cache_final": warm_cache,
        "results": [asdict(r) for r in all_results],
    }
    Path(args.output).write_text(json.dumps(out, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    print(f"\n=== summary ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    # Windows ProcessPoolExecutor (`--hash-pool process`) re-imports this
    # module in each spawn worker; freeze_support() prevents accidental
    # recursive worker spawning if the module gets imported with side effects.
    from multiprocessing import freeze_support
    freeze_support()
    raise SystemExit(main(sys.argv))
