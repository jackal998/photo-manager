"""Validate the #598 per-device ByteBudget fix on real hardware.

#598 (merged 2026-06-06) split the global 2 GiB ``ByteBudget`` into one
budget per device, on the diagnosis that D:'s clustered 100-130 MB ProRAW
DNGs were pinning the single shared ceiling and starving J:'s NAS reader
at ``acquire`` (measured: 86.5 % mean ``_inflight`` fill, 100 % during
53/53 NAS-idle samples; NAS-only control mean 0.2 %). The PR's *fix* was
verified by a unit test (one device's full budget no longer blocks
another's acquire) — but it was **never measured on real hardware**: the
post-fix multi-device ``_inflight`` trace, with the SAME instrumentation
that diagnosed the pre-fix gap, was an open item the brief flagged for
this remediation pass.

This harness re-runs the pre-fix instrumentation pattern on post-fix
code:

1. Monkeypatches ``scanner.byte_budget.ByteBudget`` so every
   ``acquire`` / ``release`` records the post-call ``_inflight`` and
   ``_budget`` keyed by the device the ``per_device_budgets`` factory
   tagged the instance with. Pure observation — no behaviour change.
2. Drives the real ``ScanWorker.run()`` on (D: + J:) for the multi-device
   measurement, then again on (J:) alone for the control.
3. Captures the per-device ``_inflight`` trace as ``(time, device,
   inflight_bytes, budget_bytes, op)`` rows, plus high-level stats
   (mean fill, peak fill, # samples at >=99 %).

Expected post-fix pattern: each device's budget fills / drains
**independently**. Pre-fix the global budget filled to ~86 % and never
drained during NAS-idle windows; post-fix the NAS budget should drain
normally (similar to the NAS-only control) even while D:'s budget is
full of clustered DNGs.

Why not just inspect the unit test
----------------------------------

The unit test pins ``one device's full budget does not block another's
acquire`` — necessary, not sufficient. The real-hardware question is
whether the multi-device timeline actually matches the NAS-only control
under the genuine clustered-ProRAW load that triggered #598. A unit test
with synthetic byte counts cannot exhibit the 100-130 MB DNG clustering
that pinned the pre-fix global budget.

CLI usage
---------

    python scripts/probe_byte_budget_598.py \\
        --sources "D:\\Takeout-0508" "J:\\圖片" --limit 2000 \\
        --output probe_598_dj.json

    # NAS-only control
    python scripts/probe_byte_budget_598.py \\
        --sources "J:\\圖片" --nas-only --limit 2000 \\
        --output probe_598_j_only.json

Pair the multi-device run against the control to check the
NAS-starvation symptom (NAS ``_inflight`` near 100 % while NAS-idle) is
gone post-fix.
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402


_DEFAULT_PER_SCAN_TIMEOUT = 1800.0
_INTERRUPT_GRACE = 8.0


@dataclass
class _BudgetSample:
    """One observation of (device, _inflight, _budget, op) at a wall-time."""
    t: float
    device: str
    inflight: int
    budget: int
    op: str  # "acquire" | "release" | "init"


@dataclass
class ProbeResult:
    sources: list[str]
    wall_s: float
    n_files_walked: int
    n_files_hashed: int
    per_device_readers: dict[str, int]
    samples: list[dict] = field(default_factory=list)  # _BudgetSample dicts
    progress_lines: list[str] = field(default_factory=list)
    cancelled: bool = False
    final_status: str = ""
    exiftool_orphans_post_scan: int = -1
    # Computed stats per device — populated post-scan.
    stats: dict = field(default_factory=dict)


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
    """Snapshot of currently-running ``exiftool.exe`` PIDs (Windows only).

    Used before/after the scan so the orphan check only counts processes
    that appeared during THIS scan and survived past its teardown —
    a concurrent process (parallel harness, user exiftool) won't pollute.
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
    except Exception:
        return set()


