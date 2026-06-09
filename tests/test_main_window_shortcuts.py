"""Layer-1 tests for the 'd' / 'k' QShortcut pair wired on the main result
tree in MainWindow._setup_tree_shortcuts (#615).

Each test catches a concrete bug class:

- test_tree_shortcuts_exist: a refactor that forgets to call
  _setup_tree_shortcuts() means the shortcuts are never created — both
  attributes would be absent and the user gets silently-broken keyboard
  shortcuts.
- test_shortcut_keys: a wrong QKeySequence (e.g. "Ctrl+D" instead of "D")
  would register the wrong accelerator; this pins the exact key.
- test_shortcut_context_is_widget_with_children: using Qt.WindowShortcut
  instead of Qt.WidgetWithChildrenShortcut would make the shortcut fire
  when focus is anywhere in the window (search boxes, dialogs) — the
  wrong-context bug can silently corrupt decisions typed elsewhere.
- test_shortcut_parent_is_tree: a shortcut parented to the main window
  (wrong) rather than the tree (correct) would use window-context
  regardless of the setContext() call because the parent overrides scope.
- test_d_activated_calls_set_decision_delete /
  test_k_activated_calls_set_decision_keep: if the lambda captures the
  wrong decision value ("" for delete, "delete" for keep) the shortcuts
  swap their actions — both would pass a naive type-check but the user
  experience is inverted.

These are NOT metric-gaming tests: each asserts a contract that, if
broken, produces a user-visible bug.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut

from app.viewmodels.main_vm import MainVM
from app.views.main_window import MainWindow


@pytest.fixture(scope="module")
def win(qapp):
    """One real MainWindow for the whole module — construction is slow (~25s).

    Closed + deleted in the module-level finalizer so qt object ownership
    is clean.
    """
    window = MainWindow(MainVM(), image_service=None, settings=None)
    yield window
    window.close()
    window.deleteLater()


class TestTreeShortcuts:
    def test_tree_shortcuts_exist(self, win):
        """Both shortcut attributes must be present after construction.

        Bug: _setup_tree_shortcuts() not called from _setup_ui() → shortcuts
        silently absent → 'd'/'k' keys do nothing.
        """
        assert hasattr(win, "_tree_shortcut_delete")
        assert hasattr(win, "_tree_shortcut_keep")

    def test_shortcut_types_are_qshortcut(self, win):
        """Shortcuts must be QShortcut instances, not some other binding."""
        assert isinstance(win._tree_shortcut_delete, QShortcut)
        assert isinstance(win._tree_shortcut_keep, QShortcut)

    def test_shortcut_keys(self, win):
        """'D' for delete, 'K' for keep — wrong key = silently wrong binding.

        Bug: QKeySequence("Ctrl+D") instead of QKeySequence("D") would not
        fire on a bare 'd' press.
        """
        assert win._tree_shortcut_delete.key().toString() == "D"
        assert win._tree_shortcut_keep.key().toString() == "K"

    def test_shortcut_context_is_widget_with_children(self, win):
        """Context must be WidgetWithChildrenShortcut.

        Bug: WindowShortcut (the default) makes 'd'/'k' fire whenever the
        main window is active — including when focus is in the search box,
        regex dialog, or any text-edit widget.
        """
        assert win._tree_shortcut_delete.context() == Qt.WidgetWithChildrenShortcut
        assert win._tree_shortcut_keep.context() == Qt.WidgetWithChildrenShortcut

    def test_shortcut_parent_is_tree(self, win):
        """Shortcuts must be parented to the tree widget, not the window.

        Bug: parenting to the main window means Qt uses window scope
        regardless of the setContext() call — the tree-scope restriction
        is then silently bypassed.
        """
        assert win._tree_shortcut_delete.parent() is win.tree
        assert win._tree_shortcut_keep.parent() is win.tree

    def test_d_activated_calls_set_decision_delete(self, win):
        """Emitting _tree_shortcut_delete.activated must call
        file_operations.set_decision_to_highlighted('delete').

        Bug: lambda captures wrong value → pressing 'd' clears decisions
        instead of marking them for delete.
        """
        with patch.object(win.file_operations, "set_decision_to_highlighted") as mock_fn:
            win._tree_shortcut_delete.activated.emit()
            mock_fn.assert_called_once_with("delete")

    def test_k_activated_calls_set_decision_keep(self, win):
        """Emitting _tree_shortcut_keep.activated must call
        file_operations.set_decision_to_highlighted('').

        Bug: lambda captures wrong value → pressing 'k' marks delete
        instead of clearing.
        """
        with patch.object(win.file_operations, "set_decision_to_highlighted") as mock_fn:
            win._tree_shortcut_keep.activated.emit()
            mock_fn.assert_called_once_with("")
