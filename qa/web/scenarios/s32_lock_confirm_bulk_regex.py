"""Web scenario s32 — bulk-regex lock-confirm → "Apply to Unlocked Only" (#674).

Ported from qa/scenarios/s32_lock_confirm_bulk_regex.py (Qt UIA).

Qt intent (the most interesting verdict, exercised end-to-end):
  - Scan qa/sandbox/near-duplicates (5 neardup_NN_qXX.jpg in one group).
  - Bulk-lock the q95 row via File-Name regex ``q95`` + action 'lock'
    (the lock sentinel is FREE — no confirm dialog).
  - Bulk-delete via File-Name regex ``q[89]\\d`` (matches q95 LOCKED + q88,
    q80 UNLOCKED) + action 'delete'. The inline lock-confirm dialog fires.
  - Click "Apply to Unlocked Only" → q95 stays locked + undecided; q88 + q80
    get decision='delete'; the two non-matches (q72, q65) untouched.

Web slice (all assertions via Playwright testids + sqlite + GET /api/manifest):
  1. run_scan(near-duplicates) into a per-run tempdir db. 5 rows, all unlocked.
  2. ActionDialog (menu Action → Set Action): field "File Name", regex ``q95``,
     action '__lock__', Apply → q95 is_locked (no confirm; lock is free).
  3. ActionDialog again: regex ``q[89]\\d``, action 'delete', Apply →
     DeleteConfirm sheet → Yes → POST bulk-decide returns 409 locked_paths →
     the ActionDialog inline lock dialog (ACTION_LOCK_CONFIRM_DIALOG) opens.
  4. "Apply to Unlocked Only" (ACTION_LOCK_CONFIRM_BTN_UNLOCKED_ONLY) is ENABLED
     (matched_total=3, 1 locked → 2 unlocked). Click it → re-POST skip_locked.
  5. ASSERT (both layers, per the #674 adversarial-review):
       - sqlite (authoritative is_locked): q95 (decision='', is_locked=1),
         q88/q80 (decision='delete'), q72/q65 (decision='').
       - GET /api/manifest (serialization layer): same decisions + is_locked.

Qt divergences:
  - Qt reads a FIXED manifest (qa/run-manifest.sqlite); the web app scans into a
    per-run tempdir, so this scenario sqlite-connects to the tempdir db_path it
    scanned into — NOT the desktop MANIFEST_PATH constant.
  - Qt's lock-confirm fires before any delete-confirm; the web ActionDialog shows
    the DeleteConfirm sheet first (action=delete), then the 409 → lock dialog.
  - The fixture is scanned read-only (no file is deleted — s32 only records
    decisions/locks in the db), so no tmpdir copy of the photos is needed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan
from qa.web.testid_constants import (
    ACTION_ACTION_COMBO,
    ACTION_BTN_APPLY,
    ACTION_DIALOG,
    ACTION_FIELD_COMBO,
    ACTION_REGEX_INPUT,
    ACTION_LOCK_CONFIRM_DIALOG,
    ACTION_LOCK_CONFIRM_BTN_UNLOCKED_ONLY,
    EXECUTE_ALL_DELETE_CONFIRM,
    EXECUTE_ALL_DELETE_CONFIRM_YES,
    MENU_ACTION,
    MENU_ACTION_SET,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

_FIELD = "File Name"
_LOCK_REGEX = r"q95"            # matches neardup_00_q95.jpg only
_DESTRUCTIVE_REGEX = r"q[89]\d"  # matches q95 (locked) + q88 + q80

_LOCK_TARGET = "neardup_00_q95.jpg"
_EXPECTED_DELETE = {"neardup_01_q88.jpg", "neardup_02_q80.jpg"}
_EXPECTED_UNTOUCHED = {"neardup_03_q72.jpg", "neardup_04_q65.jpg"}


# ---------------------------------------------------------------------------
# State readers — sqlite (authoritative) + HTTP (serialization layer)
# ---------------------------------------------------------------------------


def _read_db_state(db_path: str) -> dict[str, tuple[str, bool]]:
    """Return {basename: (user_decision, is_locked)} read straight from the
    tempdir manifest the scan wrote (NOT the desktop MANIFEST_PATH)."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision, is_locked FROM migration_manifest"
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: ((d or ""), bool(loc)) for p, d, loc in rows}


