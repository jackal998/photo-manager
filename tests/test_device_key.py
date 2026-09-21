"""Tests for infrastructure/device_key.py — physical-device bucket key.

Moved here from ``tests/test_scanner_workers.py`` by #622 Phase 2, which
extracted ``device_key`` (and the ``is_remote_drive`` probe it depends on)
out of ``scanner/workers.py`` so the preview coordinator can bucket by the
same key without importing the scanner's worker-count module. The assertions
are unchanged; what changed is the monkeypatch target.

**Why the target matters** (the #605 input-shape lesson): these tests patch
``is_remote_drive`` to stay platform-independent. ``device_key`` resolves that
name in its OWN module, so patching the re-exporting ``scanner.workers`` would
now rebind a name nothing reads — the patch would go silently inert and the
tests would fall back to the real Win32 probe, passing on a dev machine with a
mapped ``J:`` for the wrong reason and behaving differently on Linux CI.
``TestDeviceKeyReexport`` below pins both halves of that contract.

Coverage kept from the original file:
- Non-Windows ``is_remote_drive`` always False; a bad path never raises.
- Drive-letter bucket key, upper-cased; relative path → '' (one bucket).
- #565 NAS-server collapsing: two letters on one box → one ``\\\\SERVER`` key.
- Fail-open on resolver exception / empty / non-UNC / malformed results.
- The ``_unc_cache`` memo calls the resolver once per distinct letter.
"""

from __future__ import annotations

import sys

import pytest

import infrastructure.device_key as dk


def test_is_remote_drive_non_windows_is_false(tmp_path, monkeypatch):
    """On non-Windows the helper returns False unconditionally — there is
    no GetDriveTypeW equivalent and the historical 4-worker default is
    fine for POSIX NFS / SMB mounts.
    """
    monkeypatch.setattr(dk.sys, "platform", "linux")
    assert dk.is_remote_drive(tmp_path) is False


def test_is_remote_drive_bad_path_returns_false():
    """Defensive: a path that cannot be resolved must not raise."""
    # No exceptions even for empty input — falls into except block or
    # the no-drive branch.
    assert dk.is_remote_drive("") is False


# --- #548 — per-device grouping key ---


def test_device_key_drive_letter_uppercased(monkeypatch):
    """A local drive-letter path groups by its drive, upper-cased so two paths
    on the same device land in the same bucket regardless of case.

    is_remote_drive is patched to False so the test behaves consistently on
    any machine — without the patch, a developer whose D: or J: is a mapped
    NAS share would get the server-key result instead of the letter."""
    monkeypatch.setattr(dk, "is_remote_drive", lambda p: False)
    dk._unc_cache.clear()

    assert dk.device_key(r"D:\photos\a.jpg") == "D:"
    assert dk.device_key(r"d:\photos\b.jpg") == "D:"
    assert dk.device_key(r"J:\nas\c.heic") == "J:"


@pytest.mark.skipif(sys.platform != "win32", reason="UNC splitdrive is Windows-only")
def test_device_key_unc_path_groups_by_server():
    """On Windows a UNC path groups by its ``\\\\server`` prefix (#565), so two
    shares on the same physical server land in the same device bucket.

    Before #565 paths on ``\\\\srv\\share1`` and ``\\\\srv\\share2`` would
    have produced two distinct buckets — now they share one pool.
    """
    # Two files on different shares of the same server → same bucket key.
    assert dk.device_key("\\\\srv\\share\\a") == "\\\\SRV"
    assert dk.device_key("\\\\srv\\other\\sub\\b") == "\\\\SRV"


def test_device_key_relative_path_is_empty_bucket():
    """A relative / driveless path has no device — callers treat '' as one
    bucket so such records still get hashed (single flat pool)."""
    assert dk.device_key("photos/a.jpg") == ""
    assert dk.device_key("a.jpg") == ""


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
    monkeypatch.setattr(dk, "is_remote_drive", lambda p: str(p).upper() in {"H:", "J:"})
    # Clear module-level cache to avoid cross-test pollution.
    dk._unc_cache.clear()

    key_h = dk.device_key("H:\\photos\\a.jpg", unc_resolver=_fake_resolver_linxiaoyun)
    key_j = dk.device_key("J:\\backup\\b.jpg", unc_resolver=_fake_resolver_linxiaoyun)

    assert key_h == "\\\\LINXIAOYUN"
    assert key_j == "\\\\LINXIAOYUN"
    assert key_h == key_j  # same bucket → share one pool


def test_device_key_native_unc_same_server_collapse():
    """Native UNC paths on the same server collapse without needing a resolver.

    \\\\LINXIAOYUN\\home\\x.jpg and \\\\LINXIAOYUN\\J\\y.jpg both key to
    \\\\LINXIAOYUN regardless of which share they're under.
    """
    key1 = dk.device_key("\\\\LINXIAOYUN\\home\\x.jpg")
    key2 = dk.device_key("\\\\LINXIAOYUN\\J\\y.jpg")

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
    monkeypatch.setattr(dk, "is_remote_drive", lambda p: str(p).upper() in {"H:", "J:"})
    dk._unc_cache.clear()

    def _raising_resolver(letter: str) -> str:
        raise OSError("The network resource is not available")

    key_h = dk.device_key("H:\\photos\\a.jpg", unc_resolver=_raising_resolver)
    key_j = dk.device_key("J:\\backup\\b.jpg", unc_resolver=_raising_resolver)

    # Fail-open: stay with the per-letter key — independent buckets, no crash.
    assert key_h == "H:"
    assert key_j == "J:"


