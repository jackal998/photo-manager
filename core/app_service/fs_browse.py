"""Filesystem browser for the manifest-picker UI.

Returns directory listings in a structured format so the web client
can implement a folder-picker dialog without any server-side path logic
leaking into the client.

NO PySide6 imports — headless for the FastAPI process.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any


def _list_roots() -> list[dict[str, Any]]:
    """Return filesystem roots.

    On Windows: enumerate drive letters A-Z that exist as directories.
    On POSIX: return a single "/" entry.
    """
    if sys.platform == "win32":
        roots = []
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:\\")
            try:
                if drive.exists():
                    roots.append({
                        "name": f"{letter}:\\",
                        "path": str(drive),
                        "is_dir": True,
                        "is_manifest": False,
                    })
            except (PermissionError, OSError):
                pass
        return roots
    else:
        return [{"name": "/", "path": "/", "is_dir": True, "is_manifest": False}]


def browse(path: str | None) -> dict[str, Any]:
    """List directory contents or filesystem roots.

    Args:
        path: Absolute directory path to list, or None/empty to list roots.

    Returns:
        {
            "path": str (absolute path or "" for roots),
            "parent": str | None (parent path or None at root),
            "entries": [{"name", "path", "is_dir", "is_manifest"}],
        }
        Entries are sorted: directories first, then files, case-insensitive.

    Raises:
        FileNotFoundError: If the path is given but does not exist.
        NotADirectoryError: If the path exists but is not a directory.
    """
    if not path or not path.strip():
        return {"path": "", "parent": None, "entries": _list_roots()}

    path = path.strip()
    # A bare Windows drive letter ("C:") is CWD-relative, NOT the drive root:
    # Path("C:") resolves against the process working directory. Normalise it
    # to "C:\\" so the picker opens at the drive root when a caller passes a
    # bare drive (e.g. splitPath of "C:\\out.db" yields the dir "C:").
    if sys.platform == "win32" and re.fullmatch(r"[A-Za-z]:", path):
        path = path + "\\"

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Directory not found: {path!r}")
    if not p.is_dir():
        raise NotADirectoryError(f"Not a directory: {path!r}")

    abs_path = str(p.resolve())
    parent_path: str | None
    parent = p.resolve().parent
    if parent == p.resolve():
        # Filesystem root — no parent.
        parent_path = None
    else:
        parent_path = str(parent)

    dirs: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []

    try:
        for entry in p.iterdir():
            try:
                is_dir = entry.is_dir()
            except PermissionError:
                continue
            except OSError:
                continue

            name = entry.name
            # entry is already absolute when p is absolute; avoid resolve()
            # which raises OSError on broken symlinks (POSIX).
            entry_path = str(entry)
            # The app accepts BOTH extensions as manifests: Qt's scan dialog
            # appends ".sqlite" by default but its file filter accepts ".db"
            # too (scan_dialog.py:774), and every qa scenario writes ".db".
            # Match both, case-insensitively, so the picker highlights real
            # manifests regardless of which extension the user chose.
            is_manifest = name.lower().endswith((".sqlite", ".db"))

            record = {
                "name": name,
                "path": entry_path,
                "is_dir": is_dir,
                "is_manifest": is_manifest,
            }
            if is_dir:
                dirs.append(record)
            else:
                files.append(record)
    except PermissionError:
        pass

    dirs.sort(key=lambda e: e["name"].lower())
    files.sort(key=lambda e: e["name"].lower())

    return {
        "path": abs_path,
        "parent": parent_path,
        "entries": dirs + files,
    }
