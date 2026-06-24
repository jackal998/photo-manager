"""Web scenario s61 — mixed-bucket singleton prune on the "ask" path
(#686, web port of qa/scenarios/s61_actioned_singleton_prune.py).

Qt intent
---------
After a destructive op leaves singleton groups, ``ui.prune_singletons="ask"``
fires the SingletonPruneConfirmDialog. It classifies singletons into a PLAIN
bucket (no pending decision) and an ACTIONED bucket (an un-executed
delete/ignore decision). When BOTH exist (mixed layout) the actioned bucket is
opt-in via a checkbox (default unchecked); the Remove button label is dynamic.
Variant D adds the D6 locked-singleton gate: a locked singleton routes through
the LockConfirmDialog before the prune dialog.

Web adaptation — accumulation (the single-select reality)
---------------------------------------------------------
The desktop collapses two groups with ONE multi-row "Remove from List". The web
result-tree "Remove from list" only STAGES (s20 divergence); the FINALIZING
remove is the EXECUTE-dialog menu (s54), which is SINGLE-row. So a single
gesture can only collapse ONE group. To assemble a MIXED offer we ACCUMULATE:

  1. Under ``prune="never"`` finalize-remove A_drop → A collapses to the plain
     (or locked) A_keep singleton, silently (no dialog on the "never" path).
  2. Flip ``prune="ask"`` and finalize-remove B_drop → B collapses to the
     actioned B_keep singleton. maybeOfferPrune now classifies ALL current
     singletons (A_keep + B_keep) → the MIXED dialog (or, for the locked
     variant, the lock gate then the actioned-only prune dialog).

This exercises the SAME behaviour the desktop pins — the dialog offers every
current singleton, not just the one the last op collapsed.

Five variants:
  * remove_plain — mixed, checkbox UNCHECKED, Remove → only A_keep (plain)
    pruned; B_keep (actioned) held with its 'delete' decision intact.
  * remove_both  — mixed, checkbox CHECKED, Remove → both pruned.
  * keep         — mixed, Keep all → neither pruned.
  * lock_cancel  — A_keep LOCKED; lock gate Cancel → A_keep held; the prune
    dialog (actioned-only B_keep) is dismissed with Keep → B_keep held.
  * lock_apply   — lock gate Unlock&Apply → A_keep pruned; prune dialog Keep →
    B_keep held (the Qt to_prune.extend(prunable_locked) tail: A_keep prunes
    regardless of the prune-dialog verdict).

Assertions read sqlite directly — a held singleton (outcome='') is a
single-member group the web review view filters out (orphan-skip), so GET
/api/manifest cannot observe it. See s67 for the same rationale.

Desktop source: qa/scenarios/s61_actioned_singleton_prune.py
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._prune_fixtures import write_cluster
from qa.web._invariants import (
    click_context_item,
    open_execute_dialog,
    right_click_row,
    run_scan,
    set_prune_pref,
)
from qa.web.testid_constants import (
    CTX_LOCK,
    CTX_SET_ACTION_DELETE,
    CTX_SET_ACTION_REMOVE,
    EXECUTE_REMOVE_CONFIRM,
    EXECUTE_REMOVE_CONFIRM_YES,
    LOCK_CONFIRM_BTN_CANCEL,
    LOCK_CONFIRM_BTN_UNLOCK_APPLY,
    LOCK_CONFIRM_DIALOG,
    PRUNE_BTN_KEEP,
    PRUNE_BTN_REMOVE,
    PRUNE_CONFIRM_DIALOG,
    PRUNE_INCLUDE_ACTIONED,
    execute_row_testid,
    row_file_testid,
)

A_KEEP = "s61_a_keep_q95.jpg"   # plain (or locked) singleton after A_drop goes
A_DROP = "s61_a_drop_q65.jpg"
B_KEEP = "s61_b_keep_q95.jpg"   # actioned singleton (staged 'delete', un-executed)
B_DROP = "s61_b_drop_q65.jpg"


def _manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/manifest?path={encoded}", timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _gid_of(base_url: str, db_path: str, basename: str) -> str:
    """group_number of the group currently containing ``basename``."""
    for g in _manifest(base_url, db_path).get("groups", []):
        if any(Path(it["file_path"]).name == basename for it in g["items"]):
            return str(g["group_number"])
    raise AssertionError(f"{basename} not found in any group")


def _sqlite(db_path: str, basename: str) -> tuple[str, str, int]:
    """(outcome, user_decision, is_locked) for ``basename`` — read-only."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(outcome,''), COALESCE(user_decision,''), COALESCE(is_locked,0) "
            "FROM migration_manifest WHERE source_path LIKE ?",
            (f"%{basename}",),
        ).fetchone()
    finally:
        conn.close()
    return (row[0], row[1], row[2]) if row else ("", "", 0)