def install_byte_budget_probe(samples_list: list[_BudgetSample],
                              samples_lock: threading.Lock,
                              t0: float) -> dict[int, str]:
    """Monkeypatch ``scanner.byte_budget.per_device_budgets`` so every
    returned ``ByteBudget`` records its ``_inflight`` on each acquire /
    release. Returns a mapping {id(ByteBudget): device_key} so the caller
    can correlate samples back to devices.

    Patches ``per_device_budgets`` rather than ``ByteBudget.__init__``
    because the device-key context only exists at the factory level —
    a raw ``ByteBudget`` instance has no device tag.
    """
    import scanner.byte_budget as _bb
    real_factory = _bb.per_device_budgets
    id_to_device: dict[int, str] = {}

    def wrap_budget(budget, device_key: str) -> None:
        """Wrap acquire/release on a single ByteBudget instance to record samples."""
        id_to_device[id(budget)] = device_key
        real_acquire = budget.acquire
        real_release = budget.release

        def acquire_with_probe(n_bytes: int, timeout: float = 0.05) -> bool:
            result = real_acquire(n_bytes, timeout)
            if result:
                # Sample post-acquire state. _inflight is guarded by the budget's
                # internal lock; reading from another thread is racy by a few bytes
                # but the timeline shape is what matters.
                with samples_lock:
                    samples_list.append(_BudgetSample(
                        t=time.monotonic() - t0,
                        device=device_key,
                        inflight=budget._inflight,
                        budget=budget._budget,
                        op="acquire",
                    ))
            return result

        def release_with_probe(n_bytes: int) -> None:
            real_release(n_bytes)
            with samples_lock:
                samples_list.append(_BudgetSample(
                    t=time.monotonic() - t0,
                    device=device_key,
                    inflight=budget._inflight,
                    budget=budget._budget,
                    op="release",
                ))

        budget.acquire = acquire_with_probe  # type: ignore[method-assign]
        budget.release = release_with_probe  # type: ignore[method-assign]

    def probing_factory(total_bytes, device_keys, cancel_check):
        budgets = real_factory(total_bytes, device_keys, cancel_check)
        for dev_key, budget in budgets.items():
            wrap_budget(budget, dev_key)
            # Record the "init" baseline so we have an explicit anchor for each device.
            with samples_lock:
                samples_list.append(_BudgetSample(
                    t=time.monotonic() - t0,
                    device=dev_key,
                    inflight=0,
                    budget=budget._budget,
                    op="init",
                ))
        return budgets

    _bb.per_device_budgets = probing_factory
    # Also patch the import alias used by scan_worker — it imports the
    # function by name, so the reference resolved at module-load time
    # may not see our monkeypatch. Patch both sites to be safe.
    import app.views.workers.scan_worker as _sw
    if hasattr(_sw, "per_device_budgets"):
        _sw.per_device_budgets = probing_factory
    return id_to_device


def compute_device_stats(samples: list[_BudgetSample]) -> dict:
    """Aggregate per-device fill statistics for one scan."""
    by_dev: dict[str, list[_BudgetSample]] = {}
    for s in samples:
        by_dev.setdefault(s.device, []).append(s)
    stats = {}
    for dev, ss in by_dev.items():
        if not ss:
            continue
        fills = [s.inflight / s.budget for s in ss if s.budget > 0]
        if not fills:
            continue
        n_high = sum(1 for f in fills if f >= 0.99)
        stats[dev] = {
            "n_samples": len(fills),
            "mean_fill": statistics.mean(fills),
            "peak_fill": max(fills),
            "n_samples_above_99pct": n_high,
            "fill_above_99pct_ratio": n_high / len(fills),
            "budget_bytes": ss[0].budget,
        }
    return stats


