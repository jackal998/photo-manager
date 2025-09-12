from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from core.services.interfaces import DeletePlanGroupSummary


class DeleteConfirmDialog(QDialog):
    def __init__(self, summaries: list[DeletePlanGroupSummary], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm Delete")

        root = QVBoxLayout(self)

        total_groups = len(summaries)
        affected_groups = sum(1 for s in summaries if s.selected_count > 0)
        total_files = sum(s.total_count for s in summaries)
        files_to_remove = sum(s.selected_count for s in summaries)
        full_groups = [s for s in summaries if s.is_full_delete]
        any_full = len(full_groups) > 0

        title = QLabel("即將刪除所選檔案。")
        root.addWidget(title)

        groups_info = QLabel(f"群組：將影響 {affected_groups} / {total_groups}")
        files_info = QLabel(f"檔案：將刪除 {files_to_remove} / {total_files}")
        root.addWidget(groups_info)
        root.addWidget(files_info)

        if any_full:
            warn = QLabel("警告：以下群組為『全選刪除』，請再次確認：")
            warn.setStyleSheet("color: #b00020; font-weight: bold;")
            root.addWidget(warn)
            lst = QListWidget()
            for s in full_groups:
                text = f"Group {s.group_number}（{s.total_count} 檔）"
                item = QListWidgetItem(text)
                lst.addItem(item)
            root.addWidget(lst)

        self._confirm_box = QCheckBox("我已了解刪除風險並確認執行")
        if any_full:
            root.addWidget(self._confirm_box)
        else:
            self._confirm_box.setChecked(True)

        btns = QHBoxLayout()
        self.btn_ok = QPushButton("Delete")
        self.btn_cancel = QPushButton("Cancel")
        btns.addWidget(self.btn_ok)
        btns.addStretch(1)
        btns.addWidget(self.btn_cancel)
        root.addLayout(btns)

        self.btn_ok.clicked.connect(self._on_accept)
        self.btn_cancel.clicked.connect(self.reject)

    def _on_accept(self) -> None:
        if not self._confirm_box.isChecked():
            return
        self.accept()
