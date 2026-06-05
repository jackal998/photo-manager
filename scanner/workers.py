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
from typing import Callable, Iterable

# DRIVE_REMOTE per WinBase.h — Windows GetDriveTypeW returns this for SMB
# shares, mapped network drives, and DFS roots.
_DRIVE_REMOTE = 4

# Per-device hash-stage worker counts.
#   NAS  → 8: SMB request latency dominates, more concurrent reads pay off.
#   HDD  → 1: single sequential reader on a spinning disk (#552 anti-thrash
#             principle, logical extreme). On a single mechanical HDD the disk
#             is the bottleneck (observed 97% active, CPU only 38%); the goal
#             is to MINIMISE inter-file seeks — one sequential reader does that
#             best. Two readers still bounce the head between two
#             concurrently-open files.
#   else → min(4, cpu): SSD / NVMe / unknown — decode-bound, the historical
#             local default. Unknown stays here so a detection miss never
#             regresses an SSD-only user.
_NAS_WORKERS = 8
_HDD_WORKERS = 1

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

# Memoization cache for drive-letter → UNC resolution (WNetGetConnectionW).
# Populated lazily by device_key; one real Win32 call per distinct letter.
_unc_cache: dict[str, str | None] = {}


def _resolve_unc_via_win32(letter: str) -> str | None:
    """Return the UNC path for a drive letter, or None on any failure.

    This is the real Win32 WNetGetConnectionW boundary — excluded from
    coverage because it can't run on Linux CI, and mocking ctypes is banned
    as coverage padding (#548). All testable logic (server extraction,
    memoization, fail-open) is in device_key and covered there.
    """
    try:  # pragma: no cover
        import ctypes

        buf = ctypes.create_unicode_buffer(260)
        buf_size = ctypes.c_ulong(260)
        # WNetGetConnectionW: maps a drive letter (e.g. "J:") to its remote
        # name (e.g. "\\\\LINXIAOYUN\\home"). Returns 0 (NO_ERROR) on success.
        result = ctypes.windll.mpr.WNetGetConnectionW(
            letter, buf, ctypes.byref(buf_size)
        )
        if result == 0:
            unc = buf.value.strip()
            return unc if unc else None
        return None
    except (OSError, AttributeError, ValueError):  # pragma: no cover
        return None


def is_remote_drive(path: Path | str) -> bool:
    """Return True if ``path`` lives on a Windows network drive.

    Non-Windows always returns False — we don't currently distinguish
    NFS / SMB mounts on POSIX, and the historical 4-worker default is
    fine there. Errors (bad path, missing API) also return False so
    a caller can treat this as a soft hint.

    A UNC path (starts with ``\\\\``, including a bare ``\\\\SERVER`` key
    produced by device_key) is a network resource by definition — return
    True immediately without calling GetDriveTypeW (which only accepts a
    drive letter or ``\\\\server\\share\\`` root).
    """
    if sys.platform != "win32":
        return False
    # UNC paths are always remote — includes bare \\SERVER keys from device_key.
    if str(path).startswith("\\\\"):
        return True
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


