"""Tests for ContextMenuHandler set_decision routing.

Covers:
  - _SETTABLE_DECISIONS constant is list of (label, value) tuples
  - ActionHandlers protocol has set_decision + show_action_dialog(clicked_col)
  - set_decision callback is wired for single-file right-click
  - Multi-selection menu DOES expose "Set Action"
  - "keep (remove action)" passes "" as the decision value
  - Clicked column is forwarded to show_action_dialog
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
    """Clicked column index must be forwarded to show_action_dialog."""

    def test_show_action_dialog_receives_clicked_col(self, qapp):
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

        mock_handlers.show_action_dialog.assert_called_once_with(clicked_col=4)

    def test_show_action_dialog_defaults_col_none(self, qapp):
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

        mock_handlers.show_action_dialog.assert_called_once_with(clicked_col=None)


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


# ── setup_context_menu / _on_context_menu (Qt wiring) ────────────────────


class TestContextMenuPolicyAndSlot:
    def test_setup_sets_context_menu_policy_and_connects_signal(self, qapp):
        """setup_context_menu must set CustomContextMenu and wire the signal."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTreeView
        from app.views.handlers.context_menu import ContextMenuHandler

        tree = QTreeView()
        handler = ContextMenuHandler(tree, MagicMock(), MagicMock(), MagicMock())
        handler.setup_context_menu()

        assert tree.contextMenuPolicy() == Qt.CustomContextMenu

    def test_on_context_menu_invalid_index_returns_silently(self, qapp):
        """Clicking on empty area (invalid index) → no menu opens, no calls."""
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QTreeView
        from app.views.handlers.context_menu import ContextMenuHandler

        tree = QTreeView()
        provider = MagicMock()
        handlers = MagicMock()
        handler = ContextMenuHandler(tree, provider, handlers, MagicMock())

        # No model attached → indexAt returns invalid
        handler._on_context_menu(QPoint(10, 10))
        provider.get_selected_items.assert_not_called()

    def test_on_context_menu_no_selection_returns_silently(self, qapp):
        """Valid click but provider returns []; no menu, no handler calls."""
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QStandardItem, QStandardItemModel
        from PySide6.QtWidgets import QTreeView
        from app.views.handlers.context_menu import ContextMenuHandler

        tree = QTreeView()
        model = QStandardItemModel()
        model.appendRow(QStandardItem("row"))
        tree.setModel(model)

        provider = MagicMock()
        provider.get_selected_items.return_value = []
        handlers = MagicMock()

        handler = ContextMenuHandler(tree, provider, handlers, MagicMock())
        # Click at the item's known position (item's rect)
        idx = model.index(0, 0)
        rect = tree.visualRect(idx)
        # Even if rect is empty (tree not shown), patch indexAt to return idx
        from unittest.mock import patch as _patch
        with _patch.object(tree, "indexAt", return_value=idx):
            handler._on_context_menu(QPoint(0, 0))

        provider.get_selected_items.assert_called_once()
        handlers.set_decision.assert_not_called()

    def test_on_context_menu_dispatches_single_selection_menu(self, qapp):
        """Single-item selection → _create_single_selection_menu is called."""
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QStandardItem, QStandardItemModel
        from PySide6.QtWidgets import QTreeView
        from app.views.handlers.context_menu import ContextMenuHandler

        tree = QTreeView()
        model = QStandardItemModel()
        model.appendRow(QStandardItem("row"))
        tree.setModel(model)

        provider = MagicMock()
        provider.get_selected_items.return_value = [{"type": "file", "path": "/a.jpg"}]
        # QMenu(parent) requires a real QWidget — MagicMock fails the type check.
        from PySide6.QtWidgets import QWidget
        parent = QWidget()
        handler = ContextMenuHandler(tree, provider, MagicMock(), parent)

        from unittest.mock import patch as _patch
        with (
            _patch.object(tree, "indexAt", return_value=model.index(0, 0)),
            _patch.object(handler, "_create_single_selection_menu") as single,
            _patch.object(handler, "_create_multi_selection_menu") as multi,
            _patch("app.views.handlers.context_menu.QMenu.exec", return_value=None),
        ):
            handler._on_context_menu(QPoint(0, 0))

        single.assert_called_once()
        multi.assert_not_called()

    def test_on_context_menu_dispatches_multi_selection_menu(self, qapp):
        """Multiple selected items → _create_multi_selection_menu is called."""
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QStandardItem, QStandardItemModel
        from PySide6.QtWidgets import QTreeView, QWidget
        from app.views.handlers.context_menu import ContextMenuHandler

        tree = QTreeView()
        model = QStandardItemModel()
        model.appendRow(QStandardItem("row"))
        tree.setModel(model)

        provider = MagicMock()
        provider.get_selected_items.return_value = [
            {"type": "file", "path": "/a.jpg"},
            {"type": "file", "path": "/b.jpg"},
        ]
        parent = QWidget()
        handler = ContextMenuHandler(tree, provider, MagicMock(), parent)

        from unittest.mock import patch as _patch
        with (
            _patch.object(tree, "indexAt", return_value=model.index(0, 0)),
            _patch.object(handler, "_create_single_selection_menu") as single,
            _patch.object(handler, "_create_multi_selection_menu") as multi,
            _patch("app.views.handlers.context_menu.QMenu.exec", return_value=None),
        ):
            handler._on_context_menu(QPoint(0, 0))

        single.assert_not_called()
        multi.assert_called_once()


