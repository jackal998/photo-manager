"""Web scenario s65 — passenger-bridge forms correctly on real burst photos.

Ported from qa/scenarios/s65_passenger_bridge.py (Qt UIA / sqlite-direct, #544).

Qt intent:
  Scan the two passenger-bridge fixture directories (passenger-bridge-a and
  passenger-bridge-b) in a single scan and verify that the three real burst
  frames form the "passenger" structure introduced by PR #538's true transitive
  closure:

    - ``scene_left.jpg``  (source A, Ref-tier)
    - ``scene_right.jpg`` (source A, Ref-tier — the "passenger", 2nd Ref in group)
    - ``scene_bridge.jpg`` (source B, lower priority, REVIEW_DUPLICATE)

  ``scene_bridge`` is a near-dup of BOTH ``scene_left`` AND ``scene_right``
  while left ≁ right.  After transitive closure all three unite in ONE group,
  the two source-A frames stay Ref-tier, and the lower-priority bridge becomes
  REVIEW_DUPLICATE.  Because every frame has a real pHash the passenger renders
  a numeric similarity (``N%`` / ``N*%``), never a bare ``—`` (which signals a
  no-pHash row like a Live Photo MOV).

  The Qt driver reads the manifest directly via sqlite3 to avoid UIA row
  y-filtering; this web port does the same via GET /api/manifest.

PRE-CONDITIONS:
  qa/sandbox/passenger-bridge-a (scene_left.jpg, scene_right.jpg) and
  qa/sandbox/passenger-bridge-b (scene_bridge.jpg) must exist as REAL photos
  (not regenerable); the scenario raises a clear RuntimeError if either dir is
  missing.

Web slice:
  1. Copy each fixture dir into its OWN flat tmpdir source dir, then scan
     them as TWO ordered sources:
       source 0 "bridge-a" → {scene_left.jpg, scene_right.jpg}  (priority 0)
       source 1 "bridge-b" → {scene_bridge.jpg}                 (priority 1)
     Two sources — not one parent dir — because (a) the web ScanDialog adds
     sources non-recursively, so a single parent-of-subdirs source walks zero
     files, and (b) source ORDER encodes priority: bridge-a (first) is
     Ref-tier, bridge-b (second) is the lower-priority bridge that becomes
     REVIEW_DUPLICATE, matching the desktop driver's two-source priority
     setup.  The output db lives BESIDE the source dirs (tmpdir root),
     matching the s24 convention.
  2. run_scan(...) waits on main-status-bar for "N groups · M files" (the
     post-load signal — ScanProgress unmounts on the SSE 'finished' event
     before loadManifest resolves, so we never wait on the progress log).
  3. Assert:
     A. total_files across the manifest == 3 (all three fixture files indexed).
     B. All 3 items share exactly ONE group_number (len(unique group_numbers)==1).
     C. Exactly 1 item has action == 'REVIEW_DUPLICATE' and basename ==
        'scene_bridge.jpg' — the lower-priority bridge.
     D. The remaining 2 items are Ref-tier: action in ('', 'KEEP', 'UNDATED').
        (Ref-tier action values are sourced from dedup.py / the s65 desktop
        driver's REF_TIER_ACTIONS constant.)
     E. Every item has a non-empty phash so the passenger renders a real
        similarity %, not '—'.

Qt divergences:
  (i)  The Qt driver reads rows via sqlite3 after the scan; the web port reads
       the serialised manifest via GET /api/manifest?path= (the same HTTP
       endpoint asserted by s24/s13/s05).  The data contract is identical
       (review_view._build_file_row lines 121-149); only the read path differs.
  (ii) The Qt driver opens the scan dialog via UIA (open_scan_dialog +
       read_configured_sources) and expects the fixture sources to be
       pre-configured in the running app's settings.  The web port copies the
       two fixture directories into two tmpdir sources and adds them in order,
       removing any dependency on ambient app state.
  (iii) The Qt driver asserts group via group_id (a path-derived string); the
        web port asserts via group_number (the integer key in the manifest JSON,
        serialised as an int in the Group level dict).
  (iv) Ref-tier action values: the desktop driver's REF_TIER_ACTIONS = {'',
       'KEEP', 'UNDATED'} — ported verbatim.  The serialised 'action' field
       from _build_file_row is ``getattr(record, 'action', '') or ''``, so a
       None action becomes ''.
  (v)  Source priority is encoded via scan-source ORDER in both drivers.  The
       web port adds bridge-a before bridge-b, so the pipeline assigns
       bridge-a priority 0 (Ref-tier: scene_left/scene_right) and bridge-b
       priority 1 (the lower-priority bridge: scene_bridge → REVIEW_DUPLICATE).
       Verified live: a single flattened source instead makes scene_bridge the
       Ref winner, so the two-source order is load-bearing for the desktop-
       intended passenger structure.
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
_BRIDGE_A = _REPO / "qa" / "sandbox" / "passenger-bridge-a"
_BRIDGE_B = _REPO / "qa" / "sandbox" / "passenger-bridge-b"

# Ref-tier action values — matches the desktop driver's REF_TIER_ACTIONS and
# the dedup.py classifier for the winning / passenger rows in a group.
_REF_TIER_ACTIONS: frozenset[str] = frozenset({"", "KEEP", "UNDATED"})


# ---------------------------------------------------------------------------
# HTTP helper (self-contained, mirrors s24 / s13 convention exactly)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>.

    Uses urllib (stdlib only) to keep the module import-time dependency-free,
    matching the convention established by s13/s24/s05.
    """
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Scan both bridge fixture dirs in one scan; assert the passenger structure.

    Copies passenger-bridge-a and passenger-bridge-b into a single tmpdir,
    scans it as one source, then asserts via GET /api/manifest that all three
    frames unite in one group with the expected Ref-tier / REVIEW_DUPLICATE
    split and that every frame carries a non-empty phash.
    """
    # ── Pre-condition: fixture directories must exist ────────────────────────
    if not _BRIDGE_A.is_dir():
        raise RuntimeError(
            f"passenger-bridge-a fixture directory not found: {_BRIDGE_A}\n"
            "These are REAL photos and cannot be regenerated.  "
            "Ensure qa/sandbox/passenger-bridge-a/ is checked out."
        )
    if not _BRIDGE_B.is_dir():
        raise RuntimeError(
            f"passenger-bridge-b fixture directory not found: {_BRIDGE_B}\n"
            "These are REAL photos and cannot be regenerated.  "
            "Ensure qa/sandbox/passenger-bridge-b/ is checked out."
        )

    tmpdir = tempfile.mkdtemp(prefix="qa_s65_")
    try:
        # ── Copy each fixture dir into its own FLAT source dir ──────────────
        # Two scan sources (NOT one parent-of-subdirs) for two reasons:
        #   1. The web ScanDialog adds sources non-recursively (recursive
        #      defaults to false), so a single parent-of-subdirs source would
        #      walk ZERO files.  Each fixture dir holds its images directly, so
        #      a flat per-dir source needs no recursion.
        #   2. Source ORDER encodes priority: bridge-a (added first → priority
        #      0) is Ref-tier; bridge-b (added second → priority 1) is the
        #      lower-priority bridge that becomes REVIEW_DUPLICATE — matching
        #      the desktop driver's two-source priority setup.  (A single
        #      flattened source instead makes scene_bridge the Ref winner,
        #      which is NOT the desktop-intended passenger structure.)
        dst_a = os.path.join(tmpdir, "bridge-a")
        dst_b = os.path.join(tmpdir, "bridge-b")
        shutil.copytree(str(_BRIDGE_A), dst_a)
        shutil.copytree(str(_BRIDGE_B), dst_b)

        # Output db lives BESIDE (not inside) the source dirs — s24 pattern.
        db_path = os.path.join(tmpdir, "s65_manifest.db")

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan both sources (order = priority) → fresh manifest ─
            # run_scan waits on main-status-bar for "N groups · M files"
            # (the post-load signal); ScanProgress unmounts on SSE 'finished'
            # before loadManifest resolves, so we never wait on the progress log.
            run_scan(
                page,
                sources=[("bridge-a", dst_a), ("bridge-b", dst_b)],
                output_path=db_path,
                scan_timeout=60_000,
            )
            assert os.path.exists(db_path), (
                f"scan did not produce a manifest db: {db_path}"
            )

            # ── Step 2: fetch the manifest JSON ─────────────────────────────
            manifest = _get_manifest(base_url, db_path)

            # Flatten all items across all groups for convenience.
            all_items: list[dict] = []
            for group in manifest.get("groups", []):
                all_items.extend(group.get("items", []))

            # ── Assertion A: total_files == 3 ────────────────────────────────
            total_files = manifest.get("total_files", 0)
            assert total_files == 3, (
                f"Expected total_files=3 (all three bridge fixture files), "
                f"got total_files={total_files}.  "
                f"basenames found: {sorted(Path(it['file_path']).name for it in all_items)}"
            )
            assert len(all_items) == 3, (
                f"Expected 3 items across all groups, got {len(all_items)}.  "
                f"Check that the singleton-drop guard in dedup.py did not drop a file."
            )

            # ── Assertion B: all 3 items share ONE group_number ──────────────
            group_numbers = {
                group["group_number"]
                for group in manifest.get("groups", [])
                for _item in group.get("items", [])
            }
            assert len(group_numbers) == 1, (
                f"Expected all 3 items to share ONE group (transitive closure via "
                f"scene_bridge pHash bridge), got {len(group_numbers)} distinct "
                f"group_numbers: {group_numbers}.  "
                f"If #538 transitive closure regressed, scene_left and scene_right "
                f"will be in a separate group from scene_bridge."
            )

            # ── Assertion C: exactly 1 REVIEW_DUPLICATE with the right name ──
            review_items = [
                it for it in all_items if it.get("action") == "REVIEW_DUPLICATE"
            ]
            assert len(review_items) == 1, (
                f"Expected exactly 1 REVIEW_DUPLICATE item (scene_bridge.jpg), "
                f"got {len(review_items)}: "
                f"{[Path(it['file_path']).name for it in review_items]}"
            )
            bridge_item = review_items[0]
            bridge_basename = Path(bridge_item["file_path"]).name
            assert bridge_basename == "scene_bridge.jpg", (
                f"The REVIEW_DUPLICATE item should be 'scene_bridge.jpg' "
                f"(the lower-priority cross-dir bridge), got {bridge_basename!r}"
            )

            # ── Assertion D: remaining 2 items are Ref-tier ──────────────────
            ref_items = [
                it for it in all_items if it.get("action") != "REVIEW_DUPLICATE"
            ]
            assert len(ref_items) == 2, (
                f"Expected 2 Ref-tier items (scene_left + scene_right), "
                f"got {len(ref_items)}: "
                f"{[Path(it['file_path']).name for it in ref_items]}"
            )
            for item in ref_items:
                action = item.get("action", "")
                basename = Path(item["file_path"]).name
                assert action in _REF_TIER_ACTIONS, (
                    f"Ref-tier item {basename!r} has unexpected action={action!r}; "
                    f"expected one of {sorted(_REF_TIER_ACTIONS)}.  "
                    f"Only REVIEW_DUPLICATE items should be non-Ref-tier."
                )

            # ── Assertion E: every item has a non-empty phash ────────────────
            # A missing phash causes the passenger similarity to render '—'
            # rather than a real numeric % — which is a regression of the #538
            # pHash-bridge rendering fix.
            no_phash = [
                Path(it["file_path"]).name
                for it in all_items
                if not it.get("phash")
            ]
            assert not no_phash, (
                f"Items {no_phash} have no phash in the manifest — the passenger "
                f"would render '—' instead of a real similarity %.  "
                f"Check that exiftool / the scanner computed pHash for these files."
            )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