def _await(predicate, *, timeout: float = 4.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _stage(page, base_url, db_path, basename, item_testid):
    gid = _gid_of(base_url, db_path, basename)
    right_click_row(page, row_file_testid(gid, basename))
    click_context_item(page, item_testid)


def _dismiss_modal_overlays(page, *, max_presses: int = 6) -> None:
    """Dismiss any lingering Radix modal overlays so the toolbar is clickable.

    The execute dialog's close after a remove is timing-dependent — it
    auto-closes cleanly on a local Windows run but can stay OPEN (sometimes
    STACKED under its nested confirm sheet — the CI log showed an
    `aria-hidden` overlay, i.e. a dialog behind another) on the faster CI
    runner. Any such modal overlay (`fixed inset-0 z-50` backdrop) intercepts the
    click on `main-execute-button`, hanging the next `open_execute_dialog` for
    30s. The execute / confirm dialogs close on Escape (they only
    `preventDefault` the Escape while a job is running, which it is not at an
    open point), so press Escape until NO overlay element remains in the DOM —
    waiting for the close animation to fully DETACH it (not merely flip
    `data-state`, since a `data-state="closed"` overlay mid-fade still
    intercepts). Returns immediately when nothing is open (the common path)."""
    overlay = page.locator("div.fixed.inset-0.z-50")
    open_overlay = page.locator('div.fixed.inset-0.z-50[data-state="open"]')
    for _ in range(max_presses):
        if overlay.count() == 0:
            return
        if open_overlay.count() > 0:
            page.keyboard.press("Escape")
        page.wait_for_timeout(450)  # let the close animation detach the overlay


def _execute_remove(page, base_url, db_path, basename):
    """Finalize-remove one row via the EXECUTE-dialog menu (the removeFromList
    path). Dismisses any lingering modal overlay then opens a fresh execute
    dialog (the close after a remove is timing-dependent — see
    _dismiss_modal_overlays); the fresh open re-reads the renumbered groups
    (after one group collapses to a hidden singleton, the survivor is
    renumbered)."""
    _dismiss_modal_overlays(page)
    open_execute_dialog(page)
    gid = _gid_of(base_url, db_path, basename)
    right_click_row(page, execute_row_testid(gid, basename))
    click_context_item(page, CTX_SET_ACTION_REMOVE)
    page.get_by_test_id(EXECUTE_REMOVE_CONFIRM).wait_for(state="visible", timeout=10_000)
    page.get_by_test_id(EXECUTE_REMOVE_CONFIRM_YES).click()


def _run_variant(base_url: str, *, label: str, lock: bool, prune_action: str) -> None:
    """prune_action ∈ {"remove_plain", "remove_both", "keep"}.

    For lock=True, the lock gate is driven first (Cancel for keep-ish holds,
    Unlock&Apply otherwise — encoded by the caller via label) and the prune
    dialog is always dismissed with Keep so the assertion isolates the lock
    effect on A_keep while B_keep (actioned) stays intact.
    """
    tmpdir = tempfile.mkdtemp(prefix="qa_s61_")
    try:
        write_cluster(Path(tmpdir), (A_KEEP, A_DROP), exif_month=1)
        write_cluster(Path(tmpdir), (B_KEEP, B_DROP), exif_month=2)
        db = os.path.join(tmpdir, "s61_manifest.db")

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")
            run_scan(page, sources=[tmpdir], output_path=db, scan_timeout=120_000)
            m = _manifest(base_url, db)
            assert m["total_groups"] == 2, f"expected 2 clusters, got {m['total_groups']}"

            # Stage decisions on the main tree (so the drops appear in the
            # execute tree; B_keep's un-executed 'delete' makes it actioned).
            _stage(page, base_url, db, A_DROP, CTX_SET_ACTION_DELETE)
            _stage(page, base_url, db, B_DROP, CTX_SET_ACTION_DELETE)
            _stage(page, base_url, db, B_KEEP, CTX_SET_ACTION_DELETE)
            assert _await(lambda: _sqlite(db, B_KEEP)[1] == "delete"), "B_keep not staged delete"
            if lock:
                _stage(page, base_url, db, A_KEEP, CTX_LOCK)
                assert _await(lambda: _sqlite(db, A_KEEP)[2] == 1), "A_keep not locked"

            # Phase 1 — collapse A under "never": A_keep becomes a plain/locked
            # singleton with NO prune dialog. (Each variant re-asserts "never"
            # because the previous variant's Phase 2 left the pref at "ask".)
            set_prune_pref(base_url, "never")
            _execute_remove(page, base_url, db, A_DROP)
            assert _await(lambda: _sqlite(db, A_DROP)[0] == "ignored"), "A_drop not removed"
            assert not page.get_by_test_id(PRUNE_CONFIRM_DIALOG).is_visible(), \
                'prune dialog must NOT fire under "never"'

            # Phase 2 — flip to "ask" and collapse B → maybeOfferPrune sees BOTH
            # singletons (A_keep + B_keep).
            set_prune_pref(base_url, "ask")
            _execute_remove(page, base_url, db, B_DROP)
            assert _await(lambda: _sqlite(db, B_DROP)[0] == "ignored"), "B_drop not removed"

            if lock:
                # D6 gate fires first for the locked A_keep.
                page.get_by_test_id(LOCK_CONFIRM_DIALOG).wait_for(state="visible", timeout=8_000)
                lock_btn = LOCK_CONFIRM_BTN_CANCEL if label == "lock_cancel" else LOCK_CONFIRM_BTN_UNLOCK_APPLY
                page.get_by_test_id(lock_btn).click()
                # Then the prune dialog (actioned-only B_keep) — dismiss with Keep.
                page.get_by_test_id(PRUNE_CONFIRM_DIALOG).wait_for(state="visible", timeout=8_000)
                page.get_by_test_id(PRUNE_BTN_KEEP).click()
            else:
                # Mixed dialog: assert the opt-in checkbox is present + the label.
                page.get_by_test_id(PRUNE_CONFIRM_DIALOG).wait_for(state="visible", timeout=8_000)
                assert page.get_by_test_id(PRUNE_INCLUDE_ACTIONED).is_visible(), \
                    "mixed layout must render the actioned opt-in checkbox"
                assert "Remove 1" in page.get_by_test_id(PRUNE_BTN_REMOVE).inner_text(), \
                    "dynamic Remove label should show the plain count (1)"
                if prune_action == "keep":
                    page.get_by_test_id(PRUNE_BTN_KEEP).click()
                else:
                    if prune_action == "remove_both":
                        page.get_by_test_id(PRUNE_INCLUDE_ACTIONED).click()
                    page.get_by_test_id(PRUNE_BTN_REMOVE).click()

            # ---- Assert the outcomes (sqlite — review view hides singletons) ----
            if label == "remove_plain":
                _await(lambda: _sqlite(db, A_KEEP)[0] == "ignored")
                assert _sqlite(db, A_KEEP)[0] == "ignored", f"plain A_keep should be pruned, got {_sqlite(db, A_KEEP)}"
                assert _sqlite(db, B_KEEP)[0] == "", "actioned B_keep should be HELD (box unchecked)"
                assert _sqlite(db, B_KEEP)[1] == "delete", "actioned B_keep must keep its 'delete' decision"
            elif label == "remove_both":
                _await(lambda: _sqlite(db, A_KEEP)[0] == "ignored" and _sqlite(db, B_KEEP)[0] == "ignored")
                assert _sqlite(db, A_KEEP)[0] == "ignored", "A_keep should be pruned"
                assert _sqlite(db, B_KEEP)[0] == "ignored", "B_keep should be pruned (box checked)"
            elif label == "keep":
                assert _sqlite(db, A_KEEP)[0] == "", "Keep all must prune nothing (A_keep)"
                assert _sqlite(db, B_KEEP)[0] == "", "Keep all must prune nothing (B_keep)"
                assert _sqlite(db, B_KEEP)[1] == "delete", "B_keep must keep its decision on Keep all"
            elif label == "lock_cancel":
                assert _sqlite(db, A_KEEP)[0] == "", "Cancel must HOLD the locked A_keep"
                assert _sqlite(db, A_KEEP)[2] == 1, "A_keep should still be locked"
                assert _sqlite(db, B_KEEP)[0] == "", "B_keep held under Keep"
            else:  # lock_apply
                _await(lambda: _sqlite(db, A_KEEP)[0] == "ignored")
                assert _sqlite(db, A_KEEP)[0] == "ignored", "Unlock&Apply must prune the locked A_keep"
                assert _sqlite(db, B_KEEP)[0] == "", "B_keep held under Keep (actioned bucket intact)"
            print(f"probe_status: s61 {label} A_keep={_sqlite(db, A_KEEP)[0]!r} "
                  f"B_keep_outcome={_sqlite(db, B_KEEP)[0]!r} B_keep_decision={_sqlite(db, B_KEEP)[1]!r}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run(*, base_url: str) -> None:
    _run_variant(base_url, label="remove_plain", lock=False, prune_action="remove_plain")
    _run_variant(base_url, label="remove_both", lock=False, prune_action="remove_both")
    _run_variant(base_url, label="keep", lock=False, prune_action="keep")
    _run_variant(base_url, label="lock_cancel", lock=True, prune_action="keep")
    _run_variant(base_url, label="lock_apply", lock=True, prune_action="keep")
