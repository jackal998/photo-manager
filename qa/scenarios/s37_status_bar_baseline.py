"""Scenario 37 — Persistent status-bar baseline (#138, #140).

Required source: qa/sandbox/near-duplicates (drives the scan + close-and-load
that exercises the post-load status text).

Two regressions ride together because they share a root cause — the lack of
a persistent baseline widget on the QStatusBar:

  #138 — Startup ``status_ready`` was shown via
         ``statusBar().showMessage(text, 3000)``. After 3s the message
         expired and the bar went blank with no fallback. The fix
         attaches a QLabel via ``addWidget`` so the bar always has a
         resting message even between transient action toasts.

  #140 — After a manifest load, opening any menu (File, etc.) cleared
         the load-summary status text and left the bar empty
         permanently. Root cause: Qt's QAction hover path calls
         ``statusBar().showMessage(action.statusTip())`` even when the
         tip is empty — overwriting the prior load-summary temp message
         with an empty string and never restoring it. With the
         persistent QLabel baseline in place, Qt continues to render
         the baseline text once the menu closes (its hide-during-temp /
         show-after-clear semantics fall back to the label).

Probe ordering matters. The pre-scan #138 probe must run BEFORE we
trigger any menu hover (which would mask the bug by producing a fresh
temp clearMessage cycle) — so we sleep past the original 3s timeout
window first and check the bar still reads ``Ready``.
"""
from __future__ import annotations

import sys
import time

from qa.scenarios import _uia

# Window text we expect to see at the baseline. Pulled from
# translations/en.yml main_window.status_ready — kept as a literal here
# rather than re-translated at runtime because the scenario already
# assumes the en locale (the rest of the suite does).
EXPECTED_READY = "Ready"


def main() -> int:
    print("scenario: s37_status_bar_baseline")
    _, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    # ── #138 — startup baseline survives the 3s timeout window ──────────
    # The bug was a 3000ms timeout on the startup showMessage. Wait past
    # the old window before probing so a regression (re-introduced
    # timeout) would actually surface as an empty bar.
    print("step: wait_past_old_3s_timeout")
    time.sleep(3.5)

    print("step: probe_startup_status_bar")
    startup_text = _uia.read_status_bar_text(win)
    print(f"  startup_status_bar_text={startup_text!r}")
    if not startup_text:
        print("FAIL (#138): status bar empty after 3s — baseline was not "
              "persisted; the original showMessage(text, 3000) timeout "
              "behavior has regressed")
        return 1
    if EXPECTED_READY not in startup_text:
        print(f"FAIL (#138): startup status bar did not show the Ready "
              f"baseline — got {startup_text!r}, expected to contain "
              f"{EXPECTED_READY!r}")
        return 1

    # ── Drive a scan + close-and-load to produce a load-summary state ──
    print("step: scan_and_load_manifest")
    dlg, _ = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")
    _, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    _uia.close_and_load_manifest(dlg)

    # Re-connect after the scan-dialog close cycle. Mirrors s01.
    _, win = _uia.connect_main()

    print("step: probe_post_load_status_bar")
    post_load_text = _uia.read_status_bar_text(win)
    print(f"  post_load_status_bar_text={post_load_text!r}")
    if not post_load_text:
        print("FAIL: status bar empty immediately after manifest load — "
              "set_status_baseline did not run or the QLabel is not "
              "wired into the status bar")
        return 1
    # The post-scan path emits "Loaded manifest: …"; the Open Manifest
    # path emits "Opened manifest: …". This scenario takes the post-scan
    # route, so check for the synchronous wording. (s16 covers the Open
    # Manifest wording.)
    if "manifest" not in post_load_text.lower():
        print(f"FAIL: post-load baseline did not mention the manifest — "
              f"got {post_load_text!r}")
        return 1

    # ── #140 — open + close a menu, baseline must survive ───────────────
    # probe_menu_items opens the File menu, enumerates items (causing Qt
    # to fire hover statusTip events that previously wiped the status
    # text), then dismisses with a 2x Esc sequence. This is the exact
    # gesture that left the bar permanently empty pre-fix.
    print("step: open_and_dismiss_file_menu")
    items = _uia.probe_menu_items(win, _uia.MENU_FILE)
    print(f"  file_menu_items={[t for t, _ in items]}")

    print("step: probe_post_menu_status_bar")
    post_menu_text = _uia.read_status_bar_text(win)
    print(f"  post_menu_status_bar_text={post_menu_text!r}")
    if not post_menu_text:
        print("FAIL (#140): status bar empty after opening + dismissing "
              "the File menu — Qt's QAction-hover path wiped the temp "
              "message and no persistent baseline took over")
        return 1
    if "manifest" not in post_menu_text.lower():
        print(f"FAIL (#140): post-menu baseline lost the manifest summary "
              f"— got {post_menu_text!r}")
        return 1

    print("scenario: s37_status_bar_baseline DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
