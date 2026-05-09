"""Scenario 22 — View → Language → 繁體中文 confirms then switches live.

Required source: ``SCENARIO_SOURCES["s22_language_switch"] is None`` —
this driver only exercises menu wiring + the confirm prompt + the
live language switch; no scan needed. The qa/settings.json baseline
is read AND mutated, so this scenario MUST restore ``ui.locale = "en"``
before exiting or every subsequent scenario in the batch launches
in 繁體中文.

What's verified at layer 3:
  * View menu exists with a Language submenu containing both locales.
  * Clicking 繁體中文 fires a confirmation prompt
    (``Switch language?``). After Yes, the window rebuilds live — no
    restart, no app relaunch. The new window's menu bar reads
    "檔案 / 動作 / 清單 / 紀錄 / 檢視" instead of the English titles.
  * qa/settings.json gets ``ui.locale = "zh_TW"`` written.

What's NOT verified (covered elsewhere):
  * The No path on the confirm prompt — covered by the layer-1
    ``test_on_language_chosen_no_skips_relocalize`` test in
    ``tests/test_menu_controller_manifest_actions.py``.
  * Switching back via the menu in zh_TW — would need 繁體中文 UIA
    constants (檢視 / 語言 / English). Cleanup just resets
    settings.json + closes the window so the next scenario relaunches
    in English.
  * Full string parity en ↔ zh_TW — covered by the layer-1 test
    ``test_zh_tw_has_every_key_present_in_english``.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO / "qa" / "settings.json"

# Localized menu-bar titles expected to appear after the live switch.
# Hardcoded here rather than imported from _uia.py because they're
# zh_TW-specific and only this scenario needs them.
ZH_TW_MENU_TITLES = {"檔案", "動作", "清單", "紀錄", "檢視"}


def _read_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))


def _write_settings(data: dict) -> None:
    SETTINGS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _set_locale(data: dict, locale: str) -> dict:
    """Return a new dict with ``ui.locale`` set to *locale*."""
    out = dict(data)
    ui = dict(out.get("ui", {}))
    ui["locale"] = locale
    out["ui"] = ui
    return out


def _menu_titles(win) -> set[str]:
    """Return the set of top-level menu titles, mnemonic-stripped.

    Qt exposes "&File" as a QAction with text "&File"; UIA reports
    the accessible name as the visible string ("File"), so we read
    the menubar items by their `window_text` and strip any leading
    ampersand the test happens to see.
    """
    titles: set[str] = set()
    try:
        bar = win.descendants(control_type="MenuBar")
        if bar:
            for item in bar[0].children():
                txt = (item.window_text() or "").strip().replace("&", "")
                if txt:
                    titles.add(txt)
    except Exception:
        pass
    return titles


def main() -> int:
    print("scenario: s22_language_switch")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    failures: list[str] = []

    # Snapshot for restoration in the cleanup step. We need to put the
    # batch back in English regardless of what we found, otherwise every
    # later scenario launches in zh_TW and breaks on every menu-label
    # assertion.
    initial_settings = _read_settings()
    print(f"  initial_locale={initial_settings.get('ui', {}).get('locale', 'en')!r}")

    print("step: open_view_language_zh_tw")
    try:
        # Open the View menu, then navigate Language → 繁體中文 via the
        # nested-popup helper. menu_path only handles two levels (menu
        # bar → leaf item); the language picker is three.
        _uia.open_menu(win, _uia.MENU_VIEW)
        _uia.select_popup_menu_path(
            pid, [_uia.VIEW_LANGUAGE, _uia.VIEW_LANG_ZH_TW]
        )
    except Exception as exc:
        print(f"FAIL: navigating View → Language → 繁體中文 raised {exc!r}")
        # Best-effort settings restore even on failure — never strand
        # the batch in zh_TW.
        _write_settings(_set_locale(initial_settings, "en"))
        return 1

    # A confirm prompt ("Switch language?") fires before the relocalize
    # to give the user a chance to back out. Default button is No, so
    # we explicitly click Yes to proceed.
    print("step: confirm_language_switch_yes")
    try:
        confirm_hwnd = _uia.wait_for_dialog(
            pid, _uia.LANGUAGE_CONFIRM_TITLE, timeout=3
        )
    except Exception as exc:
        print(f"FAIL: language-confirm prompt did not appear: {exc!r}")
        _write_settings(_set_locale(initial_settings, "en"))
        return 1
    confirm_dlg = _uia.connect_by_handle(confirm_hwnd)
    confirm_dlg.child_window(title="Yes", control_type="Button").click_input()

    # The recreate path closes the current window and shows a fresh one.
    # Give Qt a beat to finish that transition before reconnecting; the
    # new window publishes a new HWND so our `win` UIAWrapper above is
    # stale.
    time.sleep(1.0)

    print("step: reconnect_after_live_switch")
    try:
        app, win = _uia.connect_main()
    except Exception as exc:
        # Live-switch failed to produce a new window — flag and bail
        # to the cleanup step.
        failures.append(f"could not reconnect to MainWindow after switch: {exc!r}")
        win = None  # type: ignore

    print("step: verify_menu_bar_in_zh_tw")
    if win is not None:
        titles = _menu_titles(win)
        print(f"  menu_titles={sorted(titles)!r}")
        missing = ZH_TW_MENU_TITLES - titles
        if missing:
            failures.append(
                f"Menu bar after switch missing zh_TW titles: {sorted(missing)!r} "
                f"(saw {sorted(titles)!r})"
            )

    print("step: verify_settings_json_persisted")
    persisted = _read_settings()
    persisted_locale = persisted.get("ui", {}).get("locale")
    print(f"  persisted_locale={persisted_locale!r}")
    if persisted_locale != "zh_TW":
        failures.append(
            f"qa/settings.json ui.locale={persisted_locale!r}, expected 'zh_TW' "
            f"after clicking 繁體中文"
        )

    # ── Cleanup — ALWAYS restore en, even on failure ───────────────────
    # The next scenario relaunches the app, which reads settings.json
    # afresh; resetting here is enough.
    print("step: restore_locale_to_en")
    _write_settings(_set_locale(persisted or initial_settings, "en"))
    restored = _read_settings().get("ui", {}).get("locale")
    print(f"  restored_locale={restored!r}")
    if restored != "en":
        failures.append(
            f"Could not restore ui.locale=en for subsequent scenarios; "
            f"settings.json still says {restored!r}"
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s22_language_switch DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
