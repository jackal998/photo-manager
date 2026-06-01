"""Shared pytest fixtures for the photo-manager test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication for tests that need a Qt event loop."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
