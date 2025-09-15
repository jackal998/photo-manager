"""FileOperationsHandler: Handles file-related operations like CSV import/export and deletion."""

from __future__ import annotations

from typing import Any, Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox
from loguru import logger


class UIUpdateCallback(Protocol):
    """Protocol for UI update callbacks."""

    def refresh_tree(self, groups: list) -> None:
        """Refresh the tree view with new groups."""
        ...

    def show_group_counts(self, count: int) -> None:
        """Show group count (legacy compatibility)."""
        ...

    def show_groups_summary(self, groups: list) -> None:
        """Show groups summary (legacy compatibility)."""
        ...


class StatusReporter(Protocol):
    """Protocol for status reporting callback."""

    def show_status(self, message: str, timeout: int = 3000) -> None:
        """Show status message."""
        ...


class FileOperationsHandler:
    """Handles file-related operations including CSV import/export and file deletion.

    This class encapsulates all file operation workflows including:
    - CSV import with error handling
    - CSV export with validation
    - File deletion with confirmation
    - List manipulation (remove from list)
    """

    def __init__(
        self,
        vm: Any,
        repo: Any,
        delete_service: Any,
        settings: Any,
        parent_widget: QObject,
        ui_updater: UIUpdateCallback,
        status_reporter: StatusReporter,
    ) -> None:
        """Initialize with required services and callbacks.

        Args:
            vm: ViewModel instance for data operations
            repo: Repository instance for CSV operations
            delete_service: Delete service for file deletion
            settings: Settings instance for configuration
            parent_widget: Parent widget for dialogs
            ui_updater: Callback for UI updates
            status_reporter: Callback for status messages
        """
        self.vm = vm
        self.repo = repo
        self.deleter = delete_service
        self.settings = settings
        self.parent = parent_widget
        self.ui_updater = ui_updater
        self.status_reporter = status_reporter

    def import_csv(self) -> None:
        """Handle CSV import with file dialog and error handling."""
        path, _ = QFileDialog.getOpenFileName(self.parent, "Import CSV", "", "CSV Files (*.csv)")
        if not path:
            return

        try:
            self.vm.load_csv(path)
            self.ui_updater.show_group_counts(self.vm.group_count)
            self.ui_updater.show_groups_summary(self.vm.groups)
            self.ui_updater.refresh_tree(self.vm.groups)

            logger.info(
                "Imported CSV: {} | groups={} items={}",
                path,
                self.vm.group_count,
                sum(len(g.items) for g in self.vm.groups),
            )
            self.status_reporter.show_status(f"Imported {self.vm.group_count} groups")

        except Exception as ex:
            logger.exception("Import CSV failed: {}", ex)
            QMessageBox.critical(self.parent, "Import Error", str(ex))
            self.status_reporter.show_status("Import failed")

    def export_csv(self) -> None:
        """Handle CSV export with validation and error handling."""
        if not self.vm.groups:
            QMessageBox.information(self.parent, "Export", "No data to export.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self.parent, "Export CSV", "export.csv", "CSV Files (*.csv)"
        )
        if not path:
            return

        try:
            self.repo.save(path, self.vm.groups)
            logger.info(
                "Exported CSV: {} | groups={} items={} (bytes correct)",
                path,
                self.vm.group_count,
                sum(len(g.items) for g in self.vm.groups),
            )
            QMessageBox.information(self.parent, "Export", "Export completed.")
            self.status_reporter.show_status("Export completed")

        except Exception as ex:
            logger.exception("Export CSV failed: {}", ex)
            QMessageBox.critical(self.parent, "Export Error", str(ex))
            self.status_reporter.show_status("Export failed")

    def delete_selected_files(self, selected_paths: list[str]) -> None:
        """Handle file deletion workflow with confirmation and cleanup.

        Args:
            selected_paths: List of file paths to delete
        """
        if not self.deleter:
            QMessageBox.information(self.parent, "Delete", "Delete service not available.")
            return

        if not selected_paths:
            QMessageBox.information(self.parent, "Delete", "No items checked.")
            return

        try:
            from app.views.dialogs.delete_confirm_dialog import DeleteConfirmDialog

            plan = self.deleter.plan_delete(self.vm.groups, selected_paths)

            # Show confirmation dialog if enabled
            if self.settings and bool(self.settings.get("delete.confirm_group_full_delete", True)):
                dlg = DeleteConfirmDialog(plan.group_summaries, self.parent)
                if dlg.exec() != QDialog.Accepted:
                    return

            result = self.deleter.execute_delete(self.vm.groups, plan)

            # Handle success notifications
            if result.success_paths:
                log_path = getattr(result, "log_path", "")
                self.status_reporter.show_status(
                    f"Deleted {len(result.success_paths)} items. Log: {log_path}", timeout=5000
                )

                try:
                    # Best-effort info dialog for success (optional)
                    QMessageBox.information(
                        self.parent,
                        "Delete",
                        f"Deleted {len(result.success_paths)} items.\nLog: {log_path}",
                    )
                except Exception:
                    pass

            # Handle failure notifications
            if result.failed:
                QMessageBox.warning(
                    self.parent, "Delete", f"Failed: {len(result.failed)} items. See log."
                )

            # Update VM: remove deleted files and prune groups
            try:
                if result.success_paths:
                    self.vm.remove_deleted_and_prune(result.success_paths)
                    self.ui_updater.refresh_tree(self.vm.groups)
            except Exception:
                pass

            # Prompt to update source CSV after list actions completed
            self._prompt_csv_update_after_delete(result.success_paths)

        except Exception as ex:
            logger.error("Delete selected files failed: {}", ex)
            QMessageBox.critical(self.parent, "Error", f"Delete failed: {str(ex)}")

    def remove_from_list_toolbar(
        self, checked_paths: list[str], highlighted_items: list[dict]
    ) -> None:
        """Remove selected files or groups from the list via toolbar.

        Args:
            checked_paths: List of checked file paths
            highlighted_items: List of highlighted items (files/groups)
        """
        try:
            # First try to get checked files
            if checked_paths:
                logger.info("Removing {} checked files from list via toolbar", len(checked_paths))
                self.vm.remove_from_list(checked_paths)
                self.ui_updater.refresh_tree(self.vm.groups)
                self.status_reporter.show_status(f"Removed {len(checked_paths)} file(s) from list")
                return

            # If no checked files, try to get currently highlighted rows
            if highlighted_items:
                logger.info(
                    "Removing {} highlighted items from list via toolbar", len(highlighted_items)
                )
                file_paths = [item for item in highlighted_items if item.get("type") == "file"]
                group_numbers = [item for item in highlighted_items if item.get("type") == "group"]

                if file_paths:
                    paths = [item["path"] for item in file_paths]
                    self.vm.remove_from_list(paths)

                if group_numbers:
                    for item in group_numbers:
                        self.vm.remove_group_from_list(item["group_number"])

                self.ui_updater.refresh_tree(self.vm.groups)
                self.status_reporter.show_status("Removed items from list")
                return

            QMessageBox.information(
                self.parent,
                "Remove from List",
                "No items selected. Please check files or select rows first.",
            )

        except Exception as e:
            logger.error("Remove from list via toolbar failed: {}", e)
            QMessageBox.critical(self.parent, "Error", f"Remove from list failed: {str(e)}")

    def remove_items_from_list(self, items: list[dict]) -> None:
        """Remove multiple items (files and/or groups) from the list.

        Args:
            items: List of items with 'type' ('file'|'group') and relevant identifiers
        """
        try:
            file_paths = []
            group_numbers = []

            # Separate files and groups
            for item in items:
                if item["type"] == "file":
                    file_paths.append(item["path"])
                elif item["type"] == "group":
                    group_numbers.append(item["group_number"])

            # Remove files first
            if file_paths:
                logger.info("Removing {} files from list", len(file_paths))
                self.vm.remove_from_list(file_paths)

            # Remove groups
            if group_numbers:
                logger.info("Removing {} groups from list", len(group_numbers))
                for group_num in group_numbers:
                    self.vm.remove_group_from_list(group_num)

            self.ui_updater.refresh_tree(self.vm.groups)

            total_removed = len(file_paths) + len(group_numbers)
            self.status_reporter.show_status(f"Removed {total_removed} item(s) from list")

        except Exception as e:
            logger.error("Remove items from list failed: {}", e)
            QMessageBox.critical(self.parent, "Error", f"Remove from list failed: {str(e)}")

    def delete_files(self, items: list[dict]) -> None:
        """Delete multiple files from the given items list.

        Args:
            items: List of items containing files to delete
        """
        try:
            if not self.deleter:
                QMessageBox.information(self.parent, "Delete", "Delete service not available.")
                return

            # Extract file paths from items
            file_paths = []
            for item in items:
                if item["type"] == "file":
                    file_paths.append(item["path"])

            if not file_paths:
                QMessageBox.information(self.parent, "Delete", "No files to delete.")
                return

            logger.info("Deleting {} files from context menu", len(file_paths))
            result = self.deleter.delete_to_recycle(file_paths)

            if result.success_paths:
                self.vm.remove_deleted_and_prune(result.success_paths)
                self.ui_updater.refresh_tree(self.vm.groups)
                self.status_reporter.show_status(f"Deleted {len(result.success_paths)} file(s)")

            if result.failed:
                failed_msg = f"Failed to delete {len(result.failed)} file(s)"
                QMessageBox.warning(self.parent, "Delete", failed_msg)

        except Exception as e:
            logger.error("Delete files failed: {}", e)
            QMessageBox.critical(self.parent, "Error", f"Delete failed: {str(e)}")

    def _prompt_csv_update_after_delete(self, success_paths: list[str]) -> None:
        """Prompt user to update source CSV after successful delete operation.

        Args:
            success_paths: List of successfully deleted file paths
        """
        try:
            if success_paths:
                src = getattr(self.vm, "get_source_csv_path", lambda: None)()
                if src:
                    resp = QMessageBox.question(
                        self.parent, "Update CSV?", f"Update source CSV file?\n{src}"
                    )
                    if resp == QMessageBox.Yes:
                        self.vm.export_csv(src)
                        self.status_reporter.show_status("CSV updated")
        except Exception as ex:
            logger.error("Update CSV after delete failed: {}", ex)
