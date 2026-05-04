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
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        metavar="N",
        help="Parallel file-hashing workers (default: 4). "
             "Tune to storage type: NAS → 4–8, local SSD → 2–4, local HDD → 1–2. "
             "Use 1 to disable parallelism.",
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
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from scanner.walker import scan_sources
    from scanner.hasher import compute_hashes
    from scanner.exif import ExiftoolProcess, batch_read_dates, parse_exif_date
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

    # --- Hash + PIL EXIF (parallel) ---
    # Each worker reads the file once: SHA-256, pHash, and EXIF date for JPEG/PNG
    # are all extracted from the same in-memory buffer.
    # HEIC / RAW / MOV / MP4 return no date here — a targeted exiftool batch follows.
    chunk_size = 500
    _EXIFTOOL_TYPES = frozenset(("heic", "raw", "mov", "mp4"))

    print(f"Hashing {len(records):,} files (workers={args.workers})…", flush=True)
    hash_results: list[HashResult] = [None] * len(records)  # type: ignore[list-item]
    skipped: list[tuple] = []  # (path, exc type, exc msg)

    def _hash_one(idx_record: tuple) -> tuple:
        idx, record = idx_record
        try:
            sha256, phash, mean_color, raw_date, px_w, px_h = compute_hashes(record.path, record.file_type)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # One bad file must never abort the whole scan — log + skip.
            skipped.append((record.path, type(exc).__name__, str(exc)))
            return idx, None
        pil_date = parse_exif_date(raw_date) if raw_date else None
        return idx, HashResult(
            record=record, sha256=sha256, phash=phash, mean_color=mean_color,
            exif_date=pil_date, pixel_width=px_w, pixel_height=px_h,
        )

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_hash_one, (i, r)): i for i, r in enumerate(records)}
        iterable = tqdm(as_completed(futures), total=len(records), desc="Hashing", unit="file") \
            if _TQDM else as_completed(futures)
        for future in iterable:
            idx, result = future.result()
            hash_results[idx] = result
            done += 1
            if not _TQDM and (done % 500 == 0 or done == len(records)):
                print(f"  Hashed {done:,}/{len(records):,}", end="\r", flush=True)
    if not _TQDM:
        print(f"  Hashed {len(records):,}/{len(records):,}", flush=True)

    # Drop skipped slots (None entries) before downstream stages.
    hash_results = [r for r in hash_results if r is not None]
    if skipped:
        print(f"  Skipped {len(skipped):,} unreadable file(s):", file=sys.stderr, flush=True)
        for p, exc_type, exc_msg in skipped[:10]:
            print(f"    {p}  [{exc_type}: {exc_msg}]", file=sys.stderr, flush=True)
        if len(skipped) > 10:
            print(f"    … and {len(skipped) - 10:,} more", file=sys.stderr, flush=True)

    # --- exiftool for HEIC / RAW / MOV / MP4 only ---
    # JPEG and PNG dates are already populated from the PIL pass above.
    et_records = [r for r in hash_results if r.exif_date is None
                  and r.record.file_type in _EXIFTOOL_TYPES]
    if et_records:
        et_paths = [r.record.path for r in et_records]
        n_chunks = (len(et_paths) + chunk_size - 1) // chunk_size
        print(f"EXIF via exiftool for {len(et_paths):,} non-JPEG files ({n_chunks} chunk(s))…",
              flush=True)
        try:
            with ExiftoolProcess() as et:
                dates: dict = {}
                for i in range(0, len(et_paths), chunk_size):
                    chunk = et_paths[i: i + chunk_size]
                    dates.update(batch_read_dates(chunk, et, chunk_size=chunk_size))
                    done_et = min(i + chunk_size, len(et_paths))
                    print(f"  EXIF {done_et:,}/{len(et_paths):,}", end="\r", flush=True)
            found = sum(1 for v in dates.values() if v)
            print(f"  EXIF done — {found:,} dates found", flush=True)
            for r in et_records:
                r.exif_date = dates.get(r.record.path)
        except FileNotFoundError:
            print(
                "\nWARNING: exiftool not found on PATH — EXIF dates for HEIC/RAW/video unavailable.\n"
                "Install from https://exiftool.org/ and ensure it is in your PATH.",
                file=sys.stderr,
            )

    print("Classifying…", flush=True)
    rows = classify(hash_results, threshold=args.threshold, source_priority=source_priority)

    print_summary(rows, skipped=len(skipped))

    if args.dry_run:
        print("--dry-run: manifest not written.", flush=True)
        return 0

    write_manifest(rows, args.output)
    print(f"Manifest written to: {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
