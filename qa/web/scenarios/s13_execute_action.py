"""Web scenario s13 — Destructive execute round-trip (canonical Execute Action).

Ported from qa/scenarios/s13_execute_action.py (Qt UIA).

Qt intent:
  - Regenerate a disposable 5-JPEG near-duplicate fixture (random gradients
    at qualities 95/88/80/72/65) so the recycle bin doesn't accumulate identical
    content across runs.
  - Scan → close & load → open ExecuteActionDialog → mark ALL rows 'delete'
    via a "Set by Field / Regex" bulk-apply (field=File Name, regex=.+) → Apply
    → close inner dialog → click Execute → confirm "All Files Will Be Deleted" →
    Yes → verify (a) fixture files no longer exist on disk, (b) manifest rows
    carry executed=1 (checked via SQLite).

Web slice:
  1. Copy the 5 stable qa/sandbox/near-duplicates/*.jpg into a tmpdir to guard
     the repo fixture from real deletion.
  2. run_scan([tmpdir]) → fresh manifest.db under the same tmpdir.
  3. GET /api/manifest → assert 1 group, 5 items; extract group_id +
     5 basenames (mirrors s15 group_number extraction).
  4. Right-click each of the 5 file rows → CTX_SET_ACTION_DELETE via context menu.
  5. GET /api/manifest → assert every item has user_decision == 'delete'.
  6. open_execute_dialog(page) → wait EXECUTE_DIALOG visible.
  7. Click EXECUTE_BTN_EXECUTE → all-delete safety gate fires:
     wait EXECUTE_ALL_DELETE_CONFIRM visible.
  8. (Optional) Assert the confirm sheet body contains a digit (row count sanity).
  9. Click EXECUTE_ALL_DELETE_CONFIRM_YES.
  10. Poll GET /api/manifest until total_files==0 — the execute dialog does
      NOT auto-close on the web (the store keeps executeOpen=true and just
      refreshes the now-empty tree); the completion signal is the manifest
      emptying, not the dialog hiding.
  11. ASSERT absence-based (corrected — see divergence notes):
      (a) Every one of the 5 copied files is absent from disk (os.path.exists
          returns False) — files went to the OS recycle bin via send2trash.
      (b) GET /api/manifest shows total_files == 0 — the group vanished because
          all rows now have outcome='deleted', which is filtered out by the
          WHERE outcome='' predicate in ManifestRepository._LOAD_ALL_SQL.
  12. finally: shutil.rmtree(tmpdir) cleans up any file that somehow survived.

Qt divergences:
  - Qt regenerates its fixture from random gradients each run (guaranteeing
    pHash-cluster convergence across 5 retries).  The web port copies the
    stable qa/sandbox/near-duplicates/*.jpg (which already pass the scanner's
    near-duplicate threshold) to avoid the imagehash/numpy dependency and the
    random-retry logic; the scan outcome is identical.
  - Qt marks all rows via a bulk-regex "mark_all_via_regex" (ActionDialog
    field=File Name, regex=.+).  The web port drives the same result row-by-row
    via right-click context-menu CTX_SET_ACTION_DELETE.  Both paths exercise
    the same PATCH /api/decision write-path and produce user_decision='delete'
    on every row; the route through the UI differs.
  - Qt verifies post-execute state by checking a SQLite column (executed=1).
    The web port uses the CORRECTED ABSENCE-BASED assertion contract instead:
      * executed/outcome column check: NOT done — the manifest endpoint
        filters rows with WHERE outcome='' (ManifestRepository._LOAD_ALL_SQL
        line 60), so deleted rows are absent from the GET /api/manifest
        response.  Asserting item['outcome']=='deleted' on a visible item
        would be a vacuous assertion (it can never be true for a row that
        the endpoint returns).
      * Correct assertion: (a) file absent from disk, (b) total_files==0 in
        the manifest response (all rows gone).
  - Qt reads the file-system state after Execute completes by checking
    fixture_paths directly.  The web port checks copies in tmpdir — the
    actual near-duplicates sandbox files are never touched.
  - The Qt scenario uses qa/run-manifest.sqlite (a persistent shared manifest
    path). The web port creates a fresh manifest.db under tmpdir on every run,
    which avoids cross-run manifest state leakage and makes cleanup trivial.

MIXED-MANIFEST phase (#733 — the regression the OLD gate missed):
  Qt intent: ``_complete_delete_groups`` (app/views/dialogs/execute_action_dialog.py)
  fires the pre-execute "All Files Will Be Deleted" confirm whenever ANY GROUP
  in scope would have every one of its members deleted — a per-group
  "complete" check, not a whole-scope one. Before #733 the web port's gate
  only fired when the ENTIRE execute scope was 100% delete, so a manifest with
  one fully-delete group sitting alongside a group that also carried a decided
  non-delete row (e.g. one 'ignore') silently skipped the confirm — the exact
  miss this phase pins.

  Web slice (second scan, fresh tmpdir + manifest, same PWContext):
    1. Seed TWO exif-edge exact-dup groups (s44/s60's proven deterministic
       2-distinct-source pattern): group A = 2 copies of tz_offset.jpg (both
       will be marked 'delete' — a COMPLETE group); group B = 3 copies of
       subsecond.jpg, of which only ONE is marked 'ignore' (a decided
       non-delete row) and the other two are left undecided — group B is
       therefore NOT complete-delete and must never itself trigger the gate.
       Three members in group B (not two) so group B never collapses to a
       singleton once the ignore row is finalized by Execute, sidestepping
       the prune-singleton flow entirely.
    2. Mark every group-A row 'delete' via the result-tree context menu
       (CTX_SET_ACTION_DELETE); stage group-B's one 'ignore' row via the
       per-row DecisionControl dropdown (mirrors s60 — the context menu
       "Remove from list" finalizes since #694, so staging needs the
       dropdown). Leave the other two group-B rows undecided.
    3. Click EXECUTE_BTN_EXECUTE (unscoped, filter="all" — the default) and
       assert EXECUTE_ALL_DELETE_CONFIRM DOES appear (the #733 fix — with
       the pre-fix whole-scope-only gate this manifest would NOT have
       qualified, because the overall decided scope includes a non-delete
       'ignore' row). Soft-probe the sheet's ``inner_text()`` for group A's
       group number, proving the copy names the QUALIFYING group, not just a
       generic count.
    4. Drive EXECUTE_ALL_DELETE_CONFIRM_NO first (cancel semantics): assert
       every copied file is still on disk and GET /api/manifest decisions are
       byte-for-byte unchanged from the pre-click snapshot — Cancel must be a
       true no-op, not a partial commit.
    5. Click Execute again, drive EXECUTE_ALL_DELETE_CONFIRM_YES: the
       unscoped commit (scope_paths=null) applies to EVERY decided row in the
       manifest, not just group A — group A's 2 delete rows go to the OS
       recycle bin, AND group B's 1 ignore row is finalized (outcome=
       'ignored', file untouched). Assert: (a) group A's 2 files gone from
       disk, (b) group B's 2 undecided files still on disk AND still present
       in GET /api/manifest with user_decision=='', (c) the ignore row's file
       still exists on disk but its basename is ABSENT from GET /api/manifest
       (outcome='ignored' drops it from WHERE outcome='' — the same
       absence-based contract as the primary phase above, s54's precedent).

  Qt divergences (mixed-manifest phase):
    - Qt's ``_complete_delete_groups`` gate is unit-level (app/views tests);
      this phase is the web's layer-3 proof that ``completeDeleteGroupIds``
      (ExecuteDialog.tsx) reproduces the same per-group semantics live,
      including the DeleteConfirmDialog copy naming the qualifying group
      number(s) (#733's ``groupIds`` prop — new for this PR; the primary
      phase above predates it and only probed for a stray digit).
    - Group membership / decisions / the ignore row's finalized-outcome
      absence are all read via GET /api/manifest JSON, matching the primary
      phase's absence-based contract (never assert on outcome directly — the
      endpoint filters WHERE outcome='').
    - EXECUTE_DIALOG-closes-on-Cancel (observed live): clicking
      EXECUTE_ALL_DELETE_CONFIRM_NO also closes the parent EXECUTE_DIALOG.
      DeleteConfirmDialog is a SIBLING Radix Dialog, not nested inside
      ExecuteDialog's DOM (``ExecuteDialog.tsx`` renders
      ``<><DeleteConfirmDialog/><Dialog ...EXECUTE_DIALOG/></>``), so
      dismissing it registers as an interact-outside on ExecuteDialog; nothing
      in ExecuteDialog's ``onInteractOutside`` guards this (it only checks
      ``executeRunning``, which is still false — nothing has POSTed yet), so
      Radix closes it too. This is the SAME cascade shape s34/s53 document for
      the nested LOCK_CONFIRM_DIALOG, now also confirmed for the sibling
      DeleteConfirmDialog. Qt keeps its single dialog open across Cancel and
      lets the user retry in place; the web returns the user to the main
      review tree. This phase re-opens the execute dialog via
      ``open_execute_dialog`` before the second Execute click when the
      close is observed. The safety contract (Cancel deletes nothing, decisions
      unchanged) is identical; only the post-Cancel dialog state differs.
"""
from __future__ import annotations

