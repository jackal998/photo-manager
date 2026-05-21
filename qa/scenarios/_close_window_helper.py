"""Subprocess entry point that closes the running photo-manager window.

Invoked by :func:`qa.scenarios._batch._close_window` at the tail end of
every scenario run. Lives in its own module (not embedded as a code
string) so it can be lint-checked, type-checked, and unit-tested
directly — the previous implementation was a ~40-line string passed
to ``python -c`` which silently rotted when ``MainWindow.closeEvent``
reordered its buttons (see #325).

The subprocess shape is preserved (rather than calling the helper
in-process from ``_batch.py``) because pywinauto state has historically
been touchy across re-uses inside a single Python session — isolating
each close-window invocation in its own process is the cheapest way to
keep the batch runner deterministic.

Usage::

    python -m qa.scenarios._close_window_helper --leave-label "Leave"

The ``--leave-label`` is the *display text* of the "Leave" button as
rendered in the running app's current locale (``"Leave"`` for ``en``,
``"離開"`` for ``zh_TW``). The batch runner resolves it once via the
i18n catalog against the persisted ``ui.locale`` and passes it in
verbatim — the subprocess does no translation work itself, which keeps
it free of YAML / Qt imports at startup.

Failure mode is deliberately silent (no non-zero exit code) because
``_close_window`` is best-effort cleanup. If we cannot find the dialog
or the button, the batch runner will fall through to ``proc.wait()``,
time out, and force-terminate the child — same as the legacy
"Tab+Tab+Enter then maybe kill" path.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import sys
import time

_user32 = ctypes.windll.user32

WM_CLOSE = 0x0010
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
VK_TAB = 0x09
VK_RETURN = 0x0D

_WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
)


def _enum_visible_windows() -> list[tuple[int, str]]:
    """Return (hwnd, window_text) pairs for every visible top-level window."""
    out: list[tuple[int, str]] = []

    def cb(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetWindowTextW(hwnd, buf, 256)
        out.append((hwnd, buf.value))
        return True

    _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return out


def _find_window_containing(needle: str) -> list[int]:
    return [h for h, title in _enum_visible_windows() if needle in title]


def _find_window_exact(title: str) -> list[int]:
    return [h for h, t in _enum_visible_windows() if t == title]


def close_main_window() -> None:
    """Send WM_CLOSE to every visible 'Photo Manager' top-level window.

    Fire-and-forget. Qt translates WM_CLOSE into ``closeEvent``, which
    either accepts (if the manifest is clean) or pops the "Unsaved
    Changes" QMessageBox.
    """
    for hwnd in _find_window_containing("Photo Manager"):
        _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)


def _wait_for_dialog(title: str, timeout: float) -> int | None:
    """Poll EnumWindows until a window with exactly ``title`` appears."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnds = _find_window_exact(title)
        if hwnds:
            return hwnds[0]
        time.sleep(0.1)
    return None


def click_leave_button(dlg_hwnd: int, leave_label: str) -> bool:
    """Click the "Leave" button on the Unsaved Changes dialog by label.

    Uses pywinauto's UIA backend via ``connect(handle=...)`` (NOT
    ``connect(title=...)`` — the title-based connect empirically times
    out on QMessageBox even when EnumWindows already sees the dialog).
    Once connected, enumerate every descendant control and click the
    first ``Button`` whose visible text equals ``leave_label``.

    Returns ``True`` on a successful click, ``False`` if no matching
    button was found or pywinauto errored.

    Note on Qt + Win32: QMessageBox child buttons are not real Win32
    HWND children — Qt composite widgets share their parent's HWND. So
    ``EnumChildWindows(dlg_hwnd, …)`` returns empty here. UIA sees them
    fine because Qt exposes them through the UI Automation provider.
    """
    try:
        from pywinauto import Application
    except ImportError:
        return False

    try:
        app = Application(backend="uia").connect(handle=dlg_hwnd, timeout=3)
        dlg = app.window(handle=dlg_hwnd)
        # ``descendants(control_type="Button")`` finds the buttons even
        # though they aren't HWND children, because UIA flattens the
        # tree across the Qt accessibility bridge.
        for btn in dlg.descendants(control_type="Button"):
            try:
                if btn.window_text() == leave_label:
                    btn.click_input()
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def fallback_tab_enter(dlg_hwnd: int) -> None:
    """Legacy Tab+Tab+Enter sequence used when label-based click fails.

    Preserved as a last resort so transient pywinauto/UIA failures
    don't regress us below the previous behaviour. Relies on the
    button order in ``EXIT_DIALOG_BUTTONS`` (save / leave / back) with
    Back focused by default — Tab Tab from Back lands on Leave; Enter
    clicks it. If that order ever changes, the L1 test in
    ``tests/test_main_window.py::TestExitDialogButtonsConstant`` fires
    and this fallback should be updated alongside.
    """
    _user32.SetForegroundWindow(dlg_hwnd)
    time.sleep(0.15)
    for _ in range(2):
        _user32.PostMessageW(dlg_hwnd, WM_KEYDOWN, VK_TAB, 0)
        _user32.PostMessageW(dlg_hwnd, WM_KEYUP, VK_TAB, 0)
        time.sleep(0.05)
    _user32.PostMessageW(dlg_hwnd, WM_KEYDOWN, VK_RETURN, 0)
    _user32.PostMessageW(dlg_hwnd, WM_KEYUP, VK_RETURN, 0)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m qa.scenarios._close_window_helper",
        description=(
            "Close the running photo-manager window and dismiss the "
            "'Unsaved Changes' dialog by clicking the Leave button."
        ),
    )
    parser.add_argument(
        "--leave-label",
        required=True,
        help=(
            "Display text of the Leave button in the running app's locale "
            "(e.g. 'Leave' for en, '離開' for zh_TW)."
        ),
    )
    parser.add_argument(
        "--dialog-title",
        default="Unsaved Changes",
        help=(
            "Win32 window title of the dirty-state dialog. Defaults to "
            "the en string; pass the localised title (e.g. '尚未儲存的變更') "
            "when running under non-en locales."
        ),
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=2.5,
        help=(
            "Seconds to wait for the dialog to appear after WM_CLOSE. "
            "The original 1.0s wasn't enough on slower runners — 2-3s "
            "is the sweet spot per the issue notes (#325)."
        ),
    )
    args = parser.parse_args(argv)

    close_main_window()
    dlg_hwnd = _wait_for_dialog(args.dialog_title, timeout=args.settle)
    if dlg_hwnd is None:
        # No dirty prompt fired (manifest was clean, or app already
        # exited). Nothing to dismiss; let the batch runner's
        # proc.wait() see a clean exit.
        return 0

    if click_leave_button(dlg_hwnd, args.leave_label):
        return 0

    # UIA click didn't work — fall back to the positional sequence
    # rather than leaking the process. This is the legacy behaviour
    # and is documented as a known-fragile path that will be flagged
    # by the L1 button-order test if it ever silently breaks.
    fallback_tab_enter(dlg_hwnd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
