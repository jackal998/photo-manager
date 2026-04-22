"""Tests for ContextMenuHandler set_decision routing.

Covers:
  - _SETTABLE_DECISIONS constant is exactly ("delete", "keep")
  - ActionHandlers protocol has set_decision method (no delete_files)
  - set_decision callback is wired for single-file right-click
  - Multi-selection menu does NOT expose "Set Action" or "Delete Files"
  - Direct-delete actions ("Delete File", "Delete Files") are absent
"""

from __future__ import annotations

from unittest.mock import MagicMock, call


# ── constants & protocol ───────────────────────────────────────────────────

class TestSettableDecisions:
    def test_decisions_are_delete_and_keep(self):
        from app.views.handlers.context_menu import _SETTABLE_DECISIONS
        assert set(_SETTABLE_DECISIONS) == {"delete", "keep"}

    def test_no_scanner_actions_in_settable(self):
        from app.views.handlers.context_menu import _SETTABLE_DECISIONS
        scanner_actions = {"EXACT", "REVIEW_DUPLICATE", "MOVE", "KEEP", "UNDATED", "SKIP"}
        assert not scanner_actions.intersection(_SETTABLE_DECISIONS)


class TestActionHandlersProtocol:
    def test_protocol_has_set_decision(self):
        from app.views.handlers.context_menu import ActionHandlers
        assert "set_decision" in dir(ActionHandlers)

    def test_protocol_does_not_have_delete_files(self):
        """delete_files is removed — direct delete is no longer in the context menu."""
        from app.views.handlers.context_menu import ActionHandlers
        assert "delete_files" not in dir(ActionHandlers)

    def test_protocol_does_not_have_set_action(self):
        """The old set_action name must not appear in the protocol."""
        from app.views.handlers.context_menu import ActionHandlers
        annotations = getattr(ActionHandlers, "__protocol_attrs__", None)
        if annotations is not None:
            assert "set_action" not in annotations
        else:
            assert hasattr(ActionHandlers, "set_decision")


# ── handler routing ────────────────────────────────────────────────────────

class TestContextMenuSetDecisionRouting:
    """Verify set_decision is called with the right args for single-file right-click."""

    def _make_handler(self, qapp):
        from PySide6.QtWidgets import QTreeView
        from app.views.handlers.context_menu import ContextMenuHandler

        tree = QTreeView()
        handlers = MagicMock()
        provider = MagicMock()
        return ContextMenuHandler(tree, provider, handlers, MagicMock()), handlers

    def test_set_decision_called_with_delete(self, qapp):
        from app.views.handlers.context_menu import ContextMenuHandler

        handler, mock_handlers = self._make_handler(qapp)
        item = {"type": "file", "path": "/a.jpg"}

        from PySide6.QtWidgets import QMenu
        menu = QMenu()
        handler._create_single_selection_menu(menu, item)

        set_action_menu = None
        for action in menu.actions():
            if action.menu() and "Action" in action.text():
                set_action_menu = action.menu()
                break

        assert set_action_menu is not None, "Set Action submenu not found"

        delete_action = next(
            (a for a in set_action_menu.actions() if a.text() == "delete"), None
        )
        assert delete_action is not None, "'delete' action not in Set Action submenu"
        delete_action.trigger()

        mock_handlers.set_decision.assert_called_once_with([item], "delete")

    def test_set_decision_called_with_keep(self, qapp):
        from app.views.handlers.context_menu import ContextMenuHandler

        handler, mock_handlers = self._make_handler(qapp)
        item = {"type": "file", "path": "/a.jpg"}

        from PySide6.QtWidgets import QMenu
        menu = QMenu()
        handler._create_single_selection_menu(menu, item)

        set_action_action = next(
            (a for a in menu.actions() if a.menu() and "Action" in a.text()),
            None,
        )
        assert set_action_action is not None
        set_action_menu = set_action_action.menu()

        keep_action = next(
            (a for a in set_action_menu.actions() if a.text() == "keep"), None
        )
        assert keep_action is not None
        keep_action.trigger()

        mock_handlers.set_decision.assert_called_once_with([item], "keep")

    def test_multi_selection_menu_has_no_set_action(self, qapp):
        """The batch-select multi-selection context menu should not expose Set Action."""
        from app.views.handlers.context_menu import ContextMenuHandler
        from PySide6.QtWidgets import QMenu, QTreeView

        tree = QTreeView()
        handler = ContextMenuHandler(tree, MagicMock(), MagicMock(), MagicMock())
        items = [
            {"type": "file", "path": "/a.jpg"},
            {"type": "file", "path": "/b.jpg"},
        ]
        menu = QMenu()
        handler._create_multi_selection_menu(menu, items)

        menu_texts = [a.text() for a in menu.actions()]
        assert not any("Action" in t for t in menu_texts), (
            "Multi-selection menu must not include 'Set Action'; "
            "batch decision is done via File menu"
        )


# ── no direct-delete actions ───────────────────────────────────────────────

class TestContextMenuNoDirectDelete:
    """Verify direct-delete actions are absent; use Set Action + Execute Action instead."""

    def _make_handler(self, qapp):
        from PySide6.QtWidgets import QTreeView
        from app.views.handlers.context_menu import ContextMenuHandler
        return ContextMenuHandler(QTreeView(), MagicMock(), MagicMock(), MagicMock())

    def test_single_file_menu_has_no_delete_file_action(self, qapp):
        from PySide6.QtWidgets import QMenu
        handler = self._make_handler(qapp)
        item = {"type": "file", "path": "/a.jpg"}
        menu = QMenu()
        handler._create_single_selection_menu(menu, item)

        top_level_texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert "Delete File" not in top_level_texts

    def test_multi_file_menu_has_no_delete_files_action(self, qapp):
        from PySide6.QtWidgets import QMenu
        handler = self._make_handler(qapp)
        items = [{"type": "file", "path": "/a.jpg"}, {"type": "file", "path": "/b.jpg"}]
        menu = QMenu()
        handler._create_multi_selection_menu(menu, items)

        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert "Delete Files" not in texts
        assert not any("Delete" in t for t in texts), (
            "Multi-selection menu must not contain any Delete action"
        )