def test_device_key_local_drive_unchanged(monkeypatch):
    """A local drive letter (C:) is unaffected by the NAS-server collapsing.

    is_remote_drive returns False for local drives, so device_key falls
    straight through to the existing behaviour: return the upper-cased drive
    letter. SSD/NVMe/HDD users see no change.
    """
    monkeypatch.setattr(dk, "is_remote_drive", lambda p: False)
    dk._unc_cache.clear()

    assert dk.device_key("C:\\Users\\J\\photos\\a.jpg") == "C:"
    assert dk.device_key(r"c:\documents\b.jpg") == "C:"


def test_device_key_resolver_cache_hit(monkeypatch):
    """The second call for the same remote letter uses the cached UNC result —
    the resolver is called exactly once per distinct drive letter.
    """
    monkeypatch.setattr(dk, "is_remote_drive", lambda p: str(p).upper() == "H:")
    dk._unc_cache.clear()

    call_count = {"n": 0}

    def _counting_resolver(letter: str) -> str:
        call_count["n"] += 1
        return "\\\\LinXiaoYun\\home"

    key1 = dk.device_key("H:\\a.jpg", unc_resolver=_counting_resolver)
    key2 = dk.device_key("H:\\b.jpg", unc_resolver=_counting_resolver)

    assert key1 == "\\\\LINXIAOYUN"
    assert key2 == "\\\\LINXIAOYUN"
    assert call_count["n"] == 1  # resolver called once, second hit was cached


def test_device_key_resolver_returns_empty_fails_open(monkeypatch):
    """When the resolver returns an empty string (e.g. a drive not currently
    connected to any network share), device_key falls back to the per-letter
    key — the same fail-open contract as a raising resolver.
    """
    monkeypatch.setattr(dk, "is_remote_drive", lambda p: str(p).upper() == "Z:")
    dk._unc_cache.clear()

    assert dk.device_key("Z:\\share\\x.jpg", unc_resolver=lambda l: "") == "Z:"


def test_device_key_resolver_returns_non_unc_fails_open(monkeypatch):
    """If the resolver returns something that doesn't look like a UNC path
    (no ``\\\\`` prefix), _extract_server passes it through unchanged and
    device_key returns that non-empty string.  This exercises the non-UNC
    branch in _extract_server (a real failure mode where WNetGetConnectionW
    returns an unexpected format like a device name).
    """
    monkeypatch.setattr(dk, "is_remote_drive", lambda p: str(p).upper() == "X:")
    dk._unc_cache.clear()

    # A resolver that returns a non-UNC string (e.g. a device path).
    result = dk.device_key("X:\\photos\\a.jpg", unc_resolver=lambda l: "DevicePath")
    # _extract_server returns "DEVICEPATH" (upper-cased non-UNC → pass through).
    assert result == "DEVICEPATH"


def test_extract_server_malformed_unc_no_server(monkeypatch):
    """_extract_server with a malformed UNC (just ``\\\\`` with no server name)
    returns the input unchanged rather than crashing — a real edge case if
    WNetGetConnectionW ever returns a truncated result (e.g. a drive that
    resolves to the UNC root without a server component).
    """
    monkeypatch.setattr(dk, "is_remote_drive", lambda p: str(p).upper() == "M:")
    dk._unc_cache.clear()

    # Resolver returns bare "\\\\" — no server component after the UNC prefix.
    # _extract_server: rest = '' → server = '' → returns input ("\\\\\") unchanged.
    result = dk.device_key("M:\\x.jpg", unc_resolver=lambda l: "\\\\")
    assert isinstance(result, str)  # no crash — fail-open guarantee


# --- #622 Phase 2 — the extraction contract itself ---


class TestDeviceKeyReexport:
    """``scanner.workers`` must keep re-exporting these names, not re-implement.

    The scanner's HASH stage and the preview coordinator both decide "how many
    concurrent reads may this physical device take" from this key. If the two
    ever computed it differently — a second implementation left behind in
    ``scanner/workers.py`` after a bad merge, say — one side would treat H: and
    J: as two boxes while the other treated them as one, and the NAS would
    quietly see double the intended concurrency. That is invisible to every
    other test in the suite: both copies would be individually correct.
    """

    def test_scanner_workers_reexports_the_same_objects(self):
        import scanner.workers as wm

        assert wm.device_key is dk.device_key
        assert wm.is_remote_drive is dk.is_remote_drive
        # The memo must be the SAME dict, not a copy: tests and the scanner
        # clear it through ``scanner.workers`` and expect device_key to see it.
        assert wm._unc_cache is dk._unc_cache

    def test_patching_the_defining_module_reaches_the_reexported_callable(
        self, monkeypatch
    ):
        """The monkeypatch seam this whole file relies on, asserted directly.

        If ``device_key`` were ever changed to resolve ``is_remote_drive``
        somewhere else, every platform-independence patch above would go inert
        and keep passing for the wrong reason. This test fails loudly instead.
        """
        import scanner.workers as wm

        dk._unc_cache.clear()
        monkeypatch.setattr(dk, "is_remote_drive", lambda p: False)
        assert wm.device_key("J:\\nas\\a.jpg") == "J:"

        monkeypatch.setattr(dk, "is_remote_drive", lambda p: str(p).upper() == "J:")
        dk._unc_cache.clear()
        assert wm.device_key(
            "J:\\nas\\a.jpg", unc_resolver=lambda l: "\\\\LinXiaoYun\\J"
        ) == "\\\\LINXIAOYUN"
