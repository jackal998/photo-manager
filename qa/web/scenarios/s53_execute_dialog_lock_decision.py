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

Qt divergences:
  D1. Manifest read via GET /api/manifest JSON vs Qt SQLite.
  D2. The web execute tree is decided-rows-only (#676 tracks full-group display);
      desktop shows the whole group. The web port seeds decisions so the target
      rows are present, asserting the same lock/decision write paths. Decisions
      are seeded through the RESULT-tree context menu (UI), not a back-door API.
  D3. No multi-row Ctrl+click — the web menu targets the right-clicked row only
      (same precedent as web s35).

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
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
