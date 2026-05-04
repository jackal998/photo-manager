"""Scenario 18 — Log menu (4 items): drift coverage for the diagnostics paths.

Required source: none (driver doesn't run a scan; needs an empty source list).

Drives all four items under the Log menu:
  - Open Latest Log              → opens latest app_*.log in default app
  - Open Latest Delete Log       → opens latest delete_*.csv in default app
  - Open Log Directory           → opens log dir in Explorer
  - Open Delete Log Directory    → opens delete-log dir in Explorer

Each item has two paths in main_window.py:_open_*_log*:
  - Success: infrastructure.logging.open_*() returns True (file/dir found,
    os.startfile spawned a real Notepad/Explorer in its OWN process). No
    Photo Manager-owned dialog appears.
  - Failure: open_*() returns False (target missing). A QMessageBox.warning
    fires with one of: "Log File Not Found", "Delete Log Not Found",
    "Log Directory Not Found", "Delete Log Directory Not Found".

Which path fires depends on the user's machine state — today's app log
exists post-init_logging, but the delete-log dir only exists if the user
has executed deletions before. Either path is acceptable per item; this
driver verifies that nothing UNEXPECTED appears (a third title would mean
copy drift or a misrouted handler).

Cleanup (added in #102 follow-up): each click captures a baseline of
top-level shell windows (CabinetWClass / Notepad / Notepad++) before
clicking, then sends WM_CLOSE to any new ones in those classes after.
This keeps the user's desktop clean across runs. Caveats:
  - Windows that aren't in DEFAULT_SHELL_CLASSES (e.g. VSCode,
    Sublime, EditPad if those are the user's default for .log/.csv)
    are NOT auto-closed — the OS treats those as long-lived editors
    where forced WM_CLOSE could surprise the user.
  - WM_CLOSE is asynchronous; the post-test verification is best-effort.

Mode B (forcibly emptying the log dirs to verify all four Not-Found
warning copies deterministically) is explicitly out-of-scope per #101's
"Constraints" section — the cleanup would be operator-destructive on
the user's real PhotoManager appdata directory.

Catches drift in: menu item titles (registered in
app/views/components/menu_controller.py), QMessageBox warning titles
(in main_window.py:_open_*_log*), and signal wiring between menu actions
and the four handlers.
"""
from __future__ import annotations

import sys
import time

from qa.scenarios import _uia

# (menu item title, expected Not-Found QMessageBox title)
_LOG_ITEMS: list[tuple[str, str]] = [
    (_uia.LOG_OPEN_LATEST_LOG, _uia.LOG_TITLE_LOG_FILE_NOT_FOUND),
    (_uia.LOG_OPEN_LATEST_DELETE_LOG, _uia.LOG_TITLE_DELETE_LOG_NOT_FOUND),
    (_uia.LOG_OPEN_LOG_DIRECTORY, _uia.LOG_TITLE_LOG_DIR_NOT_FOUND),
    (_uia.LOG_OPEN_DELETE_LOG_DIRECTORY, _uia.LOG_TITLE_DELETE_LOG_DIR_NOT_FOUND),
]


def _photo_manager_window_titles(pid: int) -> set[str]:
    """Return current top-level window titles owned by Photo Manager's pid.

    OS shell processes (Explorer, Notepad) spawned by os.startfile have
    their own pids and don't show up here — exactly what we want, so the
    snapshot only catches dialogs PM owns (its own QMessageBoxes).
    """
    return {t for _, _, t in _uia.list_process_windows(pid) if t}


def main() -> int:
    print("scenario: s18_log_menu")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    for item, expected_not_found_title in _LOG_ITEMS:
        print(f"step: click {item!r}")
        baseline_pm = _photo_manager_window_titles(pid)
        baseline_shell = _uia.list_top_level_windows(_uia.DEFAULT_SHELL_CLASSES)

        try:
            _uia.menu_path(win, _uia.MENU_LOG, item)
        except Exception as exc:
            print(f"FAIL: menu navigation to Log > {item!r} raised {exc!r}")
            return 1

        # Give either path (success → external shell window; not-found →
        # PM-owned QMessageBox) ~1s to settle. Both modal show() and
        # os.startfile spawn happen async-ish from the slot's perspective,
        # but UIA + EnumWindows enumeration of new top-level windows can
        # lag the show() by a few frames.
        time.sleep(1.0)
        after_pm = _photo_manager_window_titles(pid)
        new_pm_titles = after_pm - baseline_pm
        print(f"  new_pm_windows={sorted(new_pm_titles)!r}")

        # Clean up any shell windows the click may have spawned BEFORE
        # checking the PM-side outcome — keeps the desktop tidy even if
        # an assertion below fails and we early-return.
        closed = _uia.close_new_shell_windows(baseline_shell)
        if closed:
            print(
                f"  closed_shell_windows="
                f"{[(c, t) for _h, c, t in closed]!r}"
            )

        if not new_pm_titles:
            print(f"  ok: success path (cleaned up {len(closed)} shell window(s))")
        elif new_pm_titles == {expected_not_found_title}:
            print(f"  ok: not-found path with expected title — dismissing")
            if not _uia.dismiss_dialog_by_title(pid, expected_not_found_title):
                print(
                    f"FAIL: could not dismiss {expected_not_found_title!r} "
                    f"(Esc didn't close it)"
                )
                return 1
        else:
            print(
                f"FAIL: unexpected dialogs after clicking {item!r}: "
                f"{sorted(new_pm_titles)!r} "
                f"(expected either no new dialog OR exactly "
                f"{{{expected_not_found_title!r}}})"
            )
            return 1

    print("scenario: s18_log_menu DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
