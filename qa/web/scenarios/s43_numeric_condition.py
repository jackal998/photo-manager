"""Web scenario s43 — ActionDialog numeric threshold on Size (Bytes) (#209).

Ported from qa/scenarios/s43_numeric_condition.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/near-duplicates (5 JPEG re-saves at qualities 95/88/80/72/65
    — file sizes are well-separated along the quality ladder).
  - Open the Set Action dialog, pick a NUMERIC field (Size (Bytes)) so the
    numeric-condition panel surfaces, set a "> boundary" threshold, action
    delete, Apply — and verify exactly the 3 larger files get
    user_decision='delete' while the 2 smaller stay unchanged. The boundary is
    the 2nd-smallest size, so "> boundary" cleanly splits 5 into 3 matched + 2
    unchanged regardless of the fixture's absolute sizes.

Web slice (mirrors the s56 ActionDialog numeric flow, field=Size instead of
Score):
  1. run_scan seeds a fresh temp manifest from the near-duplicates fixture.
  2. The threshold boundary is derived at runtime from the fixture's on-disk
     sizes (== file_size_bytes the server thresholds on) — boundary =
     2nd-smallest, so the expected match is the 3 largest. Robust to fixture
     regeneration (only the relative ordering matters), matching Qt s43's design.
  3. ACTION_MAIN_BUTTON opens ActionDialog (toolbar entry point).
  4. ACTION_FIELD_COMBO -> "Size (Bytes)" reveals ACTION_NUMERIC_ROW (the
     numeric panel mounts on field change).
  5. ACTION_NUMERIC_CMP -> ">" ; ACTION_NUMERIC_VALUE -> str(boundary).
  6. ACTION_MATCH_COUNTER waits for a non-zero hit count (asserted BEFORE Apply —
     the post-Apply counter cannot be read because the dialog auto-closes; the
     s56 precedent).
  7. ACTION_ACTION_COMBO -> "delete" ; ACTION_BTN_APPLY fires.
  8. EXECUTE_ALL_DELETE_CONFIRM / EXECUTE_ALL_DELETE_CONFIRM_YES confirms
     (ActionDialog shows the delete-confirm whenever action=='delete').
  9. ACTION_DIALOG closes; GET /api/manifest verifies the exact 3-vs-2 split.

Non-destructive: Apply only sets user_decision on matched rows; no files are
deleted (the destructive Execute path is never opened). The temp manifest is
rmtree'd in the finally block.

Qt divergences:
  D1. SOURCE READ: Qt reads file_size_bytes + user_decision from SQLite; the web
      port derives the boundary from os.path.getsize on the fixture files (the
      same attribute the server thresholds on) and asserts decisions via GET
      /api/manifest.
  D2. NO POST-APPLY COUNTER READ: Qt reads ACTION_MATCH_COUNTER's "Applied to N"
      flash AFTER Apply; the web ActionDialog auto-closes on a successful Apply,
      so the counter is unmountable. The web port asserts a non-zero counter
      BEFORE Apply (s56 precedent) and makes the manifest split the load-bearing
      assertion.
  D3. SOFT PROBES OMITTED: Qt's Wave-11 numeric-panel polish probes (A4
      threshold-validation icon, B6 Top-N counter text, A14 spinbox-5000,
      D5/D8 Top-N grouped preview) are print-only soft probes; the validation
      icon in particular has no web element (ACTION_VALIDATION_ICON is wired
      only in RegexPanel, not NumericPanel). They are omitted from the web port.
  D4. HARDER SPLIT ASSERTION: Qt s43 and the related web s56 assert ">= 1 row
      flipped"; the web s43 asserts the EXACT 3-vs-2 split (every size>boundary
      is 'delete', every size<=boundary is unchanged) — the threshold's precise
      partition is the #209 contract.

Desktop source: qa/scenarios/s43_numeric_condition.py
Fixture:        qa/sandbox/near-duplicates/ (5 JPEGs)
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
    ACTION_MAIN_BUTTON,
    ACTION_MATCH_COUNTER,
    ACTION_NUMERIC_CMP,
    ACTION_NUMERIC_ROW,
    ACTION_NUMERIC_VALUE,
    EXECUTE_ALL_DELETE_CONFIRM,
    EXECUTE_ALL_DELETE_CONFIRM_YES,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")
# The ActionDialog field-combo uses the English label as the <option value>.
_SIZE_FIELD_LABEL = "Size (Bytes)"


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _extract_decisions(manifest: dict) -> dict[str, str]:
    """Return {basename: user_decision} for all file rows in the manifest."""
    result: dict[str, str] = {}
    for group in manifest.get("groups", []):
        for file_row in group.get("items", []):
            basename = Path(file_row["file_path"]).name
            result[basename] = file_row.get("user_decision", "") or ""
    return result


def run(*, base_url: str) -> None:
    """Scan near-dups, drive ActionDialog Size>boundary delete, assert the split."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s43_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        # ── Derive the threshold from on-disk sizes (== file_size_bytes) ────
        sizes: dict[str, int] = {}
        for entry in sorted(os.listdir(_NEAR_DUPS_DIR)):
            full = os.path.join(_NEAR_DUPS_DIR, entry)
            if os.path.isfile(full) and entry.lower().endswith(".jpg"):
                sizes[entry] = os.path.getsize(full)
        assert len(sizes) == 5, (
            f"Expected 5 near-duplicate fixtures, got {sorted(sizes)}"
        )
        ordered = sorted(sizes.items(), key=lambda kv: kv[1])
        boundary = ordered[1][1]  # 2nd-smallest size
        expected_match = {n for n, s in sizes.items() if s > boundary}
        expected_unchanged = {n for n, s in sizes.items() if s <= boundary}
        assert len(expected_match) == 3 and len(expected_unchanged) == 2, (
            f"Fixture sizes don't produce a 3-vs-2 split at boundary={boundary}: "
            f"{ordered}"
        )

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the near-duplicates fixture ────────────────────
            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            pre = _extract_decisions(_get_manifest(base_url, db_path))
            assert len(pre) == 5, (
                f"Expected 5 neardup fixture rows, got {sorted(pre)}"
            )

            # ── Step 2: open ActionDialog via the toolbar ───────────────────
            action_btn = page.get_by_test_id(ACTION_MAIN_BUTTON)
            action_btn.wait_for(state="visible", timeout=10_000)
            assert not action_btn.is_disabled(), (
                "main-action-button is disabled; the manifest should be loaded "
                "and the button enabled."
            )
            action_btn.click()
            page.get_by_test_id(ACTION_DIALOG).wait_for(state="visible", timeout=10_000)

            # ── Step 3: field = Size (Bytes) -> numeric panel mounts ────────
            field_combo = page.get_by_test_id(ACTION_FIELD_COMBO)
            field_combo.wait_for(state="visible", timeout=5_000)
            field_combo.select_option(_SIZE_FIELD_LABEL)
            page.get_by_test_id(ACTION_NUMERIC_ROW).wait_for(
                state="visible", timeout=5_000
            )

            # ── Step 4: comparator ">" + value = boundary ──────────────────
            cmp_combo = page.get_by_test_id(ACTION_NUMERIC_CMP)
            cmp_combo.wait_for(state="visible", timeout=5_000)
            cmp_combo.select_option(">")
            value_input = page.get_by_test_id(ACTION_NUMERIC_VALUE)
            value_input.wait_for(state="visible", timeout=5_000)
            value_input.fill(str(boundary))
            time.sleep(0.6)  # past the 300ms debounce + preview round-trip

            # ── Step 5: live counter shows a non-zero match (pre-Apply) ─────
            counter = page.get_by_test_id(ACTION_MATCH_COUNTER)
            counter.wait_for(state="visible", timeout=10_000)
            page.wait_for_function(
                """() => {
                    const el = document.querySelector(
                        '[data-testid="action-match-counter"]'
                    );
                    return el && /[1-9]/.test(el.innerText || '');
                }""",
                timeout=10_000,
            )
            counter_text = counter.inner_text()
            assert re.search(r"[1-9]", counter_text), (
                f"action-match-counter should show a non-zero count for "
                f"Size > {boundary}, got {counter_text!r}"
            )

            # ── Step 6: action = delete -> Apply -> confirm ─────────────────
            action_combo = page.get_by_test_id(ACTION_ACTION_COMBO)
            action_combo.wait_for(state="visible", timeout=5_000)
            action_combo.select_option("delete")

            apply_btn = page.get_by_test_id(ACTION_BTN_APPLY)
            apply_btn.wait_for(state="visible", timeout=5_000)
            assert not apply_btn.is_disabled(), (
                "action-btn-apply is disabled; it should be enabled with a "
                "threshold pattern set and a manifest loaded."
            )
            apply_btn.click()

            confirm = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM)
            confirm.wait_for(state="visible", timeout=10_000)
            confirm_yes = page.get_by_test_id(EXECUTE_ALL_DELETE_CONFIRM_YES)
            confirm_yes.wait_for(state="visible", timeout=5_000)
            confirm_yes.click()

            page.get_by_test_id(ACTION_DIALOG).wait_for(state="hidden", timeout=10_000)

            # ── Step 7: verify the exact 3-vs-2 split via the manifest ──────
            post = _extract_decisions(_get_manifest(base_url, db_path))
            print(
                f"probe_status: s43 boundary={boundary} "
                f"expected_match={sorted(expected_match)} "
                f"post={ {k: post[k] for k in sorted(post)} }"
            )
            for name in sorted(expected_match):
                assert post[name] == "delete", (
                    f"{name} (size > {boundary}) expected user_decision='delete', "
                    f"got {post[name]!r} — the __cmp__ threshold dispatch may have "
                    f"silently no-op'd (the #392/#209 regression class)."
                )
            for name in sorted(expected_unchanged):
                assert post[name] == "", (
                    f"{name} (size <= {boundary}) expected unchanged (''), "
                    f"got {post[name]!r} — the threshold matched too many rows."
                )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
