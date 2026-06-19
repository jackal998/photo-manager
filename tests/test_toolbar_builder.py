"""Tests for the main-window toolbar builder.

Verifies it reuses the supplied menu actions (so their gating carries
over), adds exactly three bulk-decision actions wired to the callbacks,
and tags Scan / Execute with the object names the theme styles.
"""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QToolBar

from app.views.components.toolbar_builder import build_main_toolbar


def _actions(win) -> dict[str, QAction]:
    return {
        "scan_sources": QAction("Scan", win),
        "open_manifest": QAction("Open", win),
        "execute_action": QAction("Exec", win),
    }


def test_toolbar_reuses_actions_and_styles_them(qapp):
    win = QMainWindow()
    actions = _actions(win)
    tb = build_main_toolbar(
        win, actions,
        on_bulk_keep=lambda: None,
        on_bulk_delete=lambda: None,
        on_bulk_remove=lambda: None,
    )
    assert isinstance(tb, QToolBar)
    assert tb in win.findChildren(QToolBar)

    tb_actions = tb.actions()
    assert actions["scan_sources"] in tb_actions
    assert actions["open_manifest"] in tb_actions
    assert actions["execute_action"] in tb_actions

    assert tb.widgetForAction(actions["scan_sources"]).objectName() == "primaryAction"
    assert tb.widgetForAction(actions["execute_action"]).objectName() == "dangerAction"


def test_three_bulk_actions_wired_in_order(qapp):
    win = QMainWindow()
    actions = _actions(win)
    calls: list[str] = []
    tb = build_main_toolbar(
        win, actions,
        on_bulk_keep=lambda: calls.append("keep"),
        on_bulk_delete=lambda: calls.append("delete"),
        on_bulk_remove=lambda: calls.append("remove"),
    )
    # The bulk actions are the toolbar actions that aren't the reused menu
    # actions, aren't separators, and carry text (the label/spacer widgets
    # are textless QWidgetActions).
    reused = set(actions.values())
    bulk = [
        a for a in tb.actions()
        if a not in reused and not a.isSeparator() and a.text()
    ]
    assert len(bulk) == 3
    bulk[0].trigger()
    bulk[1].trigger()
    bulk[2].trigger()
    assert calls == ["keep", "delete", "remove"]


def test_missing_optional_actions_are_skipped(qapp):
    """A sparse action dict (e.g. before all menu actions exist) must not
    crash — the builder guards each reused action."""
    win = QMainWindow()
    tb = build_main_toolbar(
        win, {},
        on_bulk_keep=lambda: None,
        on_bulk_delete=lambda: None,
        on_bulk_remove=lambda: None,
    )
    # Still builds, still has the three bulk actions.
    bulk = [a for a in tb.actions() if not a.isSeparator() and a.text()]
    assert len(bulk) == 3
