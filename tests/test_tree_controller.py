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


# ── setup_tree_properties ──────────────────────────────────────────────────


class TestSetupTreeProperties:
    def test_autoscroll_disabled(self, qapp):
        """Clicking a row must not jerk the viewport to align the clicked
        column. setup_tree_properties turns Qt's implicit auto-scroll off;
        if this regresses the wide-table horizontal jump returns."""
        from app.views.components.tree_controller import TreeController

        tree = QTreeView()
        TreeController(tree).setup_tree_properties()
        assert tree.hasAutoScroll() is False


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


# ── column-layout persistence (#214) ───────────────────────────────────────


def _ini_store(tmp_path):
    """Build a real-file ``QSettings`` so the round-trip exercises the
    same serialisation Qt uses in production (no in-memory mock that
    could let a Qt-vs-Python type mismatch slip through)."""
    from PySide6.QtCore import QSettings
    return QSettings(str(tmp_path / "state.ini"), QSettings.IniFormat)


class TestColumnStateRoundTrip:
    """``save_column_state`` → ``restore_column_state`` must return the
    user's visual section order. This is the headline bug from #214 —
    if it doesn't round-trip, every restart resets the layout."""

    KEY = "geometry/column_header"

    def test_round_trip_preserves_visual_order(self, qapp, tmp_path):
        from app.views.constants import NUM_COLUMNS

        controller, _vm = _build(qapp)
        header = controller.tree.header()
        # Move logical column 0 to visual position 3 — a non-trivial
        # rearrangement that survives ONLY if both saveState() and
        # restoreState() are wired correctly.
        header.moveSection(0, 3)
        before_order = [header.visualIndex(i) for i in range(NUM_COLUMNS)]
        store = _ini_store(tmp_path)
        controller.save_column_state(store, self.KEY)
        store.sync()

        # Fresh controller + tree to simulate a relaunch — but reuse the
        # same store file. The new header starts in default visual order.
        controller2, _vm2 = _build(qapp)
        ok = controller2.restore_column_state(store, self.KEY)
        assert ok is True
        header2 = controller2.tree.header()
        after_order = [header2.visualIndex(i) for i in range(NUM_COLUMNS)]
        assert after_order == before_order

    def test_round_trip_preserves_column_width(self, qapp, tmp_path):
        """The other half of #214 — column widths must also survive."""
        controller, _vm = _build(qapp)
        controller.tree.header().resizeSection(2, 333)
        store = _ini_store(tmp_path)
        controller.save_column_state(store, self.KEY)
        store.sync()

        controller2, _vm2 = _build(qapp)
        assert controller2.restore_column_state(store, self.KEY) is True
        assert controller2.tree.header().sectionSize(2) == 333

    def test_missing_key_returns_false(self, qapp, tmp_path):
        """First launch: nothing saved yet. Must fall back to defaults
        without raising — restoring a missing key is the normal path."""
        controller, _vm = _build(qapp)
        store = _ini_store(tmp_path)
        assert controller.restore_column_state(store, self.KEY) is False

    def test_section_count_mismatch_skips_restore(self, qapp, tmp_path):
        """Future-proof against new column additions (issue #214 Notes).
        A saved state from when the schema had fewer/more sections
        must be ignored rather than silently producing a broken layout.
        """
        controller, _vm = _build(qapp)
        header = controller.tree.header()
        header.moveSection(0, 3)
        store = _ini_store(tmp_path)
        controller.save_column_state(store, self.KEY)
        # Forge a schema drift: rewrite the section_count sidecar key
        # as if it had been saved with a different schema. The blob
        # itself is untouched — the count guard is what protects us.
        store.setValue(f"{self.KEY}/section_count", 99)
        store.sync()

        controller2, _vm2 = _build(qapp)
        # Default visual order must be unchanged after the skipped restore.
        from app.views.constants import NUM_COLUMNS
        default_order = [
            controller2.tree.header().visualIndex(i) for i in range(NUM_COLUMNS)
        ]
        assert controller2.restore_column_state(store, self.KEY) is False
        after = [
            controller2.tree.header().visualIndex(i) for i in range(NUM_COLUMNS)
        ]
        assert after == default_order

    def test_save_writes_section_count_sentinel(self, qapp, tmp_path):
        """The section_count sidecar is the protection against schema
        drift. If it's not saved, the mismatch guard can't fire."""
        from app.views.constants import NUM_COLUMNS

        controller, _vm = _build(qapp)
        store = _ini_store(tmp_path)
        controller.save_column_state(store, self.KEY)
        store.sync()
        assert int(store.value(f"{self.KEY}/section_count")) == NUM_COLUMNS


