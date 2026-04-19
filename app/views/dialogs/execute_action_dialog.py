"""ExecuteActionDialog — review and confirm planned file operations."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from loguru import logger

_PLAN_TEXT = {
    "delete": "Delete file",
    "keep": "Mark as kept / executed",
}


class ExecuteActionDialog(QDialog):
    """Shows planned file operations based on user decisions; executes on confirm."""

    def __init__(self, groups: list, manifest_path: str | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Execute Actions — Review")
        self.setMinimumSize(720, 460)
        self.deleted_paths: list[str] = []
        self.executed_paths: list[str] = []
        self._manifest_path = manifest_path
        self._ops = self._collect_ops(groups)
        self._build_ui()

    # ------------------------------------------------------------------ build

    def _collect_ops(self, groups: list) -> list[dict]:
        ops = []
        for group in groups:
            for rec in getattr(group, "items", []):
                decision = getattr(rec, "user_decision", "") or ""
                if not decision:
                    continue
                ops.append({"decision": decision, "path": rec.file_path})
        return ops

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        n_delete = sum(1 for op in self._ops if op["decision"] == "delete")
        n_keep = sum(1 for op in self._ops if op["decision"] == "keep")

        summary = QLabel(
            f"<b>{len(self._ops)}</b> file(s) with decisions: "
            f"{n_delete} delete, {n_keep} keep."
        )
        layout.addWidget(summary)

        table = QTableWidget(len(self._ops), 3)
        table.setHorizontalHeaderLabels(["Decision", "File", "Plan"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        for row, op in enumerate(self._ops):
            decision = op["decision"]
            table.setItem(row, 0, QTableWidgetItem(decision))
            table.setItem(row, 1, QTableWidgetItem(Path(op["path"]).name))
            table.setItem(row, 2, QTableWidgetItem(_PLAN_TEXT.get(decision, decision)))
        table.resizeColumnsToContents()
        layout.addWidget(table)

        if not self._ops:
            layout.addWidget(QLabel("No decisions set — use 'Set Action' to mark files first."))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Execute")
        buttons.button(QDialogButtonBox.Ok).setEnabled(bool(self._ops))
        buttons.accepted.connect(self._on_execute)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ execute

    def _on_execute(self) -> None:
        for op in self._ops:
            decision = op["decision"]
            path = op["path"]
            if decision == "delete":
                self._delete_file(path)
            elif decision == "keep":
                self.executed_paths.append(path)

        if self._manifest_path:
            all_done = self.deleted_paths + self.executed_paths
            if all_done:
                try:
                    from infrastructure.manifest_repository import ManifestRepository
                    ManifestRepository().mark_executed(self._manifest_path, all_done)
                except Exception as exc:
                    logger.warning("Failed to mark executed in manifest: {}", exc)

        self.accept()

    def _delete_file(self, path: str) -> None:
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
