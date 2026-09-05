"""Web scenario s24 — Open a manifest whose source files no longer exist.

Ported from qa/scenarios/s24_stale_manifest_paths.py (Qt UIA, #123).

Qt intent:
  - Open a VALID sqlite manifest whose SOURCE files have since been
    deleted (drive unmounted, NAS offline, cleanup pass), and verify:
      (a) the load does not crash,
      (b) the status bar matches a healthy happy-path open,
      (c) the load layer does NOT silently drop rows whose source files
          are missing — sqlite is the row-identity source of truth; file
          existence is purely a rendering concern,
      (d) manifest-gated affordances (Set Action / Execute) stay enabled.
  - Non-destructive: the Qt scenario explicitly scopes execute-on-stale-row
    OUT (delete_to_recycle on a dead path is a separate issue, #68).

Web slice:
  1. Copy 3+ stable near-duplicate JPEGs into ``<tmpdir>/s24_source/`` and
     scan that subdir to produce ``<tmpdir>/s24_manifest.db``.  The db lives
     NEXT TO the source subdir (in tmpdir), NOT inside it, so deleting the
     source dir orphans the manifest while keeping the db readable.
  2. GET /api/manifest snapshots the expected basenames + total_files.
  3. ORPHAN: shutil.rmtree(<tmpdir>/s24_source) — every source file is now
     gone; the db survives.
  4. Reload the page (clears in-memory manifest → empty state, s16 precedent),
     then re-open the stale manifest via the toolbar input + Open button.
  5. Assert:
     A. Load did not crash — load_manifest returning the matched status
        proves GET /api/manifest answered 200; empty-state is gone.
     B. Status matches the happy-path open exactly AND matches the web
        ``N groups · M files`` pattern.
     C. PRIMARY (source-existence-agnostic load): the post-stale manifest
        JSON still carries every expected basename and the same total_files —
        the load path read sqlite, never the filesystem.  Belt-and-suspenders:
        at least one row testid still renders in the tree.
     D. Manifest-gated menu affordances (Set Action, Execute) are enabled
        after the stale load (web equivalent of the Qt
        ``assert_manifest_actions_consistent(expected_enabled=True)``).

Qt divergences:
  (i)   Fixture is the repo's stable near-duplicate JPEGs
        (neardup_00_q95.jpg …), not Qt's freshly generated identical-gradient
        JPEGs.  Both produce a multi-row manifest the load path can orphan;
        the web fixture avoids the numpy/PIL generation + random-retry logic.
  (ii)  Basenames are asserted against the GET /api/manifest JSON (the
        authoritative server serialisation, strictly STRONGER) rather than
        Qt's ``descendants(control_type="TreeItem")`` DOM scraping.  This
        keeps the Qt scenario's load-bearing assertion ("the load does NOT
        silently drop missing-source rows") but pins it at the data layer
        where it cannot be confounded by virtualisation / y_min row filters.
  (iii) Status phrasing differs: Qt asserts ``"Opened manifest: N pairs to
        review (M files)"``; the web App.tsx status bar emits
        ``"N groups · M files"``.  We assert the WEB pattern and additionally
        require post-stale == post-scan status (the load step is
        existence-agnostic, so the phrasing must be byte-for-byte identical).
  (iv)  Qt prints (does not assert) a post-stale raw-TreeItem count delta to
        flag any orphan-hide UX.  On the web there is no orphan-hide
        behaviour — every sqlite row always renders a tree row — so that
        observation-only probe has no web analogue and is omitted.
  (v)   Qt verifies "no error dialog".  The web equivalent is that
        load_manifest resolves (the status-bar wait succeeds) and the
        main-empty-state placeholder is no longer visible; there is no modal
        error surface to dismiss.

  Thumbnail loading is intentionally NOT asserted: the image route may 404
  for a missing source file, which is exactly the rendering concern Qt
  leaves unasserted.  Pinning it would assert a behaviour neither harness
  pins.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan, load_manifest
from qa.web.testid_constants import (
    MAIN_EMPTY_STATE,
    MENU_ACTION,
    MENU_ACTION_SET,
    MENU_ACTION_EXECUTE,
    row_file_testid,
)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = _REPO / "qa" / "sandbox" / "near-duplicates"

# Use 3 of the 5 stable near-duplicate basenames — the Qt scenario uses 3
# source files (NUM_FILES = 3); 3 is enough to prove the load path keeps
# every row.  Listed explicitly so a rename in the sandbox fails loudly.
_FIXTURE_BASENAMES = [
    "neardup_00_q95.jpg",
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
]


# ---------------------------------------------------------------------------
# HTTP helper (self-contained, mirrors s13 _get_manifest exactly)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>.

    Uses urllib (stdlib only) to keep the module import-time dependency-free,
    matching the convention established by s13/s15/s31/s51/s56.
    """
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _basenames(manifest: dict) -> set[str]:
    """Return the set of file basenames across all groups in a manifest JSON.

    Iterates ``group["items"]`` (the server serialisation key, NOT "files")
    and takes ``Path(item["file_path"]).name``.  Only rows with outcome=''
    appear (WHERE outcome='' in _LOAD_ALL_SQL), but this scenario is
    non-destructive so every scanned row is present both before and after.
    """
    names: set[str] = set()
    for group in manifest.get("groups", []):
        for file_row in group.get("items", []):
            names.add(Path(file_row["file_path"]).name)
    return names


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> int:
    """Scan → orphan source files → re-open the stale manifest; assert it loads.

    Returns 0 on success.  Raises AssertionError (treated as failure by the
    batch runner) on any regression.
    """
    tmpdir = tempfile.mkdtemp(prefix="qa_s24_")
    src_subdir = os.path.join(tmpdir, "s24_source")
    db_path = os.path.join(tmpdir, "s24_manifest.db")
    try:
        # ── Copy fixture JPEGs into the dedicated source subdir ──────────────
        os.makedirs(src_subdir, exist_ok=True)
        for basename in _FIXTURE_BASENAMES:
            shutil.copy(str(_NEAR_DUPS_DIR / basename), os.path.join(src_subdir, basename))

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the source subdir → fresh manifest.db ───────────
            status_scan = run_scan(
                page,
                sources=[src_subdir],
                output_path=db_path,
                scan_timeout=120_000,
            )
            assert os.path.exists(db_path), f"scan did not write manifest: {db_path}"

            # ── Step 2: snapshot expected basenames + total_files via JSON ───
            manifest_before = _get_manifest(base_url, db_path)
            expected_basenames = _basenames(manifest_before)
            total_before = manifest_before["total_files"]
            assert total_before >= 3, (
                f"Expected at least 3 manifest rows after scanning "
                f"{len(_FIXTURE_BASENAMES)} near-duplicate sources, got "
                f"total_files={total_before} (basenames={sorted(expected_basenames)})"
            )

            # ── Step 3: ORPHAN — delete every source file (db survives) ──────
            shutil.rmtree(src_subdir)
            assert not os.path.exists(src_subdir), (
                f"source dir still present after rmtree: {src_subdir}"
            )
            assert os.path.exists(db_path), (
                f"manifest db must survive the source delete (it lives in tmpdir, "
                f"not the source subdir): {db_path}"
            )

            # ── Step 4: reload to clear in-memory state, then re-open stale ──
            page.reload()
            page.get_by_test_id(MAIN_EMPTY_STATE).wait_for(state="visible", timeout=10_000)
            status_stale = load_manifest(page, db_path, timeout=30_000)

            # ── Assertion A: load did not crash ──────────────────────────────
            # load_manifest only returns once main-status-bar matched the
            # "N groups · M files" pattern, which it can only do after
            # GET /api/manifest answered 200.  The empty-state placeholder must
            # also be gone now that a manifest is loaded.
            assert page.get_by_test_id(MAIN_EMPTY_STATE).count() == 0, (
                "main-empty-state should be gone after a stale manifest loads; "
                "its presence implies the load silently failed"
            )

            # ── Assertion B: status matches happy-path open ──────────────────
            assert status_stale == status_scan, (
                f"status after stale re-open ({status_stale!r}) differs from the "
                f"post-scan status ({status_scan!r}); the load step is "
                f"source-existence-agnostic so the phrasing must be identical"
            )
            assert re.search(r"\d+\s*groups?\s*·\s*\d+\s*files?", status_stale), (
                f"stale-open status does not match the web 'N groups · M files' "
                f"pattern: {status_stale!r}"
            )

            # ── Assertion C: PRIMARY — source-existence-agnostic load ────────
            # review_service.load_review reads sqlite and NEVER checks source
            # file existence; review.py's only existence check is on the .db
            # file itself (line 45).  So every row must survive the orphaning.
            manifest_after = _get_manifest(base_url, db_path)
            after_basenames = _basenames(manifest_after)
            assert expected_basenames <= after_basenames, (
                f"stale load silently DROPPED rows whose source files are missing "
                f"— sqlite is the row-identity truth, not the filesystem. "
                f"expected ⊆ after failed: missing="
                f"{sorted(expected_basenames - after_basenames)} "
                f"(expected={sorted(expected_basenames)}, after={sorted(after_basenames)})"
            )
            assert manifest_after["total_files"] == total_before, (
                f"total_files changed across the orphaning "
                f"({total_before} → {manifest_after['total_files']}); the load path "
                f"must not depend on source-file existence"
            )

            # Belt-and-suspenders: at least one row still renders in the tree.
            first_group = manifest_after["groups"][0]
            group_id = str(first_group["group_number"])
            a_basename = sorted(expected_basenames)[0]
            row = page.get_by_test_id(row_file_testid(group_id, a_basename))
            row.wait_for(state="attached", timeout=10_000)

            # ── Assertion D: manifest-gated affordances stay enabled ─────────
            # Radix dropdown content portals lazily: click the menu TRIGGER,
            # then read data-disabled on each item (None == enabled).  Mirrors
            # the s14 / s50 menu-gating probe.
            page.get_by_test_id(MENU_ACTION).click()
            set_item = page.get_by_test_id(MENU_ACTION_SET)
            set_item.wait_for(state="visible", timeout=5_000)
            assert set_item.get_attribute("data-disabled") is None, (
                "Action → Set Action must stay enabled after a stale manifest "
                "loads (the manifest gate is row-identity-based, not "
                "source-file-existence-based)"
            )
            execute_item = page.get_by_test_id(MENU_ACTION_EXECUTE)
            execute_item.wait_for(state="visible", timeout=5_000)
            assert execute_item.get_attribute("data-disabled") is None, (
                "Action → Execute must stay enabled after a stale manifest loads"
            )
            page.keyboard.press("Escape")  # close the menu

        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
