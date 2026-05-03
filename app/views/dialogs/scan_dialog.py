"""ScanDialog — multi-source folder picker + background scan with live progress log."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QDir, QModelIndex, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFileSystemModel,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from app.views.workers.scan_worker import ScanWorker


@dataclass
class _SourceEntry:
    """One user-selected source folder with its scan options."""

    path: str
    recursive: bool = True


def _auto_label(name: str, existing: set[str]) -> str:
    """Return a unique label derived from ``name``.

    Appends ``_2``, ``_3``, … until the label is not already in ``existing``.

    Args:
        name: Preferred label (typically the folder's basename).
        existing: Set of labels already in use.

    Returns:
        A label string not present in ``existing``.
    """
    label = name
    counter = 2
    while label in existing:
        label = f"{name}_{counter}"
        counter += 1
    return label


class _FolderTreePanel(QWidget):
    """Embedded filesystem tree for browsing and selecting source folders.

    Emits ``folder_requested(path)`` when the user adds a folder via:
      - double-click in the tree
      - "Add Selected Folder" button
      - typing/pasting a path into the path field and pressing Enter / "+ Add"
    """

    folder_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model: QFileSystemModel
        self._tree: QTreeView
        self._path_field: QLineEdit
        self._build_ui()

    def _build_ui(self) -> None:
        """Construct the directory tree view, path entry, and add button."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Path entry — lets the user paste / type an absolute path instead of
        # scrolling the tree 10+ levels deep to a known fixture.
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Path:"))
        self._path_field = QLineEdit()
        self._path_field.setPlaceholderText("Paste or type an absolute folder path…")
        self._path_field.returnPressed.connect(self._on_add_typed)
        path_add_btn = QPushButton("+ Add")
        path_add_btn.setFixedWidth(80)
        path_add_btn.clicked.connect(self._on_add_typed)
        path_row.addWidget(self._path_field, stretch=1)
        path_row.addWidget(path_add_btn)
        layout.addLayout(path_row)

        self._model = QFileSystemModel(self)
        self._model.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot)
        root_index = self._model.setRootPath("")

        self._tree = QTreeView()
        self._tree.setModel(self._model)
        self._tree.setRootIndex(root_index)
        for col in range(1, self._model.columnCount()):
            self._tree.hideColumn(col)
        self._tree.setHeaderHidden(True)
        self._tree.doubleClicked.connect(self._on_double_click)

        home = str(Path.home())
        home_index = self._model.index(home)
        if home_index.isValid():
            self._tree.expand(home_index)
            self._tree.scrollTo(home_index)

        add_btn = QPushButton("+ Add Selected Folder")
        add_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        add_btn.clicked.connect(self._on_add)

        layout.addWidget(self._tree)
        layout.addWidget(add_btn)

    def _on_add(self) -> None:
        """Emit ``folder_requested`` for the currently highlighted directory."""
        index = self._tree.currentIndex()
        if index.isValid():
            path = self._model.filePath(index)
            self.folder_requested.emit(path)

    def _on_add_typed(self) -> None:
        """Emit ``folder_requested`` for the path typed/pasted into the field.

        Silently no-ops on empty input or non-existent directories so the
        dialog doesn't bark at the user mid-typing.
        """
        raw = self._path_field.text().strip().strip('"')
        if not raw:
            return
        if not Path(raw).is_dir():
            return
        self.folder_requested.emit(raw)
        self._path_field.clear()

    def _on_double_click(self, index: QModelIndex) -> None:
        """Emit ``folder_requested`` on double-click."""
        path = self._model.filePath(index)
        self.folder_requested.emit(path)


