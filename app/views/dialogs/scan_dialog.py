"""ScanDialog — folder picker + background scan with live progress log."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.views.workers.scan_worker import ScanWorker

# (internal_key, display_label, placeholder_hint)
# Internal keys are stored in settings.json under "sources.*".
# Priority order: first entry wins when the same file appears in multiple folders.
_SOURCES = [
    ("iphone",  "Folder 1  (highest priority)", "Browse to select a source folder…"),
    ("takeout", "Folder 2",                     "Browse to select a source folder…"),
    ("jdrive",  "Folder 3  (lowest priority)",  "Browse to select a source folder…"),
]


class _SourceRow(QWidget):
    """One row: label + path field + Browse button."""

    def __init__(self, label: str, hint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.field = QLineEdit()
        self.field.setPlaceholderText(hint)
        self.field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        browse = QPushButton("Browse…")
        browse.setFixedWidth(80)
        browse.clicked.connect(self._browse)

        layout.addWidget(self.field)
        layout.addWidget(browse)

    def _browse(self) -> None:
        start = self.field.text() or ""
        chosen = QFileDialog.getExistingDirectory(self, "Select folder", start)
        if chosen:
            self.field.setText(chosen)

    @property
    def path(self) -> str:
        return self.field.text().strip()

    @path.setter
    def path(self, value: str) -> None:
        self.field.setText(value)


class ScanDialog(QDialog):
    """Modal dialog that lets the user pick source folders and run a scan.

    After a successful scan the manifest path is available via `.manifest_path`.
    The caller should connect `on_scan_complete` to be notified.
    """

    def __init__(
        self,
        settings,                          # JsonSettings instance
        on_scan_complete: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scan Sources")
        self.setMinimumWidth(600)
        self.settings = settings
        self._on_complete = on_scan_complete
        self._worker: ScanWorker | None = None
        self.manifest_path: str | None = None

        self._build_ui()
        self._load_from_settings()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Read-only notice
        notice = QLabel(
            "Read-only scan — no files are moved or deleted. "
            "MOVE / SKIP in the log are planned actions stored in the manifest only."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet("color: #555; font-style: italic; padding: 4px 0;")
        root.addWidget(notice)

        # Source folder rows
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self._source_rows: dict[str, _SourceRow] = {}
        for key, display, hint in _SOURCES:
            row = _SourceRow(key, hint, self)
            self._source_rows[key] = row
            form.addRow(QLabel(display + ":"), row)

        # Manifest output path
        self._output_row = _SourceRow("output", "migration_manifest.sqlite", self)
        self._output_row.field.setPlaceholderText("Output manifest path (.sqlite)")
        form.addRow(QLabel("Save manifest to:"), self._output_row)

        root.addLayout(form)

        # Priority note
        priority_note = QLabel(
            "When the same photo exists in multiple folders, "
            "the copy from the highest-priority folder is kept."
        )
        priority_note.setWordWrap(True)
        priority_note.setStyleSheet("color: #666; font-size: 11px; padding: 2px 0 6px 0;")
        root.addWidget(priority_note)

        # Progress log
        self._log_widget = QPlainTextEdit()
        self._log_widget.setReadOnly(True)
        self._log_widget.setMinimumHeight(200)
        self._log_widget.setPlaceholderText("Scan progress will appear here…")
        root.addWidget(self._log_widget)

        # Buttons
        self._btn_scan = QPushButton("Start Scan")
        self._btn_scan.setDefault(True)
        self._btn_scan.clicked.connect(self._start_scan)

        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.reject)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._btn_scan)
        btn_row.addWidget(self._btn_close)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------ settings

    def _load_from_settings(self) -> None:
        for key in self._source_rows:
            saved = self.settings.get(f"sources.{key}", "")
            if saved:
                self._source_rows[key].path = saved
        saved_out = self.settings.get("sources.output", "migration_manifest.sqlite")
        self._output_row.path = saved_out

    def _save_to_settings(self) -> None:
        for key, row in self._source_rows.items():
            if row.path:
                self.settings.set(f"sources.{key}", row.path)
        if self._output_row.path:
            self.settings.set("sources.output", self._output_row.path)
        try:
            self.settings.save()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    # ------------------------------------------------------------------ scan

    def _start_scan(self) -> None:
        sources = {k: row.path for k, row in self._source_rows.items() if row.path}
        output = self._output_row.path

        if not sources:
            QMessageBox.warning(self, "No sources", "Please select at least one source folder.")
            return
        if not output:
            QMessageBox.warning(self, "No output", "Please specify an output path for the manifest.")
            return

        self._save_to_settings()
        self._log_widget.clear()
        self._log("Starting scan…")
        self._btn_scan.setEnabled(False)

        self._worker = ScanWorker(sources=sources, output_path=output)
        self._worker.progress.connect(self._log)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _log(self, msg: str) -> None:
        self._log_widget.appendPlainText(msg)
        self._log_widget.verticalScrollBar().setValue(
            self._log_widget.verticalScrollBar().maximum()
        )

    def _on_finished(self, manifest_path: str) -> None:
        self.manifest_path = manifest_path
        self._btn_scan.setEnabled(True)
        self._btn_close.setText("Close & Load")
        self._btn_close.clicked.disconnect()
        self._btn_close.clicked.connect(self._load_and_close)

    def _on_failed(self, error: str) -> None:
        self._log(f"\nERROR: {error}")
        self._btn_scan.setEnabled(True)
        QMessageBox.critical(self, "Scan Failed", error)

    def _load_and_close(self) -> None:
        if self._on_complete and self.manifest_path:
            self._on_complete(self.manifest_path)
        self.accept()

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(3000)
        super().closeEvent(event)
