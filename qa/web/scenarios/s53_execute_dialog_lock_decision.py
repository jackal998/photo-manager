"""Web scenario s53 — Lock / Unlock / Set-Action via the EXECUTE dialog menu.

Ported from qa/scenarios/s53_execute_dialog_lock_decision.py (Qt UIA).

Qt intent (5-file near-duplicates fixture):
  Three non-regex paths on the Execute Action dialog's file-row right-click:
  (a) Lock a single row, (b) Unlock the same row (idempotent round-trip), and
  (c) Set Action > delete on a different row. Lock/Unlock and the decision are
  verified directly in the manifest.

Web slice — cluster B (the execute-dialog row context menu, ExecuteContextMenu):
  The web ExecuteTree shows only DECIDED rows (user_decision != ''), so the
  scenario first SEEDS two decisions via the result-tree menu (already covered
  by s35/s40) to make two rows visible in the execute dialog, then drives the
  EXECUTE-dialog menu:
    (a) right-click the delete-seeded row -> Lock      -> is_locked True
    (b) right-click the same row          -> Unlock    -> is_locked False
    (c) right-click the ignore-seeded row -> Delete     -> user_decision 'delete'
  Lock/decision writes go through PATCH /api/lock and PATCH /api/decision (the
  same store actions the result menu uses) and are asserted via GET /api/manifest.

LOCKED-ROW DECISION phase (#733 — PATCH /api/decision now lock-gated):
  Qt intent (#417 parity): setting a decision on a locked row from the
  execute-dialog row menu must NEVER silently overwrite it. Before #733 the
  web PATCH /api/decision had no lock gate at all — right-clicking a locked
  row and choosing "Set Action > Delete" wrote straight through, silently
  discarding the lock's protection.

  Web slice (continuing the SAME manifest/page from steps (a)-(c) above):
    (d) Re-lock the q88 row (already decision='delete' from step (a)/(c)
        above) via the execute-menu CTX_LOCK.
    (e) Right-click the SAME locked row -> CTX_SET_ACTION_DELETE (the exact
        path that previously silently overwrote the decision). PATCH
        /api/decision now returns 409 locked_paths -> the store reverts its
        optimistic apply and opens LOCK_CONFIRM_DIALOG with op="decision"
        DEFERRED wording ("...Nothing is deleted yet — this only queues the
        decision...", LockConfirmDialog.tsx line ~144).
    (f) Drive LOCK_CONFIRM_BTN_CANCEL -> GET /api/manifest proves q88's
        user_decision and is_locked are BYTE-FOR-BYTE unchanged (the
        silent-overwrite regression assertion: pre-#733 this PATCH would
        have gone straight through with no gate to cancel).
    (g) Re-open the execute dialog (see Qt divergence note below), repeat the
        right-click -> CTX_SET_ACTION_DELETE, drive
        LOCK_CONFIRM_BTN_UNLOCK_APPLY -> GET /api/manifest proves
        user_decision=='delete' AND is_locked==False (Unlock & Apply both
        writes the decision AND force-unlocks the row in the same call —
        Qt #417 verdict parity, backend confirmed in
        core/app_service/review_service.py:set_decisions' force_locked path).

Qt divergences:
  D1. Manifest read via GET /api/manifest JSON vs Qt SQLite.
  D2. The web execute tree is decided-rows-only (#676 tracks full-group display);
      desktop shows the whole group. The web port seeds decisions so the target
      rows are present, asserting the same lock/decision write paths. Decisions
      are seeded through the RESULT-tree context menu (UI), not a back-door API.
  D3. No multi-row Ctrl+click — the web menu targets the right-clicked row only
      (same precedent as web s35).
  D4. LOCK_CONFIRM_DIALOG cascade (LOCKED-ROW DECISION phase, observed live):
      dismissing LOCK_CONFIRM_DIALOG (either Cancel or Unlock & Apply) closes
      the parent EXECUTE_DIALOG too — the same Radix interact-outside cascade
      s34 documents for op="execute" (LockConfirmDialog is portaled OUTSIDE
      ExecuteDialog's DOM subtree; dismissing it registers as an
      interact-outside on ExecuteDialog, and nothing in ExecuteDialog's
      onInteractOutside guards op="decision" — only ``executeRunning``, which
      a decision PATCH never sets — so Radix always closes it). This phase
      re-opens the execute dialog via ``open_execute_dialog`` between (f) and
      (g) to restore a known state before the second right-click; Qt keeps its
      single dialog open throughout. The safety contract (no silent
      overwrite) is identical; only the post-dismiss dialog state differs.

Desktop source: qa/scenarios/s53_execute_dialog_lock_decision.py
Fixture:        qa/sandbox/near-duplicates/ (5 JPEGs)
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    click_context_item,
    open_execute_dialog,
    right_click_row,
    run_scan,
    set_row_decision,
)
from qa.web.testid_constants import (
    CTX_LOCK,
    CTX_SET_ACTION_DELETE,
    CTX_UNLOCK,
    EXECUTE_DIALOG,
    LOCK_CONFIRM_BTN_CANCEL,
    LOCK_CONFIRM_BTN_UNLOCK_APPLY,
    LOCK_CONFIRM_DIALOG,
    execute_row_testid,
    row_decision_testid,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

_Q95 = "neardup_00_q95.jpg"
_Q88 = "neardup_01_q88.jpg"  # seeded delete → lock/unlock target
_Q80 = "neardup_02_q80.jpg"  # seeded ignore → set-action-delete target
_Q72 = "neardup_03_q72.jpg"
_Q65 = "neardup_04_q65.jpg"


def _manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _rows(manifest: dict) -> dict[str, dict]:
    """Return {basename: {decision, is_locked}} across all groups."""
    out: dict[str, dict] = {}
    for group in manifest.get("groups", []):
        for it in group.get("items", []):
            out[Path(it["file_path"]).name] = {
                "decision": it.get("user_decision", "") or "",
                "is_locked": bool(it.get("is_locked")),
            }
    return out


def _await(base_url: str, db_path: str, predicate, *, timeout: float = 6.0) -> dict:
    """Poll GET /api/manifest until ``predicate(rows)`` is true (async PATCH)."""
    deadline = time.monotonic() + timeout
    rows: dict[str, dict] = {}
    while time.monotonic() < deadline:
        rows = _rows(_manifest(base_url, db_path))
        if predicate(rows):
            return rows
        time.sleep(0.1)
    return rows


def _first_group_id(base_url: str, db_path: str) -> str:
    data = _manifest(base_url, db_path)
    assert data["total_groups"] >= 1, "expected at least one group in the manifest"
    return str(data["groups"][0]["group_number"])


def run(*, base_url: str) -> None:
    """Seed two decisions, then drive Lock/Unlock/Set-Action via the execute menu."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            pre = _rows(_manifest(base_url, db_path))
            assert len(pre) == 5, f"expected 5 fixture rows, got {sorted(pre)}"
            assert not any(r["is_locked"] for r in pre.values()), (
                f"fresh manifest must be all-unlocked: {pre}"
            )
            assert all(r["decision"] == "" for r in pre.values()), (
                f"fresh manifest must have no decisions: {pre}"
            )

            gid = _first_group_id(base_url, db_path)

            # ── Seed two decisions on the RESULT tree ──────────────────────────
            # q88 → delete via the context menu (lock/unlock target); q80 → ignore
            # via the per-row decision dropdown (set-delete target).  Since #694
            # the context-menu "Remove from list" FINALIZES outcome='ignored', so
            # staging a reversible 'ignore' decision uses the DecisionControl.
            right_click_row(page, row_file_testid(gid, _Q88))
            click_context_item(page, CTX_SET_ACTION_DELETE)
            set_row_decision(page, row_decision_testid(gid, _Q80), "Ignore")
            seeded = _await(
                base_url,
                db_path,
                lambda r: r[_Q88]["decision"] == "delete"
                and r[_Q80]["decision"] == "ignore",
            )
            assert seeded[_Q88]["decision"] == "delete", f"seed q88 delete failed: {seeded}"
            assert seeded[_Q80]["decision"] == "ignore", f"seed q80 ignore failed: {seeded}"

            # ── Open the execute dialog (shows the two decided rows) ───────────
            open_execute_dialog(page)

            # ── (a) Lock the delete-seeded row via the EXECUTE menu ────────────
            right_click_row(page, execute_row_testid(gid, _Q88))
            click_context_item(page, CTX_LOCK)
            state = _await(base_url, db_path, lambda r: r[_Q88]["is_locked"])
            assert state[_Q88]["is_locked"] is True, f"(a) {_Q88} should be locked: {state}"
            assert all(
                not r["is_locked"] for n, r in state.items() if n != _Q88
            ), f"(a) lock leaked beyond {_Q88}: {state}"

            # ── (b) Unlock the same row (menu now offers Unlock) ───────────────
            right_click_row(page, execute_row_testid(gid, _Q88))
            click_context_item(page, CTX_UNLOCK)
            state = _await(base_url, db_path, lambda r: not r[_Q88]["is_locked"])
            assert not any(r["is_locked"] for r in state.values()), (
                f"(b) all rows should be unlocked: {state}"
            )

            # ── (c) Set Action > Delete on the ignore-seeded row ───────────────
            right_click_row(page, execute_row_testid(gid, _Q80))
            click_context_item(page, CTX_SET_ACTION_DELETE)
            state = _await(base_url, db_path, lambda r: r[_Q80]["decision"] == "delete")
            assert state[_Q80]["decision"] == "delete", (
                f"(c) {_Q80} should be 'delete' after the execute menu set-action: {state}"
            )

            # ── Untouched rows never changed ───────────────────────────────────
            assert state[_Q88]["decision"] == "delete", f"q88 decision drifted: {state}"
            for name in (_Q95, _Q72, _Q65):
                assert state[name]["decision"] == "", f"{name} decision drifted: {state}"
                assert state[name]["is_locked"] is False, f"{name} lock drifted: {state}"
            print(f"probe_status: s53 final={ {k: state[k] for k in sorted(state)} }")

            # ── (d) Re-lock q88 (already decision='delete') via the EXECUTE
            #     menu ───────────────────────────────────────────────────────
            right_click_row(page, execute_row_testid(gid, _Q88))
            click_context_item(page, CTX_LOCK)
            state = _await(base_url, db_path, lambda r: r[_Q88]["is_locked"])
            assert state[_Q88]["is_locked"] is True, f"(d) {_Q88} should be locked: {state}"
            assert state[_Q88]["decision"] == "delete", (
                f"(d) {_Q88} decision must stay 'delete' across the lock: {state}"
            )
            pre_gate = dict(state)

            # ── (e) Right-click the SAME locked row -> Set Action > Delete.
            #     PATCH /api/decision now returns 409 locked_paths (#733) ────
            right_click_row(page, execute_row_testid(gid, _Q88))
            click_context_item(page, CTX_SET_ACTION_DELETE)
            lock_confirm = page.get_by_test_id(LOCK_CONFIRM_DIALOG)
            lock_confirm.wait_for(state="visible", timeout=15_000)

            # DEFERRED wording (op="decision" — Qt #417 parity): setting a
            # decision never deletes anything by itself, so the body must NOT
            # read like the IMMEDIATE/execute-context copy s34 asserts.
            dialog_text = lock_confirm.inner_text()
            assert "queue" in dialog_text.lower() or (
                "nothing is deleted" in dialog_text.lower()
            ), (
                "LOCK_CONFIRM_DIALOG body must carry the DEFERRED op='decision' "
                f"wording ('...queues the decision...'); got: {dialog_text[:200]!r}"
            )
            print(f"probe_status: s53 lock_confirm_decision_text={dialog_text!r}")

            # ── (f) Cancel -> the silent-overwrite regression assertion:
            #     decision + lock must be BYTE-FOR-BYTE unchanged ────────────
            cancel_btn = page.get_by_test_id(LOCK_CONFIRM_BTN_CANCEL)
            cancel_btn.wait_for(state="visible", timeout=5_000)
            cancel_btn.click()
            lock_confirm.wait_for(state="hidden", timeout=10_000)

            # WEB DIVERGENCE (D4, see module docstring): dismissing the nested
            # lock-confirm also closes the parent execute dialog (the same
            # interact-outside cascade s34 documents for op="execute"). Observed
            # live for op="decision" too — assert the actual end-state so a
            # future regression toward a stuck-open-forever modal is caught,
            # then re-open before continuing.
            execute_dialog_closed = False
            try:
                page.get_by_test_id(EXECUTE_DIALOG).wait_for(
                    state="hidden", timeout=3_000
                )
                execute_dialog_closed = True
            except Exception:  # noqa: BLE001 — Qt-parity path: dialog stayed open
                pass
            print(f"probe_status: s53 execute_dialog_closed_after_cancel={execute_dialog_closed}")

            post_cancel = _rows(_manifest(base_url, db_path))
            assert post_cancel[_Q88] == pre_gate[_Q88], (
                "(f) Cancel must leave q88's decision + lock BYTE-FOR-BYTE "
                f"unchanged (the silent-overwrite regression check): "
                f"pre={pre_gate[_Q88]} post={post_cancel[_Q88]}"
            )

            if execute_dialog_closed:
                open_execute_dialog(page)

            # ── (g) Repeat -> Unlock & Apply: writes 'delete' AND unlocks ───
            right_click_row(page, execute_row_testid(gid, _Q88))
            click_context_item(page, CTX_SET_ACTION_DELETE)
            lock_confirm.wait_for(state="visible", timeout=15_000)
            unlock_apply_btn = page.get_by_test_id(LOCK_CONFIRM_BTN_UNLOCK_APPLY)
            unlock_apply_btn.wait_for(state="visible", timeout=5_000)
            unlock_apply_btn.click()
            lock_confirm.wait_for(state="hidden", timeout=10_000)

            final_state = _await(
                base_url,
                db_path,
                lambda r: r[_Q88]["decision"] == "delete" and not r[_Q88]["is_locked"],
            )
            assert final_state[_Q88] == {"decision": "delete", "is_locked": False}, (
                "(g) Unlock & Apply must write user_decision='delete' AND "
                f"force-unlock q88 in the same call, got {final_state[_Q88]}"
            )
            print(f"probe_status: s53 final_after_unlock_apply={final_state[_Q88]}")
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
