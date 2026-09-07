"""H1 probe: concurrency-depth read knee ladder over a target directory.

Mimics the hash stage's read path EXACTLY (``Path.read_bytes()`` — see
``scanner/hasher.py:read_for_record``/``compute_hashes``) with no decode/
compute on top, so any rung-to-rung throughput change is attributable to
read concurrency alone. Existing knee-detection code
(``scanner/autotune.py`` / the #551 ramp) only climbs 1->2->4->8; this
probe extends the ladder past 8 to test H1 (does NAS throughput keep
climbing beyond the current cap on the owner's real hardware).

Cold-file discipline: the corpus is shuffled once (``--shuffle-seed``) and
consumed by a single shared iterator across rungs, so no rung re-reads a
file an earlier rung already pulled through the OS/NAS page cache. This
does NOT eliminate NAS-side (Synology DSM) caching, which lives beyond
this process's control — the runbook's mitigation is a corpus large
enough, and shuffled enough, that repeat-directory locality stays low.

Each rung runs a bounded pipeline: up to ``--concurrency`` reads in flight
at once, replenished from the shared file iterator as each completes,
until either the per-rung file budget (``--files-per-rung``) or the
per-rung time budget (``--seconds-per-rung``, default 30s) is hit —
whichever the caller asked for. If the corpus is exhausted mid-rung the
rung stops early and ``files_exhausted`` is set.

CLI usage
---------

    python scripts/probe_read_knee_ladder.py --root D:\\nas-probe-corpus \\
        --rungs "1,2,4,8,16,32,64" --seconds-per-rung 30 \\
        --output probe_read_knee.json

JSON schema
-----------

    {
      "env": {"host": str, "python": str, "timestamp": str, "root": str,
              "file_count": int, "size_histogram": {...}},
      "args": {...},
      "rungs": [
        {"concurrency": int, "files_read": int, "errors": int,
         "total_bytes": int, "wall_s": float, "files_per_s": float,
         "mb_per_s": float, "p50_latency_ms": float, "p95_latency_ms": float,
         "files_exhausted": bool}
      ]
    }
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import random
import socket
import statistics
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_DEFAULT_RUNGS = "1,2,4,8,16,32,64"
_DEFAULT_SECONDS_PER_RUNG = 30.0


def discover_files(root: Path) -> list[Path]:
    """All regular files under ``root`` (recursive)."""
    files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            files.append(Path(dirpath) / fn)
    return files


def _size_histogram(files: list[Path]) -> dict:
    sizes = []
    for f in files:
        try:
            sizes.append(f.stat().st_size)
        except OSError:
            continue
    if not sizes:
        return {"count": 0}
    sizes.sort()
    return {
        "count": len(sizes),
        "min_bytes": sizes[0],
        "max_bytes": sizes[-1],
        "mean_bytes": statistics.mean(sizes),
        "median_bytes": statistics.median(sizes),
        "total_bytes": sum(sizes),
    }


def _read_one(path: Path) -> tuple[float, int, bool]:
    """Read-only, no decode — the exact hasher READ-stage operation."""
    t0 = time.perf_counter()
    try:
        data = path.read_bytes()
        return time.perf_counter() - t0, len(data), True
    except OSError:
        return time.perf_counter() - t0, 0, False


def run_rung(
    files: Iterator[Path], concurrency: int, *,
    seconds: float | None, count: int | None,
) -> dict:
    """Bounded ``concurrency``-deep read pipeline over ``files``.

    Stops on whichever of ``seconds`` / ``count`` is reached first (only
    one is expected to be set by the caller); always drains in-flight reads
    before returning.
    """
    latencies: list[float] = []
    total_bytes = 0
    n_files = 0
    n_errors = 0
    exhausted = False
    deadline = time.perf_counter() + seconds if seconds is not None else None

    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        in_flight: dict = {}

        def submit_next() -> bool:
            nonlocal exhausted
            try:
                p = next(files)
            except StopIteration:
                exhausted = True
                return False
            in_flight[ex.submit(_read_one, p)] = p
            return True

        for _ in range(concurrency):
            if not submit_next():
                break

        while in_flight:
            done, _pending = wait(in_flight, timeout=1.0, return_when=FIRST_COMPLETED)
            for fut in done:
                dt, n, ok = fut.result()
                del in_flight[fut]
                latencies.append(dt)
                if ok:
                    total_bytes += n
                    n_files += 1
                else:
                    n_errors += 1

            time_up = deadline is not None and time.perf_counter() >= deadline
            count_up = count is not None and (n_files + n_errors + len(in_flight)) >= count
            if not (time_up or count_up):
                for _ in range(len(done)):
                    submit_next()

    wall = time.perf_counter() - t_start
    latencies.sort()
    p50 = latencies[int(0.50 * (len(latencies) - 1))] if latencies else 0.0
    p95 = latencies[int(0.95 * (len(latencies) - 1))] if latencies else 0.0
    return {
        "concurrency": concurrency,
        "files_read": n_files,
        "errors": n_errors,
        "total_bytes": total_bytes,
        "wall_s": round(wall, 3),
        "files_per_s": round(n_files / wall, 2) if wall > 0 else 0.0,
        "mb_per_s": round((total_bytes / 1e6) / wall, 2) if wall > 0 else 0.0,
        "p50_latency_ms": round(p50 * 1000, 2),
        "p95_latency_ms": round(p95 * 1000, 2),
        "files_exhausted": exhausted,
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", required=True)
    p.add_argument("--rungs", default=_DEFAULT_RUNGS,
                    help=f"Comma-separated concurrency levels (default {_DEFAULT_RUNGS!r})")
    p.add_argument("--seconds-per-rung", type=float, default=_DEFAULT_SECONDS_PER_RUNG,
                    help="Time-box per rung (mutually exclusive with --files-per-rung)")
    p.add_argument("--files-per-rung", type=int, default=None,
                    help="Fixed file-count budget per rung (overrides --seconds-per-rung)")
    p.add_argument("--shuffle-seed", type=int, default=42)
    p.add_argument("--output", default=None)
    args = p.parse_args(argv[1:])

    root = Path(args.root)
    if not root.is_dir():
        print(f"ERROR: --root {root} is not a directory", file=sys.stderr)
        return 2

    rungs = [int(x) for x in args.rungs.split(",") if x.strip()]

    print(f"=== probe_read_knee_ladder === root={root} rungs={rungs}")
    all_files = discover_files(root)
    if not all_files:
        print(f"ERROR: no files found under {root}", file=sys.stderr)
        return 2

    rng = random.Random(args.shuffle_seed)
    rng.shuffle(all_files)
    file_iter = iter(all_files)

    env = {
        "host": socket.gethostname(),
        "python": platform.python_version(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "shuffle_seed": args.shuffle_seed,
        "size_histogram": _size_histogram(all_files),
    }
    print(f"  discovered {env['size_histogram'].get('count', 0)} files, "
          f"mean {env['size_histogram'].get('mean_bytes', 0):.0f} bytes")

    seconds = None if args.files_per_rung is not None else args.seconds_per_rung
    count = args.files_per_rung

    rung_results = []
    for c in rungs:
        print(f"  rung c={c} ...")
        result = run_rung(file_iter, c, seconds=seconds, count=count)
        print(f"    files_per_s={result['files_per_s']}  mb_per_s={result['mb_per_s']}  "
              f"p50_ms={result['p50_latency_ms']}  p95_ms={result['p95_latency_ms']}  "
              f"exhausted={result['files_exhausted']}")
        rung_results.append(result)
        if result["files_exhausted"]:
            print("  WARNING: corpus exhausted mid-ladder; remaining rungs will read 0 files")

    out = {
        "env": env,
        "args": {
            "root": str(root), "rungs": rungs,
            "seconds_per_rung": args.seconds_per_rung,
            "files_per_rung": args.files_per_rung,
            "shuffle_seed": args.shuffle_seed,
        },
        "rungs": rung_results,
    }
    payload = json.dumps(out, indent=2, ensure_ascii=False)
    print("\n--- JSON ---")
    print(payload)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
