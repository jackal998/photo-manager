"""Monitor all python.exe processes during a scan — surface what spawn
workers are doing that Task Manager hides if you only watch the parent.

The post-#609 process-pool path runs HASH compute in spawn worker
subprocesses, not in the main ScanWorker QThread. If the user looks at
their app's python.exe in Task Manager, they see the parent (mostly
idle) and miss the 8 worker processes doing the real work. This script
samples every python.exe by PID every 2s, captures CPU times +
working-set + network, and reports per-second deltas after the scan.

CLI:
    python scripts/probe_process_monitor.py \\
        --sources "D:\\Takeout-0508" "H:\\Photos\\MobileBackup" "J:\\圖片" \\
        --limit 1500 --hash-pool auto --output monitor.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402


def snapshot_python_processes() -> list[dict]:
    """List every python.exe PID + CPU time (kernel+user, ms) + memory (KB)
    via WMIC (faster than spawning psutil; no install needed)."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "processid,kernelmodetime,usermodetime,workingsetsize", "/format:csv"],
            capture_output=True, text=True, timeout=5,
        )
        rows = []
        for line in out.stdout.strip().splitlines()[1:]:  # skip header
            cols = line.split(",")
            if len(cols) < 5:
                continue
            try:
                # WMIC csv: Node,KernelModeTime,ProcessId,UserModeTime,WorkingSetSize
                rows.append({
                    "pid": int(cols[2].strip() or 0),
                    "kernel_100ns": int(cols[1].strip() or 0),
                    "user_100ns": int(cols[3].strip() or 0),
                    "ws_kb": int(int(cols[4].strip() or 0) / 1024),
                })
            except (ValueError, IndexError):
                continue
        return [r for r in rows if r["pid"] > 0]
    except Exception:
        return []


def snapshot_exiftool() -> int:
    if sys.platform != "win32":
        return 0
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq exiftool.exe", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=5,
        )
        return 0 if "INFO:" in out.stdout else len([
            line for line in out.stdout.splitlines() if "exiftool" in line.lower()
        ])
    except Exception:
        return 0


def sample_loop(interval: float, samples: list, stop_event: threading.Event,
                t0: float, my_pid: int):
    """Background sampler — every `interval`s, snapshot python.exe processes."""
    last_by_pid: dict[int, dict] = {}
    while not stop_event.is_set():
        t = time.monotonic() - t0
        procs = snapshot_python_processes()
        per_proc_cpu_pct: dict[int, float] = {}
        for p in procs:
            pid = p["pid"]
            prev = last_by_pid.get(pid)
            if prev:
                delta_ns = (p["kernel_100ns"] - prev["kernel_100ns"]
                            + p["user_100ns"] - prev["user_100ns"])
                delta_sec = delta_ns / 1e7
                cpu_pct = (delta_sec / interval) * 100
                per_proc_cpu_pct[pid] = cpu_pct
            last_by_pid[pid] = p
        total_cpu = sum(per_proc_cpu_pct.values())
        total_ws_mb = sum(p["ws_kb"] for p in procs) / 1024
        worker_pids = [p["pid"] for p in procs if p["pid"] != my_pid]
        worker_cpu = sum(per_proc_cpu_pct.get(p, 0) for p in worker_pids)
        samples.append({
            "t": round(t, 1),
            "n_python": len(procs),
            "n_workers": max(0, len(procs) - 2),  # parent + sampler approx
            "total_cpu_pct": round(total_cpu, 1),
            "worker_cpu_pct": round(worker_cpu, 1),
            "total_ws_mb": round(total_ws_mb, 0),
            "exiftool_count": snapshot_exiftool(),
        })
        stop_event.wait(interval)


