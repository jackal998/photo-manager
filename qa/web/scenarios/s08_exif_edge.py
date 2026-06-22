"""Web scenario s08 — EXIF edge-case date handling.

Ported from qa/scenarios/s08_exif_edge.py (Qt UIA, print-only probe).

Qt intent:
  - Scan qa/sandbox/exif-edge (6 JPEGs with EXIF date edge cases):
      tz_offset.jpg          — DateTimeOriginal with timezone offset
      subsecond.jpg          — DateTimeOriginal with sub-second precision
      createdate_only.jpg    — CreateDate tag only (no DateTimeOriginal)
      datetime_tag_only.jpg  — DateTime tag only (legacy fallback)
      zero_date_sentinel.jpg — Date field value 0000:00:00 00:00:00
      dash_sentinel.jpg      — Date field value "---" / dash sentinel
  - After scan, read the Date column for each file row via Qt UIA and PRINT
    the per-file date mapping.  No assertions — a pure observation probe.

Web slice (UPGRADED to hard per-file assertions — see "Divergences"):
  The six exif-edge files are all visually DISTINCT (verified live: each gets
  its own pHash, none pair), so on the raw fixture every file is a singleton
  and the grouped manifest (GET /api/manifest, which drops groups < 2 members)
  is EMPTY.  A naive port that scanned the raw fixture would assert over an
  empty manifest — a hollow test that catches nothing.

  To make every file observable, this port gives each exif-edge file a
  byte-identical twin.  Each file then forms an EXACT-duplicate pair (matched
  on the content hash) and survives the singleton-drop, so all six date
  edge-cases appear in the manifest with their parsed shot_date.  The
  assertion verifies the EXIF date parser handles each edge case correctly:
  the four well-formed-date files get a non-null shot_date, and the two
  sentinel files (zero-date, dash) parse to null (the correct "no date"
  result, not a bogus datetime).  This is a real parser-correctness gate,
  far stronger than the Qt print-only probe.

  Same singleton-defeating duplicate-to-observe pattern as s05 / s06.

Assertions:
  1. All six exif-edge basenames appear in the manifest (each as part of an
     exact-dup pair) — 12 rows total.
  2. Every item carries a shot_date key (always serialised).
  3. The four well-formed-date files have a non-null shot_date.
  4. The two sentinel files (zero_date_sentinel, dash_sentinel) have a null
     shot_date — the correct EXIF parse of a 0000-date / dash value.

Qt divergences:
  D1. SOURCE READ: Qt reads the per-file Date column from the UIA result tree.
      The web port reads the canonical shot_date field (ISO 8601 / null) from
      GET /api/manifest — full precision, immune to UI truncation.
  D2. PRINT -> ASSERT: Qt only prints the per-file dates.  The web port hard-
      asserts non-null for the well-formed files and null for the sentinels,
      turning the observation into a parser-correctness gate.
  D3. DUPLICATE-TO-OBSERVE: Qt scans the raw 6-file fixture (all singletons);
      the web grouped manifest would be empty.  The web port duplicates each
      file (byte-identical twin) so every edge-case forms an exact-dup pair
      and appears in the manifest.
  D4. NO LOG PROBE: Qt extracts the UNDATED count from the scan log.  The web
      port does NOT probe the progress log — ScanProgress unmounts on the SSE
      'finished' event, so the log is gone before a fast 12-file scan could be
      probed (a guaranteed flake on fast CI).  The "undated" behaviour is
      asserted instead via the sentinel files' null shot_date in the manifest.

Desktop source: qa/scenarios/s08_exif_edge.py
Fixture:        qa/sandbox/exif-edge/ (6 JPEGs)
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
_EXIF_EDGE_DIR = _REPO / "qa" / "sandbox" / "exif-edge"

# Files whose EXIF carries a well-formed date → shot_date must be non-null.
_DATED_BASENAMES = frozenset({
    "tz_offset.jpg",
    "subsecond.jpg",
    "createdate_only.jpg",
    "datetime_tag_only.jpg",
})
# Files whose EXIF date is a sentinel (0000-date / dash) → shot_date must be
# null.  A non-null ISO string here is the parser bug this scenario catches.
_SENTINEL_BASENAMES = frozenset({"zero_date_sentinel.jpg", "dash_sentinel.jpg"})

_ALL_BASENAMES = _DATED_BASENAMES | _SENTINEL_BASENAMES


# ---------------------------------------------------------------------------
# HTTP helper (self-contained — mirrors s06/s07/s24 exactly)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _collect_shot_dates(manifest: dict) -> dict[str, object]:
    """Return {basename: shot_date} for all items in all groups.

    shot_date is either an ISO 8601 string or None.  Uses the sentinel
    "__MISSING__" if the key is absent (which must never happen — the
    serialiser always emits it).
    """
    result: dict[str, object] = {}
    for group in manifest.get("groups", []):
        for file_row in group.get("items", []):
            basename = Path(file_row["file_path"]).name
            result[basename] = file_row.get("shot_date", "__MISSING__")
    return result


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Scan exif-edge (each file duplicated); assert per-file shot_date parse."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s08_")
    src_subdir = os.path.join(tmpdir, "s08_source")
    db_path = os.path.join(tmpdir, "s08_manifest.db")
    try:
        # ── Copy each exif-edge file plus a byte-identical twin ─────────────
        # The twin makes every file an EXACT-duplicate pair so it survives the
        # manifest singleton-drop (the raw files are all distinct singletons).
        os.makedirs(src_subdir, exist_ok=True)
        for basename in _ALL_BASENAMES:
            src = _EXIF_EDGE_DIR / basename
            assert src.exists(), (
                f"exif-edge fixture missing: {src} "
                "(expected qa/sandbox/exif-edge/ with all six edge-case JPEGs)"
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
            shot_dates = _collect_shot_dates(manifest)
            print(
                f"probe_status: s08 total_files={manifest.get('total_files')} "
                f"shot_dates={ {k: shot_dates[k] for k in sorted(shot_dates)} }"
            )

            # ── Assertion 1: all 6 originals present (12 rows incl. twins) ───
            row_count = count_file_rows(page)
            assert row_count == 12, (
                f"Expected 12 file rows (6 exif-edge files x 2 byte-identical "
                f"copies), got {row_count}."
            )
            missing = _ALL_BASENAMES - set(shot_dates)
            assert not missing, (
                f"These exif-edge files did not appear in the manifest: "
                f"{sorted(missing)} (each should form an exact-dup pair)."
            )

            # ── Assertion 2: shot_date key always present ───────────────────
            for basename, shot_date in shot_dates.items():
                assert shot_date != "__MISSING__", (
                    f"shot_date key absent for {basename!r}; "
                    f"review_view._build_file_row must always serialise it."
                )

            # ── Assertion 3: well-formed-date files parse to a non-null date ─
            for basename in _DATED_BASENAMES:
                assert shot_dates.get(basename) is not None, (
                    f"{basename!r} has a well-formed EXIF date but parsed to "
                    f"shot_date=null — the date parser dropped a valid date. "
                    f"Got {shot_dates.get(basename)!r}."
                )

            # ── Assertion 4: sentinel files parse to null ───────────────────
            for basename in _SENTINEL_BASENAMES:
                assert shot_dates.get(basename) is None, (
                    f"Sentinel file {basename!r} (zero-date / dash value) must "
                    f"parse to shot_date=null, but got {shot_dates.get(basename)!r}. "
                    f"The EXIF parser is converting a sentinel date into a real "
                    f"datetime instead of treating it as absent."
                )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
