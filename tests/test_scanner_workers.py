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


def test_device_key_drive_letter_uppercased():
    """A drive-letter path groups by its drive, upper-cased so two paths on
    the same device land in the same bucket regardless of case."""
    from scanner.workers import device_key

    assert device_key(r"D:\photos\a.jpg") == "D:"
    assert device_key(r"d:\photos\b.jpg") == "D:"
    assert device_key(r"J:\nas\c.heic") == "J:"


@pytest.mark.skipif(sys.platform != "win32", reason="UNC splitdrive is Windows-only")
def test_device_key_unc_path_groups_by_share():
    """On Windows a UNC path groups by its ``\\\\server\\share`` prefix, so two
    files on the same share share a device bucket."""
    from scanner.workers import device_key

    assert device_key(r"\\srv\share\a") == r"\\SRV\SHARE"
    assert device_key(r"\\srv\share\sub\b") == r"\\SRV\SHARE"


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


def test_hash_workers_for_root_local_returns_cpu_capped(monkeypatch):
    """A local device gets the historical ``min(4, cpu_count())`` — PR-A does
    NOT cap HDD lower (that's the WMI follow-on PR-B), so SSD users don't
    regress."""
    from scanner import workers as wm

    monkeypatch.setattr(wm, "is_remote_drive", lambda root: False)
    assert wm.hash_workers_for_root("D:") == min(4, os.cpu_count() or 4)
