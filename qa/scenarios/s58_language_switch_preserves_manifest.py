"""Scenario 58 — language switch preserves the loaded manifest (#428).

Required source: ``qa/sandbox/near-duplicates`` (5 files, basenames
``neardup_NN_qXX.jpg``). The scan-and-load preamble is what makes
this scenario different from s22 — s22 only checks menu wiring
post-switch (the menu titles become zh_TW), and intentionally runs
on an empty source list. This driver loads a manifest first so the
relocalize path is exercised in the state where the bug actually
fires: ``MainWindow._capture_relocalize_state`` snapshots a
manifest in flight, the swap happens, and the new MainWindow has
to repopulate the tree.

What this catches that s22 can't:
  * The user-visible #428 regression — language.confirm_body
    promises "your loaded manifest and decisions stay intact", but
    pre-fix the freshly-built MainWindow showed the empty-state
    hint despite vm still holding the groups in memory. Pre-fix
    ``read_tree_row_order`` post-switch would return ``[]``;
    post-fix it returns the 5 neardup basenames the user saw before
    clicking the language item.
  * Coupling between ``_capture_relocalize_state`` and
    ``_apply_relocalize_state`` — a future refactor that strips
    ``manifest_path`` from the state-dict on one side but not the
    other would pass s22 (no manifest involved) and fail here.

What this does NOT verify (covered elsewhere):
  * Language switch end-to-end (menu → confirm → relocale) — s22.
  * No-path (cancel) confirm branch — layer-1 in
    ``tests/test_menu_controller_manifest_actions.py``.
  * Per-key capture/apply round-trip of ``manifest_path`` — layer-1
    in ``tests/test_main_window.py``.

INI / settings.json lifecycle:
  * Mirrors s22 — read ``qa/settings.json`` to snapshot ``ui.locale``,
    ALWAYS restore to ``en`` on exit (success or failure) so the
    next scenario in the batch doesn't relaunch in 繁體中文.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO / "qa" / "settings.json"


def _read_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))


def _write_settings(data: dict) -> None:
    SETTINGS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _set_locale(data: dict, locale: str) -> dict:
    out = dict(data)
    ui = dict(out.get("ui", {}))
    ui["locale"] = locale
    out["ui"] = ui
    return out


def main() -> int:
    print("scenario: s58_language_switch_preserves_manifest")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    failures: list[str] = []
    initial_settings = _read_settings()
    print(f"  initial_locale={initial_settings.get('ui', {}).get('locale', 'en')!r}")

    # ── Preamble: scan + close-and-load so a manifest is in flight ──
    # Mirrors AUTHORING.md "Scan + close-and-load to set up a manifest"
    # row — the standard way to reach a "manifest loaded" state
    # without poking at SQLite directly.
    print("step: scan_and_load_manifest")
    try:
        scan_dlg, _ = _uia.open_scan_dialog(win)
        log, elapsed = _uia.run_scan_and_wait(scan_dlg, timeout=30)
        print(f"  scan_elapsed_s={elapsed:.2f}")
        _uia.close_and_load_manifest(scan_dlg)
        # close_and_load_manifest tears down the dialog wrapper; refresh
        # the main-window handle for the menu-driven step below.
        _, win = _uia.connect_main()
    except Exception as exc:
        print(f"FAIL: scan preamble raised {exc!r}")
        _write_settings(_set_locale(initial_settings, "en"))
        return 1

    # Capture the pre-switch tree state — the user's oracle for "did
    # my manifest survive". Use the same basename-regex helper s49 /
    # s52 / s56 use; it returns file rows in display order, so the
    # post-switch comparison is order-sensitive (within-group order
    # is part of the language switch's "intact" promise).
    print("step: capture_pre_switch_tree_rows")
    pre_rows = _uia.read_tree_row_order(win)
    print(f"  pre_rows={pre_rows!r}")
    if not pre_rows:
        # The scan should have produced 5 neardup file rows in 1 group.
        # If we got 0 here, the preamble failed silently — bail rather
        # than running the language switch and asserting nothing.
        failures.append(
            "Pre-switch tree had no file rows — scan preamble failed "
            "silently. Aborting before language switch."
        )
        _write_settings(_set_locale(initial_settings, "en"))
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    old_hwnd = win.handle

    # ── Trigger View → Language → 繁體中文 ──────────────────────────
    print("step: open_view_language_zh_tw")
    try:
        _uia.open_menu(win, _uia.MENU_VIEW)
        _uia.select_popup_menu_path(
            pid, [_uia.VIEW_LANGUAGE, _uia.VIEW_LANG_ZH_TW]
        )
    except Exception as exc:
        print(f"FAIL: navigating View → Language → 繁體中文 raised {exc!r}")
        _write_settings(_set_locale(initial_settings, "en"))
        return 1

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

    # ── Reconnect to the post-switch window ─────────────────────────
    # Same poll pattern as s22 — the relocalize swap can take >1 s on
    # CI runners, and the result-tree population on the new window is
    # what we actually care about, not just "a new HWND exists". Poll
    # for both (HWND change AND tree rows non-empty) so the assertion
    # below isn't racing the relocalize.
    print("step: reconnect_after_live_switch")
    deadline = time.time() + 15.0
    new_win = None
    last_hwnd: int | None = None
    last_rows: list[str] = []
    while time.time() < deadline:
        try:
            _, candidate = _uia.connect_main(timeout=1)
        except Exception:
            time.sleep(0.2)
            continue
        cand_hwnd = candidate.handle
        if cand_hwnd == old_hwnd:
            time.sleep(0.2)
            continue
        last_hwnd = cand_hwnd
        last_rows = _uia.read_tree_row_order(candidate)
        if last_rows:
            new_win = candidate
            break
        # New window exists but tree not yet rebuilt — keep polling.
        # If this loop times out with new_win=None but last_hwnd set,
        # the bug is present (window was rebuilt but tree never
        # repopulated) and the assertion below will report it.
        time.sleep(0.2)
    print(
        f"  new_hwnd={last_hwnd!r}  old_hwnd={old_hwnd!r}  "
        f"reconnect_ok={new_win is not None}"
    )

    # ── Core assertion (#428) ────────────────────────────────────────
    # Post-switch the tree MUST show file rows. Pre-fix this is where
    # the scenario fails — last_rows stays [] for the full 15 s deadline
    # because the new MainWindow never calls refresh_tree on its own.
    print("step: verify_tree_repopulated_after_switch")
    post_rows = last_rows if new_win is None else _uia.read_tree_row_order(new_win)
    print(f"  post_rows={post_rows!r}")
    if not post_rows:
        failures.append(
            "Post-switch tree has no file rows — language.confirm_body "
            "promises the manifest stays intact, but the new MainWindow "
            "is showing the empty-state hint instead (#428 regressed)."
        )

    # Stronger oracle when the basic check passed — the user-visible
    # promise is the SAME manifest, not just A manifest. Compare the
    # display-ordered basenames; tolerate order-only drift by treating
    # them as multisets too (with a separate failure message so the
    # bisect points at the right thing).
    if post_rows:
        if set(post_rows) != set(pre_rows):
            failures.append(
                f"Post-switch row basenames {sorted(post_rows)!r} "
                f"don't match pre-switch {sorted(pre_rows)!r} — "
                f"different manifest survived the swap"
            )
        elif post_rows != pre_rows:
            failures.append(
                f"Row order shifted across switch: pre={pre_rows!r} "
                f"post={post_rows!r} — within-group display order is "
                f"part of the 'stays intact' promise"
            )

    # ── Cleanup — ALWAYS restore en, even on failure ───────────────
    print("step: restore_locale_to_en")
    persisted = _read_settings()
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

    print("scenario: s58_language_switch_preserves_manifest DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
