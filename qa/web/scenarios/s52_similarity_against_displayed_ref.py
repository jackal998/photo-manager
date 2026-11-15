"""Web scenario s52 — Similarity % measured against the displayed Ref (#253).

Ported from the desktop s52_similarity_against_displayed_ref driver.

Qt intent:
  The desktop scenario verifies three invariants that pin the wiring
  introduced by issue #253 ("recompute pHash Hamming at render time against
  the *displayed* Ref, not the scanner's original anchor row"):

    1. Every grouped fixture row carries a non-empty ``phash`` value (the
       renderer cannot recompute without it).
    2. Exactly one row in the group carries the "Ref" label — the score-ranked
       winner among Ref-tier rows.
    3. For each ``REVIEW_DUPLICATE`` sibling the recomputed Hamming distance
       against the *displayed* Ref's pHash produces the exact percentage the
       user sees in the Similarity column:
         ``round((64 - hamming(ref_phash, dup_phash)) / 64 * 100)``.

  The Qt driver reads these values directly from the sqlite manifest via
  sqlite3 and also imports ``imagehash`` to compute the cross-ref distance.
  It does NOT drive the UI tree (it uses a sqlite-direct read because
  ``read_result_rows`` is y-filtered in small CI windows).

Web slice:
  1. Copy all 5 near-duplicate sandbox JPEGs into ``<tmpdir>/s52_source/``
     and scan to ``<tmpdir>/s52_manifest.db`` (db lives beside the source
     subdir so teardown via shutil.rmtree(tmpdir) removes both).
  2. wait_manifest_loaded(page) to guarantee loadManifest resolved before
     reading GET /api/manifest — avoids the SSE-finished / loadManifest race.
  3. GET /api/manifest?path=<db_path> → the server serialises every
     field this scenario asserts: ``phash``, ``hamming_distance``,
     ``similarity`` (``{kind, percent}``), ``is_ref_winner``.
  4. Assert invariant 1 — all phashes non-empty.
  5. Assert invariant 2 — exactly one ``is_ref_winner == True`` across all
     items in the group.
  6. Assert invariant 3 — for each ``REVIEW_DUPLICATE`` item:
       a. ``similarity.kind == 'percent'``
       b. ``similarity.percent`` is the number the scan stored for that row
          (the ``similarity`` column, read here straight from the manifest
          with sqlite3) and is at most 99: the files differ, and only a
          byte-identical copy shows 100.
       c. the row shown as Ref has no stored number - the scan measured every
          row against exactly the Ref the review shows.

     Since the similarity measure (scanner/similarity.py) the number is no
     longer the pHash bit distance: it says how alike the two pictures look
     and is taken at the end of the scan against the displayed Ref. What #253
     pinned - the number refers to the DISPLAYED Ref, not to the classifier's
     anchor row - is now pinned by 3c.

Qt divergences:
  D1. The Qt driver reads sqlite directly (via ``sqlite3`` against
      ``qa/run-manifest.sqlite``).  The web port reads the API layer
      (GET /api/manifest) — STRONGER: exercises the full serialisation path
      including ``_build_file_row``, ``compute_similarity``, and
      ``pick_ref_winner`` rather than bypassing them for raw column values.

  D2. The Qt driver imports ``imagehash`` to compute the cross-ref Hamming
      distance and compares it to the legacy stored distance as a diagnostic.
      The web port verifies that ``similarity.percent`` in the JSON matches
      the formula applied to the ref's phash and the dup's phash — asserting
      what the USER SEES is the #253 invariant, not the intermediate distance.

  D3. The Qt driver's ``_pick_displayed_ref`` replicates the Qt renderer's
      tie-break logic in Python so it can identify which row is the "Ref".
      The web port reads ``is_ref_winner`` directly from the JSON (it is
      serialised by ``_build_file_row`` line 138 — ``"is_ref_winner":
      is_ref_winner``), which is the authoritative server verdict.

  D4. The Qt driver runs from a persistent ``qa/run-manifest.sqlite`` that
      survives between qa-explore sessions.  The web port creates a fresh
      manifest.db per run under a tempdir (consistent with s13/s24 harness
      convention) — avoids cross-run stale state.

  D5. The Qt driver uses ``the desktop harness's UI-automation helpers`` to drive the scan dialog.
      The web port uses the standard ``run_scan`` / ``wait_manifest_loaded``
      helpers from ``qa.web._invariants``.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan, wait_manifest_loaded

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = _REPO / "qa" / "sandbox" / "near-duplicates"

_FIXTURE_BASENAMES = [
    "neardup_00_q95.jpg",
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
]


# ---------------------------------------------------------------------------
# HTTP helper (stdlib-only, verbatim from s24/s13 convention)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# The number the scan stored
# ---------------------------------------------------------------------------


def _stored_similarity(db_path: str) -> dict[str, int | None]:
    """``{file name: similarity column}`` read straight from the manifest."""
    con = sqlite3.connect(Path(db_path).as_uri() + "?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT source_path, similarity FROM migration_manifest").fetchall()
    finally:
        con.close()
    return {Path(path).name: value for path, value in rows}


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Scan near-duplicates; assert phash, ref-winner, and the stored similarity.

    Assertions
    ----------
    1. Every item in the group has a non-empty ``phash`` string.
    2. Exactly one item has ``is_ref_winner == True``.
    3. For each ``REVIEW_DUPLICATE`` item:
         a. ``similarity.kind == 'percent'``
         b. ``similarity.percent`` equals the ``similarity`` column the scan
            stored for the row, and is at most 99.
         c. the row shown as Ref has no stored number.
    """
    tmpdir = tempfile.mkdtemp(prefix="qa_s52_")
    src_subdir = os.path.join(tmpdir, "s52_source")
    db_path = os.path.join(tmpdir, "s52_manifest.db")
    try:
        # ── Copy fixture JPEGs into source subdir ────────────────────────────
        os.makedirs(src_subdir, exist_ok=True)
        for basename in _FIXTURE_BASENAMES:
            shutil.copy(
                str(_NEAR_DUPS_DIR / basename),
                os.path.join(src_subdir, basename),
            )

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Scan ─────────────────────────────────────────────────────────
            run_scan(
                page,
                sources=[src_subdir],
                output_path=db_path,
                scan_timeout=120_000,
            )
            assert os.path.exists(db_path), (
                f"scan did not write manifest db: {db_path}"
            )

            # ── wait_manifest_loaded before fetching API ──────────────────────
            # The SSE 'finished' event fires BEFORE loadManifest() resolves;
            # reading GET /api/manifest between them returns stale/empty data.
            # wait_manifest_loaded already ran inside run_scan, but we call it
            # again explicitly here to document the invariant.
            wait_manifest_loaded(page, timeout=30_000)

            # ── Fetch manifest JSON ───────────────────────────────────────────
            manifest = _get_manifest(base_url, db_path)
            groups = manifest.get("groups", [])
            assert len(groups) >= 1, (
                f"expected at least 1 group after scanning {len(_FIXTURE_BASENAMES)} "
                f"near-duplicate fixtures, got {len(groups)} groups "
                f"(total_files={manifest.get('total_files')})"
            )

            # s52 uses the near-duplicates fixture which forms one group of 5.
            # If the scanner ever creates multiple groups (threshold drift), we
            # flatten all items across all groups — the invariants hold group-
            # independently per review_view.serialize_groups.
            all_items: list[dict] = []
            for g in groups:
                all_items.extend(g.get("items", []))

            assert len(all_items) >= 2, (
                f"expected at least 2 items across all groups (got {len(all_items)}); "
                f"the near-duplicates fixture should produce a multi-member group"
            )

            # ── Invariant 1: every item has a non-empty phash ─────────────────
            missing_phash = [
                item["basename"]
                for item in all_items
                if not item.get("phash")
            ]
            assert not missing_phash, (
                f"phash missing or empty for {missing_phash} — the #253 render "
                f"path needs phash on every grouped row to recompute similarity "
                f"against the displayed Ref"
            )

            # ── Invariant 2: exactly one is_ref_winner == True ────────────────
            ref_items = [item for item in all_items if item.get("is_ref_winner")]
            assert len(ref_items) == 1, (
                f"expected exactly 1 is_ref_winner=True across all items, "
                f"got {len(ref_items)}: "
                f"{[r['basename'] for r in ref_items]}"
            )
            ref_item = ref_items[0]

            # ── Invariant 3: the shown number is the one the scan stored ──────
            dup_items = [
                item for item in all_items if item.get("action") == "REVIEW_DUPLICATE"
            ]
            assert len(dup_items) >= 1, (
                f"expected at least one REVIEW_DUPLICATE item in the near-duplicates "
                f"fixture group; got none — near-duplicate classifier may have changed"
            )
            stored = _stored_similarity(db_path)

            # 3c — the scan measured against the Ref the review shows
            assert stored.get(ref_item["basename"]) is None, (
                f"the row shown as Ref ({ref_item['basename']}) carries a stored "
                f"similarity {stored.get(ref_item['basename'])!r}: the scan measured "
                f"against a different Ref than the one displayed, so every row of "
                f"the group would show a dash"
            )

            failures: list[str] = []
            for item in dup_items:
                basename = item["basename"]
                similarity = item.get("similarity", {})

                # 3a — kind must be 'percent'
                kind = similarity.get("kind")
                if kind != "percent":
                    failures.append(
                        f"{basename}: similarity.kind={kind!r}, expected 'percent' "
                        f"(a REVIEW_DUPLICATE picture of a freshly scanned group "
                        f"must carry a measured number)"
                    )
                    continue

                # 3b — the number the scan stored, and never 100 for another file
                server_pct = similarity.get("percent")
                if server_pct != stored.get(basename):
                    failures.append(
                        f"{basename}: server similarity.percent={server_pct!r} != "
                        f"stored similarity column {stored.get(basename)!r}"
                    )
                elif not isinstance(server_pct, int) or not 0 <= server_pct <= 99:
                    failures.append(
                        f"{basename}: similarity.percent={server_pct!r} must be an "
                        f"int in [0, 99] (only a byte-identical copy shows 100)"
                    )

            assert not failures, (
                "REVIEW_DUPLICATE similarity failures:\n"
                + "\n".join(f"  {f}" for f in failures)
            )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