import json
import os
import re
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
    EXECUTE_BTN_EXECUTE,
    EXECUTE_ALL_DELETE_CONFIRM,
    EXECUTE_ALL_DELETE_CONFIRM_YES,
    EXECUTE_ALL_DELETE_CONFIRM_NO,
    row_file_testid,
    row_decision_testid,
)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = _REPO / "qa" / "sandbox" / "near-duplicates"
_EXIF_EDGE_DIR = _REPO / "qa" / "sandbox" / "exif-edge"

# Mixed-manifest phase fixture — two visually-distinct exif-edge sources
# (s44/s60: proven never to pHash-pair with each other) copied to form two
# independent EXACT-dup groups.
_MIXED_SRC_A = _EXIF_EDGE_DIR / "tz_offset.jpg"
_MIXED_SRC_B = _EXIF_EDGE_DIR / "subsecond.jpg"
_MIXED_GROUP_A_BASENAMES = ["s13m_groupA_00.jpg", "s13m_groupA_01.jpg"]
_MIXED_GROUP_B_BASENAMES = [
    "s13m_groupB_00.jpg",  # marked 'ignore'
    "s13m_groupB_01.jpg",  # left undecided
    "s13m_groupB_02.jpg",  # left undecided
]

# The 5 stable fixture basenames.  Listed explicitly so any addition /
# rename in the sandbox immediately causes a test failure rather than a
# silent mis-count.
_FIXTURE_BASENAMES = [
    "neardup_00_q95.jpg",
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
]


