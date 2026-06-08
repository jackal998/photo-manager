"""TreeController: Manages tree view operations and model management."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtWidgets import QHeaderView, QTreeView
from loguru import logger

from app.views.constants import (
    COL_ACTION,
    COL_GROUP,
    COL_LOCK,
    COL_NAME,
    NUM_COLUMNS,
    PATH_ROLE,
    SORT_ROLE,
)
from app.views.tree_model_builder import (
    _DECISION_SORT,
    _action_display,
    _lock_display,
    build_model,
)


class TreeController:
    """Manages tree view operations, model management, and item selection.

    This class encapsulates all tree-related functionality including:
    - Model building and management
    - Sort state preservation
    - Item selection and extraction
    - Header configuration
    """

    def __init__(self, tree_view: QTreeView) -> None:
        """Initialize with a QTreeView instance.

        Args:
            tree_view: The QTreeView widget to manage
        """
        self.tree = tree_view
        self._model = None
        self._proxy = None
        self._current_sort_column: int = COL_GROUP
        self._current_sort_order: Qt.SortOrder = Qt.AscendingOrder

    def setup_tree_properties(self) -> None:
        """Configure tree view properties and behavior."""
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        self.tree.setSelectionMode(QTreeView.ExtendedSelection)
        # Stop Qt auto-scrolling the viewport to the clicked cell on every
        # selection change. With autoScroll on, clicking a row whose cells
        # extend past the viewport made the view jerk horizontally to "align"
        # the clicked column into view — disorienting on a wide table. This
        # only gates the implicit scrollTo(current) inside currentChanged();
        # our deliberate scrollTo() calls (re-select after manifest load /
        # post-scan auto-select) still scroll the target into view. Trade-off:
        # drag-select near a viewport edge no longer auto-scrolls, negligible
        # for this fully-expanded ExtendedSelection tree.
        self.tree.setAutoScroll(False)
        # Take over double-click handling — our dispatcher routes file rows
        # to the OS viewer (#143) and group rows to toggle expand. Leaving
        # Qt's default on would race our own setExpanded() call, producing
        # a no-op (expand → our toggle re-collapses → user sees nothing).
        self.tree.setExpandsOnDoubleClick(False)

    def setup_double_click(self, file_open_handler: callable) -> None:
        """Wire the ``doubleClicked`` signal to a row-type dispatcher (#143).

        Args:
            file_open_handler: Callback for file rows, invoked with the
                absolute file path. Group-row double-clicks toggle the
                tree's own expand state internally — they don't reach the
                handler.

        Routing:
            - **File row** (``index.parent().isValid()``) → call
              ``file_open_handler(path)`` if we can resolve a path.
            - **Group header row** → toggle ``tree.isExpanded(index)``.
              We have to do this ourselves because ``setup_tree_properties``
              disables Qt's default ``setExpandsOnDoubleClick`` so the file
              branch can act without a race.

        The dispatcher swallows exceptions — a stray Qt index from a
        racing model rebuild shouldn't crash the UI for a non-essential
        action.
        """
        try:
            self.tree.doubleClicked.connect(
                lambda idx: self._on_double_click(idx, file_open_handler)
            )
        except Exception:
            pass

    def _on_double_click(self, index, file_open_handler: callable) -> None:
        """Dispatch a doubleClicked signal by row type. See setup_double_click."""
        try:
            if not index.isValid():
                return
            if index.parent().isValid():
                # File row — hand the path to the caller-supplied opener
                # (kept out of this class so the controller stays UI-only).
                path = self.get_file_path_from_index(index)
                if path:
                    file_open_handler(path)
            else:
                # Group header row — toggle expand state. Map the proxy
                # index onto itself; ``isExpanded`` / ``setExpanded`` both
                # take the view index, NOT the source index.
                self.tree.setExpanded(index, not self.tree.isExpanded(index))
        except Exception as e:
            logger.error("Error dispatching double-click: {}", e)

    def setup_header_behavior(self, header_click_handler: callable) -> None:
        """Setup header interactions and connect click handler.

        Args:
            header_click_handler: Callback for header clicks with signature (int) -> None
        """
        try:
            header = self.tree.header()
            header.setSectionsMovable(True)
            header.setStretchLastSection(False)
            header.setSectionsClickable(True)
            header.setSectionResizeMode(QHeaderView.Interactive)
            # Track sort changes to preserve order after refresh
            header.sectionClicked.connect(header_click_handler)
        except Exception:
            pass

    def connect_layout_change_signal(self, callback: Callable[[], None]) -> None:
        """Fire ``callback`` when the user moves or resizes a column.

        ``sectionMoved(int, int, int)`` and ``sectionResized(int, int, int)``
        are emitted by ``QHeaderView`` on every user-driven drag/resize.
        The callback is invoked with no arguments — it's the persistence
        trigger, not a layout consumer; the receiver pulls the current
        state via ``save_column_state``.

        Programmatic ``resizeColumnToContents`` calls inside
        ``refresh_model`` also fire ``sectionResized``. That's fine for
        the save-on-change use case — the auto-sized widths become the
        new "user-current" state until the user resizes manually, which
        is the same outcome as not having persistence at all on first
        launch.
        """
        try:
            header = self.tree.header()
            header.sectionMoved.connect(lambda *_: callback())
            header.sectionResized.connect(lambda *_: callback())
        except Exception as exc:
            logger.error("Failed to connect header layout-change signals: {}", exc)

    def save_column_state(self, store, key: str) -> None:
        """Persist the current header layout (visual order + widths) to QSettings.

        Wraps ``QHeaderView.saveState()`` — an opaque bytes blob whose
        format is Qt-version-specific but stable across launches of the
        same Qt build. Also writes a sibling ``{key}/section_count``
        sentinel so :meth:`restore_column_state` can detect a column-
        schema change and fall back to defaults rather than apply a
        mis-sized blob.

        Callers typically invoke this from a ``sectionMoved`` /
        ``sectionResized`` handler wired up via
        :meth:`connect_layout_change_signal`.

        Args:
            store: A ``QSettings`` instance (caller owns the path / format).
            key: The QSettings key under which to store the state bytes.
        """
        try:
            header = self.tree.header()
            state = header.saveState()
            store.setValue(key, state)
            store.setValue(f"{key}/section_count", header.count())
        except Exception as exc:
            logger.error("Failed to save column state: {}", exc)

    def restore_column_state(self, store, key: str) -> bool:
        """Restore the saved header layout, or skip when incompatible.

        Calling order matters: ``refresh_model`` runs a
        ``ResizeToContents → Interactive`` cycle that silently overwrites
        any previously-restored widths, so the restore must happen
        AFTER ``refresh_model`` returns.

        Section-count guard: ``QHeaderView.restoreState()`` accepts a
        blob from a different column count and silently produces a
        broken layout (hidden columns, mis-aligned widths). We compare
        the section count saved alongside the blob against the live
        header's section count and skip the restore on mismatch — a
        future column addition then falls back cleanly to the auto-
        sized defaults rather than presenting a malformed table.
        Encoding the count as a sidecar key (rather than parsing it out
        of Qt's opaque saveState blob) keeps us Qt-version-independent.

        Args:
            store: A ``QSettings`` instance.
            key: The QSettings key holding the state bytes.

        Returns:
            ``True`` if the saved state was restored, ``False`` if the
            key was absent, the blob was unreadable, or the section
            count didn't match the current header.
        """
        try:
            raw = store.value(key)
            if raw is None:
                return False
            if isinstance(raw, (bytes, bytearray)):
                state = QByteArray(bytes(raw))
            elif isinstance(raw, QByteArray):
                state = raw
            else:
                return False
            if state.isEmpty():
                return False
            header = self.tree.header()
            saved_count_raw = store.value(f"{key}/section_count")
            if saved_count_raw is not None:
                try:
                    saved_count = int(saved_count_raw)
                except (TypeError, ValueError):
                    saved_count = -1
                if saved_count != header.count():
                    logger.info(
                        "Saved column state has {} sections, header has {} — "
                        "skipping restore, falling back to defaults.",
                        saved_count, header.count(),
                    )
                    return False
            return bool(header.restoreState(state))
        except Exception as exc:
            logger.error("Failed to restore column state: {}", exc)
            return False

    def refresh_model(self, groups: list) -> None:
        """Build and set the tree model, preserving sort order.

        Args:
            groups: List of group objects to display in the tree
        """
        # Capture prior refs before overwriting so we can schedule teardown
        # after the new model is installed (#618).
        old_proxy = getattr(self, '_proxy', None)
        old_model = getattr(self, '_model', None)

        model, proxy = build_model(groups)
        if proxy is not None:
            proxy.setParent(self.tree)
            self.tree.setModel(proxy)
            self._proxy = proxy
            self._model = model
            # Preserve the current sort order instead of resetting to default
            self.tree.sortByColumn(self._current_sort_column, self._current_sort_order)
        else:
            self.tree.setModel(model)
            self._proxy = None
            self._model = model

        # Tear down prior model+proxy AFTER setModel detached them from the view.
        # Order: setSourceModel(None) first to sever the proxy→model reference,
        # then setParent(None) to remove the proxy from self.tree's Qt children
        # (without this the proxy stays in tree.children() until the tree is
        # destroyed, keeping all ~163k QStandardItem alive — the #618 leak).
        # deleteLater() defers the final C++ destruction to the next event-loop
        # iteration, safely past any in-flight dataChanged / signal delivery.
        # See feedback_qt_timer_teardown_uaf for the Qt UAF rationale.
        # (#618 — root cause of #614's 8-10 GB residency).
        if old_proxy is not None:
            old_proxy.setSourceModel(None)
            old_proxy.setParent(None)
            old_proxy.deleteLater()
        if old_model is not None and old_model is not model:
            old_model.deleteLater()

        # Expand all first so content-based width accounts for children
        try:
            self.tree.expandAll()
        except Exception:
            pass

        # Auto size columns to contents, then leave interactive for user drag.
        # Block header signals during the resize cycle so the
        # ``sectionResized`` listener (used to persist the user's column
        # widths) doesn't fire on every programmatic ResizeToContents step
        # and clobber the saved layout with the auto-sized widths.
        try:
            header = self.tree.header()
            blocked = header.blockSignals(True)
            try:
                for i in range(NUM_COLUMNS):
                    header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
                self.tree.doItemsLayout()
                for i in range(NUM_COLUMNS):
                    header.setSectionResizeMode(i, QHeaderView.Interactive)
            finally:
                header.blockSignals(blocked)
        except Exception:
            for i in range(NUM_COLUMNS):
                self.tree.resizeColumnToContents(i)

    def reconnect_selection_handler(self, selection_handler: callable) -> None:
        """Reconnect selection change handler after model reset.

        Args:
            selection_handler: Callback for selection changes
        """
        self.tree.selectionModel().selectionChanged.connect(selection_handler)

    def calculate_tree_width(self) -> int:
        """Calculate the total width needed for the tree view.

        Returns:
            Total width in pixels needed for all columns plus margins
        """
        try:
            return sum(self.tree.columnWidth(i) for i in range(NUM_COLUMNS)) + 24
        except Exception:
            return 400  # Fallback width

    def get_selected_items(self) -> list[dict]:
        """Get currently selected items (files and groups).

        Returns:
            List of dicts with keys 'type' ('file'|'group'), 'path' (for files),
            or 'group_number' (for groups)
        """
        selected_items = []
        try:
            selection_model = self.tree.selectionModel()
            if not selection_model:
                return selected_items

            selected_indexes = selection_model.selectedRows()
            for index in selected_indexes:
                if index.parent().isValid():
                    # File row
                    file_path = self.get_file_path_from_index(index)
                    if file_path:
                        selected_items.append({"type": "file", "path": file_path})
                else:
                    # Group row
                    group_number = self.get_group_number_from_index(index)
                    if group_number is not None:
                        selected_items.append({"type": "group", "group_number": group_number})
        except Exception as e:
            logger.error("Error gathering selected items: {}", e)
        return selected_items

    def get_file_path_from_index(self, index) -> str | None:
        """Extract file path from tree index.

        Args:
            index: QModelIndex pointing to a file row

        Returns:
            File path string or None if not found/invalid
        """
        try:
            view_model = self.tree.model()
            src_model = self._model
            proxy = self._proxy

            # Handle proxy model
            if proxy is not None and hasattr(proxy, "mapToSource"):
                src_index = proxy.mapToSource(index)
                model = src_model
                idx = src_index
            else:
                model = view_model
                idx = index

            if idx.isValid() and idx.parent().isValid():
                # This is a file row - get the path from the name column
                name_index = model.index(idx.row(), COL_NAME, idx.parent())
                return model.data(name_index, PATH_ROLE)
        except Exception as e:
            logger.error("Error getting file path from index: {}", e)
        return None

    def get_group_number_from_index(self, index) -> int | None:
        """Extract group number from tree index.

        Args:
            index: QModelIndex pointing to a group row

        Returns:
            Group number or None if not found/invalid
        """
        try:
            view_model = self.tree.model()
            src_model = self._model
            proxy = self._proxy

            # Handle proxy model
            if proxy is not None and hasattr(proxy, "mapToSource"):
                src_index = proxy.mapToSource(index)
                model = src_model
                idx = src_index
            else:
                model = view_model
                idx = index

            if idx.isValid() and not idx.parent().isValid():
                # This is a group row - try to get group number from SORT_ROLE first
                group_index = model.index(idx.row(), COL_GROUP, idx.parent())

                # Try SORT_ROLE first (most reliable)
                group_num = model.data(group_index, SORT_ROLE)
                if group_num is not None:
                    logger.debug("Got group number from SORT_ROLE: {}", group_num)
                    return int(group_num)

                # Fallback to parsing display text
                group_text = model.data(group_index, Qt.DisplayRole)
                logger.debug("Group text from index: '{}'", group_text)

                if group_text and isinstance(group_text, str) and group_text.startswith("Group "):
                    try:
                        group_num = int(group_text.split(" ")[1])
                        logger.debug("Extracted group number from text: {}", group_num)
                        return group_num
                    except (IndexError, ValueError) as e:
                        logger.error("Failed to parse group number from '{}': {}", group_text, e)
                else:
                    logger.warning("Invalid group text format: '{}'", group_text)
        except Exception as e:
            logger.error("Error getting group number from index: {}", e)
        return None

    def update_sort_state(self, logical_index: int, sort_order: Qt.SortOrder) -> None:
        """Update current sort state for preservation across refreshes.

        Args:
            logical_index: Column index that was clicked
            sort_order: New sort order (Ascending/Descending)
        """
        self._current_sort_column = logical_index
        self._current_sort_order = sort_order
        logger.debug("Sort state updated - Column: {}, Order: {}", logical_index, sort_order)

    def get_current_sort_state(self) -> tuple[int, Qt.SortOrder]:
        """Get current sort column and order.

        Returns:
            Tuple of (column_index, sort_order)
        """
        return self._current_sort_column, self._current_sort_order

    def update_decision_cells(
        self, changes: list[tuple[int, int, str]]
    ) -> None:
        """Update COL_ACTION display text + SORT_ROLE for changed file rows.

        ``changes`` is a list of ``(group_idx, member_idx, new_decision)``
        tuples — the coords come from FileOperationsHandler's path index
        and correspond to positions in the source model (not the proxy).

        Skips post-rebuild side effects (restore_column_state,
        reconnect_selection_handler, expandAll, ResizeToContents) that are
        only needed when the model is fully replaced.  Group-level
        SORT_ROLE aggregates are NOT updated here — that would require
        reading all sibling rows.  set_decision_by_regex stays on the
        full-rebuild path for exactly this reason.
        """
        model = self._model
        if model is None:
            return
        for g_i, m_i, decision in changes:
            try:
                group_item = model.item(g_i, COL_GROUP)
                if group_item is None:
                    continue
                action_item = group_item.child(m_i, COL_ACTION)
                if action_item is None:
                    continue
                action_item.setText(_action_display(decision))
                action_item.setData(_DECISION_SORT.get(decision, 3), SORT_ROLE)
            except Exception as exc:
                logger.error("update_decision_cells failed at ({}, {}): {}", g_i, m_i, exc)

    def update_lock_cells(
        self, changes: list[tuple[int, int, bool]]
    ) -> None:
        """Update COL_LOCK display glyph + SORT_ROLE for changed file rows.

        ``changes`` is a list of ``(group_idx, member_idx, locked)`` tuples.
        Same incremental pattern as :meth:`update_decision_cells` — no full
        rebuild, no expandAll, no ResizeToContents.
        """
        model = self._model
        if model is None:
            return
        for g_i, m_i, locked in changes:
            try:
                group_item = model.item(g_i, COL_GROUP)
                if group_item is None:
                    continue
                lock_item = group_item.child(m_i, COL_LOCK)
                if lock_item is None:
                    continue
                lock_item.setText(_lock_display(locked))
                lock_item.setData(1 if locked else 0, SORT_ROLE)
            except Exception as exc:
                logger.error("update_lock_cells failed at ({}, {}): {}", g_i, m_i, exc)

    @property
    def model(self):
        """Get the current source model."""
        return self._model

    @property
    def proxy(self):
        """Get the current proxy model."""
        return self._proxy
