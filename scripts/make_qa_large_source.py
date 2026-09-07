"""Generate a large disposable source tree for the WALK-stage cancel scenario.

Sibling of ``scripts/make_qa_sandbox.py``. The standard sandbox fixtures
(``near-duplicates``, ``unique``, …) hold a handful of files each, so the
scanner's WALK stage finishes in <50 ms and a mid-scan cancel never lands
*inside* WALK. s63 (#475) needs the walker to still be enumerating files
when the cancel arrives, so it points the scanner at a directory containing
several thousand 1-KiB stub JPEGs built here.

Why a separate generator (not a ``make_qa_sandbox`` subdir):

* The output is huge in *file count* (thousands of entries) even though
  it's small in bytes — committing it would bloat the working tree and
  every clone. It lives under ``qa/sandbox/_disposable/`` which is
  gitignored (see ``.gitignore``: "Runtime-generated disposable fixtures"),
  exactly like the s13 / s36 / s44 disposable source dirs.
* It's regenerated at scenario-setup time, so a fresh checkout that has
  never run the generator still works — the scenario builds it on demand.

The stubs are minimal: ``Image.new("RGB", (8, 8)).save(..., quality=1)``
produces a valid ~1-KiB JPEG. Content is irrelevant — the scenario cancels
during WALK (before HASH/CLASSIFY), so the files are never hashed or
deduplicated. We only need the *walker* to have a large enough directory
to still be enumerating when the cancel lands.

Idempotent: re-running with the same ``--count`` is a no-op when the
directory already holds that many ``*.jpg`` files. Pass ``--force`` to
rebuild from scratch.

Usage::

    python scripts/make_qa_large_source.py [--count 6000] [--force]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
# Mirrors the s13/s36/s44 disposable-source convention. Gitignored.
LARGE_SOURCE_DIR = (
    REPO_ROOT / "qa" / "sandbox" / "_disposable" / "s63_walk_source"
)
DEFAULT_COUNT = 6000

# Local-only real-RAW fixtures for the T4 full-res-decode OOM bench
# (scripts/bench_thumbnail_latency.py).  Gitignored: real ProRAW DNGs are
# 70-120 MB each (repo bloat) and personal photos (PII), so they are never
# committed.  Real RAW cannot be synthesised (rawpy is read-only; there is no
# DNG writer in the deps), so --include-large-raw VALIDATES + REPORTS this dir
# rather than generating files — the dev populates it from their own library.
RAW_FIXTURE_DIR = REPO_ROOT / "qa" / "fixtures" / "raw_local"
_RAW_EXTS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2"}


def _count_jpgs(p: Path) -> int:
    if not p.is_dir():
        return 0
    return sum(1 for f in p.glob("*.jpg") if f.is_file())


def report_large_raw() -> int:
    """Validate + report the local real-RAW OOM-bench fixtures.

    Real RAW cannot be generated here, so this is a discovery/validation step,
    not a generator: it lists the DNGs the dev has placed in
    ``qa/fixtures/raw_local/`` (gitignored) and prints how to populate it when
    empty.  ``bench_thumbnail_latency.py`` reads the same dir and skips its
    OOM-bound check when it is empty (CI has no RAW codecs anyway).

    Returns the number of RAW fixtures found.
    """
    if not RAW_FIXTURE_DIR.is_dir():
        RAW_FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    raws = sorted(
        p for p in RAW_FIXTURE_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in _RAW_EXTS
    )
    if not raws:
        print(f"large-raw fixtures: NONE in {RAW_FIXTURE_DIR}")
        print("  The full-res-decode OOM bench needs real ProRAW-sized RAW(s).")
        print("  These can't be synthesised — copy a few large landscape/food")
        print("  RAWs (no people; strip GPS via `exiftool -gps:all=`) into that")
        print("  dir. It is gitignored, so they stay local (dev-rig checkpoint).")
        return 0
    print(f"large-raw fixtures: {len(raws)} in {RAW_FIXTURE_DIR}")
    for p in raws:
        print(f"  {p.stat().st_size / 1e6:6.1f}MB  {p.name}")
    return len(raws)


def make_large_source(count: int = DEFAULT_COUNT, *, force: bool = False) -> Path:
    """Populate ``LARGE_SOURCE_DIR`` with ``count`` 1-KiB stub JPEGs.

    Returns the directory path. Idempotent: skips the build when the
    directory already holds exactly ``count`` ``*.jpg`` files and
    ``force`` is False.
    """
    LARGE_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    existing = _count_jpgs(LARGE_SOURCE_DIR)
    if not force and existing == count:
        return LARGE_SOURCE_DIR

    # Rebuild from scratch so a changed --count doesn't leave stale files.
    for f in LARGE_SOURCE_DIR.glob("*.jpg"):
        if f.is_file():
            f.unlink()

    # One tiny image template, saved repeatedly at quality=1. The bytes
    # are near-identical across files but that's fine — WALK never reads
    # content, and the scenario cancels before HASH/CLASSIFY.
    template = Image.new("RGB", (8, 8))
    for i in range(count):
        out = LARGE_SOURCE_DIR / f"stub_{i:05d}.jpg"
        template.save(str(out), "JPEG", quality=1)
    return LARGE_SOURCE_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the s63 WALK-stage-cancel large source tree."
    )
    parser.add_argument(
        "--count", type=int, default=DEFAULT_COUNT,
        help=f"number of stub JPEGs to generate (default {DEFAULT_COUNT})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="rebuild even when the directory already holds --count files",
    )
    parser.add_argument(
        "--include-large-raw", action="store_true",
        help="validate + report the local real-RAW fixtures used by the T4 "
             "full-res-decode OOM bench (qa/fixtures/raw_local/), instead of "
             "building the WALK stub corpus. Real RAW can't be synthesised — "
             "this reports what's present and how to populate it.",
    )
    args = parser.parse_args(argv)

    if args.include_large_raw:
        report_large_raw()
        return 0

    path = make_large_source(args.count, force=args.force)
    print(f"large source: {path} ({_count_jpgs(path)} stub JPEGs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
