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
        delete_service: Any,
        settings: Any,
        parent_widget: QObject,
        ui_updater: UIUpdateCallback,
        status_reporter: StatusReporter,
        checked_paths_provider: object | None = None,
        highlighted_items_provider: object | None = None,
    ) -> None:
        self.vm = vm
        self.deleter = delete_service
        self.settings = settings
        self.parent = parent_widget
        self.ui_updater = ui_updater
        self.status_reporter = status_reporter
        # Optional callable or object with gather_checked_paths() to pull UI state
        self.checked_paths_provider = checked_paths_provider
        # Optional callable returning list[dict] of highlighted (tree-selected) items
        self.highlighted_items_provider = highlighted_items_provider

    def import_manifest(self) -> None:
        """Open a migration_manifest.sqlite and load REVIEW_DUPLICATE groups."""
        path, _ = QFileDialog.getOpenFileName(
            self.parent, "Open Manifest", "", "SQLite Files (*.sqlite *.db);;All Files (*)"
        )
        if not path:
            return

        try:
            from infrastructure.manifest_repository import ManifestRepository
            manifest_repo = ManifestRepository()
            self.vm.load_from_repo(manifest_repo, path)
            self.ui_updater.show_group_counts(self.vm.group_count)
            self.ui_updater.show_groups_summary(self.vm.groups)
            self.ui_updater.refresh_tree(self.vm.groups)

            # Enable manifest-dependent menu actions
            self._manifest_path = path
            _manifest_actions = (
                "save_manifest", "execute_action",
                "set_action_hl_delete", "set_action_hl_keep",
                "set_action_sel_delete", "set_action_sel_keep",
            )
            for _act in _manifest_actions:
                try:
                    self.parent.menu_controller.enable_action(_act, True)
                except AttributeError:
                    pass

            n_groups = self.vm.group_count
            n_items = sum(len(g.items) for g in self.vm.groups)
            logger.info("Opened manifest: {} | groups={} items={}", path, n_groups, n_items)
            self.status_reporter.show_status(
                f"Opened manifest: {n_groups} pairs to review ({n_items} files)"
            )

        except Exception as ex:
            logger.exception("Open manifest failed: {}", ex)
            QMessageBox.critical(self.parent, "Open Manifest Error", str(ex))
            self.status_reporter.show_status("Open manifest failed")

    def save_manifest_decisions(self) -> None:
        """Export current decisions to a (possibly new) manifest file."""
        import os
        import shutil

        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            QMessageBox.information(self.parent, "Save Manifest", "No manifest open.")
            return

        save_path, _ = QFileDialog.getSaveFileName(
            self.parent,
            "Save Manifest Decisions",
            manifest_path,
            "SQLite Files (*.sqlite *.db);;All Files (*)",
        )
        if not save_path:
            return

        try:
            # Sync marks from UI checkboxes before saving
            checked_paths: list[str] = []
            provider = self.checked_paths_provider
            if provider is not None:
                if callable(provider):
                    checked_paths = provider()
                elif hasattr(provider, "gather_checked_paths"):
                    checked_paths = provider.gather_checked_paths()
            if hasattr(self.vm, "update_marks_from_checked_paths"):
                self.vm.update_marks_from_checked_paths(checked_paths)

            # Copy manifest to new location if saving elsewhere
            if os.path.normcase(os.path.normpath(save_path)) != os.path.normcase(
                os.path.normpath(manifest_path)
            ):
                shutil.copy2(manifest_path, save_path)

            from infrastructure.manifest_repository import ManifestRepository
            updated = ManifestRepository().save(save_path, self.vm.groups)
            self._manifest_path = save_path
            logger.info("Manifest decisions saved to {}: {} rows updated", save_path, updated)
            QMessageBox.information(
                self.parent, "Save Manifest", f"Saved decisions for {updated} file(s)."
            )
            self.status_reporter.show_status(f"Manifest saved ({updated} decisions written)")

        except Exception as ex:
            logger.exception("Save manifest failed: {}", ex)
            QMessageBox.critical(self.parent, "Save Manifest Error", str(ex))
            self.status_reporter.show_status("Save manifest failed")

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
                self._sync_removed_to_db(checked_paths)
                self.status_reporter.show_status(f"Removed {len(checked_paths)} file(s) from list")
                return

            # If no checked files, try to get currently highlighted rows
            if highlighted_items:
                logger.info(
                    "Removing {} highlighted items from list via toolbar", len(highlighted_items)
                )
                file_items = [item for item in highlighted_items if item.get("type") == "file"]
                group_items = [item for item in highlighted_items if item.get("type") == "group"]

                # Collect all paths before modifying vm (groups disappear after removal)
                paths_for_db: list[str] = [item["path"] for item in file_items]
                for item in group_items:
                    for g in self.vm.groups:
                        if g.group_number == item["group_number"]:
                            paths_for_db.extend(r.file_path for r in g.items)
                            break

                if file_items:
                    self.vm.remove_from_list([item["path"] for item in file_items])

                for item in group_items:
                    self.vm.remove_group_from_list(item["group_number"])

                self.ui_updater.refresh_tree(self.vm.groups)
                self._sync_removed_to_db(paths_for_db)
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
            file_paths: list[str] = []
            group_numbers: list[int] = []

            for item in items:
                if item["type"] == "file":
                    file_paths.append(item["path"])
                elif item["type"] == "group":
                    group_numbers.append(item["group_number"])

            # Collect all paths for DB sync BEFORE vm removal (groups disappear after)
            paths_for_db: list[str] = list(file_paths)
            for gn in group_numbers:
                for g in self.vm.groups:
                    if g.group_number == gn:
                        paths_for_db.extend(r.file_path for r in g.items)
                        break

            if file_paths:
                logger.info("Removing {} files from list", len(file_paths))
                self.vm.remove_from_list(file_paths)

            if group_numbers:
                logger.info("Removing {} groups from list", len(group_numbers))
                for group_num in group_numbers:
                    self.vm.remove_group_from_list(group_num)

            self.ui_updater.refresh_tree(self.vm.groups)
            self._sync_removed_to_db(paths_for_db)

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

    def _sync_removed_to_db(self, file_paths: list[str]) -> None:
        """Mark file_paths as removed in the manifest DB (manifest workflow only)."""
        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path or not file_paths:
            return
        try:
            from infrastructure.manifest_repository import ManifestRepository
            ManifestRepository().remove_from_review(manifest_path, file_paths)
        except Exception as exc:
            logger.warning("Failed to sync removed paths to manifest: {}", exc)

    def set_decision(self, items: list[dict], new_decision: str) -> None:
        """Set user_decision for the given file items in memory and in SQLite."""
        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            return
        from infrastructure.manifest_repository import ManifestRepository
        repo = ManifestRepository()
        for item in items:
            if item.get("type") != "file":
                continue
            file_path = item["path"]
            for group in self.vm.groups:
                for rec in group.items:
                    if rec.file_path == file_path:
                        rec.user_decision = new_decision
                        break
            repo.update_decision(manifest_path, file_path, new_decision)
        self.ui_updater.refresh_tree(self.vm.groups)
        self.status_reporter.show_status(f"Decision set to '{new_decision}'")

    def batch_set_decision(self, new_decision: str) -> None:
        """Set user_decision for all Sel-checked files."""
        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            QMessageBox.information(self.parent, "Set Action", "No manifest loaded.")
            return
        checked_paths: list[str] = []
        provider = self.checked_paths_provider
        if provider is not None:
            if callable(provider):
                checked_paths = provider()
            elif hasattr(provider, "gather_checked_paths"):
                checked_paths = provider.gather_checked_paths()
        if not checked_paths:
            QMessageBox.information(self.parent, "Set Action", "No files selected (use Sel checkboxes).")
            return
        from infrastructure.manifest_repository import ManifestRepository
        repo = ManifestRepository()
        count = 0
        for group in self.vm.groups:
            for rec in group.items:
                if rec.file_path in checked_paths:
                    rec.user_decision = new_decision
                    repo.update_decision(manifest_path, rec.file_path, new_decision)
                    count += 1
        self.ui_updater.refresh_tree(self.vm.groups)
        self.status_reporter.show_status(f"Set '{new_decision}' for {count} file(s)")

    def set_decision_to_highlighted(self, new_decision: str) -> None:
        """Set user_decision for tree-highlighted (activated) file rows."""
        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            QMessageBox.information(self.parent, "Set Action", "No manifest loaded.")
            return
        items: list[dict] = []
        provider = self.highlighted_items_provider
        if provider is not None:
            if callable(provider):
                items = provider()
            elif hasattr(provider, "get_selected_items"):
                items = provider.get_selected_items()
        file_items = [it for it in items if it.get("type") == "file"]
        if not file_items:
            QMessageBox.information(self.parent, "Set Action", "No activated files.")
            return
        self.set_decision(file_items, new_decision)

    def execute_action(self) -> None:
        """Open the Execute Action review dialog and run planned operations."""
        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            QMessageBox.information(self.parent, "Execute Action", "No manifest loaded.")
            return
        from PySide6.QtWidgets import QDialog
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        dlg = ExecuteActionDialog(self.vm.groups, manifest_path, self.parent)
        if dlg.exec() == QDialog.Accepted:
            if dlg.deleted_paths:
                # prune_singles=False: manifest single-item groups must persist
                self.vm.remove_deleted_and_prune(dlg.deleted_paths, prune_singles=False)
            self.ui_updater.refresh_tree(self.vm.groups)
            total = len(dlg.deleted_paths) + len(dlg.executed_paths)
            self.status_reporter.show_status(f"Executed {total} action(s)")

