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

_ACTIONABLE = {"EXACT", "MOVE"}

_PLAN_TEXT = {
    "EXACT": "Delete (confirmed duplicate)",
    "MOVE": "Move — mark as executed",
    "REVIEW_DUPLICATE": "Skip — still needs review",
    "UNDATED": "Skip — no EXIF date",
    "KEEP": "Skip — authoritative copy",
}


class ExecuteActionDialog(QDialog):
    """Shows all planned file operations; executes them when the user confirms."""

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
                action = getattr(rec, "action", "") or ""
                if not action or action == "KEEP":
                    continue
                ops.append({"action": action, "path": rec.file_path})
        return ops

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        actionable = [op for op in self._ops if op["action"] in _ACTIONABLE]
        n_delete = sum(1 for op in actionable if op["action"] == "EXACT")
        n_move = sum(1 for op in actionable if op["action"] == "MOVE")
        n_skip = len(self._ops) - len(actionable)

        summary = QLabel(
            f"<b>{len(actionable)}</b> actionable: "
            f"{n_delete} deletion(s), {n_move} move(s) planned. "
            f"{n_skip} row(s) skipped (still need review or have no date)."
        )
        layout.addWidget(summary)

        table = QTableWidget(len(self._ops), 3)
        table.setHorizontalHeaderLabels(["Action", "File", "Plan"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        for row, op in enumerate(self._ops):
            action = op["action"]
            table.setItem(row, 0, QTableWidgetItem(action))
            table.setItem(row, 1, QTableWidgetItem(Path(op["path"]).name))
            table.setItem(row, 2, QTableWidgetItem(_PLAN_TEXT.get(action, f"Unknown: {action}")))
        table.resizeColumnsToContents()
        layout.addWidget(table)

        if not actionable:
            layout.addWidget(QLabel("No actionable operations — nothing to execute."))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Execute")
        buttons.button(QDialogButtonBox.Ok).setEnabled(bool(actionable))
        buttons.accepted.connect(self._on_execute)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ execute

    def _on_execute(self) -> None:
        for op in self._ops:
            action = op["action"]
            path = op["path"]
            if action == "EXACT":
                self._delete_file(path)
            elif action == "MOVE":
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
            logger.info("Deleted duplicate: {}", path)
        except Exception as exc:
            logger.warning("Failed to delete {}: {}", path, exc)
