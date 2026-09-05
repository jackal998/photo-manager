"""Web scenario s14 — field+regex bulk-decide via Action menu (primary entry point).

Ported from qa/scenarios/s14_action_by_regex.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/near-duplicates (5 neardup_NN_qXX.jpg files in one group).
  - Assert Action → Set Action by Regex is DISABLED before any manifest is loaded.
  - After scan, assert the same item is ENABLED (manifest-gate parity with Qt #244).
  - Open ActionDialog via the menu bar Action → Set Action by Regex item.
  - Select field "File Name", type regex ``q[89]\\d`` directly into the regex input.
  - Assert live-preview counter shows a non-zero count.
  - Set action-action-combo to "delete" and click Apply.
  - Confirm the delete-confirm dialog (execute-all-delete-confirm-yes).
  - Assert via GET /api/manifest that exactly the 3 matched rows (q95, q88, q80)
    have user_decision=="delete" and the 2 non-matched rows (q72, q65) are unchanged.

Web-observable assertions (all via Playwright testids + HTTP):
  1. menu-action-set has data-disabled before scan (gate parity).
  2. menu-action-set loses data-disabled after scan.
  3. action-dialog opens when menu-action-set is clicked.
  4. action-field-combo accepts "File Name".
  5. action-regex-input accepts the typed regex.
  6. action-match-counter shows a non-zero match count after debounce.
  7. action-btn-apply is enabled (non-empty pattern + manifest loaded).
  8. Delete confirm dialog appears on Apply; clicking yes confirms.
  9. GET /api/manifest: 3 matched rows user_decision=="delete", 2 unchanged.

Qt divergences:
  - Qt asserts Apply stays enabled on empty/invalid regex (#397 contract);
    the web port skips this — the web backend surfaces bad patterns as a 400
    from POST /api/action/bulk-decide, not a disabled button.
  - Qt has a Wave-4 Folder-regex preview probe (A2) that reads UIA list items;
    no web-UI equivalent — omitted.
  - Qt's probe_apply_always_enabled and probe_folder_regex_preview_shows_paths
    use UIA affordances (iface_value.SetValue, read_preview_items) that are
    Qt-internal and have no Playwright counterpart — omitted.
  - Qt's status-bar "Decision set" flash is matched via UIA text probe; the
    web port asserts server-side state via GET /api/manifest only.
  - The web port writes the manifest to a temp directory cleaned up in finally;
    no restore step is needed.
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
    ACTION_ACTION_COMBO,
    ACTION_BTN_APPLY,
    ACTION_DIALOG,
    ACTION_FIELD_COMBO,
    ACTION_MATCH_COUNTER,
    ACTION_REGEX_INPUT,
    EXECUTE_ALL_DELETE_CONFIRM,
    EXECUTE_ALL_DELETE_CONFIRM_YES,
    MENU_ACTION,
    MENU_ACTION_SET,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

# Regex applied in the Regex-mode input directly (no simple-mode synthesis).
# q[89]\d matches q95, q88, q80 (3 files); skips q72, q65 (2 files).
_FIELD = "File Name"
_REGEX = r"q[89]\d"
_EXPECTED_DELETE = {
    "neardup_00_q95.jpg",
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
}
_EXPECTED_UNCHANGED = {
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
}


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
    """Assert menu gating, drive field+regex dialog, assert bulk-decide write-through."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s14_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: Action menu is gated before any manifest is loaded ───
            page.get_by_test_id(MENU_ACTION).click()
            set_item = page.get_by_test_id(MENU_ACTION_SET)
            set_item.wait_for(state="visible", timeout=5_000)
            assert set_item.get_attribute("data-disabled") is not None, (
                "Action → Set Action must be disabled with no manifest loaded "
                "(manifest gate — see Qt #244)"
            )
            page.keyboard.press("Escape")

            # ── Step 2: scan the near-duplicates fixture ──────────────────────
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
            for name in _EXPECTED_DELETE | _EXPECTED_UNCHANGED:
                assert name in pre, (
                    f"Fixture file {name!r} missing from manifest rows {sorted(pre)}"
                )

            # ── Step 3: Action menu is now enabled (manifest loaded) ──────────
            page.get_by_test_id(MENU_ACTION).click()
            set_item = page.get_by_test_id(MENU_ACTION_SET)
            set_item.wait_for(state="visible", timeout=5_000)
            assert set_item.get_attribute("data-disabled") is None, (
                "Action → Set Action must be enabled once a manifest is loaded"
            )
            set_item.click()
            page.get_by_test_id(ACTION_DIALOG).wait_for(state="visible", timeout=5_000)

            # ── Step 4: select field "File Name" ─────────────────────────────
            page.get_by_test_id(ACTION_FIELD_COMBO).select_option(label=_FIELD)

            # ── Step 5: type regex directly into the regex input ──────────────
            regex_input = page.get_by_test_id(ACTION_REGEX_INPUT)
            regex_input.wait_for(state="visible", timeout=5_000)
            regex_input.fill(_REGEX)

            # Wait for the debounce (300 ms) + preview round-trip.
            time.sleep(0.5)

            # ── Step 6: live-preview counter shows a non-zero match count ─────
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

            # ── Step 7: set action to "delete" ────────────────────────────────
            action_combo = page.get_by_test_id(ACTION_ACTION_COMBO)
            action_combo.wait_for(state="visible", timeout=5_000)
            action_combo.select_option("delete")

            # ── Step 8: click Apply (button must be enabled) ──────────────────
            apply_btn = page.get_by_test_id(ACTION_BTN_APPLY)
            apply_btn.wait_for(state="visible", timeout=5_000)
            assert not apply_btn.is_disabled(), (
                "action-btn-apply must be enabled when pattern is non-empty "
                "and a manifest is loaded"
            )
            apply_btn.click()

            # ── Step 9: confirm the delete-confirm dialog ─────────────────────
            confirm_dialog = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM)
            confirm_dialog.wait_for(state="visible", timeout=10_000)
            confirm_yes = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES)
            confirm_yes.wait_for(state="visible", timeout=5_000)
            confirm_yes.click()

            # Dialog closes after confirm → Apply.
            page.get_by_test_id(ACTION_DIALOG).wait_for(state="hidden", timeout=10_000)

            # ── Step 10: assert via HTTP that exactly matched rows are deleted ─
            post_manifest = _get_manifest(base_url, db_path)
            post = _extract_decisions(post_manifest)

            # The 3 regex-matched rows must have user_decision == "delete".
            for basename in _EXPECTED_DELETE:
                assert post.get(basename) == "delete", (
                    f"Expected user_decision='delete' for matched row {basename!r}, "
                    f"got {post.get(basename)!r}"
                )

            # The 2 non-matched rows must be unchanged from their pre-apply state.
            for basename in _EXPECTED_UNCHANGED:
                assert post.get(basename, "") == pre.get(basename, ""), (
                    f"Non-matched row {basename!r} was mutated: "
                    f"pre={pre.get(basename)!r}, post={post.get(basename)!r}"
                )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
