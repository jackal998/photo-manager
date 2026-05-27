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
