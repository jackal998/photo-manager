"""Web scenario s06 — Multi-format scan: HEIC, PNG, GIF, WebP, TIFF.

Ported from qa/scenarios/s06_formats.py (Qt UIA, print-only smoke probe).

Qt intent:
  - Scan qa/sandbox/formats (5 files: fmt_gif.gif, fmt_heic.heic,
    fmt_png.png, fmt_tiff.tif, fmt_webp.webp).
  - Read the result-tree rows, extract the extension of each row, and PRINT
    extensions_in_results.  No hard assertions — a smoke probe that the
    scanner handles all five formats without crashing (GIF graceful: no EXIF).

Web slice (UPGRADED to a hard assertion — see "Divergences"):
  The web app's grouped manifest (GET /api/manifest) only returns files that
  belong to a group of >= 2 members (the singleton-drop in
  manifest_repository).  Scanning the raw 5-file fixture produces only THREE
  manifest rows: fmt_png / fmt_heic / fmt_webp share a pHash and group, while
  fmt_gif and fmt_tiff get NO pHash from the scanner (verified live: phash is
  NULL for both) so they never pair and are singleton-dropped.  A naive port
  that scanned the raw fixture could therefore only ever observe 3 of the 5
  formats — and the Qt print-only probe is no stronger (it prints whatever
  rows survive the same singleton-drop).

  To make all five formats observable AND turn the Qt smoke probe into a real
  regression gate, this port gives each format file a byte-identical twin.
  Two byte-identical files form an EXACT-duplicate pair (matched on the file
  content hash, NOT pHash), so every format — including gif/tiff which have no
  pHash — appears in the grouped manifest.  The assertion then verifies that
  ALL FIVE format extensions are present, which is the real "scanner reads and
  indexes all five formats" invariant (and catches, e.g., a HEIC codec
  regression that would silently drop fmt_heic).

  This is the same singleton-defeating duplicate-to-observe pattern used by
  s05 (huge-preview) for its lone heavy image.

Assertions:
  1. count_file_rows(page) == 10  (5 formats x 2 byte-identical copies).
  2. All five extensions {gif, heic, png, tif, webp} appear among the
     manifest items' file paths.

Qt divergences:
  D1. SOURCE READ: Qt walks pywinauto TreeItem rows and parses cell text for
      extensions.  The web port reads GET /api/manifest JSON file paths — the
      authoritative server serialisation, immune to UI virtualisation.
  D2. PRINT -> ASSERT: Qt prints extensions_in_results with no assertion.  The
      web port hard-asserts all five extensions are present, turning the smoke
      probe into a regression gate.
  D3. DUPLICATE-TO-OBSERVE: Qt scans the raw 5-file fixture.  The web port
      copies each file plus a byte-identical twin so every format forms an
      EXACT-duplicate pair and survives the manifest singleton-drop
      (manifest_repository drops groups with < 2 members).  Without this,
      fmt_gif and fmt_tiff (no pHash) and any non-grouping format would be
      invisible to the grouped manifest.
  D4. NO-PHASH FORMATS: gif and tiff receive no pHash from the scanner
      (verified live) — they pair only via the byte-identical twin (exact-dup),
      never via perceptual similarity.  That gif/tiff get no pHash is a scanner
      property surfaced by this scenario, not asserted as pass/fail here.

Desktop source: qa/scenarios/s06_formats.py
Fixture:        qa/sandbox/formats/{fmt_gif.gif, fmt_heic.heic, fmt_png.png,
                fmt_tiff.tif, fmt_webp.webp}
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import count_file_rows, run_scan

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_FORMATS_DIR = _REPO / "qa" / "sandbox" / "formats"

# The five format files and their expected extensions (lower-case, sans dot).
# fmt_tiff.tif uses the .tif extension on disk — the expected set must match.
_FORMAT_FILES = [
    "fmt_gif.gif",
    "fmt_heic.heic",
    "fmt_png.png",
    "fmt_tiff.tif",
    "fmt_webp.webp",
]
_EXPECTED_EXTENSIONS = frozenset({"gif", "heic", "png", "tif", "webp"})


# ---------------------------------------------------------------------------
# HTTP helper (self-contained — mirrors s07/s08/s24 exactly)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Scan the five formats (each duplicated); assert all five appear."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s06_")
    src_subdir = os.path.join(tmpdir, "s06_source")
    db_path = os.path.join(tmpdir, "s06_manifest.db")
    try:
        # ── Copy each format file plus a byte-identical twin ────────────────
        # The twin makes every format an EXACT-duplicate pair (matched on the
        # content hash, not pHash) so it survives the manifest singleton-drop.
        os.makedirs(src_subdir, exist_ok=True)
        for basename in _FORMAT_FILES:
            src = _FORMATS_DIR / basename
            assert src.exists(), (
                f"Formats fixture missing: {src} "
                "(expected qa/sandbox/formats/ with all five format files)"
            )
            stem, ext = os.path.splitext(basename)
            shutil.copy(str(src), os.path.join(src_subdir, basename))
            shutil.copy(str(src), os.path.join(src_subdir, f"{stem}__dup{ext}"))

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            run_scan(page, sources=[src_subdir], output_path=db_path,
                     scan_timeout=60_000)

            manifest = _get_manifest(base_url, db_path)
            all_items = [
                item
                for group in manifest.get("groups", [])
                for item in group.get("items", [])
            ]
            extensions_present = {
                Path(item["file_path"]).suffix.lstrip(".").lower()
                for item in all_items
            }
            print(
                f"probe_status: s06 total_files={manifest.get('total_files')} "
                f"groups={len(manifest.get('groups', []))} "
                f"extensions={sorted(extensions_present)}"
            )

            # ── Assertion 1: all 10 rows present (5 formats x 2 copies) ──────
            row_count = count_file_rows(page)
            assert row_count == 10, (
                f"Expected 10 file rows (5 formats x 2 byte-identical copies), "
                f"got {row_count}.  A missing pair means the scanner failed to "
                f"read/index one of the five formats — check scanner logs."
            )

            # ── Assertion 2: every one of the five formats is present ────────
            missing = _EXPECTED_EXTENSIONS - extensions_present
            assert not missing, (
                f"These format extensions did not appear in the manifest: "
                f"{sorted(missing)} (expected all of {sorted(_EXPECTED_EXTENSIONS)}). "
                f"A missing extension means the scanner could not read that "
                f"format — e.g. a missing HEIC codec drops 'heic'."
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
