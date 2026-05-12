"""Tests for ``TreeController`` — selection extraction + sort state.

Covers the genuinely-testable orchestration logic:
  - ``get_selected_items`` returns the {type, path/group_number} contract
    used by context_menu and action_handlers.
  - ``get_file_path_from_index`` / ``get_group_number_from_index`` —
    the proxy/source-model mapping and SORT_ROLE → DisplayRole fallback
    on group number resolution.
  - Sort state round-trip (update_sort_state / get_current_sort_state)
    that survives ``refresh_model`` rebuilds.

Not in scope:
  - Width calculation defaults, header setup, expandAll, column resize.
    These are pure Qt orchestration whose only failure mode is "Qt was
    called incorrectly" — caught by every qa-explore scenario at the
    visible-layout level.
  - The double-click dispatcher: covered by
    ``tests/test_tree_controller_double_click.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeView


def _rec(file_path: str = "/photos/a.jpg", **overrides) -> SimpleNamespace:
    """Build a minimal record matching tree_model_builder's attribute reads."""
    base = dict(
        file_path=file_path,
        folder_path="/photos",
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
    base.update(overrides)
    return SimpleNamespace(**base)


def _build(qapp, groups=None):
    """Build a TreeController populated with the given groups (or two by default).

    Returns ``(controller, view_model)``. The view_model is the proxy
    after refresh_model so callers can build view indices.
    """
    from app.views.components.tree_controller import TreeController

    if groups is None:
        groups = [
            SimpleNamespace(
                group_number=1,
                items=[_rec("/photos/a.jpg"), _rec("/photos/b.jpg")],
            ),
            SimpleNamespace(group_number=2, items=[_rec("/photos/c.jpg")]),
        ]
    tree = QTreeView()
    controller = TreeController(tree)
    controller.setup_tree_properties()
    controller.refresh_model(groups)
    return controller, tree.model()


# ── get_selected_items ─────────────────────────────────────────────────────


class TestGetSelectedItems:
    def test_no_selection_returns_empty(self, qapp):
        controller, _vm = _build(qapp)
        assert controller.get_selected_items() == []

    def test_file_selection_returns_file_dict(self, qapp):
        controller, view_model = _build(qapp)
        group0 = view_model.index(0, 0)
        file0 = view_model.index(0, 0, group0)
        from PySide6.QtCore import QItemSelectionModel
        controller.tree.selectionModel().select(
            file0,
            QItemSelectionModel.Select | QItemSelectionModel.Rows,
        )
        items = controller.get_selected_items()
        assert len(items) == 1
        assert items[0]["type"] == "file"
        assert items[0]["path"] == "/photos/a.jpg"

    def test_group_selection_returns_group_dict(self, qapp):
        controller, view_model = _build(qapp)
        group0 = view_model.index(0, 0)
        from PySide6.QtCore import QItemSelectionModel
        controller.tree.selectionModel().select(
            group0,
            QItemSelectionModel.Select | QItemSelectionModel.Rows,
        )
        items = controller.get_selected_items()
        assert len(items) == 1
        assert items[0]["type"] == "group"
        assert items[0]["group_number"] == 1


# ── get_file_path_from_index ───────────────────────────────────────────────


class TestGetFilePathFromIndex:
    def test_file_row_returns_path(self, qapp):
        controller, view_model = _build(qapp)
        group0 = view_model.index(0, 0)
        file0 = view_model.index(0, 0, group0)
        # Path returned should match the record we built (the first file in group 0).
        # Sort by group asc so order is deterministic.
        assert controller.get_file_path_from_index(file0) in {
            "/photos/a.jpg",
            "/photos/b.jpg",
        }

    def test_group_row_returns_none(self, qapp):
        controller, view_model = _build(qapp)
        group0 = view_model.index(0, 0)
        assert controller.get_file_path_from_index(group0) is None

    def test_invalid_index_returns_none(self, qapp):
        from PySide6.QtCore import QModelIndex
        controller, _vm = _build(qapp)
        assert controller.get_file_path_from_index(QModelIndex()) is None


# ── get_group_number_from_index ────────────────────────────────────────────


class TestGetGroupNumberFromIndex:
    def test_group_row_returns_number(self, qapp):
        controller, view_model = _build(qapp)
        group0 = view_model.index(0, 0)
        group1 = view_model.index(1, 0)
        # We don't assume a specific ordering — but the two groups must
        # resolve to {1, 2}.
        nums = {
            controller.get_group_number_from_index(group0),
            controller.get_group_number_from_index(group1),
        }
        assert nums == {1, 2}

    def test_file_row_returns_none(self, qapp):
        controller, view_model = _build(qapp)
        group0 = view_model.index(0, 0)
        file0 = view_model.index(0, 0, group0)
        assert controller.get_group_number_from_index(file0) is None

    def test_invalid_index_returns_none(self, qapp):
        from PySide6.QtCore import QModelIndex
        controller, _vm = _build(qapp)
        assert controller.get_group_number_from_index(QModelIndex()) is None


# ── sort state ─────────────────────────────────────────────────────────────


class TestSortStateRoundTrip:
    def test_default_state(self, qapp):
        controller, _vm = _build(qapp)
        col, order = controller.get_current_sort_state()
        # Default initialised to COL_GROUP / Ascending in __init__.
        from app.views.constants import COL_GROUP
        assert col == COL_GROUP
        assert order == Qt.AscendingOrder

    def test_update_round_trip(self, qapp):
        controller, _vm = _build(qapp)
        controller.update_sort_state(3, Qt.DescendingOrder)
        col, order = controller.get_current_sort_state()
        assert col == 3
        assert order == Qt.DescendingOrder

    def test_sort_state_survives_refresh(self, qapp):
        """A refresh_model rebuild MUST preserve the user's last sort
        column / order — otherwise every manifest reload would jump
        the user back to default sort.
        """
        controller, _vm = _build(qapp)
        controller.update_sort_state(5, Qt.DescendingOrder)
        # New groups, same controller
        new_groups = [
            SimpleNamespace(group_number=42, items=[_rec("/photos/x.jpg")]),
        ]
        controller.refresh_model(new_groups)
        col, order = controller.get_current_sort_state()
        assert col == 5
        assert order == Qt.DescendingOrder


# ── properties ─────────────────────────────────────────────────────────────


class TestProperties:
    def test_model_and_proxy_set_after_refresh(self, qapp):
        controller, _vm = _build(qapp)
        # refresh_model assigns _model (source) and _proxy (sort/filter wrapper)
        assert controller.model is not None
        assert controller.proxy is not None


# ── header / selection wiring ─────────────────────────────────────────────


class TestHeaderAndSelectionWiring:
    def test_setup_header_behavior_connects_handler(self, qapp):
        """Header click signal must fire the supplied handler with the
        logical column index — preserves sort across refresh_model rebuilds."""
        controller, _vm = _build(qapp)
        received: list[int] = []
        controller.setup_header_behavior(received.append)
        controller.tree.header().sectionClicked.emit(2)
        assert received == [2]

    def test_reconnect_selection_handler_fires_on_change(self, qapp):
        """Used by main_window after every refresh_model to re-bind the
        selectionChanged → on_tree_selection_changed wire (the model
        reset invalidates the previous connection)."""
        from PySide6.QtCore import QItemSelectionModel
        controller, view_model = _build(qapp)
        received: list[int] = []
        controller.reconnect_selection_handler(
            lambda *args: received.append(1)
        )
        group0 = view_model.index(0, 0)
        controller.tree.selectionModel().select(
            group0,
            QItemSelectionModel.Select | QItemSelectionModel.Rows,
        )
        assert received  # handler fired at least once


# ── calculate_tree_width ───────────────────────────────────────────────────


class TestCalculateTreeWidth:
    def test_basic_sum_includes_margin(self, qapp):
        """Total width = sum(columnWidth) + 24-px margin. Used by
        layout_manager.adjust_splitter_for_tree on first manifest load
        to size the tree pane to its content."""
        from app.views.constants import NUM_COLUMNS
        controller, _vm = _build(qapp)
        expected = sum(
            controller.tree.columnWidth(i) for i in range(NUM_COLUMNS)
        ) + 24
        assert controller.calculate_tree_width() == expected


# ── get_group_number_from_index — DisplayRole fallback ────────────────────


class TestGetGroupNumberFromIndexFallback:
    """SORT_ROLE is the primary path; if it's ever absent (older
    builds, mocked models in tests, future refactor that forgets to
    set the role), the parser must still recover the group number
    from the display text 'Group N'."""

    def test_falls_back_to_display_role_when_sort_role_missing(self, qapp):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QStandardItem, QStandardItemModel
        from app.views.components.tree_controller import TreeController
        from app.views.constants import COL_GROUP, NUM_COLUMNS

        # Build a hand-rolled model whose group-row COL_GROUP cell has
        # DisplayRole='Group 7' but NO SORT_ROLE set. Mirrors the
        # degenerate case the fallback exists for.
        model = QStandardItemModel()
        group_row = [QStandardItem("") for _ in range(NUM_COLUMNS)]
        group_row[COL_GROUP] = QStandardItem("Group 7")
        model.appendRow(group_row)

        tree = QTreeView()
        controller = TreeController(tree)
        # Assign directly so the controller knows source==view (no proxy).
        controller._model = model
        controller._proxy = None
        tree.setModel(model)

        idx = model.index(0, 0)
        assert controller.get_group_number_from_index(idx) == 7

    def test_unparseable_display_text_returns_none(self, qapp):
        """If both SORT_ROLE is absent AND the display text doesn't
        match the 'Group N' shape (corruption, locale mismatch with no
        SORT_ROLE), the resolver returns None rather than guessing."""
        from PySide6.QtGui import QStandardItem, QStandardItemModel
        from app.views.components.tree_controller import TreeController
        from app.views.constants import COL_GROUP, NUM_COLUMNS

        model = QStandardItemModel()
        group_row = [QStandardItem("") for _ in range(NUM_COLUMNS)]
        group_row[COL_GROUP] = QStandardItem("Bogus header")
        model.appendRow(group_row)

        tree = QTreeView()
        controller = TreeController(tree)
        controller._model = model
        controller._proxy = None
        tree.setModel(model)

        idx = model.index(0, 0)
        assert controller.get_group_number_from_index(idx) is None
