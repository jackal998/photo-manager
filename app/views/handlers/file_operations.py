"""FileOperationsHandler: Handles file-related operations like manifest import/export and decisions."""

from __future__ import annotations

from typing import Any, Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox
from loguru import logger

# Maps SelectDialog field names → PhotoRecord attribute names.
_FIELD_TO_ATTR: dict[str, str] = {
    "File Name":     "file_path",      # basename extracted in _get_record_field
    "Folder":        "folder_path",
    "Action":        "user_decision",
    "Size (Bytes)":  "file_size_bytes",
    "Creation Date": "creation_date",
    "Shot Date":     "shot_date",
}


def _get_record_field(rec: Any, field: str) -> str | None:
    """Return the string value of a record's field, or None if unavailable."""
    from pathlib import Path

    attr = _FIELD_TO_ATTR.get(field)
    if attr is None:
        return None
    val = getattr(rec, attr, None)
    if val is None:
        return None
    if field == "File Name":
        return Path(str(val)).name
    return str(val)


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
    """Handles file-related operations including manifest import/export and user decisions."""

    def __init__(
        self,
        vm: Any,
        settings: Any,
        parent_widget: QObject,
        ui_updater: UIUpdateCallback,
        status_reporter: StatusReporter,
        checked_paths_provider: object | None = None,
        highlighted_items_provider: object | None = None,
    ) -> None:
        self.vm = vm
        self.settings = settings
        self.parent = parent_widget
        self.ui_updater = ui_updater
        self.status_reporter = status_reporter
        self.checked_paths_provider = checked_paths_provider
        self.highlighted_items_provider = highlighted_items_provider

    def import_manifest(self) -> None:
        """Open a migration_manifest.sqlite in a background worker (non-blocking)."""
        path, _ = QFileDialog.getOpenFileName(
            self.parent, "Open Manifest", "", "SQLite Files (*.sqlite *.db);;All Files (*)"
        )
        if not path:
            return
        self._start_manifest_load(path)

    def _start_manifest_load(self, path: str) -> None:
        """Begin a background load for the manifest at *path*."""
        from app.views.workers.manifest_load_worker import ManifestLoadWorker

        self.status_reporter.show_status("Opening manifest…", 0)
        self._set_manifest_actions_enabled(False)

        default_sort = getattr(self.vm, "_default_sort", [])
        worker = ManifestLoadWorker(path, default_sort, parent=self.parent)
        worker.progress.connect(lambda msg: self.status_reporter.show_status(msg, 0))
        worker.finished.connect(lambda groups: self._on_manifest_loaded(groups, path))
        worker.failed.connect(self._on_manifest_failed)
        worker.start()
        # Keep reference so the worker is not garbage-collected
        self._load_worker = worker

    def _on_manifest_loaded(self, groups: list, path: str) -> None:
        self.vm.groups = groups
        self._manifest_path = path
        self.ui_updater.refresh_tree(groups)
        self.ui_updater.show_group_counts(self.vm.group_count)
        self.ui_updater.show_groups_summary(groups)
        self._set_manifest_actions_enabled(True)

        n_groups = self.vm.group_count
        n_items = sum(len(g.items) for g in groups)
        logger.info("Opened manifest: {} | groups={} items={}", path, n_groups, n_items)
        self.status_reporter.show_status(
            f"Opened manifest: {n_groups} pairs to review ({n_items} files)"
        )

    def _on_manifest_failed(self, error: str) -> None:
        logger.error("Open manifest failed: {}", error)
        QMessageBox.critical(self.parent, "Open Manifest Error", error)
        self.status_reporter.show_status("Open manifest failed")
        self._set_manifest_actions_enabled(False)

    def _set_manifest_actions_enabled(self, enabled: bool) -> None:
        _manifest_actions = (
            "save_manifest", "execute_action",
            "set_action_hl_delete", "set_action_hl_keep",
        )
        for act in _manifest_actions:
            try:
                self.parent.menu_controller.enable_action(act, enabled)
            except AttributeError:
                pass

    def save_manifest_decisions(self) -> None:
        """Export current decisions to a (possibly new) manifest file."""
        import os
        import shutil
        import sqlite3

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
            if os.path.normcase(os.path.normpath(save_path)) != os.path.normcase(
                os.path.normpath(manifest_path)
            ):
                # #91: scanner writes the manifest in WAL mode and may still
                # hold an active connection with uncheckpointed writes in the
                # -wal sibling. shutil.copy2 only copies the main .sqlite, so
                # without a checkpoint the destination ends up with no schema.
                ckpt_conn = sqlite3.connect(manifest_path)
                try:
                    ckpt_conn.execute("PRAGMA wal_checkpoint(FULL)")
                finally:
                    ckpt_conn.close()
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

    def remove_from_list_toolbar(self, highlighted_items: list[dict]) -> None:
        """Remove highlighted items from the list via toolbar."""
        try:
            if highlighted_items:
                logger.info(
                    "Removing {} highlighted items from list via toolbar", len(highlighted_items)
                )
                file_items = [item for item in highlighted_items if item.get("type") == "file"]
                group_items = [item for item in highlighted_items if item.get("type") == "group"]

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
                "No items selected. Please select rows first.",
            )

        except Exception as e:
            logger.error("Remove from list via toolbar failed: {}", e)
            QMessageBox.critical(self.parent, "Error", f"Remove from list failed: {str(e)}")

    def remove_items_from_list(self, items: list[dict]) -> None:
        """Remove multiple items (files and/or groups) from the list."""
        try:
            file_paths: list[str] = []
            group_numbers: list[int] = []

            for item in items:
                if item["type"] == "file":
                    file_paths.append(item["path"])
                elif item["type"] == "group":
                    group_numbers.append(item["group_number"])

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
        batch: dict[str, str] = {}
        for item in items:
            if item.get("type") != "file":
                continue
            file_path = item["path"]
            for group in self.vm.groups:
                for rec in group.items:
                    if rec.file_path == file_path:
                        rec.user_decision = new_decision
                        break
            batch[file_path] = new_decision
        if batch:
            from infrastructure.manifest_repository import ManifestRepository
            ManifestRepository().batch_update_decisions(manifest_path, batch)
        self.ui_updater.refresh_tree(self.vm.groups)
        self.status_reporter.show_status(f"Decision set to '{new_decision}'")

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

    def set_decision_by_regex(self, field: str, pattern: str, new_decision: str) -> None:
        """Find all file rows where field matches regex and set their user_decision.

        Args:
            field: Field name (e.g. "File Name", "Folder", "Action").
            pattern: Regex pattern (case-insensitive).
            new_decision: Value to write — "delete" or "" (clears any existing decision).
        """
        import re as _re

        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            QMessageBox.information(self.parent, "Set Action", "No manifest loaded.")
            return

        try:
            rx = _re.compile(pattern, _re.IGNORECASE)
        except _re.error as exc:
            QMessageBox.warning(self.parent, "Invalid Regex", str(exc))
            return

        matching: list[dict] = []
        for group in self.vm.groups:
            for rec in group.items:
                value = _get_record_field(rec, field)
                if value is not None and rx.search(value):
                    matching.append({"type": "file", "path": rec.file_path})

        if not matching:
            QMessageBox.information(self.parent, "Set Action", "No files matched the pattern.")
            return

        self.set_decision(matching, new_decision)

    def execute_action(self) -> None:
        """Open the Execute Action review dialog and run planned operations."""
        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            QMessageBox.information(self.parent, "Execute Action", "No manifest loaded.")
            return
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        dlg = ExecuteActionDialog(self.vm.groups, manifest_path, self.parent)
        if dlg.exec() == QDialog.Accepted:
            if dlg.deleted_paths:
                self.vm.remove_deleted_and_prune(dlg.deleted_paths, prune_singles=False)
            self.ui_updater.refresh_tree(self.vm.groups)
            total = len(dlg.deleted_paths) + len(dlg.executed_paths)
            self.status_reporter.show_status(f"Executed {total} action(s)")
