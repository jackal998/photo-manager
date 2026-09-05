"""Web scenario s58 — Language switch preserves loaded manifest (#428).

Ported from qa/scenarios/s58_language_switch_preserves_manifest.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/near-duplicates (5 neardup_NN_qXX.jpg files, 1 group).
  - Capture the pre-switch result-tree row order and count.
  - Trigger View → Language → 繁體中文 (plus confirm Yes).
  - Assert that the result tree in the *new* MainWindow still shows the
    same 5 file rows in the same order (#428 regression guard — pre-fix
    the new window showed the empty-state hint because the relocalize swap
    never called refresh_tree on the rebuilt MainWindow).

Web-observable assertions:
  1. After scanning near-duplicates the result tree shows 5 file rows.
  2. After clicking main-lang-toggle (EN → zh_TW) the result tree STILL
     shows the same 5 file rows in the same order — count unchanged,
     order unchanged.
  3. Finally: locale is restored to "en" via PATCH /api/settings so the
     shared server is left in English for later scenarios in the batch.

Qt divergences:
  - Qt rebuilds MainWindow in-place on language switch (a new QMainWindow
    is created, the old one destroyed).  The rebuild was the source of #428:
    the new window's tree was never re-populated.  In React, language switch
    only updates the i18n store — no component is unmounted or recreated, so
    the manifest + result tree live untouched in their Zustand stores across
    the switch.  The manifest-survives-switch guarantee is therefore
    *structural* in the web port (no rebuild can ever drop it), not just
    empirical.  This scenario still exists as a regression guard: a future
    change that resets the manifest store on locale change would be caught here.
  - Qt's confirm prompt ("Switch language?") does not exist in the web UI —
    clicking the toggle IS the confirmation.  See s22 for the full divergence
    note.
  - Qt reads the tree row order via UIA ``read_tree_row_order`` (which walks
    the UIA element tree and extracts basenames).  The web equivalent is
    locating all ``[data-testid^="row-file-"]`` elements and reading their
    testid suffixes; the baseline ``count_file_rows`` helper from _invariants
    is used for the count assertion, and a direct locator query captures order.
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    count_file_rows,
    run_scan,
)
from qa.web.testid_constants import (
    MAIN_LANG_TOGGLE,
    MAIN_SCAN_BUTTON,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

_EXPECTED_FILE_COUNT = 5

_SCAN_ZH = "掃描"


def _patch_locale(base_url: str, locale: str) -> None:
    """PATCH /api/settings to set ui.locale=locale (used for restore step)."""
    url = f"{base_url.rstrip('/')}/api/settings"
    body = json.dumps({"updates": {"ui.locale": locale}}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"PATCH /api/settings failed ({exc.code}): {exc.read().decode()}"
        ) from exc


def _read_file_row_order(page: "object") -> list[str]:
    """Return the list of row-file-* testid values in DOM order.

    Each file row carries ``data-testid="row-file-{group_id}-{basename}"``.
    Sorting by DOM position (which is the order() the virtualiser renders them)
    gives the display order the user sees.  The actual attribute values are
    returned so the post-switch comparison is both count- and order-sensitive.

    Note: the result tree is virtualised (@tanstack/react-virtual); only rows
    that are within or near the viewport are rendered.  Five rows (the
    near-duplicates fixture) all fit in any reasonable viewport, so no scrolling
    is needed here.  For larger manifests callers must scroll first.
    """
    # Use evaluate to collect all matching testids in document order.
    # This avoids the 250-element Playwright locator default limit and returns
    # a plain Python list that survives the language-switch re-render.
    from playwright.sync_api import Page  # type: ignore[import]  # noqa: PLC0415

    testids: list[str] = (page).evaluate(  # type: ignore[attr-defined]
        """() => {
            const els = Array.from(
                document.querySelectorAll('[data-testid^="row-file-"]')
            );
            return els.map(el => el.getAttribute('data-testid'));
        }"""
    )
    return testids


def run(*, base_url: str) -> None:
    """Scan near-dups, switch language, assert result tree survives unchanged."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
    locale_restored = False
    try:
        # Preamble: force the server to English BEFORE the page loads. This
        # scenario toggles EN→zh_TW and waits for the zh toolbar; if a prior
        # batch scenario crashed mid-switch and left zh_TW persisted, the page
        # would start in zh, the toggle would flip to EN, and the wait for "掃描"
        # would time out. Setting en first makes the scenario self-healing (B2).
        _patch_locale(base_url, "en")

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ------------------------------------------------------------------
            # Preamble: scan the near-duplicates fixture to load a manifest.
            # ------------------------------------------------------------------
            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # Wait for all file rows to be visible in the result tree.
            page.wait_for_function(
                f"""() => {{
                    const els = document.querySelectorAll('[data-testid^="row-file-"]');
                    return els.length >= {_EXPECTED_FILE_COUNT};
                }}""",
                timeout=15_000,
            )

            # Capture pre-switch state.
            pre_count = count_file_rows(page)
            assert pre_count >= _EXPECTED_FILE_COUNT, (
                f"Pre-switch: expected {_EXPECTED_FILE_COUNT} file rows "
                f"(near-duplicates fixture), got {pre_count}. "
                f"Scan preamble may have failed silently."
            )
            pre_order = _read_file_row_order(page)
            assert len(pre_order) >= _EXPECTED_FILE_COUNT, (
                f"Pre-switch row order list has {len(pre_order)} entries, "
                f"expected {_EXPECTED_FILE_COUNT}. DOM query may have missed rows."
            )

            # ------------------------------------------------------------------
            # Language switch: click toggle (EN → zh_TW).  No confirm dialog.
            # ------------------------------------------------------------------
            toggle_btn = page.get_by_test_id(MAIN_LANG_TOGGLE)
            toggle_btn.wait_for(state="visible", timeout=5_000)
            toggle_btn.click()

            # Wait for the toolbar to reflect zh_TW (proves the i18n store
            # updated; the result tree store is separate and must not be reset).
            page.wait_for_function(
                """() => {
                    const el = document.querySelector('[data-testid="main-scan-button"]');
                    return el && el.innerText.trim() === '掃描';
                }""",
                timeout=15_000,
            )

            # ------------------------------------------------------------------
            # Core assertion (#428 regression guard): result tree unchanged.
            # ------------------------------------------------------------------
            # Give React one additional render cycle to settle (the i18n store
            # update may coalesce with any pending manifest store render).
            page.wait_for_function(
                f"""() => {{
                    const els = document.querySelectorAll('[data-testid^="row-file-"]');
                    return els.length >= {_EXPECTED_FILE_COUNT};
                }}""",
                timeout=10_000,
            )

            post_count = count_file_rows(page)
            assert post_count == pre_count, (
                f"Post-switch file row count changed: pre={pre_count}, "
                f"post={post_count}.  Language switch reset or cleared the "
                f"manifest store (#428 regression)."
            )

            post_order = _read_file_row_order(page)
            assert post_order == pre_order, (
                f"Post-switch row order changed:\n"
                f"  pre ={pre_order}\n"
                f"  post={post_order}\n"
                "Language switch should not alter the displayed manifest "
                "or its sort order (#428)."
            )

    finally:
        # Restore locale to "en" so subsequent scenarios start in English.
        _patch_locale(base_url, "en")
        locale_restored = True  # noqa: F841 — confirms no exception
        try:
            os.unlink(db_path)
        except OSError:
            pass
