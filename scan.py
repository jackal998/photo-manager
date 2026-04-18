"""scan.py — Deduplication scanner CLI.

Walks 3 source directories, computes SHA-256 + pHash for every media file,
detects exact duplicates, cross-format duplicates, and near-duplicates, then
writes a non-destructive migration_manifest.sqlite for human review.

Usage examples:
  # Full scan
  python scan.py \\
    --source iphone="\\\\LinXiaoYun\\home\\Photos\\MobileBackup\\iPhone" \\
    --source takeout="D:\\Downloads\\Takeout\\Google 相簿" \\
    --source jdrive="J:\\圖片" \\
    --output migration_manifest.sqlite

  # Summary only, no DB written
  python scan.py --source ... --dry-run

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
        required=True,
        help="Source to scan, e.g. iphone='\\\\NAS\\Photos\\MobileBackup\\iPhone' (repeatable)",
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
    args = parser.parse_args()

    sources: dict[str, Path] = {}
    for raw in args.source:
        label, path = _parse_source(raw)
        sources[label] = path

    # --- Import scanner modules (deferred so --help works without dependencies) ---
    from scanner.walker import scan_sources
    from scanner.hasher import compute_sha256, compute_phash
    from scanner.exif import ExiftoolProcess, batch_read_dates
    from scanner.dedup import HashResult, classify
    from scanner.manifest import write_manifest, print_summary

    print(f"Scanning {len(sources)} source(s)…")
    records = scan_sources(sources)
    print(f"  Found {len(records):,} media files")

    print("Computing hashes…")
    hash_results: list[HashResult] = []

    # Batch EXIF date extraction via exiftool
    print("Reading EXIF dates (exiftool)…")
    all_paths = [r.path for r in records]
    try:
        with ExiftoolProcess() as et:
            dates = batch_read_dates(all_paths, et)
    except FileNotFoundError:
        print(
            "WARNING: exiftool not found on PATH — EXIF dates unavailable.\n"
            "Install from https://exiftool.org/ and ensure it is in your PATH.",
            file=sys.stderr,
        )
        dates = {p: None for p in all_paths}

    # Compute SHA-256 + pHash
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

    print("Classifying…")
    rows = classify(hash_results, threshold=args.threshold)

    print_summary(rows)

    if args.dry_run:
        print("--dry-run: manifest not written.")
        return 0

    write_manifest(rows, args.output)
    print(f"Manifest written to: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