class TestLayoutChangeSignalConnection:
    """The persistence trigger MUST fire on both ``sectionMoved`` and
    ``sectionResized`` — losing either signal means the user's drag /
    resize disappears on next launch."""

    def test_section_moved_fires_callback(self, qapp):
        controller, _vm = _build(qapp)
        calls: list[int] = []
        controller.connect_layout_change_signal(lambda: calls.append(1))
        controller.tree.header().moveSection(0, 2)
        assert calls, "moveSection did not trigger save callback"

    def test_section_resized_fires_callback(self, qapp):
        controller, _vm = _build(qapp)
        calls: list[int] = []
        controller.connect_layout_change_signal(lambda: calls.append(1))
        controller.tree.header().resizeSection(1, 250)
        assert calls, "resizeSection did not trigger save callback"

    def test_refresh_model_does_not_fire_callback(self, qapp):
        """``refresh_model``'s internal ResizeToContents cycle MUST NOT
        fire the save callback — otherwise every manifest reload would
        overwrite the user's saved widths with auto-sized defaults
        (the #214 fix's biggest regression risk).
        """
        from types import SimpleNamespace

        controller, _vm = _build(qapp)
        calls: list[int] = []
        controller.connect_layout_change_signal(lambda: calls.append(1))
        # Refresh with a different groups list — triggers the full
        # ResizeToContents → Interactive cycle inside refresh_model.
        new_groups = [
            SimpleNamespace(group_number=99, items=[_rec("/photos/z.jpg")]),
        ]
        controller.refresh_model(new_groups)
        assert calls == [], (
            f"refresh_model fired save callback {len(calls)} times; "
            f"signal-blocking around the resize cycle is broken."
        )


class TestUpdateDecisionCells:
    """Incremental cell update path (#613) — set_decision / set_locked_state
    patch only the changed (group_idx, member_idx) coords on the existing
    model instead of rebuilding the whole QStandardItemModel.  These tests
    pin the COL_ACTION text + SORT_ROLE contract that file_operations.py
    relies on; if the mapping breaks, sorted-by-action views silently
    re-order on every right-click.
    """

    def test_updates_action_cell_text_and_sort_role(self, qapp):
        from app.views.constants import COL_ACTION, COL_GROUP, SORT_ROLE
        from app.views.tree_model_builder import _DECISION_SORT, _action_display

        controller, _vm = _build(qapp)
        # Group 0 has a.jpg at member 0, b.jpg at member 1.
        controller.update_decision_cells([(0, 0, "delete")])

        group_item = controller.model.item(0, COL_GROUP)
        action_item = group_item.child(0, COL_ACTION)
        assert action_item.text() == _action_display("delete")
        assert action_item.data(SORT_ROLE) == _DECISION_SORT["delete"]
        # Sibling untouched.
        sibling = group_item.child(1, COL_ACTION)
        assert sibling.text() == _action_display("")
        assert sibling.data(SORT_ROLE) == 3  # "" → 3 (between keep and ignore)

    def test_clear_decision_resets_action_cell(self, qapp):
        """Setting decision back to '' (keep) must clear the text AND reset
        SORT_ROLE — otherwise a row marked delete then keep stays sorted as
        if it were still delete."""
        from app.views.constants import COL_ACTION, COL_GROUP, SORT_ROLE
        from app.views.tree_model_builder import _action_display

        controller, _vm = _build(qapp)
        controller.update_decision_cells([(0, 0, "delete")])
        controller.update_decision_cells([(0, 0, "")])

        group_item = controller.model.item(0, COL_GROUP)
        action_item = group_item.child(0, COL_ACTION)
        assert action_item.text() == _action_display("")
        assert action_item.data(SORT_ROLE) == 3

    def test_empty_changes_list_is_noop(self, qapp):
        """No changes → no model mutation, no exception."""
        controller, _vm = _build(qapp)
        controller.update_decision_cells([])  # must not raise

    def test_out_of_range_group_index_is_skipped(self, qapp):
        """Bad (group_idx, member_idx) coords from a stale path index
        must not crash — the early `if … is None` guard logs and continues."""
        controller, _vm = _build(qapp)
        # Group 999 does not exist (controller has 2 groups).
        controller.update_decision_cells([(999, 0, "delete")])  # must not raise


