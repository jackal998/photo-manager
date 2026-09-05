"""Web scenario s56 — ActionDialog Apply with field=Score writes decisions (#392).

Ported from qa/scenarios/s56_action_dialog_apply_by_score.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/near-duplicates (5 JPEG re-saves at qualities 95/88/80/72/65
    — scores cluster around 0.48–0.53 so threshold > 0.5 cleanly splits into
    ≥1 matched rows and the rest unchanged).
  - Open ActionDialog; select field='Score' → numeric condition panel appears.
  - Set comparator '>' and value '0.5'.
  - Set action='delete' → Apply → confirm delete dialog.
  - Assert via GET /api/manifest that rows with score > 0.5 have
    user_decision=='delete' and rows at/below 0.5 are unchanged.
  - The load-bearing assertion is that AT LEAST ONE row flipped: before the
    #392 fix the __cmp__: dispatch was missing, so Apply silently wrote 0 rows.

Web slice:
  1. run_scan seeds a fresh temp manifest.
  2. GET /api/manifest snapshots pre-apply decisions and real scores.
  3. ACTION_MAIN_BUTTON opens ActionDialog (toolbar entry point).
  4. ACTION_FIELD_COMBO → "Score" reveals ACTION_NUMERIC_ROW.
  5. ACTION_NUMERIC_CMP → ">" ; ACTION_NUMERIC_VALUE → "0.5".
  6. ACTION_MATCH_COUNTER waits for a non-zero hit count.
  7. ACTION_ACTION_COMBO → "delete" ; ACTION_BTN_APPLY fires.
  8. EXECUTE_ALL_DELETE_CONFIRM / EXECUTE_ALL_DELETE_CONFIRM_YES confirms.
  9. ACTION_DIALOG closes; GET /api/manifest verifies the split.

Qt divergences:
  - Qt reads decisions directly from SQLite; the web port uses GET /api/manifest.
  - Qt drives the dialog via UIA (AccessibleName/AutomationId helpers); the web
    port drives by testid (same contract, different harness).
  - Qt has a _reset_fixture_decisions() cleanup step; the web port uses a temp
    manifest that is shutil.rmtree'd in the finally block — no restore needed.
  - Qt's preview-list and counter pre-Apply UIA probe is omitted; the web port
    asserts the counter shows a digit > 0 as a sufficient proxy.
  - Qt's close_action_dialog call is implicit: the ActionDialog auto-closes on
    successful Apply (confirmed in ActionDialog.tsx handleDeleteConfirmConfirm).
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
    ACTION_NUMERIC_CMP,
    ACTION_NUMERIC_ROW,
    ACTION_NUMERIC_VALUE,
    ACTION_MAIN_BUTTON,
    EXECUTE_ALL_DELETE_CONFIRM,
    EXECUTE_ALL_DELETE_CONFIRM_YES,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

# The ActionDialog field-combo uses the English label as the <option value>.
_SCORE_FIELD_LABEL = "Score"


# ---------------------------------------------------------------------------
# HTTP helpers (mirrors s31 _get_manifest / _extract_decisions)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _extract_decisions_and_scores(manifest: dict) -> dict[str, dict]:
    """Return {basename: {decision, score}} for all file rows in the manifest.

    Uses group["items"] (the server-side serialisation key — NOT "files").
    """
    result: dict[str, dict] = {}
    for group in manifest.get("groups", []):
        for file_row in group.get("items", []):
            basename = Path(file_row["file_path"]).name
            result[basename] = {
                "decision": file_row.get("user_decision", "") or "",
                "score": float(file_row.get("score") or 0.0),
            }
    return result


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Scan near-dups, drive ActionDialog Score>0.5 threshold, assert bulk-decide."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s56_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the near-duplicates fixture ─────────────────────
            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # ── Step 2: snapshot pre-apply decisions + scores ─────────────────
            pre_manifest = _get_manifest(base_url, db_path)
            pre = _extract_decisions_and_scores(pre_manifest)
            assert len(pre) == 5, (
                f"Expected 5 neardup fixture rows, got {len(pre)}: {sorted(pre)}"
            )

            # Derive the expected split from real server-side scores.
            # The fixture's scores cluster around 0.48–0.53; at least 1 row
            # must be > 0.5 for the assertion below to be non-vacuous.
            expected_match = sorted(n for n, info in pre.items() if info["score"] > 0.5)
            expected_unchanged = sorted(
                n for n, info in pre.items() if info["score"] <= 0.5
            )
            score_summary = ", ".join(
                f"{n}:{info['score']:.4f}" for n, info in sorted(pre.items())
            )
            assert expected_match, (
                f"No fixture rows have score > 0.5 — scoring weights may have "
                f"drifted. Actual scores: {{{score_summary}}}"
            )

            # ── Step 3: open the ActionDialog via the toolbar button ──────────
            action_btn = page.get_by_test_id(ACTION_MAIN_BUTTON)
            action_btn.wait_for(state="visible", timeout=10_000)
            assert not action_btn.is_disabled(), (
                "main-action-button is disabled; manifest should be loaded "
                "and the button should be enabled."
            )
            action_btn.click()
            page.get_by_test_id(ACTION_DIALOG).wait_for(state="visible", timeout=10_000)

            # ── Step 4: select field "Score" → numeric panel appears ──────────
            field_combo = page.get_by_test_id(ACTION_FIELD_COMBO)
            field_combo.wait_for(state="visible", timeout=5_000)
            field_combo.select_option(_SCORE_FIELD_LABEL)
            # NumericPanel mounts on field change (key={field} in ActionDialog).
            page.get_by_test_id(ACTION_NUMERIC_ROW).wait_for(state="visible", timeout=5_000)

            # ── Step 5: set comparator ">" and value "0.5" ───────────────────
            # NumericPanel default mode is "threshold"; just change op + value.
            cmp_combo = page.get_by_test_id(ACTION_NUMERIC_CMP)
            cmp_combo.wait_for(state="visible", timeout=5_000)
            cmp_combo.select_option(">")

            value_input = page.get_by_test_id(ACTION_NUMERIC_VALUE)
            value_input.wait_for(state="visible", timeout=5_000)
            value_input.fill("0.5")

            # Allow 300 ms debounce + preview round-trip.
            time.sleep(0.6)

            # ── Step 6: assert live match counter shows a non-zero count ──────
            counter = page.get_by_test_id(ACTION_MATCH_COUNTER)
            counter.wait_for(state="visible", timeout=10_000)
            page.wait_for_function(
                """() => {
                    const el = document.querySelector(
                        '[data-testid="action-match-counter"]'
                    );
                    if (!el) return false;
                    // Must contain a digit > 0 (e.g. "2 of 5 match")
                    return /[1-9]/.test(el.innerText || '');
                }""",
                timeout=10_000,
            )
            counter_text = counter.inner_text()
            assert re.search(r"[1-9]", counter_text), (
                f"action-match-counter should show a non-zero count for "
                f"Score > 0.5, got {counter_text!r}"
            )

            # ── Step 7: set action to "delete" ────────────────────────────────
            action_combo = page.get_by_test_id(ACTION_ACTION_COMBO)
            action_combo.wait_for(state="visible", timeout=5_000)
            action_combo.select_option("delete")

            # ── Step 8: click Apply ───────────────────────────────────────────
            apply_btn = page.get_by_test_id(ACTION_BTN_APPLY)
            apply_btn.wait_for(state="visible", timeout=5_000)
            assert not apply_btn.is_disabled(), (
                "action-btn-apply is disabled; it should be enabled when "
                "pattern is non-empty and manifest is loaded."
            )
            apply_btn.click()

            # ── Step 9: confirm the delete dialog ────────────────────────────
            # ActionDialog shows DeleteConfirmDialog (EXECUTE_ALL_DELETE_CONFIRM)
            # when action=="delete" before calling applyBulkDecide.
            confirm_dialog = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM)
            confirm_dialog.wait_for(state="visible", timeout=10_000)

            confirm_yes = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES)
            confirm_yes.wait_for(state="visible", timeout=5_000)
            confirm_yes.click()

            # ActionDialog auto-closes after successful Apply + confirm.
            page.get_by_test_id(ACTION_DIALOG).wait_for(state="hidden", timeout=10_000)

            # ── Step 10: assert decisions via HTTP ────────────────────────────
            # Load-bearing assertion: before the #392 fix, the __cmp__: pattern
            # prefix was unrecognised and Apply returned 0 affected rows — all
            # decisions stayed ''.  After the fix, every row with score > 0.5
            # must carry user_decision == 'delete'.
            post_manifest = _get_manifest(base_url, db_path)
            post = _extract_decisions_and_scores(post_manifest)

            # At least one row must have flipped (guards against silent no-op).
            flipped = [n for n in expected_match if post[n]["decision"] == "delete"]
            post_dec_summary = ", ".join(
                f"{n}:{post[n]['decision']!r}" for n in expected_match
            )
            assert flipped, (
                f"#392 regression: Apply with field=Score > 0.5 wrote 0 rows. "
                f"expected_match={expected_match}, "
                f"post_decisions={{{post_dec_summary}}}"
            )

            # Every score > 0.5 row must be 'delete'.
            for name in expected_match:
                assert post[name]["decision"] == "delete", (
                    f"Expected user_decision='delete' for {name!r} "
                    f"(score={post[name]['score']:.4f}), "
                    f"got {post[name]['decision']!r}"
                )

            # Every score <= 0.5 row must be unchanged (empty).
            for name in expected_unchanged:
                assert post[name]["decision"] == "", (
                    f"Non-target row {name!r} was mutated: "
                    f"score={post[name]['score']:.4f}, "
                    f"got decision={post[name]['decision']!r}"
                )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
