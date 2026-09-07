"""Web scenario s07 — Format-duplicate pair (HEIC vs JPG of same scene).

Ported from qa/scenarios/s07_format_dup.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/format-dup (scene_a.heic + scene_a.jpg).
  - Verify FORMAT_DUPLICATE classification: the scanner should recognise
    both files as the same scene captured in two formats, keep the
    higher-format copy (HEIC) as the Ref winner, and mark the JPG as the
    lower-priority duplicate.
  - Qt driver reads result-tree rows, finds the "Ref" cell, and prints
    the ref filename.  No hard assertions on the Qt side — it is a
    print-and-verify smoke probe.

Web slice (UPGRADED — see "Strengthening" below):
  1. Copy scene_a.heic and scene_a.jpg into ``<tmpdir>/s07_source/``.
     The output manifest.db lives BESIDE (not inside) the source subdir,
     in tmpdir root — matching the s24 db-beside-source convention.
  2. run_scan(...) — opens the dialog, adds the source, sets output, starts
     the scan, and waits on main-status-bar for the "N groups · M files"
     manifest-loaded text.  (ScanProgress unmounts on the SSE 'finished'
     event before loadManifest resolves, so we wait on the status bar, never
     the progress log.)  The format-duplicate classification is verified
     purely from the manifest JSON below — no in-log marker is needed.
  3. GET /api/manifest → assert:
     A. Exactly 1 group with exactly 2 items (scene_a.heic + scene_a.jpg of
        the same scene group together — not a SINGLETON-DROP risk because the
        pair always has ≥ 2 members).
     B. The HEIC item has is_ref_winner == True.
     C. The JPG item has is_ref_winner == False.
     D. The JPG item has action == "REVIEW_DUPLICATE".  The HEIC and JPG are
        the same scene but NOT pHash-identical (JPG compression shifts the
        pHash ~10 hamming), so the scanner classifies the JPG as a near-dup
        REVIEW_DUPLICATE, not a hamming-0 EXACT format-duplicate.  Verified
        live (84% similarity).
     E. The JPG item similarity has kind == "percent" with percent in (0,100]
        (the near-dup % to the HEIC Ref — exact value not asserted, it depends
        on HEIC/JPG compression).
     F. The HEIC item has similarity["kind"] == "ref"
        (compute_similarity: action=="" + is_ref_winner=True → "ref").

Strengthening (Qt -> web, intentional upgrade):
  The Qt s07 has ZERO hard assertions — it is a print-only smoke probe
  that prints the ref filename.  The web port turns all the key
  properties into HARD assertions: group count, member count, ref winner
  identity, action field, and similarity object.  This is the correct
  port posture: the desktop intent was "verify HEIC wins, JPG is marked
  dup"; we verify this at the data layer via the manifest JSON.

Qt divergences:
  D1. NO UIA ROW SCRAPING: Qt reads result-tree rows via pywinauto
      automation and checks the "Ref" cell text.  The web port reads
      GET /api/manifest JSON directly — the authoritative server
      serialisation — which is strictly stronger (data-layer assertion,
      immune to UI virtualisation / row-ordering / label drift).
  D2. HARD ASSERTIONS ADDED: Qt prints ref_file but does not assert.
      The web port asserts is_ref_winner, action, and similarity on
      both files.
  D3. COPY-TO-TMPDIR: Qt scans qa/sandbox/format-dup directly.  The
      web port copies into a tmpdir (s24 / s13 convention) so the repo
      fixture is never mutated and the output db lives beside the source
      subdir in the tmpdir root.
  D4. NO LOG PROBE: Qt searches the Qt scan-dialog log for "FORMAT" lines.
      The web port does NOT probe the progress log: the web ScanProgress
      component (and its scan-progress-log element) auto-unmounts on the SSE
      'finished' event, so for a 2-file scan that finishes in well under a
      second the log is gone before any wait could observe it (a guaranteed
      flake on fast CI).  The duplicate classification is instead asserted
      directly from the manifest (action="REVIEW_DUPLICATE", is_ref_winner,
      similarity) — the authoritative data-layer signal.
  D5. SIMILARITY ASSERTION: Qt does not assert similarity.  The web port
      asserts the JPG similarity kind == "percent" with percent in (0,100]
      (the near-dup % to the HEIC Ref) and HEIC similarity["kind"] == "ref".

Desktop source: qa/scenarios/s07_format_dup.py
Fixture:        qa/sandbox/format-dup/scene_a.heic, scene_a.jpg
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
from qa.web._invariants import run_scan

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_FORMAT_DUP_DIR = _REPO / "qa" / "sandbox" / "format-dup"

_HEIC_BASENAME = "scene_a.heic"
_JPG_BASENAME = "scene_a.jpg"

_FIXTURE_BASENAMES = [_HEIC_BASENAME, _JPG_BASENAME]


# ---------------------------------------------------------------------------
# HTTP helper (self-contained — mirrors s13/s24/s51/s08 exactly)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>.

    Uses urllib (stdlib only) to keep the module dependency-free at import
    time, matching the convention established by s13/s24/s51/s08.
    """
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Scan format-dup fixture; assert HEIC wins, JPG is marked a duplicate.

    Copies scene_a.heic and scene_a.jpg into a tmpdir source subdir, scans
    them via run_scan, then asserts the duplicate classification via GET
    /api/manifest JSON: exactly 1 group / 2 items, HEIC is_ref_winner=True
    (similarity kind 'ref'), JPG is_ref_winner=False with
    action='REVIEW_DUPLICATE' and a near-dup similarity percent in (0,100].
    """
    tmpdir = tempfile.mkdtemp(prefix="qa_s07_")
    src_subdir = os.path.join(tmpdir, "s07_source")
    # db lives BESIDE the source subdir in tmpdir root — s24 convention.
    db_path = os.path.join(tmpdir, "s07_manifest.db")
    try:
        # ── Copy fixture files into the dedicated source subdir ─────────────
        os.makedirs(src_subdir, exist_ok=True)
        for basename in _FIXTURE_BASENAMES:
            src = _FORMAT_DUP_DIR / basename
            assert src.exists(), (
                f"Format-dup fixture missing: {src} "
                "(expected qa/sandbox/format-dup/scene_a.heic + scene_a.jpg)"
            )
            shutil.copy(str(src), os.path.join(src_subdir, basename))

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: run the scan ─────────────────────────────────────────
            # run_scan opens the dialog, adds the source, sets the output,
            # starts the scan, and waits on main-status-bar for the
            # "N groups · M files" manifest-loaded text (the correct post-scan
            # signal — ScanProgress auto-unmounts on the SSE 'finished' event
            # before loadManifest resolves, so we must NOT wait on the progress
            # log).  The format-duplicate classification is asserted entirely
            # via the manifest JSON below, so no in-log marker is needed.
            run_scan(page, sources=[src_subdir], output_path=db_path,
                     scan_timeout=60_000)

            # ── Step 2: fetch manifest JSON ───────────────────────────────────
            manifest = _get_manifest(base_url, db_path)

            # Probe output — printed for audit regardless of assertions below.
            print(
                f"probe_status: s07 total_groups={manifest.get('total_groups')} "
                f"total_files={manifest.get('total_files')}"
            )
            for grp in manifest.get("groups", []):
                for item in grp.get("items", []):
                    bn = Path(item["file_path"]).name
                    print(
                        f"probe_status: s07 item={bn!r} "
                        f"action={item.get('action')!r} "
                        f"is_ref_winner={item.get('is_ref_winner')} "
                        f"similarity={item.get('similarity')}"
                    )

            # ── Assertion A: exactly 1 group with exactly 2 items ────────────
            # scene_a.heic + scene_a.jpg have identical pHash (hamming==0)
            # and both are lossy formats → FORMAT_DUPLICATE path → one group.
            # This group is never SINGLETON-DROPPED (len==2 ≥ 2).
            groups = manifest.get("groups", [])
            assert len(groups) == 1, (
                f"Expected exactly 1 group from the format-dup pair "
                f"(scene_a.heic + scene_a.jpg), got {len(groups)} groups. "
                f"total_groups={manifest.get('total_groups')}"
            )
            group = groups[0]
            items = group.get("items", [])
            assert len(items) == 2, (
                f"Expected exactly 2 items in the format-dup group, "
                f"got {len(items)}: {[Path(i['file_path']).name for i in items]}"
            )

            # ── Find the HEIC and JPG rows ────────────────────────────────────
            heic_row = None
            jpg_row = None
            for item in items:
                bn = Path(item["file_path"]).name
                if bn == _HEIC_BASENAME:
                    heic_row = item
                elif bn == _JPG_BASENAME:
                    jpg_row = item

            assert heic_row is not None, (
                f"{_HEIC_BASENAME} not found in group items; "
                f"basenames present: {[Path(i['file_path']).name for i in items]}"
            )
            assert jpg_row is not None, (
                f"{_JPG_BASENAME} not found in group items; "
                f"basenames present: {[Path(i['file_path']).name for i in items]}"
            )

            # ── Assertion B: HEIC is the Ref winner ──────────────────────────
            # Format priority: heic > jpeg (scanner/dedup.py FORMAT_PRIORITY).
            # The HEIC keeper gets action="" (Ref-tier) → is_ref_winner=True.
            assert heic_row["is_ref_winner"] is True, (
                f"HEIC ({_HEIC_BASENAME}) must be the Ref winner "
                f"(highest format priority in the format-dup pair), "
                f"but is_ref_winner={heic_row['is_ref_winner']!r}. "
                f"action={heic_row.get('action')!r}"
            )

            # ── Assertion C: JPG is NOT the Ref winner ────────────────────────
            assert jpg_row["is_ref_winner"] is False, (
                f"JPG ({_JPG_BASENAME}) must NOT be the Ref winner "
                f"(lower format priority than HEIC in the format-dup pair), "
                f"but is_ref_winner={jpg_row['is_ref_winner']!r}"
            )

            # ── Assertion D: JPG is classified as a duplicate ────────────────
            # The fixture's HEIC and JPG are the same scene but NOT pHash-
            # identical: JPG compression shifts the pHash by ~10 hamming, so
            # the scanner classifies the JPG as REVIEW_DUPLICATE (pHash hamming
            # 1..threshold + dHash agrees) rather than EXACT (which requires
            # hamming==0; see scanner/dedup.py).  The desktop intent — "HEIC
            # wins, JPG is the duplicate" — holds either way; we assert the
            # actual near-dup classification rather than a hamming-0 EXACT.
            assert jpg_row.get("action") == "REVIEW_DUPLICATE", (
                f"JPG ({_JPG_BASENAME}) must be classified as a duplicate "
                f"(REVIEW_DUPLICATE — same scene, near-dup pHash of the HEIC), "
                f"got action={jpg_row.get('action')!r}"
            )

            # ── Assertion E: JPG similarity is a near-dup percent ────────────
            # review_view.compute_similarity renders a percent for a
            # REVIEW_DUPLICATE row: (64 - hamming) / 64 * 100 against the Ref's
            # pHash.  Assert the KIND and a sane range, NOT the exact value
            # (which depends on HEIC/JPG compression and would be brittle).
            jpg_sim = jpg_row.get("similarity") or {}
            assert jpg_sim.get("kind") == "percent", (
                f"JPG ({_JPG_BASENAME}) similarity kind must be 'percent' "
                f"(near-dup of the HEIC Ref); got {jpg_sim!r}"
            )
            jpg_pct = jpg_sim.get("percent")
            assert isinstance(jpg_pct, int) and 0 < jpg_pct <= 100, (
                f"JPG ({_JPG_BASENAME}) similarity percent must be in (0, 100], "
                f"got {jpg_pct!r}"
            )

            # ── Assertion F: HEIC similarity kind == "ref" ───────────────────
            # review_view.compute_similarity: action=="" + is_ref_winner=True
            # → {"kind": "ref", "percent": None}.
            heic_sim = heic_row.get("similarity", {})
            assert heic_sim.get("kind") == "ref", (
                f"HEIC ({_HEIC_BASENAME}) similarity kind must be 'ref' "
                f"(Ref-tier winner: action='' + is_ref_winner=True → kind='ref'); "
                f"got similarity={heic_sim!r}"
            )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
