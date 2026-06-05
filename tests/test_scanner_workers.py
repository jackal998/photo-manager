"""Tests for scanner/workers.py — default hash-worker count picker.

Regression coverage:
- Empty/None paths → local-SSD default (min(4, cpu_count())).
- Non-Windows is_remote_drive always False (and so the picker returns the
  local-SSD default even when a path is given) — POSIX has no equivalent
  query and the historical 4-worker default is correct there.
- Windows path with patched GetDriveTypeW returning DRIVE_REMOTE → 8.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


def test_default_workers_empty_paths_uses_local_default():
    from scanner.workers import default_hash_workers

    expected = min(4, os.cpu_count() or 4)
    assert default_hash_workers([]) == expected
    assert default_hash_workers(None) == expected


def test_default_workers_local_path_uses_local_default(tmp_path):
    from scanner.workers import default_hash_workers

    expected = min(4, os.cpu_count() or 4)
    assert default_hash_workers([tmp_path]) == expected


def test_is_remote_drive_non_windows_is_false(tmp_path, monkeypatch):
    """On non-Windows the helper returns False unconditionally — there is
    no GetDriveTypeW equivalent and the historical 4-worker default is
    fine for POSIX NFS / SMB mounts.
    """
    from scanner import workers as wm

    monkeypatch.setattr(wm.sys, "platform", "linux")
    assert wm.is_remote_drive(tmp_path) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only ctypes path")
def test_default_workers_remote_drive_returns_eight(monkeypatch, tmp_path):
    """When GetDriveTypeW reports DRIVE_REMOTE (4), the picker returns 8.

    We patch ``is_remote_drive`` rather than mocking ctypes so the test
    doesn't depend on a real network drive being mapped — the contract
    we care about is "any remote path → 8".
    """
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda p: True)
    assert wm.default_hash_workers([tmp_path]) == 8


def test_default_workers_mixed_paths_one_remote_picks_eight(monkeypatch, tmp_path):
    from scanner import workers as wm

    other = tmp_path / "other"
    other.mkdir()

    def fake(p):
        return Path(str(p)) == other

    monkeypatch.setattr(wm, "is_remote_drive", fake)
    assert wm.default_hash_workers([tmp_path, other]) == 8


def test_is_remote_drive_bad_path_returns_false(monkeypatch):
    """Defensive: a path that cannot be resolved must not raise."""
    from scanner import workers as wm

    # No exceptions even for empty input — falls into except block or
    # the no-drive branch.
    assert wm.is_remote_drive("") is False


# --- #548 — per-device grouping key + per-device worker count ---


def test_device_key_drive_letter_uppercased(monkeypatch):
    """A local drive-letter path groups by its drive, upper-cased so two paths
    on the same device land in the same bucket regardless of case.

    is_remote_drive is patched to False so the test behaves consistently on
    any machine — without the patch, a developer whose D: or J: is a mapped
    NAS share would get the server-key result instead of the letter."""
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda p: False)
    wm._unc_cache.clear()

    assert wm.device_key(r"D:\photos\a.jpg") == "D:"
    assert wm.device_key(r"d:\photos\b.jpg") == "D:"
    assert wm.device_key(r"J:\nas\c.heic") == "J:"


@pytest.mark.skipif(sys.platform != "win32", reason="UNC splitdrive is Windows-only")
def test_device_key_unc_path_groups_by_server():
    """On Windows a UNC path groups by its ``\\\\server`` prefix (#565), so two
    shares on the same physical server land in the same device bucket.

    Before #565 paths on ``\\\\srv\\share1`` and ``\\\\srv\\share2`` would
    have produced two distinct buckets — now they share one pool.
    """
    from scanner.workers import device_key

    # Two files on different shares of the same server → same bucket key.
    assert device_key("\\\\srv\\share\\a") == "\\\\SRV"
    assert device_key("\\\\srv\\other\\sub\\b") == "\\\\SRV"


def test_device_key_relative_path_is_empty_bucket():
    """A relative / driveless path has no device — callers treat '' as one
    bucket so such records still get hashed (single flat pool)."""
    from scanner.workers import device_key

    assert device_key("photos/a.jpg") == ""
    assert device_key("a.jpg") == ""


def test_hash_workers_for_root_remote_returns_eight(monkeypatch):
    """A NAS (remote) device gets 8 workers — SMB request latency dominates.

    ``is_remote_drive`` is the DI seam, patched so the test doesn't need a
    real mapped network drive.
    """
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda root: True)
    assert wm.hash_workers_for_root("J:") == 8


def test_hash_workers_for_root_local_ssd_returns_cpu_capped(monkeypatch):
    """A local SSD (seek detector returns False) gets the historical
    ``min(4, cpu_count())`` — only a *confirmed* spinning disk is capped, so
    SSD users never regress.

    The seek detector is injected (returns False) so the test is deterministic
    regardless of the machine's real D: drive type.
    """
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda root: False)
    assert (
        wm.hash_workers_for_root("D:", seek_penalty_detector=lambda root: False)
        == min(4, os.cpu_count() or 4)
    )


# --- #548 PR-B — local spinning-disk reader cap ---


def test_hash_workers_for_root_spinning_hdd_one_reader(monkeypatch):
    """A local spinning HDD (seek detector returns True) gets 1 reader — single
    sequential reader, seek-minimising (#552 anti-thrash principle). On a single
    mechanical HDD the disk is the bottleneck (observed 97% active, CPU only 38%);
    one reader keeps the head moving sequentially without inter-file seek bouncing."""
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda root: False)
    assert wm.hash_workers_for_root("D:", seek_penalty_detector=lambda root: True) == 1


def test_hash_workers_for_root_unknown_seek_uses_ssd_default(monkeypatch):
    """When the rotational state is unknown (detector returns None — off
    Windows, a non-drive-letter root, or a Win32 failure) the device gets the
    SSD-safe default, NOT the 2-cap. A detection miss must never throttle an
    SSD user."""
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda root: False)
    assert (
        wm.hash_workers_for_root("D:", seek_penalty_detector=lambda root: None)
        == min(4, os.cpu_count() or 4)
    )


def test_hash_workers_for_root_remote_does_not_probe_seek(monkeypatch):
    """A NAS short-circuits to 8 BEFORE the local seek probe — the rotational
    detector is meaningless for an SMB share and must not be called (it would
    open a \\\\.\\<drive> handle that doesn't apply)."""
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda root: True)

    def _must_not_run(root):
        raise AssertionError("seek detector called for a remote drive")

    assert wm.hash_workers_for_root("J:", seek_penalty_detector=_must_not_run) == 8


def test_disk_incurs_seek_penalty_non_drive_letter_is_none():
    """A relative / empty / non-drive-letter root has no probeable volume — the
    detector returns None (unknown) without touching Win32, so records on
    relative paths don't crash the worker-count picker."""
    from scanner.workers import disk_incurs_seek_penalty

    assert disk_incurs_seek_penalty("") is None
    assert disk_incurs_seek_penalty("photos/a.jpg") is None
    assert disk_incurs_seek_penalty("D") is None


def test_disk_incurs_seek_penalty_non_windows_is_none(monkeypatch):
    """Off Windows there is no IOCTL_STORAGE_QUERY_PROPERTY equivalent wired up,
    so the probe returns None (unknown) and the caller keeps the SSD-safe
    default — mirrors is_remote_drive's POSIX behaviour."""
    from scanner import workers as wm

    monkeypatch.setattr(wm.sys, "platform", "linux")
    assert wm.disk_incurs_seek_penalty("D:") is None


# --- #565 — NAS server collapsing: all shares on one physical box → one bucket ---


def _fake_resolver_linxiaoyun(letter: str) -> str:
    """Test double: both H: and J: map to shares on \\LINXIAOYUN."""
    mapping = {
        "H:": "\\\\LinXiaoYun\\home",
        "J:": "\\\\LinXiaoYun\\J",
    }
    return mapping.get(letter, "")


def test_device_key_two_remote_letters_same_server_collapse(monkeypatch):
    """H: and J: on the same NAS server both resolve to \\\\LINXIAOYUN.

    This pins the 16→8 over-subscription fix: before #565 each letter
    became its own device bucket (H: and J:), each got _NAS_WORKERS=8, and
    the NAS box saw 16 concurrent SMB reads. After the fix they share one
    bucket (\\\\LINXIAOYUN) and share one 8-reader pool.
    """
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda p: str(p).upper() in {"H:", "J:"})
    # Clear module-level cache to avoid cross-test pollution.
    wm._unc_cache.clear()

    key_h = wm.device_key("H:\\photos\\a.jpg", unc_resolver=_fake_resolver_linxiaoyun)
    key_j = wm.device_key("J:\\backup\\b.jpg", unc_resolver=_fake_resolver_linxiaoyun)

    assert key_h == "\\\\LINXIAOYUN"
    assert key_j == "\\\\LINXIAOYUN"
    assert key_h == key_j  # same bucket → share one pool


def test_device_key_native_unc_same_server_collapse():
    """Native UNC paths on the same server collapse without needing a resolver.

    \\\\LINXIAOYUN\\home\\x.jpg and \\\\LINXIAOYUN\\J\\y.jpg both key to
    \\\\LINXIAOYUN regardless of which share they're under.
    """
    from scanner.workers import device_key

    key1 = device_key("\\\\LINXIAOYUN\\home\\x.jpg")
    key2 = device_key("\\\\LINXIAOYUN\\J\\y.jpg")

    assert key1 == "\\\\LINXIAOYUN"
    assert key2 == "\\\\LINXIAOYUN"
    assert key1 == key2


def test_device_key_fail_open_on_resolver_exception(monkeypatch):
    """A resolver that raises (e.g. disconnected drive, WNetGetConnectionW error)
    must not crash device_key — fall back to the per-letter key so each drive
    letter becomes its own bucket rather than crashing the scan.

    This is the genuine failure mode: a mapped drive that's no longer connected
    will have WNetGetConnectionW raise or return a non-zero error code.
    """
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda p: str(p).upper() in {"H:", "J:"})
    wm._unc_cache.clear()

    def _raising_resolver(letter: str) -> str:
        raise OSError("The network resource is not available")

    key_h = wm.device_key("H:\\photos\\a.jpg", unc_resolver=_raising_resolver)
    key_j = wm.device_key("J:\\backup\\b.jpg", unc_resolver=_raising_resolver)

    # Fail-open: stay with the per-letter key — independent buckets, no crash.
    assert key_h == "H:"
    assert key_j == "J:"