def run_one_scan(*, sources: list[str], limit: int | None,
                 per_scan_timeout: float, workers: int) -> ProbeResult:
    from app.views.workers.scan_worker import ScanWorker

    probes = [probe_device(src) for src in sources]
    print(f"\n=== probe_598 scan ===")
    for p in probes:
        print(f"  PROBE {p}")
    per_device_readers = {p["device_key"]: p["hash_workers_for_root"] for p in probes}

    out_dir = Path(__file__).resolve().parent.parent / ".probe_598_artifacts"
    out_dir.mkdir(exist_ok=True)
    manifest_path = out_dir / "manifest.sqlite"
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
        hash_pool="thread",
        autotune_read_knee=False,  # keep the variable count clean — this probes byte-budget, not autotune
    )

    # Install the byte-budget probe BEFORE worker.start().
    samples: list[_BudgetSample] = []
    samples_lock = threading.Lock()
    t0 = time.monotonic()
    install_byte_budget_probe(samples, samples_lock, t0)

    sig_lock = threading.Lock()
    progress_lines: list[str] = []
    final_status: list[str] = [""]

    def on_progress(msg: str) -> None:
        with sig_lock:
            progress_lines.append(msg)

    def on_finished(_path: str) -> None:
        with sig_lock:
            final_status[0] = "Done."

    def on_failed(msg: str) -> None:
        with sig_lock:
            final_status[0] = msg

    def on_empty() -> None:
        with sig_lock:
            final_status[0] = "Done. (empty)"

    worker.progress.connect(on_progress, Qt.ConnectionType.DirectConnection)
    worker.finished.connect(on_finished, Qt.ConnectionType.DirectConnection)
    worker.failed.connect(on_failed, Qt.ConnectionType.DirectConnection)
    worker.completed_empty.connect(on_empty, Qt.ConnectionType.DirectConnection)

    pre_pids = snapshot_exiftool_pids()
    worker.start()
    deadline = t0 + per_scan_timeout
    while not worker.wait(1000):
        if time.monotonic() > deadline:
            print(f"  TIMEOUT after {per_scan_timeout:.0f}s — requesting interruption")
            worker.requestInterruption()
            if not worker.wait(int(_INTERRUPT_GRACE * 1000)):
                print(f"  WARN: worker.wait({_INTERRUPT_GRACE:.0f}s) timed out AFTER interrupt")
            break
    wall_s = time.monotonic() - t0

    with sig_lock:
        status = final_status[0]
    cancelled = status == "Scan cancelled."

    time.sleep(0.3)
    post_pids = snapshot_exiftool_pids()
    this_scan_orphans = (post_pids - pre_pids)
    n_orphans = len(this_scan_orphans)
    if n_orphans > 0:
        print(f"  ALERT: {n_orphans} new exiftool.exe PIDs survived this scan: "
              f"{sorted(this_scan_orphans)}")

    n_walked = 0
    n_hashed = 0
    with sig_lock:
        for line in progress_lines:
            if line.startswith("Hashing ") and "files across" in line:
                try:
                    n_walked = int(line.split()[1].replace(",", ""))
                except (ValueError, IndexError):
                    pass
            if line.startswith("  Hashed "):
                try:
                    n_hashed = int(line.split()[1].split("/")[0].replace(",", ""))
                except (ValueError, IndexError):
                    pass

    with samples_lock:
        samples_copy = list(samples)
    stats = compute_device_stats(samples_copy)

    print(f"  wall_s={wall_s:.2f}  n_walked={n_walked} n_hashed={n_hashed}")
    print(f"  per_device_stats:")
    for dev, st in stats.items():
        print(f"    {dev!r}: mean_fill={st['mean_fill']:.1%} "
              f"peak_fill={st['peak_fill']:.1%} "
              f">99%_ratio={st['fill_above_99pct_ratio']:.1%} "
              f"({st['n_samples_above_99pct']}/{st['n_samples']}) "
              f"budget={st['budget_bytes'] / (1024**3):.2f}GiB")

    return ProbeResult(
        sources=list(sources),
        wall_s=wall_s,
        n_files_walked=n_walked,
        n_files_hashed=n_hashed,
        per_device_readers=per_device_readers,
        samples=[asdict(s) for s in samples_copy],
        progress_lines=list(progress_lines),
        cancelled=cancelled,
        final_status=status or "(no signal)",
        exiftool_orphans_post_scan=n_orphans,
        stats=stats,
    )


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sources", nargs="+", required=True)
    p.add_argument("--limit", type=int, default=2000)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--output", required=True)
    p.add_argument("--per-scan-timeout", type=float, default=_DEFAULT_PER_SCAN_TIMEOUT)
    p.add_argument("--nas-only", action="store_true")
    args = p.parse_args(argv[1:])

    app = QCoreApplication.instance() or QCoreApplication([])
    _ = app

    from scanner.workers import is_remote_drive, device_key
    sources = list(args.sources)
    if args.nas_only:
        sources = [s for s in sources if is_remote_drive(device_key(s))]
        if not sources:
            print("ERROR: --nas-only filtered out every source", file=sys.stderr)
            return 2

    print(f"=== probe_byte_budget_598 ===")
    print(f"sources={sources}  limit={args.limit}  workers={args.workers}")

    result = run_one_scan(sources=sources, limit=args.limit,
                          per_scan_timeout=args.per_scan_timeout,
                          workers=args.workers)

    out = {
        "args": {"sources": sources, "limit": args.limit,
                 "workers": args.workers, "nas_only": args.nas_only},
        "result": asdict(result),
    }
    Path(args.output).write_text(json.dumps(out, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    print(f"\nwrote {args.output}")
    print(f"sample count: {len(result.samples)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
