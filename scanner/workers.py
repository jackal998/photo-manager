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

# Per-device hash-stage worker counts.
#   NAS  → 8: SMB request latency dominates, more concurrent reads pay off.
#   HDD  → 2: a spinning disk seek-thrashes under many concurrent readers
#             (#548 — observed 25.6 MB/s at 8 readers vs the drive's ~150 MB/s
#             sequential ceiling); 1-2 readers keep the head near-sequential.
#   else → min(4, cpu): SSD / NVMe / unknown — decode-bound, the historical
#             local default. Unknown stays here so a detection miss never
#             regresses an SSD-only user.
_NAS_WORKERS = 8
_HDD_WORKERS = 2

# IOCTL_STORAGE_QUERY_PROPERTY with StorageDeviceSeekPenaltyProperty — the
# canonical Windows "is this volume rotational" probe (Win7+). Returns a
# DEVICE_SEEK_PENALTY_DESCRIPTOR whose IncursSeekPenalty bit is True for a
# spinning HDD, False for SSD/NVMe. Used in preference to WMI MSFT_PhysicalDisk
# (#548 PR-B) because it maps a drive letter straight to the seek bit with one
# ctypes call — no COM, no WMI service dependency, same dependency surface as
# the existing GetDriveTypeW probe in is_remote_drive.
_IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
_STORAGE_DEVICE_SEEK_PENALTY_PROPERTY = 7
_PROPERTY_STANDARD_QUERY = 0


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


def disk_incurs_seek_penalty(root: str) -> bool | None:
    """Return True if the local volume ``root`` is a spinning disk, else False.

    Returns ``None`` when the answer is unknown — non-Windows, a non
    drive-letter root (UNC / relative / empty), or any Win32 failure. The
    caller treats ``None`` as "not known to be spinning" and keeps the
    SSD-safe default, so a detection miss never regresses an SSD user.

    Queries ``IOCTL_STORAGE_QUERY_PROPERTY`` for the seek-penalty descriptor
    on a no-access handle to ``\\\\.\\<drive>`` — the same low-level ctypes
    style as :func:`is_remote_drive`. Pure read-only probe; opens the volume
    with zero desired access so it needs no admin rights.
    """
    if sys.platform != "win32":
        return None
    # Only drive-letter roots are probeable here (e.g. ``'D:'``). UNC roots are
    # remote (handled by is_remote_drive before we get here); '' is relative.
    if len(root) != 2 or root[1] != ":":
        return None
    # The Win32 IOCTL boundary below is excluded from coverage (the directive
    # is on the ``try`` line): it can't run on the Linux CI runner (the
    # sys.platform guard above short-circuits there), and unit-testing it would
    # mean mocking ctypes.windll.kernel32, which the project bans as coverage
    # padding. It is exercised by real scans on the dev's Windows machine
    # (manual / layer-3). The testable contract — the guards plus the
    # True/False/None return — is covered by tests/test_scanner_workers.py.
    try:  # pragma: no cover
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32

        class _STORAGE_PROPERTY_QUERY(ctypes.Structure):
            _fields_ = [
                ("PropertyId", ctypes.c_ulong),
                ("QueryType", ctypes.c_ulong),
                ("AdditionalParameters", ctypes.c_byte * 1),
            ]

        class _DEVICE_SEEK_PENALTY_DESCRIPTOR(ctypes.Structure):
            _fields_ = [
                ("Version", ctypes.c_ulong),
                ("Size", ctypes.c_ulong),
                ("IncursSeekPenalty", ctypes.c_byte),
            ]

        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        # 0 desired access, share R|W (3), OPEN_EXISTING (3).
        handle = kernel32.CreateFileW(f"\\\\.\\{root}", 0, 3, None, 3, 0, None)
        if handle == wintypes.HANDLE(-1).value:
            return None
        try:
            query = _STORAGE_PROPERTY_QUERY(
                _STORAGE_DEVICE_SEEK_PENALTY_PROPERTY, _PROPERTY_STANDARD_QUERY, (0,)
            )
            descriptor = _DEVICE_SEEK_PENALTY_DESCRIPTOR()
            returned = wintypes.DWORD(0)
            ok = kernel32.DeviceIoControl(
                handle, _IOCTL_STORAGE_QUERY_PROPERTY,
                ctypes.byref(query), ctypes.sizeof(query),
                ctypes.byref(descriptor), ctypes.sizeof(descriptor),
                ctypes.byref(returned), None,
            )
            if not ok:
                return None
            return bool(descriptor.IncursSeekPenalty)
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):  # pragma: no cover
        return None


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


def device_key(path: Path | str) -> str:
    """Physical-device grouping key for ``path``.

    ``os.path.splitdrive`` on a drive-letter path returns the drive
    (e.g. ``'D:'``, ``'J:'``); on a UNC path it returns the
    ``\\\\server\\share`` prefix. Either way the result is upper-cased so
    two paths on the same device land in the same bucket regardless of
    case. An empty / relative path returns ``''`` — callers treat ``''``
    as a single bucket. Pure, no I/O.

    #548 — used by the HASH stage to run one ThreadPoolExecutor per
    physical device concurrently, so NAS-latency-bound reads overlap
    HDD-seek-bound reads instead of queueing behind them in one flat pool.
    """
    drive = os.path.splitdrive(str(path))[0]
    return drive.upper()


def hash_workers_for_root(root: str, *, seek_penalty_detector=None) -> int:
    """Per-device hash worker count for one device root (#548).

    * NAS (``is_remote_drive``) → ``_NAS_WORKERS`` (8) — SMB request latency
      dominates, so more concurrent reads pay off.
    * Local spinning HDD (``seek_penalty_detector`` returns True) →
      ``_HDD_WORKERS`` (2) — a mechanical disk seek-thrashes under many
      concurrent readers (#548 PR-B).
    * Everything else — local SSD / NVMe, or any device whose rotational
      state is unknown (detector returns False or ``None``) → the SSD-safe
      ``min(4, os.cpu_count())``. Unknown lands here so a detection miss
      never regresses an SSD-only user.

    ``seek_penalty_detector`` is injected so the rotational decision is
    unit-testable without real hardware or Win32. When ``None`` (the default)
    it resolves to the module-level :func:`disk_incurs_seek_penalty` at call
    time — a late lookup so tests can monkeypatch the module attribute, and so
    the production probe (which fails open to ``None`` off Windows or on any
    error) is used in the real worker.
    """
    if is_remote_drive(root):
        return _NAS_WORKERS
    detector = seek_penalty_detector or disk_incurs_seek_penalty
    # ``is True`` so both False (SSD) and None (unknown) fall through to the
    # SSD-safe default — only a *confirmed* spinning disk gets the 2-cap.
    if detector(root) is True:
        return _HDD_WORKERS
    cpu = os.cpu_count() or 4
    return min(4, cpu)
