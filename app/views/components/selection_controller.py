"""SelectionController: Handles file/group selection operations and checkbox management."""

from __future__ import annotations

import re
from typing import Any, Protocol

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from loguru import logger

from app.views.constants import COL_GROUP, COL_NAME, COL_SEL, PATH_ROLE
from app.views.selection_service import apply_select_regex


class StatusReporter(Protocol):
    """Protocol for status reporting callback."""

    def show_status(self, message: str, timeout: int = 3000) -> None:
        """Show status message."""
        ...


class SelectionController:
    """Handles file/group selection operations and checkbox management.

    This class encapsulates all selection-related functionality including:
    - Gathering checked file paths
    - Selecting/unselecting files based on items
    - Applying regex-based selection
    - Expanding group selections to individual files
    """

    def __init__(
        self,
        tree_controller,  # TreeController instance
        vm: Any,  # ViewModel instance
        status_reporter: StatusReporter | None = None,
    ) -> None:
        """Initialize with tree controller and view model.

        Args:
            tree_controller: TreeController instance for model access
            vm: ViewModel instance for group access
            status_reporter: Optional callback for status messages
        """
        self.tree_ctrl = tree_controller
        self.vm = vm
        self.status_reporter = status_reporter

    def gather_checked_paths(self) -> list[str]:
        """Get all file paths that are currently checked.

        Returns:
            List of file paths for checked items
        """
        model = self.tree_ctrl.model
        if model is None:
            return []

        paths: list[str] = []
        root_count = model.rowCount()
        for r in range(root_count):
            parent_item = model.item(r, COL_GROUP)
            if parent_item is None:
                continue
            child_count = parent_item.rowCount()
            for cr in range(child_count):
                check_item = parent_item.child(cr, COL_SEL)
                name_item = parent_item.child(cr, COL_NAME)
                if check_item and check_item.checkState() == Qt.Checked and name_item:
                    p = name_item.data(PATH_ROLE)
                    if p:
                        paths.append(p)
        return paths

    def select_files(self, items: list[dict]) -> int:
        """Mark selected files as checked (set sel checkbox).

        Args:
            items: List of items with 'type' ('file'|'group') and 'path'/'group_number'

        Returns:
            Number of files that were marked as selected
        """
        try:
            model = self.tree_ctrl.model
            if model is None:
                return 0

            # Get all file paths from items (including files within selected groups)
            file_paths = self._expand_items_to_file_paths(items)

            if not file_paths:
                return 0

            # Mark files as checked in the model
            checked_count = self._update_file_check_states(file_paths, Qt.Checked)

            logger.info("Marked {} files as selected", checked_count)
            if self.status_reporter:
                self.status_reporter.show_status(f"Marked {checked_count} file(s) as selected")

            return checked_count

        except Exception as e:
            logger.error("Select files failed: {}", e)
            return 0

    def unselect_files(self, items: list[dict]) -> int:
        """Mark selected files as unchecked (unset sel checkbox).

        Args:
            items: List of items with 'type' ('file'|'group') and 'path'/'group_number'

        Returns:
            Number of files that were marked as unselected
        """
        try:
            model = self.tree_ctrl.model
            if model is None:
                return 0

            # Get all file paths from items (including files within selected groups)
            file_paths = self._expand_items_to_file_paths(items)

            if not file_paths:
                return 0

            # Mark files as unchecked in the model
            unchecked_count = self._update_file_check_states(file_paths, Qt.Unchecked)

            logger.info("Unmarked {} files as unselected", unchecked_count)
            if self.status_reporter:
                self.status_reporter.show_status(
                    f"Unmarked {unchecked_count} file(s) as unselected"
                )

            return unchecked_count

        except Exception as e:
            logger.error("Unselect files failed: {}", e)
            return 0

    def apply_regex_selection(
        self, field: str, pattern: str, make_checked: bool, parent_widget: Any = None
    ) -> None:
        """Apply regex-based selection to files.

        Args:
            field: Field name to match against
            pattern: Regex pattern to apply
            make_checked: Whether to check (True) or uncheck (False) matches
            parent_widget: Parent widget for error dialogs
        """
        model = self.tree_ctrl.model
        if model is None:
            return

        try:
            # Validate regex first for consistent UX
            re.compile(pattern)
        except Exception:
            if parent_widget:
                QMessageBox.warning(parent_widget, "Regex", "Invalid regular expression.")
            return

        try:
            apply_select_regex(model, field, pattern, make_checked)
        except Exception:
            # Best effort; keep silent to avoid UX disruption
            pass

    def _expand_items_to_file_paths(self, items: list[dict]) -> list[str]:
        """Expand items (files and groups) to a list of file paths.

        Args:
            items: List of items with 'type' and relevant identifiers

        Returns:
            List of file paths
        """
        file_paths = []
        for item in items:
            if item["type"] == "file":
                file_paths.append(item["path"])
            elif item["type"] == "group":
                # Get all files in the group
                for g in self.vm.groups:
                    if g.group_number == item["group_number"]:
                        file_paths.extend([f.file_path for f in g.items])
        return file_paths

    def _update_file_check_states(self, file_paths: list[str], check_state: Qt.CheckState) -> int:
        """Update check states for the given file paths.

        Args:
            file_paths: List of file paths to update
            check_state: New check state (Qt.Checked or Qt.Unchecked)

        Returns:
            Number of files that were updated
        """
        model = self.tree_ctrl.model
        if model is None:
            return 0

        updated_count = 0
        root_count = model.rowCount()
        for r in range(root_count):
            parent_item = model.item(r, COL_GROUP)
            if parent_item is None:
                continue
            child_count = parent_item.rowCount()
            for cr in range(child_count):
                name_item = parent_item.child(cr, COL_NAME)
                check_item = parent_item.child(cr, COL_SEL)
                if name_item and check_item:
                    file_path = name_item.data(PATH_ROLE)
                    if file_path in file_paths:
                        check_item.setCheckState(check_state)
                        updated_count += 1

        return updated_count