def device_key(
    path: Path | str,
    *,
    unc_resolver: Callable[[str], str | None] | None = None,
) -> str:
    """Physical-device grouping key for ``path``.

    ``os.path.splitdrive`` on a drive-letter path returns the drive
    (e.g. ``'D:'``, ``'J:'``); on a UNC path it returns the
    ``\\\\server\\share`` prefix. The result is upper-cased so two paths on
    the same device land in the same bucket regardless of case. An empty /
    relative path returns ``''`` — callers treat ``''`` as a single bucket.

    **NAS server collapsing (#565):** multiple Windows drive letters that map
    to the same physical NAS server (e.g. H: and J: both on ``\\\\LINXIAOYUN``)
    are collapsed to a single ``\\\\SERVER`` key. Without this each letter
    produces its own device bucket, each gets _NAS_WORKERS=8 readers, and the
    NAS box sees 16 concurrent SMB reads instead of 8 — over-subscription.

    Resolution order for a drive letter that is_remote_drive:
    1. Look up the letter in the module-level ``_unc_cache`` (one Win32 call
       per distinct letter per process).
    2. On cache miss, call ``unc_resolver(letter)`` (default:
       ``_resolve_unc_via_win32`` — the WNetGetConnectionW boundary).
    3. Extract ``\\\\SERVER`` from the returned UNC and return it.
    4. Fail-open: any exception, non-Windows, empty UNC, or resolver returning
       None → fall back to the per-letter key. Never raises out of device_key.

    For a native UNC source path (``\\\\SERVER\\share\\...``) the same
    ``\\\\SERVER`` key is extracted directly — no resolver needed.

    ``unc_resolver`` is injected so the server-extraction logic is
    unit-testable without Win32. When ``None`` (the default) it resolves to
    ``_resolve_unc_via_win32`` at call time — the real Win32 boundary which
    is behind ``# pragma: no cover``.

    #548 — used by the HASH stage to run one ThreadPoolExecutor per physical
    device concurrently, so NAS-latency-bound reads overlap HDD-seek-bound
    reads instead of queueing behind them in one flat pool.
    """
    raw = os.path.splitdrive(str(path))[0].upper()
    try:
        # Drive letter mapping to a remote share → resolve to server key.
        if len(raw) == 2 and raw[1] == ":" and is_remote_drive(raw):
            return _server_key_for_letter(raw, unc_resolver)
        # Native UNC: \\SERVER\SHARE\... → splitdrive gives \\SERVER\SHARE.
        # Collapse to \\SERVER so two shares on the same box share one bucket.
        if raw.startswith("\\\\") and raw.count("\\") >= 3:
            # e.g. \\LINXIAOYUN\HOME → split on 3rd backslash → \\LINXIAOYUN
            parts = raw.split("\\", 3)  # ['', '', 'SERVER', 'SHARE...']
            return "\\\\" + parts[2]
    except Exception:  # noqa: BLE001 — fail-open; device_key must never raise
        pass
    return raw


def _server_key_for_letter(
    letter: str,
    unc_resolver: Callable[[str], str | None] | None,
) -> str:
    """Resolve a remote drive letter to its ``\\\\SERVER`` key.

    Memoizes results in ``_unc_cache`` so each distinct letter makes at most
    one Win32 call per process. Falls back to the per-letter key on any
    failure (non-Windows, resolver exception, empty/None UNC result).
    """
    if letter in _unc_cache:
        cached = _unc_cache[letter]
        return _extract_server(cached) if cached else letter
    resolver = unc_resolver if unc_resolver is not None else _resolve_unc_via_win32
    try:
        unc = resolver(letter)
    except Exception:  # noqa: BLE001 — fail-open on disconnected / erroring drive
        _unc_cache[letter] = None
        return letter
    _unc_cache[letter] = unc
    if not unc:
        return letter
    server = _extract_server(unc)
    return server if server else letter


def _extract_server(unc: str) -> str:
    """Extract the ``\\\\SERVER`` prefix from a UNC path string.

    ``\\\\LINXIAOYUN\\home`` → ``\\\\LINXIAOYUN``
    Returns the input unchanged if it doesn't look like a valid UNC.
    """
    upper = unc.upper()
    if not upper.startswith("\\\\"):
        return upper
    # Strip leading \\ then take the server component (up to next \\ or end).
    rest = upper[2:]
    sep = rest.find("\\")
    server = rest[:sep] if sep != -1 else rest
    if not server:
        return upper
    return "\\\\" + server


def hash_workers_for_root(root: str, *, seek_penalty_detector=None) -> int:
    """Per-device hash worker count for one device root (#548).

    * NAS (``is_remote_drive``) → ``_NAS_WORKERS`` (8) — SMB request latency
      dominates, so more concurrent reads pay off.
    * Local spinning HDD (``seek_penalty_detector`` returns True) →
      ``_HDD_WORKERS`` (1) — single sequential reader, seek-minimising (#552
      anti-thrash principle). On a single mechanical HDD the disk is the
      bottleneck (observed 97% active, CPU only 38%); one reader keeps the
      head moving sequentially. Two readers still bounce the head between two
      concurrently-open files.
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
    # SSD-safe default — only a *confirmed* spinning disk gets the 1-reader cap.
    if detector(root) is True:
        return _HDD_WORKERS
    cpu = os.cpu_count() or 4
    return min(4, cpu)
