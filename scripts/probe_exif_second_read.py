"""H3 probe: cost of the exiftool second-read pass relative to the hash read pass.

Two sequential, read-only phases over the SAME file list (capped at
``--max-files``):

  Phase A — whole-file ``Path.read_bytes()`` (hash-shaped): mirrors the
  hasher's READ stage (``scanner/hasher.py:read_for_record``), run at
  ``scanner.workers.hash_workers_for_root(root)`` concurrency — the exact
  worker count the real scan pipeline would pick for this device.

  Phase B — ``scanner.exif.batch_read_extracts`` over a single real
  ``-stay_open`` ``ExiftoolProcess`` (``scanner/exif.py:487``), chunked at
  the production ``_EXIF_CHUNK`` (500 files/call) — the exact call the
  scan pipeline's scoring extraction (#187) makes. Read-only: the tag
  selectors in ``_read_extract_chunk`` never write.

Reports each phase's wall time / throughput, then Phase B's wall-time
SHARE relative to Phase A — the number H3 asks for ("does the exiftool
pass roughly double read cost, or is it a smaller tax on this NAS").

CLI usage
---------

    python scripts/probe_exif_second_read.py --root D:\\nas-probe-corpus \\
        --max-files 2000 --output probe_exif_second_read.json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_DEFAULT_MAX_FILES = 2000


def discover_files(root: Path, max_files: int) -> list[Path]:
    files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            files.append(Path(dirpath) / fn)
            if len(files) >= max_files:
                return files
    return files


def phase_a_read(files: list[Path], workers: int) -> dict:
    """Whole-file read_bytes pass — the hasher's READ-stage shape."""

    def _read(p: Path) -> tuple[int, bool]:
        try:
            return len(p.read_bytes()), True
        except OSError:
            return 0, False

    t0 = time.perf_counter()
    total_bytes = 0
    errors = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for n, ok in ex.map(_read, files):
            if ok:
                total_bytes += n
            else:
                errors += 1
    wall = time.perf_counter() - t0
    return {
        "phase": "A_read_bytes",
        "workers": workers,
        "files": len(files),
        "errors": errors,
        "total_bytes": total_bytes,
        "wall_s": round(wall, 3),
        "files_per_s": round(len(files) / wall, 2) if wall > 0 else 0.0,
        "mb_per_s": round((total_bytes / 1e6) / wall, 2) if wall > 0 else 0.0,
    }


def phase_b_exif(files: list[Path]) -> dict:
    """The real production exiftool batch-extract pass — single process, sequential chunks."""
    from scanner.exif import ExiftoolProcess, batch_read_extracts

    t0 = time.perf_counter()
    with ExiftoolProcess() as et:
        results = batch_read_extracts(files, et)
    wall = time.perf_counter() - t0
    n_errors = sum(1 for r in results.values() if r.extraction_errors)
    return {
        "phase": "B_exiftool_batch_extracts",
        "files": len(files),
        "records_returned": len(results),
        "records_with_errors": n_errors,
        "wall_s": round(wall, 3),
        "files_per_s": round(len(files) / wall, 2) if wall > 0 else 0.0,
    }


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", required=True)
    p.add_argument("--max-files", type=int, default=_DEFAULT_MAX_FILES)
    p.add_argument("--workers", type=int, default=None,
                    help="Phase A concurrency; default = hash_workers_for_root(root)")
    p.add_argument("--output", default=None)
    args = p.parse_args(argv[1:])

    root = Path(args.root)
    if not root.is_dir():
        print(f"ERROR: --root {root} is not a directory", file=sys.stderr)
        return 2

    from scanner.workers import device_key, hash_workers_for_root, is_remote_drive

    dk = device_key(str(root))
    workers = args.workers if args.workers is not None else hash_workers_for_root(dk)

    env = {
        "host": socket.gethostname(),
        "python": platform.python_version(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "device_key": dk,
        "is_remote_drive": is_remote_drive(dk),
        "hash_workers_for_root": workers,
    }
    print(f"=== probe_exif_second_read === root={root} device_key={dk!r} "
          f"is_remote_drive={env['is_remote_drive']} workers={workers}")

    files = discover_files(root, args.max_files)
    if not files:
        print(f"ERROR: no files found under {root}", file=sys.stderr)
        return 2
    print(f"  files={len(files)}")

    print("  phase A (read_bytes) ...")
    a = phase_a_read(files, workers)
    print(f"    wall_s={a['wall_s']}  files_per_s={a['files_per_s']}  mb_per_s={a['mb_per_s']}")

    print("  phase B (exiftool batch_read_extracts) ...")
    b = phase_b_exif(files)
    print(f"    wall_s={b['wall_s']}  files_per_s={b['files_per_s']}  "
          f"records_with_errors={b['records_with_errors']}")

    share = (b["wall_s"] / a["wall_s"]) if a["wall_s"] > 0 else None
    print(f"  phase_b_share_of_phase_a = {share}")

    out = {
        "env": env,
        "args": {"root": str(root), "max_files": args.max_files, "workers": workers},
        "phase_a": a,
        "phase_b": b,
        "phase_b_share_of_phase_a": round(share, 3) if share is not None else None,
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
