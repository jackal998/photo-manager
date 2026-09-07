"""Web scenario s44 — Execute (only selected): main-tree selection scopes the
execute dialog to the selected rows' groups (#430 group-pull, #410 static label).

Ported from qa/scenarios/s44_execute_highlighted_rows.py (Qt UIA).

Qt intent:
  Mark every row delete, highlight some file rows in the MAIN result tree, then
  open "Action → Execute Action (only selected)…".  Per #430 the dialog pulls
  the WHOLE group of any highlighted row (so peer ref-rows / score comparisons
  stay visible while triaging), the OK button keeps the static "Execute" label
  (the old "Execute Action (highlighted)" string was removed in #410), and
  Execute deletes the scoped group's decided rows.

Web slice — cluster D / PR-D2 ("Execute (only selected)"):
  This is the faithful port of the desktop *menu-gated, selection-scoped*
  execute flow — NOT an in-dialog multi-select (the execute tree stays
  single-select; completing that honest-partial port of s64 is tracked
  separately).  The novel wiring this PR adds:

    - A new "Execute (only selected)…" entry (Action menu,
      ``menu-action-execute-selected``) GATED on a non-empty main-tree
      selection — the web analog of Qt's
      ``_refresh_execute_selected_only_enabled``.
    - Opening it snapshots the main-tree selection (from cluster-D1's
      multi-selection) and scopes the execute dialog to those rows' GROUPS
      (#430 group-pull): every groups-derived view — tree, banners, the
      Execute commit scope — confines to the selected groups.

  Two deliberate web divergences from desktop, asserted here:

  D1. GROUP-PULL, proven discriminating.  Desktop s44 uses ONE 5-file group, so
      "all 5 deleted" can't tell scope-confinement from execute-everything.
      This port uses TWO deterministic EXACT-dup groups (s60's exif-edge
      pattern): selecting ONE row of group A pulls group A whole into the
      dialog (group-pull) AND leaves group B — equally marked delete —
      untouched on disk and in the manifest (the load-bearing confinement
      assertion the single-group desktop fixture can't make).

  D2. STATIC LABEL is structural, not behavioural, on the web.  The web Execute
      button text is a constant ("Execute" / "Executing…") with no
      "highlighted" variant that ever existed, so #410's removed-string
      regression is impossible by construction.  We still assert the static
      "Execute" label so a future scoped-label regression would fail here.

Qt divergences (assertion mechanics):
  - Group membership / decisions / executed-state read via GET /api/manifest
    JSON, not Qt SQLite.
  - A deleted row is detected by ABSENCE from the manifest (outcome='deleted'
    drops it from the WHERE outcome='' load), not by reading executed=1.

⚠ HEADS-UP: each run sends group A's 2 files to the OS recycle bin.  The fixture
lives in a tmpdir that is rmtree'd on exit, so nothing accumulates in-repo; the
recycle-bin path is inside the tmpdir so os.path.exists still reports False.

Desktop source: qa/scenarios/s44_execute_highlighted_rows.py
Fixture:        qa/sandbox/exif-edge/{tz_offset,subsecond}.jpg (copied twice each)
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    click_context_item,
    click_row,
    right_click_row,
    run_scan,
)
from qa.web.testid_constants import (
    CTX_SET_ACTION_DELETE,
    EXECUTE_ALL_DELETE_CONFIRM,
    EXECUTE_ALL_DELETE_CONFIRM_YES,
    EXECUTE_BTN_EXECUTE,
    EXECUTE_DIALOG,
    MENU_ACTION,
    MENU_ACTION_EXECUTE_SELECTED,
    execute_row_testid,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_EXIF_EDGE_DIR = _REPO / "qa" / "sandbox" / "exif-edge"

# Two byte-identical copies of each source → one EXACT-dup group per source.
# tz_offset.jpg and subsecond.jpg differ enough that the two groups never merge
# (s60's proven deterministic 2-group pattern — no pHash regen flake).
_SRC_A = _EXIF_EDGE_DIR / "tz_offset.jpg"
_SRC_B = _EXIF_EDGE_DIR / "subsecond.jpg"
_GROUP_A_BASENAMES = ["s44_groupA_00.jpg", "s44_groupA_01.jpg"]
_GROUP_B_BASENAMES = ["s44_groupB_00.jpg", "s44_groupB_01.jpg"]
_ALL_BASENAMES = _GROUP_A_BASENAMES + _GROUP_B_BASENAMES


# ---------------------------------------------------------------------------
# HTTP helpers (mirroring s60/s64)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _basename_to_group(manifest: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for group in manifest.get("groups", []):
        gid = str(group["group_number"])
        for item in group.get("items", []):
            out[Path(item["file_path"]).name] = gid
    return out


def _basename_to_decision(manifest: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for group in manifest.get("groups", []):
        for item in group.get("items", []):
            out[Path(item["file_path"]).name] = item.get("user_decision", "") or ""
    return out


def _visible_basenames(manifest: dict) -> set[str]:
    return set(_basename_to_group(manifest))


def _count_execute_rows(page) -> int:
    return page.locator('[data-testid^="execute-row-"]').count()


def _poll_until_absent(
    base_url: str,
    db_path: str,
    targets: set[str],
    *,
    timeout_s: float = 30.0,
    interval_s: float = 0.5,
) -> dict:
    """Poll GET /api/manifest until none of ``targets`` remain visible."""
    deadline = time.monotonic() + timeout_s
    manifest: dict = {}
    while time.monotonic() < deadline:
        manifest = _get_manifest(base_url, db_path)
        if not (targets & _visible_basenames(manifest)):
            return manifest
        time.sleep(interval_s)
    raise AssertionError(
        f"Timed out after {timeout_s}s waiting for {sorted(targets)} to leave "
        f"the manifest; still visible: "
        f"{sorted(targets & _visible_basenames(manifest))}"
    )


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Gate the menu on selection, scope the dialog to the selected group
    (#430 group-pull), and prove the out-of-scope group survives Execute."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s44_")
    db_path = os.path.join(tmpdir, "s44.db")
    try:
        for src, names in ((_SRC_A, _GROUP_A_BASENAMES), (_SRC_B, _GROUP_B_BASENAMES)):
            assert src.exists(), (
                f"exif-edge fixture missing: {src} "
                "(expected qa/sandbox/exif-edge/ with tz_offset.jpg + subsecond.jpg)"
            )
            for name in names:
                shutil.copy(str(src), os.path.join(tmpdir, name))

        group_a_paths = [os.path.join(tmpdir, n) for n in _GROUP_A_BASENAMES]
        group_b_paths = [os.path.join(tmpdir, n) for n in _GROUP_B_BASENAMES]

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the tmpdir copy (db inside it → execute roots OK) ──
            run_scan(page, sources=[tmpdir], output_path=db_path, scan_timeout=60_000)

            # ── Step 2: two distinct groups, two files each ────────────────────
            manifest = _get_manifest(base_url, db_path)
            assert manifest.get("total_groups") == 2, (
                f"Expected exactly 2 groups, got {manifest.get('total_groups')}"
            )
            b2g = _basename_to_group(manifest)
            assert set(b2g) == set(_ALL_BASENAMES), (
                f"Expected fixture rows {sorted(_ALL_BASENAMES)}, got {sorted(b2g)}"
            )
            gid_a = {b2g[n] for n in _GROUP_A_BASENAMES}
            gid_b = {b2g[n] for n in _GROUP_B_BASENAMES}
            assert len(gid_a) == 1 and len(gid_b) == 1 and gid_a != gid_b, (
                f"Each cluster must form its own single group: "
                f"A={sorted(gid_a)} B={sorted(gid_b)}"
            )
            group_a_id = gid_a.pop()
            group_b_id = gid_b.pop()
            print(
                f"probe_status: s44 groups A={group_a_id} B={group_b_id} "
                f"total={manifest.get('total_groups')}"
            )

            # ── Step 3: gate is DISABLED with no main-tree selection ───────────
            page.get_by_test_id(MENU_ACTION).click()
            entry = page.get_by_test_id(MENU_ACTION_EXECUTE_SELECTED)
            entry.wait_for(state="visible", timeout=5_000)
            assert entry.get_attribute("data-disabled") is not None, (
                "Execute (only selected) must be DISABLED with no main-tree "
                "selection (the web analog of _refresh_execute_selected_only_enabled)"
            )
            page.keyboard.press("Escape")

            # ── Step 4: mark all 4 rows 'delete' via the result-tree menu ──────
            for basename in _ALL_BASENAMES:
                gid = b2g[basename]
                right_click_row(page, row_file_testid(gid, basename))
                click_context_item(page, CTX_SET_ACTION_DELETE)

            decisions = _basename_to_decision(_get_manifest(base_url, db_path))
            for basename in _ALL_BASENAMES:
                assert decisions.get(basename) == "delete", (
                    f"setup: {basename} expected 'delete', got "
                    f"{decisions.get(basename)!r}"
                )

            # ── Step 5: select ONE row of group A in the main tree ─────────────
            # Plain click REPLACES the selection (the last context-menu mark left
            # a group-B row selected) with exactly [groupA_00].  Selecting one of
            # group A's two rows is what makes the #430 group-pull observable.
            click_row(page, row_file_testid(group_a_id, _GROUP_A_BASENAMES[0]))

            # ── Step 6: gate is now ENABLED → open the scoped execute dialog ───
            page.get_by_test_id(MENU_ACTION).click()
            entry = page.get_by_test_id(MENU_ACTION_EXECUTE_SELECTED)
            entry.wait_for(state="visible", timeout=5_000)
            assert entry.get_attribute("data-disabled") is None, (
                "Execute (only selected) must be ENABLED once a row is selected"
            )
            entry.click()
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(
                state="visible", timeout=10_000
            )

            # ── Step 7: #430 group-pull + scope confinement (DOM) ──────────────
            # Both group-A rows are present even though only ONE was selected
            # (group-pull); neither group-B row appears (confinement).
            for basename in _GROUP_A_BASENAMES:
                row = page.get_by_test_id(execute_row_testid(group_a_id, basename))
                row.wait_for(state="visible", timeout=10_000)
            for basename in _GROUP_B_BASENAMES:
                assert (
                    page.get_by_test_id(
                        execute_row_testid(group_b_id, basename)
                    ).count()
                    == 0
                ), (
                    f"scope leak: out-of-scope {basename} appeared in the "
                    f"execute dialog — group-pull must confine to group A"
                )
            n_rows = _count_execute_rows(page)
            print(f"probe_status: s44 scoped_execute_rows={n_rows}")
            assert n_rows == len(_GROUP_A_BASENAMES), (
                f"scoped dialog should show exactly group A's "
                f"{len(_GROUP_A_BASENAMES)} rows, got {n_rows}"
            )

            # ── Step 8: static "Execute" label (#410 — no scoped variant) ──────
            exec_btn = page.get_by_test_id(EXECUTE_BTN_EXECUTE)
            exec_btn.wait_for(state="visible", timeout=5_000)
            label = (exec_btn.inner_text() or "").strip()
            assert label == "Execute", (
                f"Execute button must keep the static 'Execute' label, got "
                f"{label!r} (a scoped-selection label swap regressed #410)"
            )

            # ── Step 9: Execute → group A is all-delete → confirm → commit ─────
            pre_a = {p: os.path.exists(p) for p in group_a_paths}
            pre_b = {p: os.path.exists(p) for p in group_b_paths}
            assert all(pre_a.values()) and all(pre_b.values()), (
                f"all 4 fixture files should exist pre-execute: "
                f"A={pre_a} B={pre_b}"
            )

            exec_btn.click()
            confirm = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM)
            confirm.wait_for(state="visible", timeout=10_000)
            yes = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES)
            yes.wait_for(state="visible", timeout=5_000)
            yes.click()

            # ── Step 10: wait until group A leaves the manifest ────────────────
            manifest_post = _poll_until_absent(
                base_url, db_path, set(_GROUP_A_BASENAMES), timeout_s=30.0
            )

            # ── Step 11: confinement — group A gone, group B fully intact ──────
            # 11a. group A removed from disk.
            for p in group_a_paths:
                assert not os.path.exists(p), (
                    f"group A file still on disk after scoped Execute: {p}"
                )
            # 11b. group B STILL on disk — the load-bearing confinement check.
            for p in group_b_paths:
                assert os.path.exists(p), (
                    f"out-of-scope group B file was deleted: {p} — "
                    f"'Execute (only selected)' must confine to the selected group"
                )
            # 11c. group A absent from the manifest, group B present + decision kept.
            visible_post = _visible_basenames(manifest_post)
            decisions_post = _basename_to_decision(manifest_post)
            for basename in _GROUP_A_BASENAMES:
                assert basename not in visible_post, (
                    f"group A {basename} should be absent post-execute "
                    f"(outcome='deleted'), still visible"
                )
            for basename in _GROUP_B_BASENAMES:
                assert basename in visible_post, (
                    f"group B {basename} must remain in the manifest "
                    f"(out of scope, never executed)"
                )
                assert decisions_post.get(basename) == "delete", (
                    f"group B {basename} decision must stay 'delete' "
                    f"(scoped execute must not touch out-of-scope rows), got "
                    f"{decisions_post.get(basename)!r}"
                )
            print("probe_status: s44 confinement OK — group A deleted, group B intact")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