# ---------------------------------------------------------------------------
# HTTP helper (mirrors s31 / s51 / s56 — self-contained per convention)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>.

    Uses urllib (stdlib only) to keep the module dependency-free at import
    time, matching the convention established by s15/s31/s51/s56.
    """
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _extract_decisions(manifest: dict) -> dict[str, str]:
    """Return {basename: user_decision} for all visible manifest items.

    Iterates group["items"] (the server-side serialisation key, NOT "files").
    Empty string means no decision set.  Only rows with outcome=='' appear
    in the manifest (WHERE outcome='' in _LOAD_ALL_SQL).
    """
    decisions: dict[str, str] = {}
    for group in manifest.get("groups", []):
        for file_row in group.get("items", []):
            basename = Path(file_row["file_path"]).name
            decisions[basename] = file_row.get("user_decision", "")
    return decisions


def _basename_to_group(manifest: dict) -> dict[str, str]:
    """Return {basename: group_id(str)} for every visible manifest item."""
    out: dict[str, str] = {}
    for group in manifest.get("groups", []):
        gid = str(group["group_number"])
        for item in group.get("items", []):
            out[Path(item["file_path"]).name] = gid
    return out


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Two independent phases against the destructive execute round-trip:

    1. The original all-delete round-trip (unchanged — see the module
       docstring's "Web slice" section).
    2. The MIXED-MANIFEST pre-execute gate phase added for #733 (see the
       module docstring's "MIXED-MANIFEST phase" section) — a fresh scan,
       fresh tmpdir, fresh manifest, run in its own PWContext so it starts
       from a clean app state independent of phase 1's outcome.
    """
    _run_full_delete_phase(base_url)
    _run_mixed_manifest_phase(base_url)


