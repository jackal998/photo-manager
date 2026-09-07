"""Web scenario s22 — Language toggle: EN ↔ 繁體中文, live switch, persistence.

Ported from qa/scenarios/s22_language_switch.py (Qt UIA).

Qt intent:
  - Open View → Language → 繁體中文.
  - Confirm the "Switch language?" prompt (Yes).
  - Verify the main window rebuilds in place with zh_TW menu-bar titles.
  - Verify ui.locale="zh_TW" persisted to settings.json.
  - Restore locale to "en" for subsequent scenarios.

Web-observable assertions:
  1. English baseline: toolbar buttons carry English labels
     (main-scan-button="Scan", main-execute-button="Execute").
  2. After clicking main-lang-toggle once the app switches to 繁體中文 live
     (no confirm dialog — see Qt divergences):
     - main-scan-button text becomes "掃描"
     - main-execute-button text becomes "執行"
     - main-lang-toggle label becomes "中"
  3. Persistence: page.reload() re-fetches GET /api/settings (returns
     ui.locale="zh_TW") and then GET /api/i18n/zh_TW, so the toolbar
     still reads "掃描"/"執行" after a hard reload.
  4. Finally: locale is restored to "en" via PATCH /api/settings so the
     shared server is left in English for later scenarios in the batch.

Qt divergences:
  - Qt fires a "Switch language?" confirm dialog (Yes/No) before the live
    switch.  The web UI skips this prompt entirely — clicking the toggle is
    the confirmation.  React re-renders the component tree in-place by
    updating the i18n store; no window rebuild occurs and no state is
    lost.  The #428 window-rebuild risk that motivated Qt's confirm dialog
    does not exist in React (components hold state in hooks/stores, not in
    a QMainWindow that is destroyed and recreated), so the web confirm was
    deliberately omitted.
  - Qt verifies menu-bar titles (CJK vs. Latin) to confirm the language is
    active.  The web equivalent is asserting toolbar button text content,
    which is the user-visible signal.
  - Qt restores "en" by writing settings.json directly (filesystem op).
    The web scenario restores "en" via PATCH /api/settings so the server's
    on-disk settings.json is reset for subsequent batch runs.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from qa.web._pw import PWContext
from qa.web.testid_constants import (
    MAIN_EXECUTE_BUTTON,
    MAIN_LANG_TOGGLE,
    MAIN_SCAN_BUTTON,
)

_SCAN_EN = "Scan"
_EXECUTE_EN = "Execute"
_TOGGLE_EN = "EN"

_SCAN_ZH = "掃描"
_EXECUTE_ZH = "執行"
_TOGGLE_ZH = "中"


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


def run(*, base_url: str) -> None:
    """Toggle language EN→zh_TW, assert live re-render, assert persistence, restore."""
    locale_restored = False
    try:
        # Preamble: force the server to English BEFORE the page loads, so the
        # English baseline below is self-healing even if a PRIOR batch scenario
        # crashed mid-switch and left ui.locale=zh_TW persisted on disk. Without
        # this, step 1 would fail with a misleading "expected Scan, got 掃描"
        # that never names the missing precondition (peer review B2).
        _patch_locale(base_url, "en")

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ------------------------------------------------------------------
            # Step 1: English baseline
            # ------------------------------------------------------------------
            scan_btn = page.get_by_test_id(MAIN_SCAN_BUTTON)
            execute_btn = page.get_by_test_id(MAIN_EXECUTE_BUTTON)
            toggle_btn = page.get_by_test_id(MAIN_LANG_TOGGLE)

            scan_btn.wait_for(state="visible", timeout=10_000)
            execute_btn.wait_for(state="visible", timeout=5_000)
            toggle_btn.wait_for(state="visible", timeout=5_000)

            scan_text_en = scan_btn.inner_text()
            assert scan_text_en.strip() == _SCAN_EN, (
                f"English baseline: expected main-scan-button={_SCAN_EN!r}, "
                f"got {scan_text_en!r}"
            )
            execute_text_en = execute_btn.inner_text()
            assert execute_text_en.strip() == _EXECUTE_EN, (
                f"English baseline: expected main-execute-button={_EXECUTE_EN!r}, "
                f"got {execute_text_en!r}"
            )
            toggle_text_en = toggle_btn.inner_text()
            assert toggle_text_en.strip() == _TOGGLE_EN, (
                f"English baseline: expected main-lang-toggle={_TOGGLE_EN!r}, "
                f"got {toggle_text_en!r}"
            )

            # ------------------------------------------------------------------
            # Step 2: Click lang toggle → switch to zh_TW (live, no confirm)
            # ------------------------------------------------------------------
            toggle_btn.click()

            # Wait for the toolbar to re-render in zh_TW.
            # The i18n store update is synchronous after getI18n() resolves,
            # so we poll with page.wait_for_function to tolerate the async
            # fetch round-trip.
            page.wait_for_function(
                """() => {
                    const el = document.querySelector('[data-testid="main-scan-button"]');
                    return el && el.innerText.trim() === '掃描';
                }""",
                timeout=15_000,
            )

            scan_text_zh = scan_btn.inner_text()
            assert scan_text_zh.strip() == _SCAN_ZH, (
                f"After zh_TW toggle: expected main-scan-button={_SCAN_ZH!r}, "
                f"got {scan_text_zh!r}"
            )
            execute_text_zh = execute_btn.inner_text()
            assert execute_text_zh.strip() == _EXECUTE_ZH, (
                f"After zh_TW toggle: expected main-execute-button={_EXECUTE_ZH!r}, "
                f"got {execute_text_zh!r}"
            )
            toggle_text_zh = toggle_btn.inner_text()
            assert toggle_text_zh.strip() == _TOGGLE_ZH, (
                f"After zh_TW toggle: expected main-lang-toggle={_TOGGLE_ZH!r} "
                f"(toggle label for zh_TW), got {toggle_text_zh!r}"
            )

            # ------------------------------------------------------------------
            # Step 3: Persistence — hard reload must still read zh_TW
            # ------------------------------------------------------------------
            # setLocale() called PATCH /api/settings ui.locale=zh_TW before
            # this step.  On reload, initI18n() runs GET /api/settings then
            # GET /api/i18n/zh_TW, so the toolbar re-renders in zh_TW without
            # the user touching anything.
            page.reload()

            page.wait_for_function(
                """() => {
                    const el = document.querySelector('[data-testid="main-scan-button"]');
                    return el && el.innerText.trim() === '掃描';
                }""",
                timeout=15_000,
            )

            scan_text_reload = scan_btn.inner_text()
            assert scan_text_reload.strip() == _SCAN_ZH, (
                f"After reload: expected main-scan-button={_SCAN_ZH!r} (persistence), "
                f"got {scan_text_reload!r}"
            )
            execute_text_reload = execute_btn.inner_text()
            assert execute_text_reload.strip() == _EXECUTE_ZH, (
                f"After reload: expected main-execute-button={_EXECUTE_ZH!r} (persistence), "
                f"got {execute_text_reload!r}"
            )

    finally:
        # ------------------------------------------------------------------
        # Restore: ALWAYS put the server back to "en" so subsequent
        # scenarios in the batch start in English.  The Qt harness restores
        # by writing settings.json directly; the web scenario uses
        # PATCH /api/settings so the server's on-disk file is reset.
        # ------------------------------------------------------------------
        _patch_locale(base_url, "en")
        locale_restored = True  # noqa: F841 — assignment confirms no exception
