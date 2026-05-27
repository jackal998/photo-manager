"""Defaults for the hash-stage worker pool count.

The hash stage's ThreadPoolExecutor is request-latency dominated when the
source root sits on a SMB share (NAS / mapped network drive) — 4 workers
under-utilises the network pipe. On local SSD, 4 workers is already CPU-bound
on PIL HEIC decode and more threads just thrash.

``default_hash_workers`` picks a starting value from the configured source
paths: 8 if any path resolves to a Windows network drive, otherwise
``min(4, os.cpu_count())``. Callers may then override via the
``scan.workers`` setting (the Scan Dialog spinbox).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable

# DRIVE_REMOTE per WinBase.h — Windows GetDriveTypeW returns this for SMB
# shares, mapped network drives, and DFS roots.
_DRIVE_REMOTE = 4


def is_remote_drive(path: Path | str) -> bool:
    """Return True if ``path`` lives on a Windows network drive.

    Non-Windows always returns False — we don't currently distinguish
    NFS / SMB mounts on POSIX, and the historical 4-worker default is
    fine there. Errors (bad path, missing API) also return False so
    a caller can treat this as a soft hint.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        drive = os.path.splitdrive(os.path.abspath(str(path)))[0]
        if not drive:
            return False
        root = drive + "\\"
        kernel32 = ctypes.windll.kernel32
        kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetDriveTypeW.restype = ctypes.c_uint
        return kernel32.GetDriveTypeW(root) == _DRIVE_REMOTE
    except (OSError, AttributeError, ValueError):
        return False


def default_hash_workers(paths: Iterable[Path | str] | None = None) -> int:
    """Return the recommended hash-stage worker count for ``paths``.

    Picks 8 if any of ``paths`` is on a Windows network drive (SMB
    latency dominates → more concurrent reads pay off until the share's
    ``smb max mux`` kicks in). Otherwise falls back to
    ``min(4, os.cpu_count())`` — historical local-SSD default. When
    ``paths`` is empty or None, returns the local-SSD default.
    """
    if paths:
        for p in paths:
            if is_remote_drive(p):
                return 8
    cpu = os.cpu_count() or 4
    return min(4, cpu)
