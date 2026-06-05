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
    """Keep ``device_key``'s NAS-server grouping (#565) deterministic in tests.

    ``device_key`` memoises drive-letter→UNC-server in a module-level
    ``_unc_cache`` and, by default, resolves via the real
    ``WNetGetConnectionW``. On a dev machine where a test drive letter (e.g.
    ``J:``) is a live NAS mapping, the real call leaks the actual server
    (``\\\\LINXIAOYUN``) into the bucket key, and the memo persists across
    tests — so a later test that mocks ``is_remote_drive`` only for ``"J:"``
    sees a ``\\\\LINXIAOYUN`` key it doesn't recognise and the per-device
    worker count regresses 8→4. CI never hit this (no mapped drives there),
    so the suite passed in CI but failed on the dev machine in full-file runs.

    Force the default resolver to a no-op and clear the cache around every
    test, so grouping resolves to the bare drive letter — matching CI. Tests
    that exercise the resolution logic itself inject their own resolver via
    ``device_key(unc_resolver=...)`` and are unaffected by this patch.
    """
    import scanner.workers as _workers

    _workers._unc_cache.clear()
    monkeypatch.setattr(_workers, "_resolve_unc_via_win32", lambda letter: None)
    yield
    _workers._unc_cache.clear()