class _SourceListWidget(QWidget):
    """Table managing the ordered list of selected source folders.

    Columns: priority #, path, Recursive checkbox, ↑↓ reorder buttons, × remove.

    Signals:
        changed: Emitted whenever the list content or order changes.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[_SourceEntry] = []
        self._table: QTableWidget
        self._build_ui()

    def _build_ui(self) -> None:
        """Construct the header row and source table."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("Source folders (top = highest priority):"))
        header_row.addStretch()
        remove_all_btn = QPushButton("Remove All")
        remove_all_btn.clicked.connect(self.clear)
        header_row.addWidget(remove_all_btn)
        layout.addLayout(header_row)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["#", "Path", "Recursive", "", ""])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        # Reserve enough vertical room for ~6 rows so the list isn't a 2-row
        # sliver when many sources are configured. The QSplitter parent still
        # lets the user grow / shrink it.
        self._table.setMinimumHeight(180)
        layout.addWidget(self._table)

    # ------------------------------------------------------------------ public API

    def add_entry(self, path: str, recursive: bool = True) -> None:
        """Add a source folder; silently ignore duplicate paths.

        Args:
            path: Absolute folder path.
            recursive: Whether to scan subdirectories (default ``True``).
        """
        if any(entry.path == path for entry in self._entries):
            return
        self._entries.append(_SourceEntry(path=path, recursive=recursive))
        self._rebuild_table()
        self.changed.emit()

    def clear(self) -> None:
        """Remove all source entries from the list."""
        self._entries.clear()
        self._rebuild_table()
        self.changed.emit()

    def entries(self) -> list[_SourceEntry]:
        """Return a shallow copy of the current entry list."""
        return list(self._entries)

    def set_entries(self, entries: list[_SourceEntry]) -> None:
        """Replace the current list with ``entries`` (does not emit ``changed``)."""
        self._entries = list(entries)
        self._rebuild_table()

    # ------------------------------------------------------------------ private

    def _rebuild_table(self) -> None:
        """Repopulate the table widget from ``self._entries``."""
        self._table.setRowCount(0)
        for row_idx, entry in enumerate(self._entries):
            self._table.insertRow(row_idx)

            num_item = QTableWidgetItem(str(row_idx + 1))
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row_idx, 0, num_item)
            self._table.setItem(row_idx, 1, QTableWidgetItem(entry.path))

            check = QCheckBox()
            check.setChecked(entry.recursive)
            check.stateChanged.connect(
                lambda state, row=row_idx: self._on_recursive_changed(row, state)
            )
            self._table.setCellWidget(row_idx, 2, self._centered(check))

            ud_widget = QWidget()
            ud_layout = QHBoxLayout(ud_widget)
            ud_layout.setContentsMargins(2, 0, 2, 0)
            ud_layout.setSpacing(2)
            up_btn = QPushButton("↑")
            up_btn.setFixedWidth(26)
            up_btn.setToolTip("Move up (higher priority)")
            up_btn.clicked.connect(lambda _, row=row_idx: self._move(row, -1))
            dn_btn = QPushButton("↓")
            dn_btn.setFixedWidth(26)
            dn_btn.setToolTip("Move down (lower priority)")
            dn_btn.clicked.connect(lambda _, row=row_idx: self._move(row, +1))
            ud_layout.addWidget(up_btn)
            ud_layout.addWidget(dn_btn)
            self._table.setCellWidget(row_idx, 3, ud_widget)

            rm_btn = QPushButton("×")
            rm_btn.setFixedWidth(26)
            rm_btn.setToolTip("Remove from list")
            rm_btn.clicked.connect(lambda _, row=row_idx: self._remove(row))
            self._table.setCellWidget(row_idx, 4, rm_btn)

    @staticmethod
    def _centered(widget: QWidget) -> QWidget:
        """Wrap ``widget`` in a horizontally-centred container widget."""
        wrapper = QWidget()
        lay = QHBoxLayout(wrapper)
        lay.addWidget(widget)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setContentsMargins(0, 0, 0, 0)
        return wrapper

    def _on_recursive_changed(self, row: int, state: int) -> None:
        """Update the recursive flag for the entry at ``row``."""
        if 0 <= row < len(self._entries):
            self._entries[row].recursive = bool(state)
            self.changed.emit()

    def _move(self, row: int, delta: int) -> None:
        """Swap the entry at ``row`` with the entry at ``row + delta``."""
        new_row = row + delta
        if 0 <= new_row < len(self._entries):
            self._entries[row], self._entries[new_row] = (
                self._entries[new_row],
                self._entries[row],
            )
            self._rebuild_table()
            self.changed.emit()

    def _remove(self, row: int) -> None:
        """Delete the entry at ``row``."""
        if 0 <= row < len(self._entries):
            self._entries.pop(row)
            self._rebuild_table()
            self.changed.emit()


