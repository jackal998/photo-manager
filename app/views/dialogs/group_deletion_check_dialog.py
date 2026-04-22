"""GroupDeletionCheckDialog — review complete-group deletions before executing."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from loguru import logger

from core.services.selection_service import RegexSelectionService

_FIELDS = ["File Name", "Folder", "Decision"]


class GroupDeletionCheckDialog(QDialog):
    """Shows ops from complete-delete groups; lets user flip decisions via regex."""

    def __init__(
        self,
        ops: list[dict],
        complete_group_numbers: list[int],
        manifest_path: str | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Complete-Group Deletion — Review")
        self.setMinimumSize(700, 440)
        self.overrides: dict[str, str] = {}
        self._manifest_path = manifest_path
        self._review_ops: list[dict] = [
            op for op in ops if op["group_number"] in set(complete_group_numbers)
        ]
        self._build_ui()

    # ------------------------------------------------------------------ inner accessor

    class _TableModelAccessor:
        """Adapts list[dict] ops for RegexSelectionService (file-level only)."""

        def __init__(self, ops: list[dict], checked: list[bool]) -> None:
            self._ops = ops
            self._checked = checked

        def iter_groups(self) -> list[int]:
            return list(range(len(self._ops)))

        def iter_children(self, group: int) -> list[int]:
            return [0]

        def get_field_text(self, group: int, child: int | None, field_name: str) -> str | None:
            if child is None:
                return None  # all fields are file-level → service iterates children
            op = self._ops[group]
            if field_name == "File Name":
                return Path(op["path"]).name
            if field_name == "Folder":
                return str(Path(op["path"]).parent)
            if field_name == "Decision":
                return op["decision"]
            return ""

        def set_checked(self, group: int, child: int, checked: bool) -> None:
            self._checked[group] = checked

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "<b>\u26a0 The following groups will have ALL files deleted.</b><br>"
            "Use regex below to select files and change their decision before proceeding."
        ))

        # Regex row
        regex_row = QHBoxLayout()
        regex_row.addWidget(QLabel("Field:"))
        self._field_combo = QComboBox()
        self._field_combo.addItems(_FIELDS)
        regex_row.addWidget(self._field_combo)
        regex_row.addWidget(QLabel("Regex:"))
        self._regex_edit = QLineEdit()
        self._regex_edit.setPlaceholderText("e.g. IMG_\\d+\\.jpg")
        regex_row.addWidget(self._regex_edit, stretch=1)
        layout.addLayout(regex_row)

        # Action buttons row
        btn_row = QHBoxLayout()
        keep_btn = QPushButton("Set to Keep")
        keep_btn.clicked.connect(self._on_set_keep)
        btn_row.addWidget(keep_btn)
        delete_btn = QPushButton("Set to Delete")
        delete_btn.clicked.connect(self._on_set_delete)
        btn_row.addWidget(delete_btn)
        self._status_label = QLabel("")
        btn_row.addWidget(self._status_label, stretch=1)
        layout.addLayout(btn_row)

        # Table
        self._table = QTableWidget(len(self._review_ops), 3)
        self._table.setHorizontalHeaderLabels(["Decision", "Group", "File Name"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        for row, op in enumerate(self._review_ops):
            self._table.setItem(row, 0, QTableWidgetItem(op["decision"]))
            self._table.setItem(row, 1, QTableWidgetItem(str(op["group_number"])))
            self._table.setItem(row, 2, QTableWidgetItem(Path(op["path"]).name))
        self._table.resizeColumnsToContents()
        layout.addWidget(self._table)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ logic

    def _apply_decision(self, target_decision: str) -> None:
        pattern = self._regex_edit.text().strip()
        if not pattern:
            self._status_label.setText("Enter a regex pattern first.")
            return
        try:
            re.compile(pattern)
        except re.error as exc:
            self._status_label.setText(f"Invalid regex: {exc}")
            return

        field = self._field_combo.currentText()
        checked = [False] * len(self._review_ops)
        accessor = self._TableModelAccessor(self._review_ops, checked)
        svc = RegexSelectionService(accessor)
        svc.apply(field, pattern, True)

        batch: dict[str, str] = {}
        for i, op in enumerate(self._review_ops):
            if checked[i] and op["decision"] != target_decision:
                op["decision"] = target_decision
                self.overrides[op["path"]] = target_decision
                item = self._table.item(i, 0)
                if item is not None:
                    item.setText(target_decision)
                batch[op["path"]] = target_decision

        if batch and self._manifest_path:
            try:
                from infrastructure.manifest_repository import ManifestRepository
                ManifestRepository().batch_update_decisions(self._manifest_path, batch)
            except Exception as exc:
                logger.warning("Failed to persist decisions: {}", exc)

        self._status_label.setText(f"{len(batch)} row(s) set to '{target_decision}'.")

    def _on_set_keep(self) -> None:
        self._apply_decision("keep")

    def _on_set_delete(self) -> None:
        self._apply_decision("delete")
