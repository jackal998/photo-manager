"""Tests for the results-tree double-click dispatcher (#143).

Covers ``TreeController.setup_double_click`` + ``_on_double_click``:
  - File rows (``index.parent().isValid()``) call the file-open handler
    with the resolved path; group rows do NOT.
  - Group header rows toggle ``tree.isExpanded`` and do NOT call the
    file-open handler.
  - Empty/invalid indices and missing paths are silent no-ops (no
    handler call, no crash).
  - ``setup_tree_properties`` flips ``setExpandsOnDoubleClick`` off so
    the dispatcher owns group-row toggle without racing Qt's default.

Does NOT test the OS-level open behavior — that's a boundary covered
by ``test_file_opener.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def _rec(file_path: str) -> SimpleNamespace:
    """Build a minimal record matching tree_model_builder's attribute reads."""
    return SimpleNamespace(
        file_path=file_path,
        folder_path="/tmp",
        file_size_bytes=100,
        action="MOVE",
        user_decision="",
        is_locked=False,
        hamming_distance=None,
        shot_date=None,
        creation_date=None,
        pixel_width=None,
        pixel_height=None,
    )


def _build_controller_with_two_groups(qapp):
    """Build a TreeController populated with two groups, each holding one file.

    Returns ``(controller, group0_idx, group1_idx, file_idx_in_group0)``
    as **view** indices (mapped through the proxy if present).
    """
    from PySide6.QtWidgets import QTreeView
    from app.views.components.tree_controller import TreeController

    tree = QTreeView()
    controller = TreeController(tree)
    controller.setup_tree_properties()

    group_a = SimpleNamespace(group_number=1, items=[_rec("/tmp/a.jpg")])
    group_b = SimpleNamespace(group_number=2, items=[_rec("/tmp/b.jpg")])
    controller.refresh_model([group_a, group_b])

    view_model = tree.model()
    # Group header rows are at the root.
    group0 = view_model.index(0, 0)
    group1 = view_model.index(1, 0)
    # First file row under the first group.
    file0 = view_model.index(0, 0, group0)
    return controller, group0, group1, file0


class TestSetupTreeProperties:
    """The dispatcher relies on Qt's built-in double-click expansion
    being OFF — otherwise group toggles race our setExpanded call."""

    def test_disables_qt_default_double_click_expand(self, qapp):
        from PySide6.QtWidgets import QTreeView
        from app.views.components.tree_controller import TreeController

        tree = QTreeView()
        assert tree.expandsOnDoubleClick() is True  # Qt's default
        controller = TreeController(tree)
        controller.setup_tree_properties()
        assert tree.expandsOnDoubleClick() is False


class TestFileRowRouting:
    """Double-clicking a file row hands its path to the supplied handler."""

    def test_file_row_calls_handler_with_path(self, qapp):
        controller, _g0, _g1, file0 = _build_controller_with_two_groups(qapp)
        handler = MagicMock()

        controller._on_double_click(file0, handler)

        handler.assert_called_once_with("/tmp/a.jpg")

    def test_file_row_with_unresolvable_path_does_not_call_handler(self, qapp):
        """If get_file_path_from_index returns None/empty, the handler
        must not be called — a stale index shouldn't trip a no-op
        OS-spawn attempt downstream."""
        controller, _g0, _g1, file0 = _build_controller_with_two_groups(qapp)
        controller.get_file_path_from_index = MagicMock(return_value=None)
        handler = MagicMock()

        controller._on_double_click(file0, handler)

        handler.assert_not_called()


class TestGroupRowRouting:
    """Double-clicking a group header row toggles its expand state and
    does NOT call the file-open handler."""

    def test_group_row_toggles_expand_state(self, qapp):
        controller, group0, _g1, _file0 = _build_controller_with_two_groups(qapp)
        # refresh_model expandAll's, so the group starts expanded.
        assert controller.tree.isExpanded(group0) is True
        handler = MagicMock()

        controller._on_double_click(group0, handler)
        assert controller.tree.isExpanded(group0) is False

        controller._on_double_click(group0, handler)
        assert controller.tree.isExpanded(group0) is True

        handler.assert_not_called()

    def test_group_row_independent_of_other_groups(self, qapp):
        """Toggling group 0 must not affect group 1's expand state."""
        controller, group0, group1, _file0 = _build_controller_with_two_groups(qapp)
        handler = MagicMock()

        controller._on_double_click(group0, handler)

        assert controller.tree.isExpanded(group0) is False
        assert controller.tree.isExpanded(group1) is True


class TestInvalidIndexHandling:
    """A doubleClicked signal with an invalid index (e.g. blank area)
    must be a silent no-op — no handler call, no exception."""

    def test_invalid_index_is_noop(self, qapp):
        from PySide6.QtCore import QModelIndex
        controller, _g0, _g1, _file0 = _build_controller_with_two_groups(qapp)
        handler = MagicMock()

        controller._on_double_click(QModelIndex(), handler)

        handler.assert_not_called()


class TestSignalWiring:
    """setup_double_click must connect the tree's doubleClicked signal
    so the dispatcher actually fires for user input."""

    def test_setup_double_click_wires_signal(self, qapp):
        controller, _g0, _g1, file0 = _build_controller_with_two_groups(qapp)
        handler = MagicMock()
        controller.setup_double_click(handler)

        controller.tree.doubleClicked.emit(file0)

        handler.assert_called_once_with("/tmp/a.jpg")
