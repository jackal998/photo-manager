"""Shared helpers for opening files / folders in the OS default handler.

Used by:
  - ``ContextMenuHandler._create_single_selection_menu`` (right-click →
    Open Folder, #102) — calls :func:`open_folder_containing`.
  - ``MainWindow``'s double-click dispatcher (#143) — calls
    :func:`open_file_in_default_viewer` for file rows.

Centralising here avoids the copy-paste trap: the right-click Open
Folder logic predates double-click by months and duplicating the
``os.startfile`` / ``subprocess.Popen`` / ``QDesktopServices`` cascade
on the new path would mean two places to keep in sync.
"""

from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from app.views.media_utils import normalize_windows_path


def open_folder_containing(path: str) -> None:
    """Open the OS file manager pointing at ``path``'s parent folder.

    On Windows, attempts to select the file in Explorer if it exists.
    On other platforms (and as Windows fallback), opens the parent
    folder via ``QDesktopServices``.

    Silent on errors — this is a user-convenience action; surfacing a
    QMessageBox for "Explorer didn't open" would be more annoying than
    helpful.
    """
    try:
        if not path:
            return
        norm_path = normalize_windows_path(path)
        folder = os.path.dirname(norm_path) or norm_path
        if not folder:
            return
        if os.name == "nt":
            try:
                if os.path.exists(norm_path):
                    subprocess.Popen(["explorer", "/select,", norm_path])
                elif os.path.isdir(folder):
                    subprocess.Popen(["explorer", folder])
                else:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
            except Exception:
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
    except Exception:
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        except Exception:
            pass


def open_file_in_default_viewer(path: str) -> None:
    """Open ``path`` in the OS's default viewer for that file type.

    Used by the result-tree double-click handler (#143). Skips silently
    if the path is empty or the file no longer exists — a row whose
    file was deleted out-of-band would otherwise pop a system error
    dialog, which is worse than no-op.

    Routes through ``QDesktopServices.openUrl`` (Qt's cross-platform
    "open with default app" plumbing) rather than re-implementing
    ``os.startfile`` / ``xdg-open`` / ``open`` per-OS.
    """
    try:
        if not path:
            return
        norm_path = normalize_windows_path(path)
        if not os.path.exists(norm_path):
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(norm_path))
    except Exception:
        pass
