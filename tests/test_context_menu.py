"""Tests for ContextMenuHandler set_decision routing.

Covers:
  - _SETTABLE_DECISIONS constant is list of (label, value) tuples
  - ActionHandlers protocol has set_decision + show_select_dialog(clicked_col)
  - set_decision callback is wired for single-file right-click
  - Multi-selection menu DOES expose "Set Action"
  - "keep (remove action)" passes "" as the decision value
  - Clicked column is forwarded to show_select_dialog
  - Direct-delete actions ("Delete File", "Delete Files") are absent
"""

from __future__ import annotations

from unittest.mock import MagicMock, call


# ── constants & protocol ───────────────────────────────────────────────────

class TestSettableDecisions:
    def test_decisions_are_list_of_tuples(self):
        from app.views.handlers.context_menu import _SETTABLE_DECISIONS
        assert isinstance(_SETTABLE_DECISIONS, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in _SETTABLE_DECISIONS)

    def test_first_decision_is_delete(self):
        from app.views.handlers.context_menu import _SETTABLE_DECISIONS
        labels = [label for label, _ in _SETTABLE_DECISIONS]
        assert "delete" in labels
        values = [v for _, v in _SETTABLE_DECISIONS]
        assert "delete" in values

    def test_keep_remove_action_clears_decision(self):
        """'keep (remove action)' must set user_decision to empty string, not 'keep'."""
        from app.views.handlers.context_menu import _SETTABLE_DECISIONS
        keep_entry = next((t for t in _SETTABLE_DECISIONS if "keep" in t[0].lower()), None)
        assert keep_entry is not None, "No 'keep' entry found in _SETTABLE_DECISIONS"
        _label, value = keep_entry
        assert value == "", f"Expected '' but got {value!r} — keep should clear the decision"

    def test_no_scanner_actions_in_settable_values(self):
        from app.views.handlers.context_menu import _SETTABLE_DECISIONS
        scanner_actions = {"EXACT", "REVIEW_DUPLICATE", "MOVE", "KEEP", "UNDATED", "SKIP"}
        values = {v for _, v in _SETTABLE_DECISIONS}
        assert not scanner_actions.intersection(values)


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

    def test_set_decision_called_with_keep_remove_action_passes_empty_string(self, qapp):
        """'keep (remove action)' in the Set Action submenu must call set_decision with ''."""
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
            (a for a in set_action_menu.actions() if "keep" in a.text().lower()), None
        )
        assert keep_action is not None, "No 'keep' action found in Set Action submenu"
        keep_action.trigger()

        mock_handlers.set_decision.assert_called_once_with([item], "")


class TestMultiSelectSetAction:
    """Multi-selection right-click must now expose Set Action (moved from File menu)."""

    def _make_handler(self, qapp):
        from PySide6.QtWidgets import QTreeView
        from app.views.handlers.context_menu import ContextMenuHandler
        handlers = MagicMock()
        return ContextMenuHandler(QTreeView(), MagicMock(), handlers, MagicMock()), handlers

    def test_multi_selection_menu_has_set_action_submenu(self, qapp):
        from app.views.handlers.context_menu import ContextMenuHandler
        from PySide6.QtWidgets import QMenu, QTreeView

        handler, _ = self._make_handler(qapp)
        items = [{"type": "file", "path": "/a.jpg"}, {"type": "file", "path": "/b.jpg"}]
        menu = QMenu()
        handler._create_multi_selection_menu(menu, items)

        submenu_texts = [a.text() for a in menu.actions() if a.menu()]
        assert any("Action" in t for t in submenu_texts), (
            "Multi-selection menu must include a 'Set Action' submenu"
        )

    def test_multi_selection_delete_calls_set_decision(self, qapp):
        from PySide6.QtWidgets import QMenu
        handler, mock_handlers = self._make_handler(qapp)
        items = [{"type": "file", "path": "/a.jpg"}, {"type": "file", "path": "/b.jpg"}]
        menu = QMenu()
        handler._create_multi_selection_menu(menu, items)

        set_action_action = next(
            (a for a in menu.actions() if a.menu() and "Action" in a.text()), None
        )
        assert set_action_action is not None
        set_action_menu = set_action_action.menu()
        delete_action = next(
            (a for a in set_action_menu.actions() if a.text() == "delete"), None
        )
        assert delete_action is not None
        delete_action.trigger()

        mock_handlers.set_decision.assert_called_once()
        call_args = mock_handlers.set_decision.call_args
        _items_arg, decision_arg = call_args[0]
        assert decision_arg == "delete"
        assert all(it["type"] == "file" for it in _items_arg)

    def test_multi_selection_keep_remove_passes_empty_string(self, qapp):
        from PySide6.QtWidgets import QMenu
        handler, mock_handlers = self._make_handler(qapp)
        items = [{"type": "file", "path": "/a.jpg"}, {"type": "file", "path": "/b.jpg"}]
        menu = QMenu()
        handler._create_multi_selection_menu(menu, items)

        set_action_action = next(
            (a for a in menu.actions() if a.menu() and "Action" in a.text()), None
        )
        assert set_action_action is not None
        set_action_menu = set_action_action.menu()
        keep_action = next(
            (a for a in set_action_menu.actions() if "keep" in a.text().lower()), None
        )
        assert keep_action is not None
        keep_action.trigger()

        mock_handlers.set_decision.assert_called_once()
        _items_arg, decision_arg = mock_handlers.set_decision.call_args[0]
        assert decision_arg == "", f"Expected '' but got {decision_arg!r}"


class TestClickedColumnPassthrough:
    """Clicked column index must be forwarded to show_select_dialog."""

    def test_show_select_dialog_receives_clicked_col(self, qapp):
        from PySide6.QtWidgets import QMenu, QTreeView
        from app.views.handlers.context_menu import ContextMenuHandler

        mock_handlers = MagicMock()
        handler = ContextMenuHandler(QTreeView(), MagicMock(), mock_handlers, MagicMock())

        menu = QMenu()
        handler._create_single_selection_menu(menu, {"type": "file", "path": "/a.jpg"},
                                               clicked_col=4)

        select_action = next(
            (a for a in menu.actions() if "Field/Regex" in a.text()), None
        )
        assert select_action is not None
        select_action.trigger()

        mock_handlers.show_select_dialog.assert_called_once_with(clicked_col=4)

    def test_show_select_dialog_defaults_col_none(self, qapp):
        from PySide6.QtWidgets import QMenu, QTreeView
        from app.views.handlers.context_menu import ContextMenuHandler

        mock_handlers = MagicMock()
        handler = ContextMenuHandler(QTreeView(), MagicMock(), mock_handlers, MagicMock())
        menu = QMenu()
        handler._create_single_selection_menu(menu, {"type": "file", "path": "/a.jpg"})

        select_action = next(
            (a for a in menu.actions() if "Field/Regex" in a.text()), None
        )
        assert select_action is not None
        select_action.trigger()

        mock_handlers.show_select_dialog.assert_called_once_with(clicked_col=None)


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
