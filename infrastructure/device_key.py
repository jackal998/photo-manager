"""Physical-device identity: bucket key + network-locality probe.

Extracted verbatim from ``scanner/workers.py`` (#622 Phase 2) so the
preview layer can bucket per device without importing the scanner's
worker-count module. ``scanner.workers`` re-exports every public name
here, so all pre-existing callers and their imports are unchanged —
there is exactly ONE implementation of ``device_key`` in the tree.

Two consumers, one definition:

* HASH stage (#548 / #565) — one ThreadPoolExecutor per physical device
  so NAS-latency-bound reads overlap HDD-seek-bound reads instead of
  queueing behind each other in one flat pool.
* Preview coordinator (:mod:`app.views.preview_coordinator`, #622
  Phase 2) — serialises at most one in-flight decode per device so a
  burst of rapid clicks cannot put N concurrent SMB reads on one NAS.

Both must agree on what "one device" means. If they ever disagreed, the
preview pane would consider two drive letters independent while the
scanner treated them as one box (or vice versa) and the per-device
concurrency ceiling would silently double.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

# DRIVE_REMOTE per WinBase.h — Windows GetDriveTypeW returns this for SMB
# shares, mapped network drives, and DFS roots.
_DRIVE_REMOTE = 4

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
    reads instead of queueing behind them in one flat pool. #622 Phase 2 —
    used by the preview coordinator to serialise decodes per device.
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
