"""Web scenario s35 — Lock / Unlock a row via the right-click context menu.

Ported from qa/scenarios/s35_lock_via_context_menu.py (Qt UIA).

Qt intent (5-file near-duplicates fixture):
  (a) right-click row 0 (q95) → Lock; only that row is_locked=1.
  (b) same row → Unlock; is_locked back to 0.
  (c) Ctrl+click rows 1+2 (q88, q80) → Lock; both is_locked=1.
  (d) same selection → Unlock; both back to 0.
  Untouched rows never change. Exercises the context-menu Lock/Unlock wiring,
  the in-memory mutation + PATCH /api/lock write path, and the
  "is_locked orthogonal to user_decision" invariant.

Web slice (all assertions via GET /api/manifest is_locked):
  - The web context menu renders EITHER Lock (CTX_LOCK) or Unlock (CTX_UNLOCK)
    per the right-clicked row's current is_locked (ContextMenu.tsx:140-158); the
    handler calls setLock → PATCH /api/lock (review.py:122-147). is_locked is
    serialised in the manifest payload (review_view.py:137).

Qt divergences:
  - Multi-row Ctrl+click is not replicated — the web context menu targets the
    right-clicked row only. Step (c)/(d) drive two sequential single-row ops,
    covering the same PATCH /api/lock write path without web multi-select (same
    precedent as s15 step c).
  - Lock writes are asserted via GET /api/manifest (authoritative serialisation)
    rather than a direct SQLite read; a short poll absorbs the async PATCH so the
    read never races the optimistic-then-persisted update.
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
from qa.web._invariants import run_scan, right_click_row, click_context_item
from qa.web.testid_constants import CTX_LOCK, CTX_UNLOCK, row_file_testid

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

_Q95 = "neardup_00_q95.jpg"
_Q88 = "neardup_01_q88.jpg"
_Q80 = "neardup_02_q80.jpg"
_Q72 = "neardup_03_q72.jpg"
_Q65 = "neardup_04_q65.jpg"


def _lock_state(base_url: str, db_path: str) -> dict[str, bool]:
    """Return {basename: is_locked} for every file in the manifest via HTTP."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        data = json.loads(resp.read())
    state: dict[str, bool] = {}
    for group in data.get("groups", []):
        for file_row in group.get("items", []):
            state[Path(file_row["file_path"]).name] = bool(file_row.get("is_locked"))
    return state


def _await_lock_state(
    base_url: str, db_path: str, expected: dict[str, bool], *, timeout: float = 5.0
) -> dict[str, bool]:
    """Poll GET /api/manifest until every ``expected`` entry matches (async PATCH).

    Returns the final full state (matched, or the last read on timeout so the
    caller's assert produces a useful diff).
    """
    deadline = time.monotonic() + timeout
    state: dict[str, bool] = {}
    while time.monotonic() < deadline:
        state = _lock_state(base_url, db_path)
        if all(state.get(name) == val for name, val in expected.items()):
            return state
        time.sleep(0.1)
    return state


def _first_group_id(base_url: str, db_path: str) -> str:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        data = json.loads(resp.read())
    assert data["total_groups"] >= 1, "expected at least one group in the manifest"
    return str(data["groups"][0]["group_number"])


def _lock(page, group_id: str, basename: str) -> None:
    right_click_row(page, row_file_testid(group_id, basename))
    click_context_item(page, CTX_LOCK)


def _unlock(page, group_id: str, basename: str) -> None:
    right_click_row(page, row_file_testid(group_id, basename))
    click_context_item(page, CTX_UNLOCK)


def run(*, base_url: str) -> None:
    """Lock/unlock rows via the context menu; assert is_locked via the manifest."""
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

            pre = _lock_state(base_url, db_path)
            assert len(pre) == 5, f"expected 5 fixture rows, got {sorted(pre)}"
            assert not any(pre.values()), f"fresh manifest must be all-unlocked: {pre}"

            group_id = _first_group_id(base_url, db_path)

            # --- (a) single-row Lock ---
            _lock(page, group_id, _Q95)
            state = _await_lock_state(base_url, db_path, {_Q95: True})
            assert state[_Q95] is True, f"(a) {_Q95} should be locked: {state}"
            assert all(
                not v for n, v in state.items() if n != _Q95
            ), f"(a) lock leaked beyond {_Q95}: {state}"

            # --- (b) single-row Unlock (menu now offers Unlock for the locked row) ---
            _unlock(page, group_id, _Q95)
            state = _await_lock_state(base_url, db_path, {_Q95: False})
            assert not any(state.values()), f"(b) all rows should be unlocked: {state}"

            # --- (c) two sequential single-row Locks (multi-row intent) ---
            _lock(page, group_id, _Q88)
            _lock(page, group_id, _Q80)
            state = _await_lock_state(base_url, db_path, {_Q88: True, _Q80: True})
            assert state[_Q88] is True and state[_Q80] is True, (
                f"(c) {_Q88} and {_Q80} should both be locked: {state}"
            )
            assert state[_Q95] is False, f"(c) {_Q95} must stay unlocked: {state}"
            assert state[_Q72] is False and state[_Q65] is False, (
                f"(c) untouched rows must stay unlocked: {state}"
            )

            # --- (d) unlock both ---
            _unlock(page, group_id, _Q88)
            _unlock(page, group_id, _Q80)
            state = _await_lock_state(base_url, db_path, {_Q88: False, _Q80: False})
            assert not any(state.values()), f"(d) nothing should remain locked: {state}"
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