def test_collapsed_server_key_yields_nas_workers(monkeypatch):
    """hash_workers_for_root('\\\\LINXIAOYUN') must return _NAS_WORKERS (8).

    This pins the load-bearing coupling: if is_remote_drive didn't return True
    for a bare \\\\SERVER key, the collapsed bucket would silently regress from
    8 to min(4, cpu) — the fix for the 16→8 over-subscription would instead
    produce ≤4 concurrent reads on the NAS, slower than before.

    The injected is_remote_drive patch makes the test platform-independent: on
    Linux CI GetDriveTypeW is behind the sys.platform guard, but the real
    production path on Windows hits the ``startswith("\\\\")`` early-return in
    is_remote_drive (#565), so the patch accurately represents real behaviour.
    """
    from scanner import workers as wm

    # Patch is_remote_drive to mirror what it returns on Windows for a \\SERVER
    # key: True (UNC prefix check, added in #565).
    monkeypatch.setattr(wm, "is_remote_drive", lambda p: str(p).startswith("\\\\"))

    result = wm.hash_workers_for_root("\\\\LINXIAOYUN")
    assert result == wm._NAS_WORKERS  # must be 8, not min(4, cpu)


def test_device_key_local_drive_unchanged(monkeypatch):
    """A local drive letter (C:) is unaffected by the NAS-server collapsing.

    is_remote_drive returns False for local drives, so device_key falls
    straight through to the existing behaviour: return the upper-cased drive
    letter. SSD/NVMe/HDD users see no change.
    """
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda p: False)
    wm._unc_cache.clear()

    assert wm.device_key("C:\\Users\\J\\photos\\a.jpg") == "C:"
    assert wm.device_key(r"c:\documents\b.jpg") == "C:"