class TestUpdateLockCells:
    """Incremental lock-cell update (#613) — orthogonal to decision; lock
    glyph + SORT_ROLE on COL_LOCK.  Catches: wrong column index, missing
    SORT_ROLE update, broken _lock_display mapping.
    """

    def test_lock_glyph_and_sort_role(self, qapp):
        from app.views.constants import COL_GROUP, COL_LOCK, SORT_ROLE
        from app.views.tree_model_builder import _lock_display

        controller, _vm = _build(qapp)
        controller.update_lock_cells([(0, 0, True)])

        group_item = controller.model.item(0, COL_GROUP)
        lock_item = group_item.child(0, COL_LOCK)
        assert lock_item.text() == _lock_display(True)
        assert lock_item.data(SORT_ROLE) == 1

    def test_unlock_clears_glyph(self, qapp):
        from app.views.constants import COL_GROUP, COL_LOCK, SORT_ROLE
        from app.views.tree_model_builder import _lock_display

        controller, _vm = _build(qapp)
        controller.update_lock_cells([(0, 0, True)])
        controller.update_lock_cells([(0, 0, False)])

        group_item = controller.model.item(0, COL_GROUP)
        lock_item = group_item.child(0, COL_LOCK)
        assert lock_item.text() == _lock_display(False)
        assert lock_item.data(SORT_ROLE) == 0

    def test_out_of_range_member_index_is_skipped(self, qapp):
        """Member index past the group's child count must not crash."""
        controller, _vm = _build(qapp)
        # Group 0 has 2 members; index 99 is out of range.
        controller.update_lock_cells([(0, 99, True)])  # must not raise


class TestRemoveRows:
    """Incremental row removal (#630) — the structural mirror of
    update_decision_cells. ``remove_rows`` must drop matched file
    children from their group, drop the whole group when it becomes
    empty, leave sibling rows untouched (no model rebuild), and update
    the group-header COL_GROUP_COUNT text so the visible "(N files)"
    doesn't lie about membership.

    Catches the exact regressions filed in #630: callers swapping
    ``ui_updater.refresh_tree`` for the incremental path silently
    falling back to a no-op or rebuilding the whole model — both would
    invalidate the QTreeView's selection model + expanded-group state
    that #617 worked to preserve.
    """

    def test_removes_single_child_keeping_group(self, qapp):
        """Removing one of two children: group stays, COL_GROUP_COUNT
        drops from 2 to 1, sibling row survives unchanged.
        """
        from app.views.constants import COL_GROUP, COL_GROUP_COUNT, COL_NAME, PATH_ROLE

        controller, _vm = _build(qapp)
        model = controller.model
        # Sanity: group 0 starts with 2 children, group count text is "2".
        group0 = model.item(0, COL_GROUP)
        assert group0.rowCount() == 2
        assert model.item(0, COL_GROUP_COUNT).text() == "2"

        controller.remove_rows({"/photos/a.jpg"})

        assert model.rowCount() == 2  # both groups still present
        group0 = model.item(0, COL_GROUP)
        assert group0.rowCount() == 1  # one child dropped
        # Surviving child is b.jpg (not a.jpg).
        surviving = group0.child(0, COL_NAME).data(PATH_ROLE)
        assert surviving == "/photos/b.jpg"
        # COL_GROUP_COUNT text reflects the new count.
        assert model.item(0, COL_GROUP_COUNT).text() == "1"

    def test_removes_only_child_drops_group(self, qapp):
        """Removing the only child of a group must also remove the
        group header — leaving an empty group would lie about
        duplicate-set membership and confuse downstream consumers.
        """
        from app.views.constants import COL_GROUP

        controller, _vm = _build(qapp)
        model = controller.model
        assert model.rowCount() == 2  # 2 groups initially
        # Group 1 has c.jpg as its only child.

        controller.remove_rows({"/photos/c.jpg"})

        assert model.rowCount() == 1  # group 1 dropped entirely
        # Surviving group is the original group 0 (with a.jpg + b.jpg).
        assert model.item(0, COL_GROUP).rowCount() == 2

    def test_removes_paths_across_multiple_groups(self, qapp):
        """A single call can prune rows from several groups + drop
        any group that becomes empty.
        """
        from app.views.constants import COL_GROUP, COL_NAME, PATH_ROLE

        controller, _vm = _build(qapp)
        model = controller.model

        # Remove a.jpg from group 0 AND c.jpg (only child of group 1).
        controller.remove_rows({"/photos/a.jpg", "/photos/c.jpg"})

        assert model.rowCount() == 1  # only group 0 survives
        surviving_group = model.item(0, COL_GROUP)
        assert surviving_group.rowCount() == 1
        assert surviving_group.child(0, COL_NAME).data(PATH_ROLE) == "/photos/b.jpg"

    def test_empty_set_is_noop(self, qapp):
        """Passing an empty set must not mutate the model at all —
        defensive guard against callers that compute an empty paths
        list (e.g. user-confirms-zero-rows flow).
        """
        controller, _vm = _build(qapp)
        before_rows = controller.model.rowCount()
        controller.remove_rows(set())
        assert controller.model.rowCount() == before_rows

    def test_unknown_path_is_silently_ignored(self, qapp):
        """A stale path that no longer exists in the model must not
        crash — robust to duplicate-removal attempts (e.g. user
        re-clicks Remove after an undo / reload race).
        """
        controller, _vm = _build(qapp)
        before_rows = controller.model.rowCount()
        controller.remove_rows({"/nowhere/missing.jpg"})  # must not raise
        assert controller.model.rowCount() == before_rows

    def test_does_not_rebuild_model(self, qapp):
        """The incremental path must NOT swap to a new model — the
        existing model object id must survive. Catches a refactor
        that accidentally calls build_model again.
        """
        controller, _vm = _build(qapp)
        model_id_before = id(controller.model)
        controller.remove_rows({"/photos/a.jpg"})
        assert id(controller.model) == model_id_before


