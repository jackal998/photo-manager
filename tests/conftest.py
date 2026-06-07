"""Shared pytest fixtures for the photo-manager test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication for tests that need a Qt event loop."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_unc_resolution(monkeypatch):
    """Keep ``device_key``'s device-identity grouping deterministic in tests.

    ``device_key`` memoises two real-Win32 resolutions in module-level caches and
    resolves via the real OS by default:
    * NAS-server grouping (#565) — ``_unc_cache`` / ``WNetGetConnectionW``.
    * Durable local volume id (#583) — ``_volid_cache`` /
      ``GetVolumeNameForVolumeMountPointW``.

    On a dev machine where a test drive letter (e.g. ``J:``) is a live NAS
    mapping, the real UNC call leaks the actual server (``\\\\LINXIAOYUN``) into
    the bucket key; likewise the real volume call leaks a local drive's
    ``{GUID}`` instead of the bare letter. Either memo persists across tests, so
    the suite passes in CI (no mapped drives, Linux has no Win32) but fails on
    the dev machine in full-file runs (the PR #578 failure mode).

    Force BOTH default resolvers to a no-op and clear BOTH caches around every
    test, so grouping resolves to the bare drive letter — matching CI. Tests that
    exercise the resolution logic itself inject their own resolver via
    ``device_key(unc_resolver=...)`` / ``device_key(guid_resolver=...)`` and are
    unaffected by this patch.
    """
    import scanner.workers as _workers

    _workers._unc_cache.clear()
    _workers._volid_cache.clear()
    monkeypatch.setattr(_workers, "_resolve_unc_via_win32", lambda letter: None)
    monkeypatch.setattr(_workers, "_resolve_volume_id_via_win32", lambda letter: None)
    yield
    _workers._unc_cache.clear()
    _workers._volid_cache.clear()
