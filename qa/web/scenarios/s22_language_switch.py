"""Web scenario s22 — Language toggle: EN ↔ 繁體中文, live switch, persistence.

Ported from the desktop s22_language_switch driver.

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
  5. Copy-audit probe (2026-09-14): the toolbar is a three-word sample, so
     it stayed green while whole surfaces were untranslated — the owner's
     trial opened the Scan dialog in 中文 and read an entirely English
     dialog. This scenario now reads the Scan dialog's own chrome and the
     menu-bar Action entry IN BOTH LOCALES and hard-asserts each, plus
     prints probe_status lines carrying the observed strings. Assertions
     (not just probes) because the failure mode is silent: an English
     string in a zh_TW session looks like working software.

Copy-audit ids covered by step 5: SC1 (Scan dialog was hardcoded English)
and M1 (the menu entry read "Set Action by Regex", disagreeing with the
dialog it opens, with the right-click entry, and with the desktop).

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
    MENU_ACTION,
    MENU_ACTION_SET,
    SCAN_ADVANCED,
    SCAN_DIALOG,
    SCAN_START_BUTTON,
)

_SCAN_EN = "Scan"
_EXECUTE_EN = "Execute"
_TOGGLE_EN = "EN"

_SCAN_ZH = "掃描"
_EXECUTE_ZH = "執行"
_TOGGLE_ZH = "中"

# Copy-audit expectations, per locale. Each tuple is
# (label, testid-or-None, expected-substring).  A None testid means "read the
# whole Scan dialog's text" — the dialog TITLE has no testid of its own.
_COPY_EN = {
    "scan_dialog_title": "Scan Sources",
    "scan_advanced": "Advanced settings",
    "scan_start": "Start Scan",
    "menu_action_set": "Set Action by Field…",
}
_COPY_ZH = {
    "scan_dialog_title": "掃描來源",
    "scan_advanced": "進階設定",
    "scan_start": "開始掃描",
    "menu_action_set": "依欄位設定動作…",
}
# Strings that must NOT survive a switch to zh_TW — the exact leaks the
# owner's trial hit. A zh session showing any of these is the regression.
_ENGLISH_LEAKS_ZH = ("Start Scan", "Advanced settings", "Scan Sources")


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


def _read_copy_sample(page, locale_label: str) -> dict[str, str]:
    """Open the Scan dialog + the Action menu and return the strings they show.

    Leaves the UI exactly as it found it (dialog closed, menu closed) so the
    caller's next step starts clean.
    """
    observed: dict[str, str] = {}

    page.get_by_test_id(MAIN_SCAN_BUTTON).click()
    dialog = page.get_by_test_id(SCAN_DIALOG)
    dialog.wait_for(state="visible", timeout=10_000)
    try:
        observed["scan_dialog_title"] = dialog.inner_text()
        observed["scan_advanced"] = page.get_by_test_id(SCAN_ADVANCED).inner_text()
        observed["scan_start"] = page.get_by_test_id(SCAN_START_BUTTON).inner_text()
    finally:
        page.keyboard.press("Escape")
        dialog.wait_for(state="hidden", timeout=10_000)

    # Radix portals the dropdown content lazily — click the trigger first.
    page.get_by_test_id(MENU_ACTION).click()
    try:
        item = page.get_by_test_id(MENU_ACTION_SET)
        item.wait_for(state="visible", timeout=5_000)
        observed["menu_action_set"] = item.inner_text()
    finally:
        page.keyboard.press("Escape")

    print(
        f"probe_status: s22 copy[{locale_label}] "
        f"scan_advanced={observed['scan_advanced']!r} "
        f"scan_start={observed['scan_start']!r} "
        f"menu_action_set={observed['menu_action_set']!r}"
    )
    return observed


def _assert_copy(observed: dict[str, str], expected: dict[str, str], locale_label: str) -> None:
    """Every expected substring must appear in the matching observed string."""
    for key, want in expected.items():
        got = observed[key]
        assert want in got, (
            f"Copy audit [{locale_label}]: expected {key} to contain {want!r}, "
            f"got {got!r}. An untranslated (or reworded) label here is the "
            f"exact defect the 2026-09-13 copy audit filed."
        )


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
            # Step 1b: copy-audit sample in English (SC1 + M1)
            # ------------------------------------------------------------------
            _assert_copy(_read_copy_sample(page, "en"), _COPY_EN, "en")

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
            # Step 2b: copy-audit sample in zh_TW (SC1 + M1)
            # ------------------------------------------------------------------
            observed_zh = _read_copy_sample(page, "zh_TW")
            _assert_copy(observed_zh, _COPY_ZH, "zh_TW")
            dialog_text_zh = observed_zh["scan_dialog_title"]
            for leak in _ENGLISH_LEAKS_ZH:
                assert leak not in dialog_text_zh, (
                    f"Copy audit [zh_TW]: the Scan dialog still shows the "
                    f"English string {leak!r}. This is the owner-trial defect "
                    f"(SC1) — the dialog was entirely hardcoded English."
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