def _get_manifest(base_url: str, db_path: str) -> dict:
    """GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _api_state(manifest: dict) -> dict[str, tuple[str, bool]]:
    """Return {basename: (user_decision, is_locked)} from the manifest JSON."""
    state: dict[str, tuple[str, bool]] = {}
    for group in manifest.get("groups", []):
        for row in group.get("items", []):
            name = Path(row["file_path"]).name
            state[name] = (row.get("user_decision", ""), bool(row.get("is_locked")))
    return state


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


def _open_action_dialog(page) -> None:
    """Open the ActionDialog via the menu bar Action → Set Action item."""
    page.get_by_test_id(MENU_ACTION).click()
    set_item = page.get_by_test_id(MENU_ACTION_SET)
    set_item.wait_for(state="visible", timeout=5_000)
    set_item.click()
    page.get_by_test_id(ACTION_DIALOG).wait_for(state="visible", timeout=5_000)


def _apply_regex_action(page, *, regex: str, action_value: str) -> None:
    """Drive the ActionDialog: select field, type regex, pick action, Apply.

    Leaves the dialog state at the point right AFTER the Apply click — the
    caller handles any follow-up dialog (delete-confirm / lock-confirm).
    """
    page.get_by_test_id(ACTION_FIELD_COMBO).select_option(label=_FIELD)
    regex_input = page.get_by_test_id(ACTION_REGEX_INPUT)
    regex_input.wait_for(state="visible", timeout=5_000)
    regex_input.fill(regex)
    # Pattern writes through synchronously (only the preview is debounced); a
    # short settle keeps the combo/Apply interaction stable on slow runners.
    time.sleep(0.4)
    page.get_by_test_id(ACTION_ACTION_COMBO).select_option(action_value)
    apply_btn = page.get_by_test_id(ACTION_BTN_APPLY)
    apply_btn.wait_for(state="visible", timeout=5_000)
    assert not apply_btn.is_disabled(), "action-btn-apply must be enabled"
    apply_btn.click()


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Lock q95, bulk-delete q[89]\\d, choose Apply to Unlocked Only, verify."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s32_")
    db_path = os.path.join(tmpdir, "s32_manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the near-duplicates fixture ──────────────────
            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )
            pre = _read_db_state(db_path)
            assert len(pre) == 5, f"expected 5 fixture rows, got {sorted(pre)}"
            assert not any(loc for _d, loc in pre.values()), (
                f"fresh manifest must have no locked rows: {pre}"
            )

            # ── Step 2: lock q95 (lock sentinel is FREE — no confirm) ─────
            _open_action_dialog(page)
            _apply_regex_action(page, regex=_LOCK_REGEX, action_value="__lock__")
            # Lock applies with no confirm; the dialog closes on success.
            page.get_by_test_id(ACTION_DIALOG).wait_for(state="hidden", timeout=10_000)

            after_lock = _read_db_state(db_path)
            locked_rows = [n for n, (_d, loc) in after_lock.items() if loc]
            assert locked_rows == [_LOCK_TARGET], (
                f"expected only {_LOCK_TARGET} locked, got {locked_rows}"
            )

            # ── Step 3: bulk-delete q[89]\d → DeleteConfirm → 409 lock dlg ─
            _open_action_dialog(page)
            _apply_regex_action(
                page, regex=_DESTRUCTIVE_REGEX, action_value="delete"
            )

            # action='delete' shows the all-delete confirm sheet first.
            confirm = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM)
            confirm.wait_for(state="visible", timeout=10_000)
            page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES).click()

            # POST bulk-decide returns 409 locked_paths → inline lock dialog.
            lock_dialog = page.get_by_test_id(ACTION_LOCK_CONFIRM_DIALOG)
            lock_dialog.wait_for(state="visible", timeout=15_000)

            # ── Step 4: "Apply to Unlocked Only" is enabled (2 unlocked) ──
            unlocked_only = page.get_by_test_id(ACTION_LOCK_CONFIRM_BTN_UNLOCKED_ONLY)
            unlocked_only.wait_for(state="visible", timeout=5_000)
            assert not unlocked_only.is_disabled(), (
                "Apply to Unlocked Only must be enabled when matched=3 / locked=1"
            )
            unlocked_only.click()

            # The dialog closes after a successful skip_locked re-POST.
            page.get_by_test_id(ACTION_DIALOG).wait_for(state="hidden", timeout=10_000)

            # ── Step 5: assert both layers ────────────────────────────────
            db_state = _read_db_state(db_path)
            api_state = _api_state(_get_manifest(base_url, db_path))

            for label, state in (("sqlite", db_state), ("api", api_state)):
                # q95 — locked, decision untouched (skipped at the user's request).
                assert state[_LOCK_TARGET] == ("", True), (
                    f"[{label}] {_LOCK_TARGET} must stay decision='' is_locked=True; "
                    f"got {state[_LOCK_TARGET]}"
                )
                # q88 + q80 — unlocked matches, decision now 'delete'.
                for name in _EXPECTED_DELETE:
                    assert state[name][0] == "delete", (
                        f"[{label}] {name} expected decision='delete', got {state[name]}"
                    )
                # q72 + q65 — non-matches, untouched.
                for name in _EXPECTED_UNTOUCHED:
                    assert state[name][0] == "", (
                        f"[{label}] non-match {name} was modified: {state[name]}"
                    )
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
