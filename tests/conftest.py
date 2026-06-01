"""Shared pytest fixtures for the photo-manager test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _dispose_orphan_widgets():
    """Destroy orphaned top-level widgets at the end of every test (#507).

    Several widget tests construct a *bare* top-level widget — e.g.
    ``_SourceListWidget()`` in ``test_scan_dialog.py`` — and let it fall out
    of scope without an explicit ``close()``/parent. In production these
    widgets are always parented to their dialog and are destroyed
    *synchronously* when the dialog closes, so this leak path never occurs
    there. In the test process, though, the unparented widget's C++ object
    (and its child QTableWidget cell widgets, each carrying a live signal
    connection) is only queued for Qt's *deferred* delete. Nothing drains
    that queue until a *later, unrelated* test happens to call
    ``processEvents()``; the drain then runs the cell-widget destructors
    against connections whose Python receiver was already freed — a
    use-after-free that aborted ~1/3 of full-suite runs on Windows/3.12,
    surfacing far downstream at
    ``test_select_dialog::test_both_sections_visible_with_match_fn``
    (the first post-leak test that pumps the event loop). This is the
    residual orphan #495 did not reach.

    The fix is ordinary Qt test hygiene: after each test, ``deleteLater``
    every top-level orphan *and drain the deferred-delete queue right here*,
    where an event loop is available. Because the orphan's whole C++ tree is
    torn down inside the same test that created it, no half-deleted widget
    survives to a later ``processEvents()``. This is deliberately targeted
    (only unparented top-level widgets) and drained immediately — not a
    blanket ``gc.collect()`` + deferred-delete sweep, which merely relocates
    the crash by deferring unrelated orphans. Verified stable 10/10
    consecutive full-suite runs on 3.12-local.
    """
    yield
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if widget.parent() is None:
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication for tests that need a Qt event loop."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