class ScanDialog(QDialog):
    """Modal dialog for picking source folders and running a deduplication scan.

    After a successful scan the manifest path is available via ``.manifest_path``.
    Pass ``on_scan_complete`` to receive a callback when the user clicks Close & Load.
    """

    def __init__(
        self,
        settings,                          # JsonSettings instance
        on_scan_complete: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scan Sources")
        self.setMinimumWidth(700)
        self.setMinimumHeight(600)
        self.settings = settings
        self._on_complete = on_scan_complete
        self._worker: ScanWorker | None = None
        self.manifest_path: str | None = None

        self._tree_panel: _FolderTreePanel
        self._source_list: _SourceListWidget
        self._output_field: QLineEdit
        self._log_widget: QPlainTextEdit
        self._btn_scan: QPushButton
        self._btn_close: QPushButton
        self._phash_slider: QSlider
        self._phash_spin: QSpinBox
        self._color_slider: QSlider
        self._color_spin: QSpinBox

        self._build_ui()
        self._load_from_settings()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        """Construct the full dialog layout."""
        root = QVBoxLayout(self)

        notice = QLabel(
            "Read-only scan — no files are moved or deleted. "
            "MOVE / SKIP in the log are planned actions stored in the manifest only."
        )
        notice.setWordWrap(True)
        notice.setStyleSheet("color: #555; font-style: italic; padding: 4px 0;")
        root.addWidget(notice)

        splitter = QSplitter(Qt.Orientation.Vertical)

        tree_group = QGroupBox("Browse source folders:")
        tree_layout = QVBoxLayout(tree_group)
        self._tree_panel = _FolderTreePanel(self)
        self._tree_panel.folder_requested.connect(self._on_folder_requested)
        tree_layout.addWidget(self._tree_panel)
        splitter.addWidget(tree_group)

        self._source_list = _SourceListWidget(self)
        splitter.addWidget(self._source_list)
        # Give the source list more initial room so a multi-source config
        # isn't squashed into a 2-row sliver. User can still drag.
        splitter.setSizes([280, 240])
        root.addWidget(splitter, stretch=1)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Save manifest to:"))
        self._output_field = QLineEdit()
        self._output_field.setPlaceholderText("Output manifest path (.sqlite)")
        output_row.addWidget(self._output_field, stretch=1)
        browse_out_btn = QPushButton("Browse…")
        browse_out_btn.setFixedWidth(80)
        browse_out_btn.clicked.connect(self._browse_output)
        output_row.addWidget(browse_out_btn)
        root.addLayout(output_row)

        # Grouping parameters
        params_group = QGroupBox("Grouping Parameters")
        params_layout = QVBoxLayout(params_group)

        # pHash threshold
        phash_label = QLabel("<b>pHash Similarity Threshold</b> (default: 10, range: 1–20)")
        phash_desc = QLabel(
            "Perceptual hash Hamming distance between two images. "
            "A 64-bit pHash means images can differ by at most this many bits before being "
            "flagged as near-duplicates. <b>Lower = stricter</b> (fewer groups, less noise); "
            "<b>higher = more permissive</b> (catches more slightly-edited pairs)."
        )
        phash_desc.setWordWrap(True)
        phash_desc.setStyleSheet("color: #555;")
        phash_row = QHBoxLayout()
        self._phash_slider = QSlider(Qt.Orientation.Horizontal)
        self._phash_slider.setRange(1, 20)
        self._phash_slider.setValue(10)
        self._phash_spin = QSpinBox()
        self._phash_spin.setRange(1, 20)
        self._phash_spin.setValue(10)
        self._phash_spin.setFixedWidth(60)
        self._phash_slider.valueChanged.connect(self._phash_spin.setValue)
        self._phash_spin.valueChanged.connect(self._phash_slider.setValue)
        phash_row.addWidget(self._phash_slider, stretch=1)
        phash_row.addWidget(self._phash_spin)
        params_layout.addWidget(phash_label)
        params_layout.addWidget(phash_desc)
        params_layout.addLayout(phash_row)

        # Mean-color threshold
        color_label = QLabel("<b>Mean Color Gate</b> (default: 30, range: 0–100)")
        color_desc = QLabel(
            "L2 distance between the average RGB color of two images. "
            "After the pHash check, images whose mean colors differ by more than this value "
            "are excluded from grouping — catching pHash false positives where similar "
            "DCT structure but different colors were matched. "
            "<b>0 = disabled</b>; <b>higher = more permissive</b> color gate."
        )
        color_desc.setWordWrap(True)
        color_desc.setStyleSheet("color: #555;")
        color_row = QHBoxLayout()
        self._color_slider = QSlider(Qt.Orientation.Horizontal)
        self._color_slider.setRange(0, 100)
        self._color_slider.setValue(30)
        self._color_spin = QSpinBox()
        self._color_spin.setRange(0, 100)
        self._color_spin.setValue(30)
        self._color_spin.setFixedWidth(60)
        self._color_slider.valueChanged.connect(self._color_spin.setValue)
        self._color_spin.valueChanged.connect(self._color_slider.setValue)
        color_row.addWidget(self._color_slider, stretch=1)
        color_row.addWidget(self._color_spin)
        params_layout.addWidget(color_label)
        params_layout.addWidget(color_desc)
        params_layout.addLayout(color_row)

        root.addWidget(params_group)

        self._log_widget = QPlainTextEdit()
        self._log_widget.setReadOnly(True)
        self._log_widget.setMinimumHeight(150)
        self._log_widget.setPlaceholderText("Scan progress will appear here…")
        root.addWidget(self._log_widget)

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

    # ------------------------------------------------------------------ slots

    def _on_folder_requested(self, path: str) -> None:
        """Add the path emitted by the tree panel to the source list."""
        self._source_list.add_entry(path)

    def _browse_output(self) -> None:
        """Open a save-file dialog to choose the manifest output path."""
        from app.views.handlers.file_operations import MANIFEST_FILE_FILTER

        start = self._output_field.text() or "migration_manifest.sqlite"
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "Save Manifest As",
            start,
            MANIFEST_FILE_FILTER,
        )
        if chosen:
            if not chosen.lower().endswith((".sqlite", ".db")):
                chosen += ".sqlite"
            self._output_field.setText(chosen)

    # ------------------------------------------------------------------ settings

    def _load_from_settings(self) -> None:
        """Populate the dialog from saved settings (new list format or legacy keys)."""
        sources_list = self.settings.get("sources.list")
        if sources_list:
            entries = [
                _SourceEntry(path=item["path"], recursive=item.get("recursive", True))
                for item in sources_list
                if isinstance(item, dict) and item.get("path")
            ]
            self._source_list.set_entries(entries)
        else:
            # Migrate from the legacy three-key format (iphone / takeout / jdrive)
            entries = []
            for key in ("iphone", "takeout", "jdrive"):
                path = self.settings.get(f"sources.{key}", "")
                if path:
                    entries.append(_SourceEntry(path=path, recursive=True))
            self._source_list.set_entries(entries)

        saved_out = self.settings.get("sources.output", "migration_manifest.sqlite")
        self._output_field.setText(saved_out or "migration_manifest.sqlite")

    def _save_to_settings(self) -> None:
        """Persist the current source list and output path to settings."""
        entries = self._source_list.entries()
        self.settings.set("sources.list", [
            {"path": entry.path, "recursive": entry.recursive} for entry in entries
        ])
        output = self._output_field.text().strip()
        if output:
            self.settings.set("sources.output", output)
        try:
            self.settings.save()
        except OSError:
            pass  # Non-fatal — settings-save failure should not interrupt the UI

    # ------------------------------------------------------------------ scan

    def _build_sources(
        self,
    ) -> tuple[dict[str, str], dict[str, bool], dict[str, int]]:
        """Build sources, recursive_map, and source_priority from the source list.

        Labels are auto-generated from folder basenames (internal only; not shown
        to the user — the full path is already visible in the table).

        Returns:
            A 3-tuple of (sources, recursive_map, source_priority) dicts keyed
            by the auto-generated label.
        """
        entries = self._source_list.entries()
        used_labels: set[str] = set()
        sources: dict[str, str] = {}
        recursive_map: dict[str, bool] = {}
        source_priority: dict[str, int] = {}

        for priority, entry in enumerate(entries):
            folder_name = Path(entry.path).name or "source"
            label = _auto_label(folder_name, used_labels)
            used_labels.add(label)
            sources[label] = entry.path
            recursive_map[label] = entry.recursive
            source_priority[label] = priority

        return sources, recursive_map, source_priority

    def _start_scan(self) -> None:
        """Validate inputs and launch the background scan worker."""
        if not self._source_list.entries():
            QMessageBox.warning(self, "No sources", "Please add at least one source folder.")
            return
        output = self._output_field.text().strip()
        if not output:
            QMessageBox.warning(
                self, "No output", "Please specify an output path for the manifest."
            )
            return

        self._save_to_settings()
        sources, recursive_map, source_priority = self._build_sources()

        self._log_widget.clear()
        self._log("Starting scan…")
        self._btn_scan.setEnabled(False)

        self._worker = ScanWorker(
            sources=sources,
            output_path=output,
            recursive_map=recursive_map,
            source_priority=source_priority,
            threshold=self._phash_slider.value(),
            mean_color_threshold=self._color_slider.value(),
        )
        self._worker.progress.connect(self._log)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.completed_empty.connect(self._on_completed_empty)
        self._worker.start()

    def _log(self, msg: str) -> None:
        """Append ``msg`` to the progress log and scroll to the bottom."""
        self._log_widget.appendPlainText(msg)
        self._log_widget.verticalScrollBar().setValue(
            self._log_widget.verticalScrollBar().maximum()
        )

    def _on_finished(self, manifest_path: str) -> None:
        """Handle scan completion: switch Close button to Close & Load."""
        self.manifest_path = manifest_path
        self._btn_scan.setEnabled(True)
        # `&&` escapes the ampersand so Qt doesn't interpret it as a mnemonic
        # prefix and silently drop it on display (which produced the "Close
        # double-space Load" bug — #54).
        self._btn_close.setText("Close && Load")
        self._btn_close.clicked.disconnect()
        self._btn_close.clicked.connect(self._load_and_close)

    def _on_failed(self, error: str) -> None:
        """Handle scan failure: log the error and re-enable the scan button."""
        self._log(f"\nERROR: {error}")
        self._btn_scan.setEnabled(True)
        QMessageBox.critical(self, "Scan Failed", error)

    def _on_completed_empty(self) -> None:
        """Empty input is benign — re-enable Start Scan, no modal."""
        self._btn_scan.setEnabled(True)

    def _load_and_close(self) -> None:
        """Call the completion callback and close the dialog."""
        if self._on_complete and self.manifest_path:
            self._on_complete(self.manifest_path)
        self.accept()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop any running scan worker before closing."""
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(3000)
        super().closeEvent(event)
