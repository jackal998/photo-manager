"""Window/dialog geometry persistence — shared by MainWindow + dialogs.

The save/restore plumbing around Qt's ``saveGeometry`` /
``restoreGeometry`` is the same for the main window (#141) and the
three resizable dialogs (#215). Centralising it here keeps:

  * the INI path resolution (``PHOTO_MANAGER_HOME``-anchored) in one
    place so dialogs and the main window always land in the same
    ``window_state.ini``;
  * the off-screen guard in one place so a multi-monitor disconnect
    can't strand any of them outside the visible desktop;
  * dialogs free of any import on ``MainWindow`` (would be a circular
    import via the DialogHandler pathway).
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QRect, QSettings
from PySide6.QtWidgets import QApplication, QWidget

# Stable QSettings keys for every persistable window-/dialog-geometry
# blob. Changing any of these silently invalidates the round-trip
# across upgrades — bump only with care. All of them land in the same
# INI returned by :func:`window_state_qsettings`.
QSETTINGS_KEY_MAIN_WINDOW_GEOM = "geometry/main_window"
QSETTINGS_KEY_MAIN_SPLITTER_STATE = "geometry/main_splitter"
# Holds ``QHeaderView.saveState()`` bytes for the results tree (#214 —
# visual section order + per-column widths). Restore runs AFTER the
# tree's ResizeToContents→Interactive cycle so auto-sized defaults
# don't clobber the saved widths.
QSETTINGS_KEY_COLUMN_HEADER_STATE = "geometry/column_header"
QSETTINGS_KEY_SCAN_DIALOG_GEOM = "geometry/scan_dialog"
QSETTINGS_KEY_EXECUTE_ACTION_DIALOG_GEOM = "geometry/execute_action_dialog"
QSETTINGS_KEY_ACTION_DIALOG_GEOM = "geometry/action_dialog"


def qsettings_path() -> Path:
    """Return the INI path used for window-state QSettings.

    Anchored under ``PHOTO_MANAGER_HOME`` (when set) so QA scenarios
    and dev runs stay isolated from any installed-app state in the
    user's Windows registry. Falls back to the repo root.
    """
    base_dir = Path(__file__).resolve().parents[2]
    home_env = os.environ.get("PHOTO_MANAGER_HOME")
    config_home = (base_dir / home_env).resolve() if home_env else base_dir
    return config_home / "window_state.ini"


def window_state_qsettings() -> QSettings:
    """Return the INI-backed QSettings for window/dialog geometry."""
    return QSettings(str(qsettings_path()), QSettings.IniFormat)


# Minimum fraction of the restored rect that must overlap a connected
# screen for the geometry to be accepted. Anything smaller and the user
# would have to keyboard-shortcut the window back into view — exactly
# the multi-monitor-disconnect failure mode the off-screen guard
# exists to prevent.
_MIN_VISIBLE_FRACTION = 0.25


def is_rect_visible_on_any_screen(rect: QRect) -> bool:
    """True when at least ``_MIN_VISIBLE_FRACTION`` of ``rect`` overlaps
    a connected screen's available geometry.

    A 1-pixel-sliver overlap is treated as off-screen — the user can't
    drag a window back into view by its title bar if only a few pixels
    are reachable, so a fractional threshold is correct here, not just
    "any intersection".
    """
    if rect.isEmpty():
        return False
    app = QApplication.instance()
    if app is None:
        return False
    rect_area = rect.width() * rect.height()
    threshold = rect_area * _MIN_VISIBLE_FRACTION
    for screen in app.screens():
        avail = screen.availableGeometry()
        intersect = rect.intersected(avail)
        if intersect.isEmpty():
            continue
        if intersect.width() * intersect.height() >= threshold:
            return True
    return False


def restore_widget_geometry(widget: QWidget, key: str) -> bool:
    """Restore widget geometry saved under ``key``, with off-screen guard.

    Returns ``True`` if a saved blob was applied and accepted, ``False``
    when no blob exists, the blob is corrupt, or the restored rect
    would land off-screen. On a False return the widget's pre-call
    geometry is left untouched, so the caller's hardcoded defaults
    remain in effect.
    """
    store = window_state_qsettings()
    blob = store.value(key)
    if not blob:
        return False
    # Snapshot pre-restore state so we can revert if the saved rect
    # lands off-screen — Qt has no built-in "undo restoreGeometry".
    pre_state = widget.saveGeometry()
    if not widget.restoreGeometry(blob):
        return False
    if not is_rect_visible_on_any_screen(widget.frameGeometry()):
        widget.restoreGeometry(pre_state)
        return False
    return True


def save_widget_geometry(widget: QWidget, key: str) -> None:
    """Persist ``widget.saveGeometry()`` under ``key``.

    Swallows OS-level QSettings errors so a save failure (e.g.
    read-only INI dir) never aborts the close path — next launch
    simply falls back to defaults, which matches the main-window
    convention from #141.
    """
    try:
        store = window_state_qsettings()
        store.setValue(key, widget.saveGeometry())
        store.sync()
    except Exception:
        pass
