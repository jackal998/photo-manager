"""Web scenario s60 — Execute Action filter by action type (#502 / #676).

Ported from qa/scenarios/s60_execute_filter_by_action_type.py (Qt UIA).

Qt intent:
  - Two visual clusters of 4 near-dups each; mark cluster A delete + cluster B
    remove-from-list (decision='ignore') via the Set-Action-by-Field regex flow.
  - Open the Execute dialog, drive the type-filter combo:
      "Delete only"  → tree shows cluster A (4 rows);
      "Remove only"  → tree shows cluster B (4 rows) AND the hidden-destructive
                       banner is visible (the 4 hidden pending deletes);
      back to "Delete only" → Execute → cluster A deleted on disk, cluster B
                       SURVIVES on disk with decision='ignore', executed=0.
  - The load-bearing contract is "visible = committed": Execute under a
    non-"all" filter scopes the commit to the visible rows only.

Web slice (this is the qa half of the #676 FE fix that lands in the same PR):
  Before #676 the web Execute IGNORED the active type filter (handleExecute
  passed no scope → scope_paths=null → ALL decided rows committed regardless
  of the filter), and the hidden-destructive banner did not exist.  The FE fix
  scopes handleExecute to the visible rows (ExecuteDialog.tsx) and adds the
  HiddenDestructiveBanner.  This scenario is the layer-3 proof of both.

  Fixture (deterministic — no pHash-retry, unlike Qt's near-dup generator):
    Two byte-identical copies of each of TWO visually-distinct exif-edge
    JPEGs.  Each pair is an EXACT-duplicate (SHA-256 match) → survives the
    manifest singleton-drop as its own group; the two source images are
    verified-distinct (s08: "each gets its own pHash, none pair") so the two
    groups never merge.  Group A = copies of one image (marked delete),
    Group B = copies of the other (marked ignore).  Two members per group is
    the minimum to clear singleton-drop; Qt used four, but the count is a
    fixture detail — this port asserts its own 2-per-group counts.

Assertions:
  1. Scan yields exactly two groups, two files each; the group-A basenames all
     land in one group and the group-B basenames in a different group.
  2. After marking, every group-A row has user_decision='delete' and every
     group-B row has user_decision='ignore'.
  3. Execute dialog type filter "Delete only" → only the 2 group-A execute
     rows are present (group-B rows absent); hidden-destructive banner absent.
  4. Type filter "Remove only" → only the 2 group-B execute rows are present
     (group-A rows absent) AND the hidden-destructive banner IS visible (the
     2 hidden pending deletes).
  5. Back to "Delete only" → Execute → all-delete confirm (group A is fully
     delete in the visible scope) → Yes → poll until the manifest drops to the
     2 group-B rows; assert (a) both group-A files gone from disk, (b) both
     group-B files still on disk, (c) the manifest's surviving rows are the
     group-B basenames with user_decision='ignore' (executed=0 — the filter
     excluded them, the #676 "visible = committed" data-safety guarantee).

Qt divergences:
  D1. FIXTURE: Qt generates 8 random near-dups across two seeds (with an
      imagehash retry loop to force intra-cluster clustering); the web port
      copies two distinct stable exif-edge JPEGs twice each (EXACT-dup pairs),
      which is deterministic and dependency-free.  Two rows per group (vs Qt's
      four) — the per-group COUNT is a fixture detail, not the contract.
  D2. MARKING: Qt marks via the Set-Action-by-Field regex bulk-apply
      (^s60_groupA_ → delete, ^s60_groupB_ → remove).  The web port marks
      group-A 'delete' via the right-click context menu (CTX_SET_ACTION_DELETE)
      and stages group-B 'ignore' via the per-row decision dropdown
      (DecisionControl) — both the same PATCH /api/decisions write-path, reached
      row-by-row instead of by regex.  (Since #694 the result-tree "Remove from
      list" FINALIZES outcome='ignored', so staging an ignore decision uses the
      dropdown, not the context menu.)
  D3. BANNER READ: Qt scrapes the banner QLabel text for "hidden"/"隱藏" + the
      count.  The web port asserts the dedicated testid
      EXECUTE_HIDDEN_DESTRUCTIVE_BANNER (locale-independent, no text coupling).
  D4. POST-EXECUTE STATE: Qt reads executed=0 / decision='ignore' for group B
      from SQLite.  The web port reads the canonical GET /api/manifest: group-B
      rows present with user_decision='ignore' (deleted group-A rows are
      filtered out by WHERE outcome='' in _LOAD_ALL_SQL, so their absence — not
      an outcome='deleted' read — is the signal).
  D5. NO LOCK SUB-CASE: Qt notes the lock-confirm-ordering fix is covered at
      layer 1; this scenario (like Qt's) does not exercise locked rows.

⚠ HEADS-UP: each run sends 2 files (group A) to the OS recycle bin.  The
fixture lives in a tmpdir that is removed on exit, so nothing accumulates in
the repo; the recycled copies grow the bin by 2 per run until emptied — same
destructive-scenario contract as s13.

Desktop source: qa/scenarios/s60_execute_filter_by_action_type.py
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
    run_scan,
    right_click_row,
    click_context_item,
    open_execute_dialog,
    set_row_decision,
)
from qa.web.testid_constants import (
    CTX_SET_ACTION_DELETE,
    EXECUTE_DIALOG,
    EXECUTE_TYPE_FILTER,
    EXECUTE_HIDDEN_DESTRUCTIVE_BANNER,
    EXECUTE_BTN_EXECUTE,
    EXECUTE_ALL_DELETE_CONFIRM,
    EXECUTE_ALL_DELETE_CONFIRM_YES,
    row_file_testid,
    row_decision_testid,
    execute_row_testid,
)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_EXIF_EDGE_DIR = _REPO / "qa" / "sandbox" / "exif-edge"

# Two visually-DISTINCT source images (s08: exif-edge files each get their own
# pHash and never pair) so the two EXACT-dup groups stay separate.
_SRC_A = _EXIF_EDGE_DIR / "tz_offset.jpg"
_SRC_B = _EXIF_EDGE_DIR / "subsecond.jpg"

# Two byte-identical copies of each source → one EXACT-dup group per source.
_GROUP_A_BASENAMES = ["s60_groupA_00.jpg", "s60_groupA_01.jpg"]
_GROUP_B_BASENAMES = ["s60_groupB_00.jpg", "s60_groupB_01.jpg"]


# ---------------------------------------------------------------------------
# HTTP helper (self-contained — mirrors s13 exactly)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _basename_to_group(manifest: dict) -> dict[str, str]:
    """Return {basename: group_id(str)} for every visible manifest item."""
    out: dict[str, str] = {}
    for group in manifest.get("groups", []):
        gid = str(group["group_number"])
        for item in group.get("items", []):
            out[Path(item["file_path"]).name] = gid
    return out


def _basename_to_decision(manifest: dict) -> dict[str, str]:
    """Return {basename: user_decision} for every visible manifest item."""
    out: dict[str, str] = {}
    for group in manifest.get("groups", []):
        for item in group.get("items", []):
            out[Path(item["file_path"]).name] = item.get("user_decision", "")
    return out


def _select_type_filter(page, option_text: str, *, timeout: float = 5_000) -> None:
    """Open the execute-dialog type-filter Radix Select and pick an option."""
    page.get_by_test_id(EXECUTE_TYPE_FILTER).click()
    option = page.get_by_role("option", name=option_text, exact=True)
    option.wait_for(state="visible", timeout=timeout)
    option.click()


def _count_execute_rows(page) -> int:
    """Return the number of file rows currently rendered in the execute tree."""
    return page.locator('[data-testid^="execute-row-"]').count()


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Filter-scoped execute: mark A delete + B ignore, Execute under the
    Delete-only filter, prove only A commits (the #676 data-safety contract)."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s60_")
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
        db_path = os.path.join(tmpdir, "s60_manifest.db")

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            run_scan(page, sources=[tmpdir], output_path=db_path, scan_timeout=60_000)

            # ── Assertion 1: two distinct groups, two files each ──────────────
            manifest = _get_manifest(base_url, db_path)
            b2g = _basename_to_group(manifest)
            print(
                f"probe_status: s60 total_groups={manifest.get('total_groups')} "
                f"total_files={manifest.get('total_files')} b2g={b2g}"
            )
            for name in _GROUP_A_BASENAMES + _GROUP_B_BASENAMES:
                assert name in b2g, (
                    f"{name!r} missing from the manifest — the EXACT-dup pair "
                    f"did not survive singleton-drop. b2g={b2g}"
                )
            a_ids = {b2g[n] for n in _GROUP_A_BASENAMES}
            b_ids = {b2g[n] for n in _GROUP_B_BASENAMES}
            assert len(a_ids) == 1 and len(b_ids) == 1, (
                f"Each cluster must form exactly one group: "
                f"group-A ids={a_ids}, group-B ids={b_ids}"
            )
            group_a_id = a_ids.pop()
            group_b_id = b_ids.pop()
            assert group_a_id != group_b_id, (
                f"Group A and Group B collapsed into one group ({group_a_id}); "
                f"the two source images unexpectedly paired by pHash."
            )

            # ── Mark group A delete (context menu), group B ignore (dropdown) ──
            # group-B stages 'ignore' via the DecisionControl dropdown — since
            # #694 the context-menu "Remove from list" finalizes, not stages.
            for name in _GROUP_A_BASENAMES:
                right_click_row(page, row_file_testid(group_a_id, name))
                click_context_item(page, CTX_SET_ACTION_DELETE)
            for name in _GROUP_B_BASENAMES:
                set_row_decision(page, row_decision_testid(group_b_id, name), "Ignore")

            # ── Assertion 2: decisions persisted as marked ───────────────────
            decisions = _basename_to_decision(_get_manifest(base_url, db_path))
            for name in _GROUP_A_BASENAMES:
                assert decisions.get(name) == "delete", (
                    f"group-A {name!r} expected decision 'delete', got "
                    f"{decisions.get(name)!r}"
                )
            for name in _GROUP_B_BASENAMES:
                assert decisions.get(name) == "ignore", (
                    f"group-B {name!r} expected decision 'ignore', got "
                    f"{decisions.get(name)!r}"
                )

            # ── Open the execute dialog ──────────────────────────────────────
            open_execute_dialog(page)
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(state="visible", timeout=10_000)

            # ── Assertion 3: "Delete only" shows ONLY group A ────────────────
            _select_type_filter(page, "Delete only")
            page.wait_for_timeout(200)
            for name in _GROUP_A_BASENAMES:
                page.get_by_test_id(execute_row_testid(group_a_id, name)).wait_for(
                    state="visible", timeout=5_000
                )
            for name in _GROUP_B_BASENAMES:
                assert page.get_by_test_id(
                    execute_row_testid(group_b_id, name)
                ).count() == 0, (
                    f"group-B {name!r} should be HIDDEN under the Delete-only "
                    f"filter but is present in the execute tree."
                )
            delete_visible = _count_execute_rows(page)
            assert delete_visible == len(_GROUP_A_BASENAMES), (
                f"Delete-only filter: expected {len(_GROUP_A_BASENAMES)} visible "
                f"rows (group A), got {delete_visible}."
            )
            assert page.get_by_test_id(
                EXECUTE_HIDDEN_DESTRUCTIVE_BANNER
            ).count() == 0, (
                "Hidden-destructive banner must be ABSENT under Delete-only "
                "(deletes are the visible subset, nothing hidden)."
            )

            # ── Assertion 4: "Remove only" shows ONLY group B + hidden banner ─
            _select_type_filter(page, "Remove only")
            page.wait_for_timeout(200)
            for name in _GROUP_B_BASENAMES:
                page.get_by_test_id(execute_row_testid(group_b_id, name)).wait_for(
                    state="visible", timeout=5_000
                )
            for name in _GROUP_A_BASENAMES:
                assert page.get_by_test_id(
                    execute_row_testid(group_a_id, name)
                ).count() == 0, (
                    f"group-A {name!r} should be HIDDEN under the Remove-only "
                    f"filter but is present in the execute tree."
                )
            remove_visible = _count_execute_rows(page)
            assert remove_visible == len(_GROUP_B_BASENAMES), (
                f"Remove-only filter: expected {len(_GROUP_B_BASENAMES)} visible "
                f"rows (group B), got {remove_visible}."
            )
            page.get_by_test_id(EXECUTE_HIDDEN_DESTRUCTIVE_BANNER).wait_for(
                state="visible", timeout=5_000
            )
            print("probe_status: s60 hidden_destructive_banner_visible=True")

            # ── Assertion 5: Execute under Delete-only commits ONLY group A ───
            _select_type_filter(page, "Delete only")
            page.wait_for_timeout(200)

            pre_a = [p for p in group_a_paths if os.path.exists(p)]
            pre_b = [p for p in group_b_paths if os.path.exists(p)]
            assert len(pre_a) == 2 and len(pre_b) == 2, (
                f"pre-execute disk state wrong: group_a_present={len(pre_a)} "
                f"group_b_present={len(pre_b)}"
            )

            page.get_by_test_id(EXECUTE_BTN_EXECUTE).click()
            # Group A is fully delete in the visible scope → all-delete confirm.
            page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM).wait_for(
                state="visible", timeout=10_000
            )
            page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES).click()

            # Poll until group A drops out of the manifest (B's 2 rows remain).
            deadline = time.monotonic() + 30.0
            post = _get_manifest(base_url, db_path)
            while post.get("total_files", -1) != len(_GROUP_B_BASENAMES):
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        "Timed out waiting for the filtered Execute to drop "
                        f"group A; last total_files={post.get('total_files')} "
                        f"groups={post.get('groups', [])}"
                    )
                time.sleep(0.5)
                post = _get_manifest(base_url, db_path)

            # (a) group A gone from disk; (b) group B still on disk.
            a_surviving = [p for p in group_a_paths if os.path.exists(p)]
            assert not a_surviving, (
                f"group-A files should have been deleted under the Delete-only "
                f"filter but {len(a_surviving)} remain on disk: {a_surviving}"
            )
            b_missing = [p for p in group_b_paths if not os.path.exists(p)]
            assert not b_missing, (
                f"group-B files must SURVIVE (excluded by the Delete-only "
                f"filter) but {len(b_missing)} were deleted: {b_missing} — the "
                f"#676 'visible = committed' scoping failed and Execute "
                f"committed hidden rows."
            )

            # (c) the surviving manifest rows are group B, still decision='ignore'.
            post_decisions = _basename_to_decision(post)
            assert set(post_decisions) == set(_GROUP_B_BASENAMES), (
                f"Post-execute manifest should contain only the group-B rows, "
                f"got {sorted(post_decisions)}"
            )
            for name in _GROUP_B_BASENAMES:
                assert post_decisions.get(name) == "ignore", (
                    f"group-B {name!r} must retain user_decision='ignore' after "
                    f"the filtered Execute (not committed), got "
                    f"{post_decisions.get(name)!r}"
                )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
