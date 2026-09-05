"""Web scenario s30 — Set Action by Field via the EXECUTE dialog row menu (#316).

Ported from qa/scenarios/s30_execute_dialog_regex_right_click.py (Qt UIA).

Qt intent:
  Right-click a file row inside the Execute Action dialog → 'Set Action by
  Field…' opens the ActionDialog → field=File Name, regex=q[89]\\d, action=delete
  → the live preview reports 3 of 5 → Apply → the 3 matching rows persist
  user_decision='delete', the 2 non-matching rows stay unchanged.

Web slice — cluster B (ExecuteContextMenu 'Set Action by Field…'):
  The execute-dialog menu's 'Set Action by Field…' (CTX_SET_ACTION_BY_FIELD)
  opens the SAME manifest-wide ActionDialog the toolbar opens
  (store.openActionDialog), exactly as Qt routes both entry points to one
  ActionDialog class. The execute tree shows only decided rows, so the scenario
  first seeds ONE decision via the result-tree menu to expose a row to
  right-click; the regex apply is manifest-wide (3 of 5), not row-scoped.

  The ActionDialog drive mirrors s43 (the numeric sibling): pick the field,
  enter the pattern, read the live ACTION_MATCH_COUNTER, set action=delete,
  Apply, confirm the all-delete sheet, then assert the manifest split.

Qt divergences:
  D1. Manifest read via GET /api/manifest JSON vs Qt SQLite.
  D2. Status-bar 'Decision set' regression guard (#316) is a Qt-only signal; the
      web asserts the persisted manifest split instead (the load-bearing check).
  D3. No post-apply counter read — the web ActionDialog auto-closes on Apply
      (s43/s56 precedent); the counter is asserted BEFORE Apply.
  D4. The seed decision (q95=delete, to expose a row) is a matching row, so the
      final split is exactly the 3 regex matches — no extra delete leaks in.

Desktop source: qa/scenarios/s30_execute_dialog_regex_right_click.py
Fixture:        qa/sandbox/near-duplicates/ (5 JPEGs)
"""
from __future__ import annotations

import json
import os
import re
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
)
from qa.web.testid_constants import (
    ACTION_ACTION_COMBO,
    ACTION_BTN_APPLY,
    ACTION_DIALOG,
    ACTION_FIELD_COMBO,
    ACTION_MATCH_COUNTER,
    ACTION_REGEX_INPUT,
    CTX_SET_ACTION_BY_FIELD,
    CTX_SET_ACTION_DELETE,
    EXECUTE_ALL_DELETE_CONFIRM,
    EXECUTE_ALL_DELETE_CONFIRM_YES,
    execute_row_testid,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

# Regex on the File Name field: q[89]\d matches q95 / q88 / q80 (3 of 5), not
# q72 / q65. Independent of platform pHash tiering — it matches by basename.
_REGEX = r"q[89]\d"
_FILE_NAME_LABEL = "File Name"
_SEED = "neardup_00_q95.jpg"  # decided to expose a row; also a regex match
_EXPECTED_MATCH = {"neardup_00_q95.jpg", "neardup_01_q88.jpg", "neardup_02_q80.jpg"}
_EXPECTED_UNCHANGED = {"neardup_03_q72.jpg", "neardup_04_q65.jpg"}


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _decisions(manifest: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for group in manifest.get("groups", []):
        for it in group.get("items", []):
            out[Path(it["file_path"]).name] = it.get("user_decision", "") or ""
    return out


def _first_group_id(base_url: str, db_path: str) -> str:
    data = _get_manifest(base_url, db_path)
    assert data["total_groups"] >= 1, "expected at least one group in the manifest"
    return str(data["groups"][0]["group_number"])


def run(*, base_url: str) -> None:
    """Open ActionDialog from the execute menu, regex-delete 3 of 5, assert split."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s30_")
    db_path = os.path.join(tmpdir, "manifest.db")
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
            pre = _decisions(_get_manifest(base_url, db_path))
            assert len(pre) == 5, f"expected 5 fixture rows, got {sorted(pre)}"

            gid = _first_group_id(base_url, db_path)

            # ── Seed one decision so a row is visible in the execute tree ──────
            right_click_row(page, row_file_testid(gid, _SEED))
            click_context_item(page, CTX_SET_ACTION_DELETE)

            # ── Open execute dialog; right-click the seeded row → Set by Field ─
            open_execute_dialog(page)
            right_click_row(page, execute_row_testid(gid, _SEED))
            click_context_item(page, CTX_SET_ACTION_BY_FIELD)

            # ── ActionDialog opens over the execute dialog (nested modal) ──────
            page.get_by_test_id(ACTION_DIALOG).wait_for(state="visible", timeout=10_000)

            field_combo = page.get_by_test_id(ACTION_FIELD_COMBO)
            field_combo.wait_for(state="visible", timeout=5_000)
            field_combo.select_option(_FILE_NAME_LABEL)

            regex_input = page.get_by_test_id(ACTION_REGEX_INPUT)
            regex_input.wait_for(state="visible", timeout=5_000)
            regex_input.fill(_REGEX)
            time.sleep(0.6)  # past the 300ms debounce + preview round-trip

            # ── Live counter shows 3 matches (pre-Apply, s43 precedent) ────────
            counter = page.get_by_test_id(ACTION_MATCH_COUNTER)
            counter.wait_for(state="visible", timeout=10_000)
            page.wait_for_function(
                """() => {
                    const el = document.querySelector(
                        '[data-testid="action-match-counter"]'
                    );
                    return el && /\\b3\\b/.test(el.innerText || '');
                }""",
                timeout=10_000,
            )
            counter_text = counter.inner_text()
            assert re.search(r"\b3\b", counter_text), (
                f"action-match-counter should report 3 matches for File Name ~ "
                f"{_REGEX!r}, got {counter_text!r}"
            )

            # ── action=delete → Apply → confirm all-delete ─────────────────────
            page.get_by_test_id(ACTION_ACTION_COMBO).select_option("delete")
            apply_btn = page.get_by_test_id(ACTION_BTN_APPLY)
            apply_btn.wait_for(state="visible", timeout=5_000)
            apply_btn.click()

            confirm = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM)
            confirm.wait_for(state="visible", timeout=10_000)
            page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES).click()

            page.get_by_test_id(ACTION_DIALOG).wait_for(state="hidden", timeout=10_000)

            # ── Assert the manifest split: 3 delete, 2 unchanged ───────────────
            post = _decisions(_get_manifest(base_url, db_path))
            print(f"probe_status: s30 post={ {k: post[k] for k in sorted(post)} }")
            for name in sorted(_EXPECTED_MATCH):
                assert post[name] == "delete", (
                    f"{name} (File Name ~ {_REGEX!r}) expected 'delete', got {post[name]!r}"
                )
            for name in sorted(_EXPECTED_UNCHANGED):
                assert post[name] == "", (
                    f"{name} (no regex match) expected unchanged (''), got {post[name]!r}"
                )
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
