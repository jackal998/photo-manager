"""Web scenario s31 — Simple mode write-through + bulk-decide action dialog.

Ported from qa/scenarios/s31_simple_mode_regex.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/near-duplicates (5 neardup_NN_qXX.jpg files in one group).
  - Open Action dialog via the toolbar "Set Action" button.
  - Select field "File Name" in action-field-combo.
  - In the Simple section: set action-simple-op="contains" + action-simple-text="q9".
  - Assert write-through: action-regex-input must reflect the synthesised pattern
    ("q9" — re.escape("q9") == "q9", no special chars).
  - Assert live preview: action-match-counter shows a non-zero "N of M" count.
  - Set action-action-combo to "delete" and click action-btn-apply.
  - Confirm the delete-confirm dialog (execute-all-delete-confirm-yes).
  - Assert via GET /api/manifest that the matched row (neardup_00_q95.jpg) now
    has user_decision=="delete" and the other four rows are unchanged.

Web-observable assertions (all via Playwright testids + HTTP):
  1. action-dialog opens when main-action-button is clicked.
  2. action-field-combo value = "File Name" (default).
  3. action-simple-op and action-simple-text are visible (string-field mode).
  4. Typing into action-simple-text updates action-regex-input (write-through).
  5. action-match-counter shows a non-zero match count.
  6. action-btn-apply is enabled (pattern non-empty + manifest loaded).
  7. Delete confirm dialog (execute-all-delete-confirm) appears on Apply.
  8. After confirmation, GET /api/manifest reflects user_decision=="delete" for
     the matched row and unchanged decisions for all other rows.

Qt divergences:
  - Qt drives mode-toggle radio buttons (Simple ↔ Regex) that no longer exist in
    the web UI — the web dialog shows both Simple and Regex rows side-by-side
    without a mode toggle, so the toggle-and-back-round-trip probes are omitted.
  - Qt reads decisions from SQLite directly; the web port uses GET /api/manifest.
  - Qt's status-bar "Decision set" flash after Apply is matched with a UIA text
    probe; the web port asserts via the HTTP API only (no status-bar text check
    after Apply — the action dialog closes on success and the status bar update
    timing is not critical for the server-side assertion).
  - Qt verifies probe_apply_always_enabled (Apply stays enabled on invalid input);
    the web port skips this because the web backend surfaces invalid patterns as a
    400 from POST /api/action/bulk-decide — the Apply button's enabled-state
    contract is identical but the error surface is the server response, not a
    QMessageBox.
  - Qt has a Recent-pick probe (#396) and Wave-11 keyboard/mnemonic probes; these
    depend on Qt UIA affordances (type_keys, find_popup) with no web equivalent —
    omitted.
  - The web scenario does NOT restore decisions after the test run.  The manifest
    is in a temp file that is cleaned up in the finally block, so no restore is
    needed.
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
from qa.web._invariants import run_scan
from qa.web.testid_constants import (
    ACTION_MAIN_BUTTON,
    ACTION_DIALOG,
    ACTION_FIELD_COMBO,
    ACTION_SIMPLE_ROW,
    ACTION_SIMPLE_OP,
    ACTION_SIMPLE_TEXT,
    ACTION_REGEX_INPUT,
    ACTION_MATCH_COUNTER,
    ACTION_ACTION_COMBO,
    ACTION_BTN_APPLY,
    EXECUTE_ALL_DELETE_CONFIRM,
    EXECUTE_ALL_DELETE_CONFIRM_YES,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

# Simple-mode inputs: "contains" + "q9" synthesises the regex "q9"
# (re.escape("q9") == "q9" — no special chars).
# Only neardup_00_q95.jpg contains "q9" in its basename.
_SIMPLE_OP = "contains"
_SIMPLE_TEXT = "q9"
_EXPECTED_REGEX = "q9"
_EXPECTED_TARGET = "neardup_00_q95.jpg"


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _extract_decisions(manifest: dict) -> dict[str, str]:
    """Return {basename: user_decision} for all files in the manifest.

    Uses group["items"] (the server-side serialisation key) to iterate
    file rows.  Empty string means no decision set.
    """
    decisions: dict[str, str] = {}
    for group in manifest.get("groups", []):
        for file_row in group.get("items", []):
            basename = Path(file_row["file_path"]).name
            decisions[basename] = file_row.get("user_decision", "")
    return decisions


def run(*, base_url: str) -> None:
    """Scan near-dups, drive action dialog simple-mode, assert bulk-decide."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
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
            assert len(pre) == 5, (
                f"Expected 5 neardup files in manifest, got {len(pre)}: "
                f"{sorted(pre)}"
            )
            assert _EXPECTED_TARGET in pre, (
                f"Expected target {_EXPECTED_TARGET!r} not in fixture rows "
                f"{sorted(pre)}"
            )

            # ── Step 2: open the action dialog via the toolbar button ──────
            action_btn = page.get_by_test_id(ACTION_MAIN_BUTTON)
            action_btn.wait_for(state="visible", timeout=10_000)
            assert not action_btn.is_disabled(), (
                "main-action-button is disabled; manifest should be loaded "
                "and the button should be enabled."
            )
            action_btn.click()
            page.get_by_test_id(ACTION_DIALOG).wait_for(
                state="visible", timeout=10_000
            )

            # ── Step 3: verify field combo defaults to "File Name" ────────
            field_combo = page.get_by_test_id(ACTION_FIELD_COMBO)
            field_combo.wait_for(state="visible", timeout=5_000)
            field_value = field_combo.input_value()
            # The combo may use .value or the select element's value
            # attribute; fall back to evaluate if input_value() returns empty.
            if not field_value:
                field_value = page.evaluate(
                    """() => {
                        const el = document.querySelector(
                            '[data-testid="action-field-combo"]'
                        );
                        return el ? el.value : '';
                    }"""
                )
            assert field_value == "File Name", (
                f"action-field-combo expected 'File Name', got {field_value!r}"
            )

            # ── Step 4: simple section should be visible (string field) ───
            simple_row = page.get_by_test_id(ACTION_SIMPLE_ROW)
            simple_row.wait_for(state="visible", timeout=5_000)

            # ── Step 5: set simple op + text and assert write-through ─────
            simple_op = page.get_by_test_id(ACTION_SIMPLE_OP)
            simple_op.select_option(_SIMPLE_OP)

            simple_text = page.get_by_test_id(ACTION_SIMPLE_TEXT)
            simple_text.fill(_SIMPLE_TEXT)

            # Wait for the debounce (300 ms) + the network preview round-trip.
            time.sleep(0.5)

            regex_input = page.get_by_test_id(ACTION_REGEX_INPUT)
            regex_input.wait_for(state="visible", timeout=5_000)
            regex_value = regex_input.input_value()
            assert regex_value == _EXPECTED_REGEX, (
                f"Write-through invariant: action-regex-input should show "
                f"{_EXPECTED_REGEX!r} after Simple op={_SIMPLE_OP!r} + "
                f"text={_SIMPLE_TEXT!r}, got {regex_value!r}"
            )

            # ── Step 6: assert live preview counter shows a non-zero count ─
            counter = page.get_by_test_id(ACTION_MATCH_COUNTER)
            counter.wait_for(state="visible", timeout=10_000)

            # Wait for counter to reflect the preview round-trip.
            page.wait_for_function(
                """() => {
                    const el = document.querySelector(
                        '[data-testid="action-match-counter"]'
                    );
                    if (!el) return false;
                    const txt = el.innerText || '';
                    // Must contain a digit > 0 (e.g. "1 of 5 match")
                    return /[1-9]/.test(txt);
                }""",
                timeout=10_000,
            )
            counter_text = counter.inner_text()
            assert re.search(r"[1-9]", counter_text), (
                f"action-match-counter should show a non-zero count, "
                f"got {counter_text!r}"
            )

            # ── Step 7: set action to "delete" ────────────────────────────
            action_combo = page.get_by_test_id(ACTION_ACTION_COMBO)
            action_combo.wait_for(state="visible", timeout=5_000)
            action_combo.select_option("delete")

            # ── Step 8: click Apply ───────────────────────────────────────
            apply_btn = page.get_by_test_id(ACTION_BTN_APPLY)
            apply_btn.wait_for(state="visible", timeout=5_000)
            assert not apply_btn.is_disabled(), (
                "action-btn-apply is disabled; it should be enabled when "
                "pattern is non-empty and manifest is loaded."
            )
            apply_btn.click()

            # ── Step 9: confirm the delete-confirm dialog ──────────────────
            # When action is "delete" the ActionDialog surfaces
            # execute-all-delete-confirm before calling the API.
            confirm_dialog = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM)
            confirm_dialog.wait_for(state="visible", timeout=10_000)

            confirm_yes = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES)
            confirm_yes.wait_for(state="visible", timeout=5_000)
            confirm_yes.click()

            # Wait for the dialog to close (Apply + confirm → dialog closes).
            page.get_by_test_id(ACTION_DIALOG).wait_for(
                state="hidden", timeout=10_000
            )

            # ── Step 10: assert via HTTP that decisions are correct ────────
            post_manifest = _get_manifest(base_url, db_path)
            post = _extract_decisions(post_manifest)

            # The matched row must have user_decision == "delete".
            assert post.get(_EXPECTED_TARGET) == "delete", (
                f"Expected user_decision='delete' for {_EXPECTED_TARGET!r}, "
                f"got {post.get(_EXPECTED_TARGET)!r}"
            )

            # All other rows must be unchanged from their pre-apply state.
            for basename, pre_decision in pre.items():
                if basename == _EXPECTED_TARGET:
                    continue
                post_decision = post.get(basename, "")
                assert post_decision == pre_decision, (
                    f"Non-target row {basename!r} was mutated: "
                    f"pre={pre_decision!r}, post={post_decision!r}"
                )

    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
