"""scan.py — Deduplication scanner CLI.

Walks one or more source directories, computes SHA-256 + pHash for every media
file, detects exact duplicates, cross-format duplicates, and near-duplicates,
then writes a non-destructive migration_manifest.sqlite for human review.

Usage examples:
  # Full recursive scan (all subdirectories included)
  python scan.py \\
    --source photos="C:\\path\\to\\photo\\library" \\
    --source backup="\\\\NAS\\Photos\\MobileBackup" \\
    --output migration_manifest.sqlite

  # Mix recursive + top-level-only sources
  python scan.py \\
    --source archive="D:\\Archive" \\
    --source-flat inbox="D:\\Inbox" \\
    --output migration_manifest.sqlite

  # Summary only, no DB written
  python scan.py --source ... --dry-run

  # Debug: cap to 100 files per source (avoids full network read)
  python scan.py --source ... --dry-run --limit 100

  # Tighter near-duplicate threshold
  python scan.py --source ... --similarity-threshold 6
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from tqdm import tqdm
    _TQDM = True
except ImportError:
    _TQDM = False


def _parse_source(value: str) -> tuple[str, Path]:
    """Parse 'label=path' into (label, Path)."""
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"--source must be in 'label=path' format, got: {value!r}"
        )
    label, _, raw_path = value.partition("=")
    return label.strip(), Path(raw_path.strip())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Non-destructive deduplication scan → migration_manifest.sqlite"
    )
    parser.add_argument(
        "--source",
        action="append",
        metavar="LABEL=PATH",
        help="Recursive source to scan, e.g. photos='D:\\Pictures' (repeatable). "
             "Sources are listed in priority order (first = highest priority).",
    )
    parser.add_argument(
        "--source-flat",
        action="append",
        metavar="LABEL=PATH",
        dest="source_flat",
        help="Like --source but scans only the immediate folder (non-recursive). "
             "Flat sources are listed after --source entries in priority order.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("migration_manifest.sqlite"),
        help="Output SQLite path (default: migration_manifest.sqlite)",
    )
    parser.add_argument(
        "--similarity-threshold",
        type=int,
        default=10,
        dest="threshold",
        help="pHash hamming distance for REVIEW_DUPLICATE (default: 10)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print summary only; do not write the manifest file",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Cap to N files per source — for debugging without reading the full network share",
    )
    args = parser.parse_args()

    if not args.source and not args.source_flat:
        parser.error("at least one --source or --source-flat is required")

    sources: dict[str, Path] = {}
    recursive_map: dict[str, bool] = {}
    source_priority: dict[str, int] = {}
    priority = 0

    for raw in (args.source or []):
        label, path = _parse_source(raw)
        sources[label] = path
        recursive_map[label] = True
        source_priority[label] = priority
        priority += 1

    for raw in (args.source_flat or []):
        label, path = _parse_source(raw)
        sources[label] = path
        recursive_map[label] = False
        source_priority[label] = priority
        priority += 1

    # --- Import scanner modules (deferred so --help works without dependencies) ---
    from scanner.walker import scan_sources
    from scanner.hasher import compute_sha256, compute_phash
    from scanner.exif import ExiftoolProcess, batch_read_dates
    from scanner.dedup import HashResult, classify
    from scanner.manifest import write_manifest, print_summary

    print("Read-only scan — no files will be moved or deleted.", flush=True)
    print("MOVE / SKIP / REVIEW in the results are planned actions only.\n", flush=True)

    # --- Walk sources (print per-source progress) ---
    limit_note = f" (capped at {args.limit} per source)" if args.limit else ""
    print(f"Scanning {len(sources)} source(s){limit_note}…", flush=True)
    records = []
    for label, root in sources.items():
        mode = "flat" if recursive_map.get(label) is False else "recursive"
        print(f"  Walking {label} ({mode}): {root} …", end=" ", flush=True)
        partial = scan_sources(
            {label: root},
            limit=args.limit,
            recursive_map={label: recursive_map.get(label, True)},
        )
        print(f"{len(partial):,} files", flush=True)
        records.extend(partial)
    print(f"  Total: {len(records):,} media files", flush=True)

    # --- Batch EXIF date extraction via exiftool (chunked) ---
    all_paths = [r.path for r in records]
    chunk_size = 500
    n_chunks = (len(all_paths) + chunk_size - 1) // chunk_size
    print(f"Reading EXIF dates ({len(all_paths):,} files, {n_chunks} chunk(s))…", flush=True)
    try:
        with ExiftoolProcess() as et:
            dates = {}
            for i in range(0, len(all_paths), chunk_size):
                chunk = all_paths[i: i + chunk_size]
                dates.update(batch_read_dates(chunk, et, chunk_size=chunk_size))
                done = min(i + chunk_size, len(all_paths))
                print(f"  EXIF {done:,}/{len(all_paths):,}", end="\r", flush=True)
            print(f"  EXIF done — {sum(1 for v in dates.values() if v):,} dates found", flush=True)
    except FileNotFoundError:
        print(
            "\nWARNING: exiftool not found on PATH — EXIF dates unavailable.\n"
            "Install from https://exiftool.org/ and ensure it is in your PATH.",
            file=sys.stderr,
        )
        dates = {p: None for p in all_paths}

    # --- Compute SHA-256 + pHash ---
    print(f"Hashing {len(records):,} files…", flush=True)
    hash_results: list[HashResult] = []
    iterable = tqdm(records, desc="Hashing", unit="file") if _TQDM else records
    for record in iterable:
        sha256 = compute_sha256(record.path)
        phash = compute_phash(record.path, record.file_type)
        hash_results.append(HashResult(
            record=record,
            sha256=sha256,
            phash=phash,
            exif_date=dates.get(record.path),
        ))
    if not _TQDM:
        print("  Hashing done.", flush=True)

    print("Classifying…", flush=True)
    rows = classify(hash_results, threshold=args.threshold, source_priority=source_priority)

    print_summary(rows)

    if args.dry_run:
        print("--dry-run: manifest not written.", flush=True)
        return 0

    write_manifest(rows, args.output)
    print(f"Manifest written to: {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
