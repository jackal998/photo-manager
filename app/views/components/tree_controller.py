"""TreeController: Manages tree view operations and model management."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QTreeView
from loguru import logger

from app.views.constants import (
    COL_GROUP,
    COL_NAME,
    NUM_COLUMNS,
    PATH_ROLE,
    SORT_ROLE,
)
from app.views.tree_model_builder import build_model


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

    def refresh_model(self, groups: list) -> None:
        """Build and set the tree model, preserving sort order.

        Args:
            groups: List of group objects to display in the tree
        """
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

        # Expand all first so content-based width accounts for children
        try:
            self.tree.expandAll()
        except Exception:
            pass

        # Auto size columns to contents, then leave interactive for user drag
        try:
            header = self.tree.header()
            for i in range(NUM_COLUMNS):
                header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
            self.tree.doItemsLayout()
            for i in range(NUM_COLUMNS):
                header.setSectionResizeMode(i, QHeaderView.Interactive)
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

    @property
    def model(self):
        """Get the current source model."""
        return self._model

    @property
    def proxy(self):
        """Get the current proxy model."""
        return self._proxy