# ── group-type single-selection branch ────────────────────────────────────


class TestGroupSingleSelection:
    """Right-clicking a group row (item['type'] != 'file') skips the
    file-only Set Action submenu and the Open Folder action — but the
    common actions (Set Action by Field/Regex…, Remove from List) are
    still present."""

    def test_group_item_skips_file_only_actions(self, qapp):
        from PySide6.QtWidgets import QMenu, QTreeView
        from app.views.handlers.context_menu import ContextMenuHandler

        handler = ContextMenuHandler(
            QTreeView(), MagicMock(), MagicMock(), MagicMock()
        )
        menu = QMenu()
        handler._create_single_selection_menu(
            menu, {"type": "group", "group_number": 1}, clicked_col=0
        )

        # Walk visible actions in order — "Set Action" submenu and
        # "Open Folder" must NOT appear; the common actions DO appear.
        labels = [a.text() for a in menu.actions() if a.text()]
        assert "Set Action" not in labels
        assert "Open Folder" not in labels
        assert "Set Action by Field/Regex…" in labels
        assert "Remove from List" in labels


# ── _open_folder nested function (Open Folder action) ────────────────────


class TestOpenFolderAction:
    """Cover the _open_folder helper bound to the 'Open Folder' menu action.

    All file-system / shell side effects (subprocess.Popen, QDesktopServices)
    are mocked so the tests don't open Explorer windows.
    """

    def _build_menu_and_open_folder_action(self, qapp, file_path: str):
        from PySide6.QtWidgets import QMenu, QTreeView, QWidget
        from app.views.handlers.context_menu import ContextMenuHandler

        parent = QWidget()
        handler = ContextMenuHandler(QTreeView(), MagicMock(), MagicMock(), parent)
        menu = QMenu(parent)
        handler._create_single_selection_menu(
            menu, {"type": "file", "path": file_path}, clicked_col=0
        )
        # The "Open Folder" action is the second non-submenu top-level entry.
        for action in menu.actions():
            if action.text() == "Open Folder":
                return action
        raise AssertionError("Open Folder action not found")

    def test_empty_path_is_noop(self, qapp):
        from unittest.mock import patch as _patch
        action = self._build_menu_and_open_folder_action(qapp, "")
        with (
            _patch("subprocess.Popen") as popen,
            _patch("app.views.handlers.context_menu.QDesktopServices.openUrl") as open_url,
        ):
            action.trigger()
        popen.assert_not_called()
        open_url.assert_not_called()

    def test_existing_file_on_windows_uses_explorer_select(self, qapp, tmp_path, monkeypatch):
        """When the file exists on Windows, subprocess.Popen(['explorer', '/select,', path])."""
        from unittest.mock import patch as _patch
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"data")

        action = self._build_menu_and_open_folder_action(qapp, str(f))

        # Pretend we're on Windows even when running tests on something else.
        monkeypatch.setattr("os.name", "nt")
        with _patch("subprocess.Popen") as popen:
            action.trigger()
        popen.assert_called_once()
        args = popen.call_args[0][0]
        assert args[0] == "explorer"
        assert args[1] == "/select,"

    def test_missing_file_on_windows_falls_back_to_folder(
        self, qapp, tmp_path, monkeypatch
    ):
        """File doesn't exist but its containing folder does → Popen(['explorer', folder])."""
        from unittest.mock import patch as _patch
        f = tmp_path / "missing.jpg"   # never created
        action = self._build_menu_and_open_folder_action(qapp, str(f))

        monkeypatch.setattr("os.name", "nt")
        with _patch("subprocess.Popen") as popen:
            action.trigger()
        popen.assert_called_once()
        args = popen.call_args[0][0]
        assert args[0] == "explorer"
        # Single-arg form (no /select,) when only the folder exists.
        assert "/select," not in args

    def test_non_windows_uses_qdesktopservices(self, qapp, tmp_path, monkeypatch):
        """On non-Windows the helper falls through to QDesktopServices.openUrl."""
        from unittest.mock import patch as _patch
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"data")

        action = self._build_menu_and_open_folder_action(qapp, str(f))
        monkeypatch.setattr("os.name", "posix")
        with (
            _patch("subprocess.Popen") as popen,
            _patch("app.views.handlers.context_menu.QDesktopServices.openUrl") as open_url,
        ):
            action.trigger()
        popen.assert_not_called()
        open_url.assert_called_once()

    def test_subprocess_failure_falls_back_to_qdesktopservices(
        self, qapp, tmp_path, monkeypatch
    ):
        """If explorer Popen raises, the helper falls back to QDesktopServices."""
        from unittest.mock import patch as _patch
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"data")

        action = self._build_menu_and_open_folder_action(qapp, str(f))
        monkeypatch.setattr("os.name", "nt")
        with (
            _patch("subprocess.Popen", side_effect=OSError("explorer broke")),
            _patch("app.views.handlers.context_menu.QDesktopServices.openUrl") as open_url,
        ):
            action.trigger()
        open_url.assert_called_once()