def run_scan(sources, limit, hash_pool, autotune, exif_workers, workers, timeout):
    from app.views.workers.scan_worker import ScanWorker
    out_dir = Path(__file__).resolve().parent.parent / ".probe_monitor_artifacts"
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
        hash_pool=hash_pool,
        exif_workers=exif_workers,
        autotune_read_knee=autotune,
    )

    progress: list[str] = []
    final_status = [""]
    sig_lock = threading.Lock()

    def on_progress(msg):
        with sig_lock: progress.append(msg)
    def on_finished(p):
        with sig_lock: final_status[0] = "Done."
    def on_failed(m):
        with sig_lock: final_status[0] = m

    worker.progress.connect(on_progress, Qt.ConnectionType.DirectConnection)
    worker.finished.connect(on_finished, Qt.ConnectionType.DirectConnection)
    worker.failed.connect(on_failed, Qt.ConnectionType.DirectConnection)

    import os
    samples: list[dict] = []
    stop = threading.Event()
    t0 = time.monotonic()
    sampler = threading.Thread(
        target=sample_loop,
        args=(2.0, samples, stop, t0, os.getpid()),
        daemon=True,
    )
    sampler.start()

    worker.start()
    deadline = t0 + timeout
    while not worker.wait(1000):
        if time.monotonic() > deadline:
            print(f"  TIMEOUT after {timeout:.0f}s — interrupting")
            worker.requestInterruption()
            worker.wait(8000)
            break
    wall_s = time.monotonic() - t0
    stop.set()
    sampler.join(timeout=3)

    with sig_lock:
        status = final_status[0]
    return {
        "wall_s": wall_s, "status": status,
        "sources": sources, "config": {"hash_pool": hash_pool,
                                       "autotune_read_knee": autotune,
                                       "workers": workers,
                                       "exif_workers": exif_workers,
                                       "limit": limit},
        "samples": samples,
        "progress_tail": progress[-30:],
    }


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sources", nargs="+", required=True)
    p.add_argument("--limit", type=int, default=1500)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--exif-workers", type=int, default=2)
    p.add_argument("--hash-pool", choices=("thread", "process", "auto"),
                   default="auto")
    p.add_argument("--no-autotune", dest="autotune", action="store_false",
                   default=True)
    p.add_argument("--per-scan-timeout", type=float, default=900.0)
    p.add_argument("--output", required=True)
    args = p.parse_args(argv[1:])

    app = QCoreApplication.instance() or QCoreApplication([])
    _ = app

    print(f"=== probe_process_monitor sources={args.sources} hash_pool={args.hash_pool} ===")
    r = run_scan(args.sources, args.limit, args.hash_pool, args.autotune,
                 args.exif_workers, args.workers, args.per_scan_timeout)
    Path(args.output).write_text(json.dumps(r, indent=2, ensure_ascii=False,
                                            default=str), encoding="utf-8")
    print(f"\n=== SUMMARY wall_s={r['wall_s']:.1f} status={r['status']!r} ===")
    # Print sparse timeline: every 6th sample (~12s intervals)
    print(f"\n{'t':>5} {'n_py':>5} {'workers':>7} {'tot_cpu%':>9} {'wkr_cpu%':>9} {'WS_MB':>7} {'exif':>4}")
    for s in r["samples"][::6]:
        print(f"{s['t']:>5.0f} {s['n_python']:>5} {s['n_workers']:>7} "
              f"{s['total_cpu_pct']:>9.1f} {s['worker_cpu_pct']:>9.1f} "
              f"{s['total_ws_mb']:>7.0f} {s['exiftool_count']:>4}")
    # Aggregate stats
    if r["samples"]:
        cpu = [s["total_cpu_pct"] for s in r["samples"]]
        wkr = [s["worker_cpu_pct"] for s in r["samples"]]
        ws = [s["total_ws_mb"] for s in r["samples"]]
        n_idle = sum(1 for c in cpu if c < 50)
        print(f"\nTotal samples: {len(r['samples'])}, idle (<50% CPU) samples: {n_idle}")
        print(f"CPU pct: mean={sum(cpu)/len(cpu):.1f} max={max(cpu):.1f} min={min(cpu):.1f}")
        print(f"Worker CPU pct: mean={sum(wkr)/len(wkr):.1f} max={max(wkr):.1f}")
        print(f"WS MB: mean={sum(ws)/len(ws):.0f} max={max(ws):.0f}")
    print(f"\nwrote {args.output}")


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    raise SystemExit(main(sys.argv))
