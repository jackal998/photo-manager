"""ExecuteActionDialog — review and confirm planned file operations."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QMenu,
    QTreeView,
    QVBoxLayout,
)
from loguru import logger

from app.views.constants import COL_NAME, PATH_ROLE
from app.views.tree_model_builder import build_model


class ExecuteActionDialog(QDialog):
    """Shows all groups for final review; executes file decisions on confirm."""

    def __init__(self, groups: list, manifest_path: str | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Execute Actions — Review")
        self.setMinimumSize(900, 560)
        self._groups = groups
        self._manifest_path = manifest_path
        self.deleted_paths: list[str] = []
        self.executed_paths: list[str] = []
        self._missing_paths: list[str] = []
        self._build_ui()

    # ------------------------------------------------------------------ helpers

    def _decided_records(self) -> list[tuple]:
        """Return (group, rec) pairs where user_decision is set."""
        return [
            (group, rec)
            for group in self._groups
            for rec in getattr(group, "items", [])
            if getattr(rec, "user_decision", "")
        ]

    def _complete_delete_groups(self) -> list[int]:
        """Return group_numbers where every record has user_decision='delete'."""
        result = []
        for group in self._groups:
            items = getattr(group, "items", [])
            if not items:
                continue
            if all(getattr(rec, "user_decision", "") == "delete" for rec in items):
                result.append(group.group_number)
        return sorted(result)

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._summary_label = QLabel()
        self._update_summary()
        layout.addWidget(self._summary_label)

        self._tree = QTreeView()
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.setAlternatingRowColors(True)
        self._rebuild_tree_model()
        layout.addWidget(self._tree)

        # Warning banner for complete-group deletions
        self._warning_banner = QFrame()
        self._warning_banner.setFrameShape(QFrame.StyledPanel)
        self._warning_banner.setStyleSheet(
            "QFrame { background: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; }"
        )
        banner_layout = QVBoxLayout(self._warning_banner)
        banner_layout.setContentsMargins(8, 6, 8, 6)
        self._warning_label = QLabel()
        self._warning_label.setWordWrap(True)
        self._warning_label.setStyleSheet("color: #856404; font-weight: bold;")
        banner_layout.addWidget(self._warning_label)
        self._warning_banner.setVisible(False)
        layout.addWidget(self._warning_banner)

        has_decisions = bool(self._decided_records())
        self._btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._btn_box.button(QDialogButtonBox.Ok).setText("Execute")
        self._btn_box.button(QDialogButtonBox.Ok).setEnabled(has_decisions)
        self._btn_box.accepted.connect(self._on_execute_requested)
        self._btn_box.rejected.connect(self.reject)
        layout.addWidget(self._btn_box)

        self._refresh_warning_banner()

    def _rebuild_tree_model(self) -> None:
        model, proxy = build_model(self._groups)
        self._tree.setModel(proxy if proxy is not None else model)
        self._tree.expandAll()

    def _update_summary(self) -> None:
        decided = self._decided_records()
        n_delete = sum(1 for _, rec in decided if rec.user_decision == "delete")
        n_keep = sum(1 for _, rec in decided if rec.user_decision == "keep")
        if decided:
            self._summary_label.setText(
                f"<b>{len(decided)}</b> file(s) with decisions: "
                f"{n_delete} delete, {n_keep} keep. "
                "Right-click a file row to change its decision."
            )
        else:
            self._summary_label.setText(
                "No decisions set — use 'Set Action' to mark files first."
            )

    def _refresh_warning_banner(self) -> None:
        complete = self._complete_delete_groups()
        if complete:
            group_list = ", ".join(str(g) for g in complete)
            self._warning_label.setText(
                f"\u26a0 Group(s) {group_list} will have ALL files deleted. "
                "Review decisions below before clicking Execute."
            )
            self._warning_banner.setVisible(True)
        else:
            self._warning_banner.setVisible(False)

    # ------------------------------------------------------------------ context menu

    def _on_tree_context_menu(self, pos) -> None:
        index = self._tree.indexAt(pos)
        if not index.isValid():
            return
        # File rows have a parent; group rows are at the root level
        if not index.parent().isValid():
            return
        path = index.sibling(index.row(), COL_NAME).data(PATH_ROLE)
        if not path:
            return
        menu = QMenu(self)
        set_menu = menu.addMenu("Set Action")
        for decision in ("delete", "keep"):
            act = set_menu.addAction(decision)
            act.triggered.connect(
                lambda _checked=False, d=decision, p=path: self._set_decision(p, d)
            )
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _set_decision(self, path: str, decision: str) -> None:
        for group in self._groups:
            for rec in getattr(group, "items", []):
                if rec.file_path == path:
                    rec.user_decision = decision
                    break
        self._rebuild_tree_model()
        self._update_summary()
        has_decisions = bool(self._decided_records())
        self._btn_box.button(QDialogButtonBox.Ok).setEnabled(has_decisions)
        self._refresh_warning_banner()

    # ------------------------------------------------------------------ execute

    def _on_execute_requested(self) -> None:
        complete = self._complete_delete_groups()
        if complete:
            ops = [
                {
                    "path": rec.file_path,
                    "decision": rec.user_decision,
                    "group_number": group.group_number,
                }
                for group in self._groups
                for rec in getattr(group, "items", [])
                if getattr(rec, "user_decision", "")
            ]
            from app.views.dialogs.group_deletion_check_dialog import GroupDeletionCheckDialog
            dlg = GroupDeletionCheckDialog(ops, complete, self._manifest_path, self)
            if dlg.exec() != QDialog.Accepted:
                return
            for path, new_decision in dlg.overrides.items():
                for group in self._groups:
                    for rec in getattr(group, "items", []):
                        if rec.file_path == path:
                            rec.user_decision = new_decision
                            break
        self._on_execute()

    def _on_execute(self) -> None:
        # Batch-persist all current decisions before executing
        if self._manifest_path:
            batch = {
                rec.file_path: rec.user_decision
                for group in self._groups
                for rec in getattr(group, "items", [])
                if getattr(rec, "user_decision", "")
            }
            if batch:
                try:
                    from infrastructure.manifest_repository import ManifestRepository
                    ManifestRepository().batch_update_decisions(self._manifest_path, batch)
                except Exception as exc:
                    logger.warning("Failed to persist decisions before execute: {}", exc)

        for group in self._groups:
            for rec in getattr(group, "items", []):
                decision = getattr(rec, "user_decision", "") or ""
                if decision == "delete":
                    self._delete_file(rec.file_path)
                elif decision == "keep":
                    self.executed_paths.append(rec.file_path)

        if self._manifest_path:
            all_done = self.deleted_paths + self.executed_paths
            if all_done:
                try:
                    from infrastructure.manifest_repository import ManifestRepository
                    ManifestRepository().mark_executed(self._manifest_path, all_done)
                except Exception as exc:
                    logger.warning("Failed to mark executed in manifest: {}", exc)

        if self._missing_paths:
            from PySide6.QtWidgets import QMessageBox
            missing_list = "\n".join(self._missing_paths[:20])
            suffix = (
                f"\n…and {len(self._missing_paths) - 20} more"
                if len(self._missing_paths) > 20
                else ""
            )
            QMessageBox.warning(
                self,
                "Files Not Found",
                f"The following files could not be found and were skipped:\n\n"
                f"{missing_list}{suffix}",
            )

        self.accept()

    def _delete_file(self, path: str) -> None:
        if not os.path.exists(path):
            self._missing_paths.append(path)
            logger.warning("File not found, skipping delete: {}", path)
            return
        try:
            try:
                import send2trash
                send2trash.send2trash(path)
            except ImportError:
                os.remove(path)
            self.deleted_paths.append(path)
            logger.info("Deleted file: {}", path)
        except Exception as exc:
            logger.warning("Failed to delete {}: {}", path, exc)