def _run_full_delete_phase(base_url: str) -> None:
    """Destructive execute round-trip: scan copies → mark all delete → Execute.

    Copies the 5 near-duplicate fixture JPEGs into an isolated tmpdir before
    scanning, so POST /api/execute deletes only the copies (which go to the
    OS recycle bin via send2trash) and never touches the repo sandbox files.
    """
    # ── Set up tmpdir with fresh copies of the fixture ───────────────────────
    tmpdir = tempfile.mkdtemp(prefix="qa_s13_")
    try:
        # Copy each fixture file into tmpdir.
        copied_paths: list[str] = []
        for basename in _FIXTURE_BASENAMES:
            src = _NEAR_DUPS_DIR / basename
            dst = os.path.join(tmpdir, basename)
            shutil.copy(str(src), dst)
            copied_paths.append(dst)

        db_path = os.path.join(tmpdir, "s13_manifest.db")

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the tmpdir copies ───────────────────────────────
            run_scan(
                page,
                sources=[tmpdir],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # ── Step 2: read manifest; extract group_id + basenames ───────────
            # Mirror s15 exactly: first_group["group_number"] cast to str.
            manifest = _get_manifest(base_url, db_path)
            assert manifest["total_groups"] >= 1, (
                f"Expected at least 1 group after scanning near-duplicate "
                f"copies, got total_groups={manifest['total_groups']}"
            )
            first_group = manifest["groups"][0]
            group_id = str(first_group["group_number"])

            # Confirm all 5 basenames are present in the first group.
            present_basenames = [
                Path(f["file_path"]).name
                for f in first_group.get("items", [])
            ]
            assert len(present_basenames) == 5, (
                f"Expected 5 near-duplicate rows in group {group_id}, "
                f"got {len(present_basenames)}: {sorted(present_basenames)}"
            )

            # ── Step 3: mark each row 'delete' via the context menu ───────────
            # Drives the same write-path as s15 step (a), repeated for all 5.
            for basename in present_basenames:
                row_tid = row_file_testid(group_id, basename)
                right_click_row(page, row_tid)
                click_context_item(page, CTX_SET_ACTION_DELETE)

            # ── Step 4: verify all 5 items carry user_decision='delete' ───────
            post_decision = _get_manifest(base_url, db_path)
            decisions = _extract_decisions(post_decision)
            for basename in present_basenames:
                assert decisions.get(basename) == "delete", (
                    f"Pre-execute: expected user_decision='delete' for "
                    f"{basename!r}, got {decisions.get(basename)!r}"
                )

            # ── Step 5: open the execute dialog ──────────────────────────────
            open_execute_dialog(page)
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(
                state="visible", timeout=10_000
            )

            # ── Step 6: click Execute → all-delete safety gate fires ──────────
            # All rows are marked 'delete', so the frontend surfaces the
            # EXECUTE_ALL_DELETE_CONFIRM sheet before proceeding.
            execute_btn = page.get_by_test_id(EXECUTE_BTN_EXECUTE)
            execute_btn.wait_for(state="visible", timeout=10_000)
            execute_btn.click()

            # ── Step 7: wait for the all-delete confirmation sheet ────────────
            confirm_sheet = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM)
            confirm_sheet.wait_for(state="visible", timeout=10_000)

            # Sanity: the confirmation sheet body should mention a digit (the
            # count of files to be deleted).  This is a soft check — we do not
            # assert the exact wording to avoid coupling to copy changes.
            confirm_text = confirm_sheet.inner_text()
            assert re.search(r"[1-9]", confirm_text), (
                f"All-delete confirmation sheet text does not mention a row "
                f"count (expected at least one digit 1-9): {confirm_text!r}"
            )

            # ── Step 8: click Yes to confirm and fire POST /api/execute ───────
            confirm_yes = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES)
            confirm_yes.wait_for(state="visible", timeout=5_000)
            confirm_yes.click()

            # ── Step 9: wait for the execute to COMPLETE ──────────────────────
            # The web execute dialog does NOT auto-close on completion — the
            # store replaces manifest.groups from the server response but leaves
            # executeOpen=true (useAppStore.executeDecisions, ~line 414).  The
            # completion signal is therefore the manifest emptying, NOT the
            # dialog hiding.  Poll GET /api/manifest until total_files==0 (every
            # row now has outcome='deleted' and is filtered out by WHERE
            # outcome='' in _LOAD_ALL_SQL).
            deadline = time.monotonic() + 30.0
            post_execute = _get_manifest(base_url, db_path)
            while post_execute.get("total_files", -1) != 0:
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        "Timed out after 30s waiting for total_files==0 after "
                        f"Execute; last total_files={post_execute.get('total_files')} "
                        f"groups={post_execute.get('groups', [])}"
                    )
                time.sleep(0.5)
                post_execute = _get_manifest(base_url, db_path)

            # ── Step 10: ABSENCE-BASED assertions (see docstring) ─────────────
            #
            # (a) total_files==0 confirmed by the poll above — the group vanished
            #     because all rows have outcome='deleted' (do NOT assert
            #     item['outcome']; deleted rows are absent, not visible).
            assert post_execute["total_files"] == 0  # invariant held by the poll

            # (b) Every copied file must be gone from disk.  POST /api/execute
            #     calls send2trash (recycle=true default), which moves files to
            #     the OS recycle bin.  The tmpdir path is the authoritative
            #     check — files are gone from that path.
            still_present = [p for p in copied_paths if os.path.exists(p)]
            assert not still_present, (
                f"POST /api/execute should have deleted all 5 copied files "
                f"but {len(still_present)} are still on disk:\n"
                + "\n".join(f"  {p}" for p in still_present)
            )

    finally:
        # Clean up the tmpdir.  Any files that survive the execute call
        # (e.g. if the test failed before Execute) are removed here so the
        # machine's filesystem stays tidy across repeated runs.
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Mixed-manifest phase (#733)
# ---------------------------------------------------------------------------


def _run_mixed_manifest_phase(base_url: str) -> None:
    """Prove the pre-execute confirm fires on a PARTIALLY-delete manifest.

    Group A (2 files) is marked entirely 'delete' — a complete-delete group.
    Group B (3 files) has exactly ONE row marked 'ignore' and two left
    undecided — never complete-delete. The old whole-scope gate only fired
    when 100% of the decided scope was 'delete'; since group B's 'ignore' row
    is also decided, the pre-#733 scope was NOT 100% delete and the confirm
    was silently skipped. See the module docstring's "MIXED-MANIFEST phase"
    section for the full contract.
    """
    tmpdir = tempfile.mkdtemp(prefix="qa_s13_mixed_")
    db_path = os.path.join(tmpdir, "s13_mixed_manifest.db")
    try:
        for src, names in (
            (_MIXED_SRC_A, _MIXED_GROUP_A_BASENAMES),
            (_MIXED_SRC_B, _MIXED_GROUP_B_BASENAMES),
        ):
            assert src.exists(), (
                f"exif-edge fixture missing: {src} "
                "(expected qa/sandbox/exif-edge/ with tz_offset.jpg + subsecond.jpg)"
            )
            for name in names:
                shutil.copy(str(src), os.path.join(tmpdir, name))

        group_a_paths = [os.path.join(tmpdir, n) for n in _MIXED_GROUP_A_BASENAMES]
        group_b_paths = [os.path.join(tmpdir, n) for n in _MIXED_GROUP_B_BASENAMES]
        ignore_path = group_b_paths[0]
        undecided_b_basenames = _MIXED_GROUP_B_BASENAMES[1:]

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the tmpdir copies → two distinct groups ──────────
            run_scan(page, sources=[tmpdir], output_path=db_path, scan_timeout=60_000)

            manifest = _get_manifest(base_url, db_path)
            b2g = _basename_to_group(manifest)
            all_basenames = _MIXED_GROUP_A_BASENAMES + _MIXED_GROUP_B_BASENAMES
            for name in all_basenames:
                assert name in b2g, (
                    f"{name!r} missing from the manifest after scan — b2g={b2g}"
                )
            a_ids = {b2g[n] for n in _MIXED_GROUP_A_BASENAMES}
            b_ids = {b2g[n] for n in _MIXED_GROUP_B_BASENAMES}
            assert len(a_ids) == 1 and len(b_ids) == 1 and a_ids != b_ids, (
                f"Each cluster must form exactly one group and stay separate: "
                f"A={sorted(a_ids)} B={sorted(b_ids)}"
            )
            group_a_id = a_ids.pop()
            group_b_id = b_ids.pop()
            print(
                f"probe_status: s13 mixed groups A={group_a_id} B={group_b_id} "
                f"total_groups={manifest.get('total_groups')}"
            )

            # ── Step 2: mark group A entirely 'delete'; stage group B's ONE
            #    'ignore' row; leave the other two group-B rows undecided ─────
            for name in _MIXED_GROUP_A_BASENAMES:
                right_click_row(page, row_file_testid(group_a_id, name))
                click_context_item(page, CTX_SET_ACTION_DELETE)
            set_row_decision(
                page,
                row_decision_testid(group_b_id, _MIXED_GROUP_B_BASENAMES[0]),
                "Ignore",
            )

            pre_decisions = _extract_decisions(_get_manifest(base_url, db_path))
            for name in _MIXED_GROUP_A_BASENAMES:
                assert pre_decisions.get(name) == "delete", (
                    f"setup: group-A {name!r} expected decision 'delete', got "
                    f"{pre_decisions.get(name)!r}"
                )
            assert pre_decisions.get(_MIXED_GROUP_B_BASENAMES[0]) == "ignore", (
                f"setup: group-B ignore row expected decision 'ignore', got "
                f"{pre_decisions.get(_MIXED_GROUP_B_BASENAMES[0])!r}"
            )
            for name in undecided_b_basenames:
                assert pre_decisions.get(name) == "", (
                    f"setup: group-B {name!r} must stay undecided, got "
                    f"{pre_decisions.get(name)!r}"
                )

            # ── Step 3: open the execute dialog, click Execute (unscoped) ─────
            open_execute_dialog(page)
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(
                state="visible", timeout=10_000
            )
            execute_btn = page.get_by_test_id(EXECUTE_BTN_EXECUTE)
            execute_btn.wait_for(state="visible", timeout=10_000)
            execute_btn.click()

            # ── Step 4: the #733 fix — confirm DOES appear on a manifest that
            #    is only PARTIALLY delete overall (group A qualifies, group B
            #    does not) ────────────────────────────────────────────────────
            confirm_sheet = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM)
            confirm_sheet.wait_for(state="visible", timeout=10_000)
            confirm_text = confirm_sheet.inner_text()
            assert group_a_id in confirm_text, (
                f"All-delete confirm body must name the qualifying group "
                f"({group_a_id!r}) so the user knows which group triggered "
                f"it; got: {confirm_text!r}"
            )
            print(f"probe_status: s13 mixed confirm_text={confirm_text!r}")

            # ── Step 5: drive NO first — cancel must be a true no-op ──────────
            confirm_no = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_NO)
            confirm_no.wait_for(state="visible", timeout=5_000)
            confirm_no.click()
            confirm_sheet.wait_for(state="hidden", timeout=5_000)

            # WEB DIVERGENCE (observed live): DeleteConfirmDialog is a SIBLING
            # Radix Dialog, not nested inside ExecuteDialog's DOM (ExecuteDialog.tsx
            # renders ``<><DeleteConfirmDialog/><Dialog ...EXECUTE_DIALOG/></>``),
            # so dismissing it registers as an interact-outside on ExecuteDialog —
            # the same cascade s34/s53 document for the nested LOCK_CONFIRM_DIALOG.
            # ExecuteDialog's onInteractOutside only guards ``executeRunning``
            # (false here — nothing has POSTed yet), so Radix closes it too. Qt
            # keeps its single dialog open across Cancel; the web returns the
            # user to the main review tree. Re-open before the next Execute click.
            execute_dialog_closed_after_cancel = (
                page.get_by_test_id(EXECUTE_DIALOG).count() == 0
                or not page.get_by_test_id(EXECUTE_DIALOG).is_visible()
            )
            print(
                "probe_status: s13 mixed execute_dialog_closed_after_cancel="
                f"{execute_dialog_closed_after_cancel}"
            )

            still_all_present = [
                p for p in group_a_paths + group_b_paths if not os.path.exists(p)
            ]
            assert not still_all_present, (
                f"Cancel must delete nothing; missing from disk: {still_all_present}"
            )
            post_cancel_decisions = _extract_decisions(_get_manifest(base_url, db_path))
            assert post_cancel_decisions == pre_decisions, (
                "Cancel must leave every decision unchanged. "
                f"pre={pre_decisions} post={post_cancel_decisions}"
            )

            # ── Step 6: Execute again, drive YES this time ────────────────────
            if execute_dialog_closed_after_cancel:
                open_execute_dialog(page)
                execute_btn = page.get_by_test_id(EXECUTE_BTN_EXECUTE)
                execute_btn.wait_for(state="visible", timeout=10_000)
            execute_btn.click()
            confirm_sheet.wait_for(state="visible", timeout=10_000)
            confirm_yes = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES)
            confirm_yes.wait_for(state="visible", timeout=5_000)
            confirm_yes.click()

            # Unscoped commit (scope_paths=null) applies to EVERY decided row —
            # group A's 2 deletes AND group B's 1 ignore row. Poll until BOTH
            # leave the manifest: delete outcomes are finalized PER FILE inside
            # the execute call but ignore rows are finalized in one batch at
            # the END (execute_service), so group A's absence alone is not the
            # full completion signal — a GET landing between the two commits
            # still shows the ignore row (observed as a CI-only flake; local
            # runs never hit the window).
            deadline = time.monotonic() + 30.0
            post_execute = _get_manifest(base_url, db_path)
            post_b2g = _basename_to_group(post_execute)
            while (
                any(n in post_b2g for n in _MIXED_GROUP_A_BASENAMES)
                or _MIXED_GROUP_B_BASENAMES[0] in post_b2g
            ):
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        "Timed out after 30s waiting for group A + the ignore "
                        "row to leave the manifest; last "
                        f"groups={post_execute.get('groups', [])}"
                    )
                time.sleep(0.5)
                post_execute = _get_manifest(base_url, db_path)
                post_b2g = _basename_to_group(post_execute)

            # ── Step 7: post-execute assertions ────────────────────────────────
            # (a) group A's files gone from disk.
            a_surviving = [p for p in group_a_paths if os.path.exists(p)]
            assert not a_surviving, (
                f"group-A files should have been deleted but remain on disk: "
                f"{a_surviving}"
            )
            # (b) group B's two undecided files still on disk AND still visible
            #     in the manifest with user_decision=='' (never touched).
            b_undecided_paths = group_b_paths[1:]
            missing_b_undecided = [
                p for p in b_undecided_paths if not os.path.exists(p)
            ]
            assert not missing_b_undecided, (
                f"group-B undecided files must survive Execute but are gone: "
                f"{missing_b_undecided}"
            )
            post_decisions = _extract_decisions(post_execute)
            for name in undecided_b_basenames:
                assert name in post_decisions, (
                    f"group-B undecided {name!r} must remain visible in the "
                    f"manifest, got basenames={sorted(post_decisions)}"
                )
                assert post_decisions[name] == "", (
                    f"group-B undecided {name!r} decision must stay '', got "
                    f"{post_decisions[name]!r}"
                )
            # (c) the ignore row was FINALIZED: file remains on disk but its
            #     basename vanished from the manifest (outcome='ignored' is
            #     filtered by WHERE outcome='' — absence-based, same contract
            #     as the primary phase's deleted rows).
            assert os.path.exists(ignore_path), (
                f"the 'ignore' row's file must remain on disk (ignore never "
                f"deletes): {ignore_path}"
            )
            assert _MIXED_GROUP_B_BASENAMES[0] not in post_decisions, (
                f"the 'ignore' row must be ABSENT from GET /api/manifest after "
                f"Execute (outcome='ignored' filtered by WHERE outcome=''), but "
                f"{_MIXED_GROUP_B_BASENAMES[0]!r} is still present: "
                f"{sorted(post_decisions)}"
            )
            print("probe_status: s13 mixed-manifest gate + commit OK")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