def test_device_key_resolver_cache_hit(monkeypatch):
    """The second call for the same remote letter uses the cached UNC result —
    the resolver is called exactly once per distinct drive letter.
    """
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda p: str(p).upper() == "H:")
    wm._unc_cache.clear()

    call_count = {"n": 0}

    def _counting_resolver(letter: str) -> str:
        call_count["n"] += 1
        return "\\\\LinXiaoYun\\home"

    key1 = wm.device_key("H:\\a.jpg", unc_resolver=_counting_resolver)
    key2 = wm.device_key("H:\\b.jpg", unc_resolver=_counting_resolver)

    assert key1 == "\\\\LINXIAOYUN"
    assert key2 == "\\\\LINXIAOYUN"
    assert call_count["n"] == 1  # resolver called once, second hit was cached


def test_device_key_resolver_returns_empty_fails_open(monkeypatch):
    """When the resolver returns an empty string (e.g. a drive not currently
    connected to any network share), device_key falls back to the per-letter
    key — the same fail-open contract as a raising resolver.
    """
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda p: str(p).upper() == "Z:")
    wm._unc_cache.clear()

    assert wm.device_key("Z:\\share\\x.jpg", unc_resolver=lambda l: "") == "Z:"


def test_device_key_resolver_returns_non_unc_fails_open(monkeypatch):
    """If the resolver returns something that doesn't look like a UNC path
    (no ``\\\\`` prefix), _extract_server passes it through unchanged and
    device_key returns that non-empty string.  This exercises the non-UNC
    branch in _extract_server (a real failure mode where WNetGetConnectionW
    returns an unexpected format like a device name).
    """
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda p: str(p).upper() == "X:")
    wm._unc_cache.clear()

    # A resolver that returns a non-UNC string (e.g. a device path).
    result = wm.device_key("X:\\photos\\a.jpg", unc_resolver=lambda l: "DevicePath")
    # _extract_server returns "DEVICEPATH" (upper-cased non-UNC → pass through).
    assert result == "DEVICEPATH"


def test_extract_server_malformed_unc_no_server(monkeypatch):
    """_extract_server with a malformed UNC (just ``\\\\`` with no server name)
    returns the input unchanged rather than crashing — a real edge case if
    WNetGetConnectionW ever returns a truncated result (e.g. a drive that
    resolves to the UNC root without a server component).
    """
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda p: str(p).upper() == "M:")
    wm._unc_cache.clear()

    # Resolver returns bare "\\\\" — no server component after the UNC prefix.
    # _extract_server: rest = '' → server = '' → returns input ("\\\\\") unchanged.
    result = wm.device_key("M:\\x.jpg", unc_resolver=lambda l: "\\\\")
    assert isinstance(result, str)  # no crash — fail-open guarantee