class TestRefreshModelTeardown:
    """Each refresh_model call must release the previous proxy + model (#618).

    Without explicit teardown, every reload leaks the entire prior tree state
    to Qt's parent-child object tree — ~163k QStandardItem per typical 13k-row
    reload, accumulating until app close (root cause of #614's 8-10 GB residency
    report).

    Assertion strategy: we use `tree.children()` membership as the primary
    observable — if the old proxy remains in tree.children() after refresh, Qt's
    parent-child relationship keeps it alive along with all its descendant items.
    After the fix, setParent(None) removes the proxy from tree.children()
    immediately (synchronous), then deleteLater() schedules the C++ destruction.
    The PySide6 `destroyed` signal does not fire reliably for deleteLater() in
    headless tests, so we do not use it here.
    """

    def test_old_proxy_removed_from_tree_children_after_refresh(self, qapp):
        """The old proxy must NOT remain in tree.children() after refresh_model.

        Without the #618 fix, old_proxy.setParent(self.tree) was never undone,
        so every refresh added a new child but kept all prior proxies in
        tree.children() forever — each proxy kept its full QStandardItemModel
        alive.
        """
        controller, _ = _build(qapp)
        first_proxy = controller._proxy
        assert first_proxy is not None
        assert first_proxy in controller.tree.children(), (
            "pre-condition: newly built proxy must be a Qt child of the tree view"
        )

        new_groups = [
            SimpleNamespace(group_number=99, items=[_rec("/photos/z.jpg")]),
        ]
        controller.refresh_model(new_groups)

        assert first_proxy not in controller.tree.children(), (
            "old proxy must be removed from tree.children() after refresh_model; "
            "without the #618 fix the proxy lingers as a Qt child of the tree "
            "view forever, keeping ~163k QStandardItems alive"
        )
        # The new proxy should now be in children.
        assert controller._proxy in controller.tree.children()
        assert controller._proxy is not first_proxy

    def test_multiple_refreshes_only_current_proxy_in_tree_children(self, qapp):
        """After N refreshes, exactly the current proxy (and no prior ones)
        must be in tree.children() — guards against a partial fix where only
        the first old proxy is removed."""
        controller, _ = _build(qapp)

        intermediate_proxies = []
        for i in range(5):
            intermediate_proxies.append(controller._proxy)
            new_groups = [
                SimpleNamespace(
                    group_number=100 + i,
                    items=[_rec(f"/photos/r{i}.jpg")],
                ),
            ]
            controller.refresh_model(new_groups)

        tree_children = controller.tree.children()
        for idx, old_proxy in enumerate(intermediate_proxies):
            assert old_proxy not in tree_children, (
                f"intermediate proxy #{idx} from before refresh {idx+1} is "
                f"still in tree.children() — that proxy keeps its entire "
                f"QStandardItemModel subtree alive (the #618 leak)"
            )
        # Only the final current proxy should be a tree child.
        assert controller._proxy in tree_children
