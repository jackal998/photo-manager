"""Web scenario s29 — bulk regex "remove from list" writes deferred ignore decision.

Ported from qa/scenarios/s29_remove_from_list_by_regex.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/near-duplicates (5 neardup_NN_qXX.jpg files in one group).
  - Open Action dialog, type regex r"q[89]\\d" into action-regex-input.
  - Set action-action-combo to "remove_from_list" (wire value "__ignore__").
  - Click Apply — NO delete-confirm dialog for ignore actions.
  - Assert via GET /api/manifest that the 3 matched rows have
    user_decision=="ignore" and the 2 non-matched rows are unchanged.

Web slice:
  - Drives action-regex-input directly (no Simple-mode round-trip needed).
  - action-match-counter must show a non-zero count before Apply.
  - Action dialog closes on success with no confirm sheet (ignore != delete).
  - GET /api/manifest (key "items") is the load-bearing assertion.

Qt divergences:
  - The web frontend maps key="remove_from_list" → wireValue="__ignore__" in
    ActionDialog.tsx before POSTing to POST /api/action/bulk-decide; the
    resulting user_decision stored in the manifest is "ignore".
  - Qt reads decisions from SQLite directly; web port uses GET /api/manifest.
  - Qt "Decision set" status-bar flash is not asserted (HTTP assertion suffices).
  - Manifest lives in a temp directory cleaned up in finally — no restore needed.
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
from qa.web._invariants import run_scan
from qa.web.testid_constants import (
    ACTION_MAIN_BUTTON,
    ACTION_DIALOG,
    ACTION_REGEX_INPUT,
    ACTION_MATCH_COUNTER,
    ACTION_ACTION_COMBO,
    ACTION_BTN_APPLY,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

# Same partition as the Qt original: q95/q88/q80 match (3 rows); q72/q65 don't.
_REGEX = r"q[89]\d"
# The action-combo <option value> is the wireValue, not the key:
# ACTION_OPTIONS has {key: "remove_from_list", wireValue: "__ignore__"} and the
# <option> is rendered with value={wireValue} (ActionDialog.tsx). The backend
# maps "__ignore__" → stored user_decision "ignore".
_ACTION_OPTION = "__ignore__"
# After Apply the matched rows must carry this user_decision in the manifest.
_EXPECTED_DECISION = "ignore"
# Total fixture files expected in the near-duplicates sandbox.
_FIXTURE_COUNT = 5


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _extract_decisions(manifest: dict) -> dict[str, str]:
    """Return {basename: user_decision} for all files in the manifest.

    Uses group["items"] (the server-side serialisation key).
    Empty string means no decision set.
    """
    decisions: dict[str, str] = {}
    for group in manifest.get("groups", []):
        for file_row in group.get("items", []):
            basename = Path(file_row["file_path"]).name
            decisions[basename] = file_row.get("user_decision", "") or ""
    return decisions


def run(*, base_url: str) -> None:
    """Scan near-dups, drive action dialog regex-mode ignore, assert bulk-decide."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s29_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the near-duplicates fixture ─────────────────
            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # Snapshot pre-apply decisions — all should be empty.
            pre_manifest = _get_manifest(base_url, db_path)
            pre = _extract_decisions(pre_manifest)
            assert len(pre) == _FIXTURE_COUNT, (
                f"Expected {_FIXTURE_COUNT} neardup files in manifest, "
                f"got {len(pre)}: {sorted(pre)}"
            )

            # Partition: which names will the regex match?
            rx = re.compile(_REGEX, re.IGNORECASE)
            expected_match = sorted(name for name in pre if rx.search(name))
            expected_unchanged = sorted(name for name in pre if not rx.search(name))
            assert len(expected_match) > 0, (
                f"Regex {_REGEX!r} matched nothing in fixture rows {sorted(pre)}"
            )

            # ── Step 2: open the action dialog via the toolbar button ──────
            action_btn = page.get_by_test_id(ACTION_MAIN_BUTTON)
            action_btn.wait_for(state="visible", timeout=10_000)
            assert not action_btn.is_disabled(), (
                "main-action-button is disabled; manifest should be loaded."
            )
            action_btn.click()
            page.get_by_test_id(ACTION_DIALOG).wait_for(
                state="visible", timeout=10_000
            )

            # ── Step 3: type regex directly into action-regex-input ────────
            regex_input = page.get_by_test_id(ACTION_REGEX_INPUT)
            regex_input.wait_for(state="visible", timeout=5_000)
            regex_input.fill(_REGEX)

            # Wait for the debounce (300 ms) + preview round-trip.
            time.sleep(0.5)

            # ── Step 4: assert live preview counter shows a non-zero count ─
            counter = page.get_by_test_id(ACTION_MATCH_COUNTER)
            counter.wait_for(state="visible", timeout=10_000)
            page.wait_for_function(
                """() => {
                    const el = document.querySelector(
                        '[data-testid="action-match-counter"]'
                    );
                    if (!el) return false;
                    const txt = el.innerText || '';
                    return /[1-9]/.test(txt);
                }""",
                timeout=10_000,
            )
            counter_text = counter.inner_text()
            assert re.search(r"[1-9]", counter_text), (
                f"action-match-counter should show a non-zero count, "
                f"got {counter_text!r}"
            )

            # ── Step 5: set action to "remove_from_list" ──────────────────
            action_combo = page.get_by_test_id(ACTION_ACTION_COMBO)
            action_combo.wait_for(state="visible", timeout=5_000)
            action_combo.select_option(_ACTION_OPTION)

            # ── Step 6: click Apply — NO delete-confirm for ignore ─────────
            apply_btn = page.get_by_test_id(ACTION_BTN_APPLY)
            apply_btn.wait_for(state="visible", timeout=5_000)
            assert not apply_btn.is_disabled(), (
                "action-btn-apply is disabled; it should be enabled when "
                "pattern is non-empty and manifest is loaded."
            )
            apply_btn.click()

            # Dialog closes on success (no confirm sheet for ignore).
            page.get_by_test_id(ACTION_DIALOG).wait_for(
                state="hidden", timeout=10_000
            )

            # ── Step 7: assert via HTTP that decisions are correct ─────────
            post_manifest = _get_manifest(base_url, db_path)
            post = _extract_decisions(post_manifest)

            # Matched rows: user_decision must be "ignore" (deferred marker).
            for name in expected_match:
                assert post.get(name) == _EXPECTED_DECISION, (
                    f"Expected user_decision={_EXPECTED_DECISION!r} for "
                    f"{name!r} (matched {_REGEX!r}), got {post.get(name)!r}"
                )

            # Non-matched rows: must be unchanged from pre-apply state.
            for name in expected_unchanged:
                assert post.get(name, "") == pre.get(name, ""), (
                    f"Non-matched row {name!r} was mutated: "
                    f"pre={pre.get(name)!r}, post={post.get(name)!r}"
                )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
