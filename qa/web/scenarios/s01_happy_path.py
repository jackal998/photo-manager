"""Web scenario s01 — Happy path: pre-scan gating → scan → post-load state.

Ported from qa/scenarios/s01_happy_path.py (Qt UIA).

Qt intent (the three load-bearing "post-load UI state" behaviours):
  #42 — first-run empty-state hint visible at startup, hidden after a
        manifest loads; the result tree appears only post-load.
  #52 — manifest-gated menu items disabled pre-manifest, enabled after.
  #58 — status bar shows the just-loaded manifest summary
        ("Loaded manifest: N group(s), M isolated file(s)" in Qt).

Web-observable assertions:
  PRE-scan (no manifest):
    1. The empty-state hint placeholder is visible (#42 pre-state).
    2. The status bar shows a stable idle/ready text (assert_status_idle).
    3. The manifest-gated Action menu items are DISABLED — Radix exposes
       ``data-disabled`` on a disabled DropdownMenu.Item (proven in
       frontend/src/components/MenuBar.test.tsx:75); ``None`` means enabled.
  Then scan the near-duplicates fixture and load the manifest.
  POST-load (load-bearing port of inv_status + inv_actions):
    4. Status bar shows the "N groups · M files" manifest summary (#58).
    5. The empty-state hint is gone and the result tree is present (#42).
    6. The manifest-gated Action menu items are now ENABLED
       (data-disabled is None) — the web analogue of
       assert_manifest_actions_consistent(expected_enabled=True) (#52).

Non-destructive: the scenario only SCANS the read-only repo fixture
(qa/sandbox/near-duplicates) into a throwaway tmp .db; no execute / delete
runs, so the sandbox files are only read by the scanner.

Qt divergences:
  1. Status wording: Qt asserts the regex ``Loaded manifest: \\d+ group`` for
     #58.  The web status bar renders ``"N groups · M files"`` (App.tsx) —
     the literal ``Loaded manifest:`` wording does NOT exist on the web.
     Same behaviour (a manifest-summary line appears right after load); the
     port asserts the presence of both ``groups`` and ``files`` in the text,
     matching the s37 status-bar convention.
  2. Menu surface: Qt probes the **List** menu for the #52 gating transition
     (its "Remove from List" item).  The web MenuBar (MenuBar.tsx:14) defers
     the List → Remove-from-List item as a follow-up and has no List menu, so
     the web port asserts the identical disabled→enabled gating on the
     **Action** menu items (Set Action by Regex / Execute…), which carry
     ``disabled={!manifestLoaded}`` (MenuBar.tsx:116,124).  The gating
     SEMANTICS — manifest-gated items flip from disabled to enabled on load —
     are the same; only the menu hosting the gated item differs.
  3. Radix portal mechanic (no Qt counterpart): DropdownMenu.Content is
     portalled and rendered lazily, so the menu TRIGGER (MENU_ACTION) must be
     clicked before an item's disabled-state is in the DOM, and Escape is
     pressed afterwards to close the menu.  Qt's pywinauto can read menu-item
     enablement without opening the menu; the web cannot.
  4. Sources: the Qt scenario configures three sources (huge / near-duplicates
     / unique).  The web port scans near-duplicates alone — sufficient for a
     non-empty manifest and the pre→post state transition this scenario
     verifies — matching the single-source convention used by s37 and s16.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    assert_status_idle,
    run_scan,
)
from qa.web.testid_constants import (
    MAIN_EMPTY_STATE,
    MAIN_RESULT_TREE,
    MENU_ACTION,
    MENU_ACTION_SET,
    MENU_ACTION_EXECUTE,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")


def _assert_action_items_disabled(page: "object", *, expected_disabled: bool) -> None:
    """Open the Action menu and assert its gated items' disabled-state.

    Radix portals DropdownMenu.Content lazily, so the MENU_ACTION trigger is
    clicked first to render the items, each item's ``data-disabled`` attribute
    is read (``None`` == enabled; any value == disabled — see
    MenuBar.test.tsx:75), then Escape closes the menu so the next interaction
    starts from a clean state.

    Parameters
    ----------
    page:
        The Playwright Page on the app root ``/``.
    expected_disabled:
        ``True``  → assert both items are disabled (pre-manifest gate).
        ``False`` → assert both items are enabled  (post-load gate).
    """
    page.get_by_test_id(MENU_ACTION).click()
    try:
        for item_testid in (MENU_ACTION_SET, MENU_ACTION_EXECUTE):
            item = page.get_by_test_id(item_testid)
            item.wait_for(state="visible", timeout=5_000)
            data_disabled = item.get_attribute("data-disabled")
            if expected_disabled:
                assert data_disabled is not None, (
                    f"Pre-manifest: Action menu item {item_testid!r} must be "
                    f"DISABLED (Radix data-disabled present), but data-disabled "
                    f"is None (enabled)"
                )
            else:
                assert data_disabled is None, (
                    f"Post-load: Action menu item {item_testid!r} must be "
                    f"ENABLED (data-disabled absent), but data-disabled="
                    f"{data_disabled!r}"
                )
    finally:
        # Close the portalled menu so the next click starts clean.
        page.keyboard.press("Escape")
        page.get_by_test_id(MENU_ACTION_SET).wait_for(state="hidden", timeout=5_000)


def run(*, base_url: str) -> int:
    """Pre-scan gating + idle status → scan → post-load summary + gating flip."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s01_")
    db_path = os.path.join(tmpdir, "s01_manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── PRE-scan probes (no manifest loaded) ─────────────────────────
            # #42 pre-state: the empty-state hint placeholder is visible.
            page.get_by_test_id(MAIN_EMPTY_STATE).wait_for(
                state="visible", timeout=10_000
            )

            # #138/idle: the status bar shows a stable ready/idle text.
            assert_status_idle(page, timeout=10_000)

            # #52 pre-state: the manifest-gated Action items are DISABLED.
            _assert_action_items_disabled(page, expected_disabled=True)

            # ── SCAN + load (exactly like s37) ───────────────────────────────
            status_text = run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # ── POST-load assertions (port of inv_status + inv_actions) ──────
            # #58 status: the manifest summary line is present.  run_scan
            # already waited on the "N groups · M files" pattern; we re-assert
            # both tokens explicitly so a wording regression fails loudly (web
            # analogue of the Qt `Loaded manifest: \d+ group` regex).
            assert (
                "groups" in status_text.lower() and "files" in status_text.lower()
            ), (
                f"Post-scan status bar must contain 'groups' and 'files', "
                f"got: {status_text!r}"
            )

            # #42 post-state: the empty-state hint is gone and the tree appears.
            # App.tsx renders these mutually exclusively (the empty-state node
            # is UNMOUNTED once a manifest loads — App.test.tsx:213-214), so
            # `hidden` succeeds on the now-detached placeholder.
            page.get_by_test_id(MAIN_EMPTY_STATE).wait_for(
                state="hidden", timeout=10_000
            )
            page.get_by_test_id(MAIN_RESULT_TREE).wait_for(
                state="visible", timeout=10_000
            )

            # #52 post-state: the manifest-gated Action items are now ENABLED.
            _assert_action_items_disabled(page, expected_disabled=False)

        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
