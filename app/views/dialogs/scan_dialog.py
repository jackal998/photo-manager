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
from infrastructure.i18n import t


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
        self._path_error: QLabel
        self._build_ui()

    def _build_ui(self) -> None:
        """Construct the directory tree view, path entry, and add button."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Path entry — lets the user paste / type an absolute path instead of
        # scrolling the tree 10+ levels deep to a known fixture.
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel(t("scan_dialog.path_label")))
        self._path_field = QLineEdit()
        self._path_field.setPlaceholderText(t("scan_dialog.path_placeholder"))
        self._path_field.returnPressed.connect(self._on_add_typed)
        # Clear the error indicator as soon as the user types again — the
        # message was about whatever they had before, not what they have now.
        self._path_field.textChanged.connect(self._clear_path_error)
        path_add_btn = QPushButton(t("scan_dialog.add_button"))
        path_add_btn.setFixedWidth(80)
        path_add_btn.clicked.connect(self._on_add_typed)
        path_row.addWidget(self._path_field, stretch=1)
        path_row.addWidget(path_add_btn)
        layout.addLayout(path_row)

        # Inline error label — surfaces non-existent / non-directory paths
        # so "+ Add" stops being a silent no-op (#144). Hidden until needed
        # so the row stays compact in the common case.
        self._path_error = QLabel("")
        self._path_error.setStyleSheet("color: #b00020;")
        self._path_error.setWordWrap(True)
        self._path_error.setVisible(False)
        layout.addWidget(self._path_error)

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

        add_btn = QPushButton(t("scan_dialog.add_selected_button"))
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

        Empty input stays a silent no-op (the user clicked ``+ Add`` with
        nothing typed — barking at them would be noise). A typed path that
        doesn't resolve to an existing directory surfaces via the inline
        error label below the row (#144) so the click stops being a silent
        no-op.
        """
        raw = self._path_field.text().strip().strip('"')
        if not raw:
            return
        if not Path(raw).is_dir():
            self._path_error.setText(t("scan_dialog.path_not_found", path=raw))
            self._path_error.setVisible(True)
            return
        self.folder_requested.emit(raw)
        self._path_field.clear()
        self._clear_path_error()

    def _clear_path_error(self) -> None:
        """Hide the inline error label and drop its text.

        Called both on successful add and on every keystroke — the message
        was about the previous value of the field; once the user edits it,
        the message no longer corresponds to what's there.
        """
        self._path_error.setText("")
        self._path_error.setVisible(False)

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
        header_row.addWidget(QLabel(t("scan_dialog.source_list_header")))
        header_row.addStretch()
        remove_all_btn = QPushButton(t("scan_dialog.remove_all"))
        remove_all_btn.clicked.connect(self.clear)
        header_row.addWidget(remove_all_btn)
        layout.addLayout(header_row)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            t("scan_dialog.table_col_priority"),
            t("scan_dialog.table_col_path"),
            t("scan_dialog.table_col_recursive"),
            "",
            "",
        ])
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
            up_btn.setToolTip(t("scan_dialog.tooltip_move_up"))
            up_btn.clicked.connect(lambda _, row=row_idx: self._move(row, -1))
            dn_btn = QPushButton("↓")
            dn_btn.setFixedWidth(26)
            dn_btn.setToolTip(t("scan_dialog.tooltip_move_down"))
            dn_btn.clicked.connect(lambda _, row=row_idx: self._move(row, +1))
            ud_layout.addWidget(up_btn)
            ud_layout.addWidget(dn_btn)
            self._table.setCellWidget(row_idx, 3, ud_widget)

            rm_btn = QPushButton("×")
            rm_btn.setFixedWidth(26)
            rm_btn.setToolTip(t("scan_dialog.tooltip_remove"))
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
        should_proceed: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("scan_dialog.title"))
        # Two-column layout (tree+list left, output/params/log right) needs
        # ~1000 px to be comfortable; below that the source-list paths and
        # the slider rows start to crowd. Height stays at 600 — the inner
        # vertical splitters absorb shorter windows gracefully.
        self.setMinimumWidth(1000)
        self.setMinimumHeight(600)
        self.settings = settings
        self._on_complete = on_scan_complete
        # Optional gate fired right before the scan worker starts. Returns
        # True to proceed, False to cancel. Defaults to always-proceed so
        # callers (and tests) that don't care can omit it.
        # See photo-manager#142 — used by MainWindow to prompt when the
        # currently-loaded manifest has pending user decisions that would
        # be replaced by a re-scan.
        self._should_proceed = should_proceed or (lambda: True)
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

        notice = QLabel(t("scan_dialog.notice"))
        notice.setWordWrap(True)
        notice.setStyleSheet("color: #555; font-style: italic; padding: 4px 0;")
        root.addWidget(notice)

        # Two-column layout: tree + source list on the left (each gets a full
        # column-half via an inner vertical splitter), output / params / log
        # on the right (also vertically split so the user can drag the log
        # taller during a long scan). Replaces the previous flat vertical
        # stack where tree and list were squeezed into the top ~520 px.
        outer_splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left pane: tree + source list ────────────────────────────────
        left_splitter = QSplitter(Qt.Orientation.Vertical)

        tree_group = QGroupBox(t("scan_dialog.browse_group"))
        tree_layout = QVBoxLayout(tree_group)
        self._tree_panel = _FolderTreePanel(self)
        self._tree_panel.folder_requested.connect(self._on_folder_requested)
        tree_layout.addWidget(self._tree_panel)
        left_splitter.addWidget(tree_group)

        self._source_list = _SourceListWidget(self)
        left_splitter.addWidget(self._source_list)
        # Roughly 50/50 — both lists are equally valuable and the user can
        # drag if their workflow favours one.
        left_splitter.setSizes([320, 280])

        outer_splitter.addWidget(left_splitter)

        # ── Right pane: output + params (top), log (bottom) ──────────────
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        right_top = QWidget()
        right_top_layout = QVBoxLayout(right_top)
        right_top_layout.setContentsMargins(0, 0, 0, 0)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel(t("scan_dialog.output_label")))
        self._output_field = QLineEdit()
        self._output_field.setPlaceholderText(t("scan_dialog.output_placeholder"))
        output_row.addWidget(self._output_field, stretch=1)
        browse_out_btn = QPushButton(t("scan_dialog.browse_button"))
        browse_out_btn.setFixedWidth(80)
        browse_out_btn.clicked.connect(self._browse_output)
        output_row.addWidget(browse_out_btn)
        right_top_layout.addLayout(output_row)

        # Advanced settings — pHash threshold + mean-color gate.
        #
        # Collapsed by default (the 95% case never touches these knobs).
        # Qt's ``QGroupBox.setCheckable`` renders the title as a checkbox,
        # but by default Qt only DISABLES the children when unchecked —
        # they stay visible (just greyed) and keep occupying space. To
        # truly collapse and reclaim the vertical space, wrap all the
        # content in a child ``QWidget`` and toggle its visibility
        # alongside the check state.
        #
        # State is persisted to ``ui.scan_dialog.advanced_expanded`` via
        # ``_on_advanced_toggled`` so power users who keep it open don't
        # re-toggle every session. See photo-manager#163.
        self._params_group = QGroupBox(t("scan_dialog.advanced_settings_label"))
        self._params_group.setCheckable(True)
        self._params_group.setChecked(False)  # default — will be overwritten by _load_from_settings
        self._params_group.toggled.connect(self._on_advanced_toggled)
        outer_params_layout = QVBoxLayout(self._params_group)
        # Removing the default groupbox padding around the collapsible
        # container — when collapsed, the title-row alone should be
        # visually compact, not framed by extra whitespace.
        outer_params_layout.setContentsMargins(8, 4, 8, 4)
        self._params_content = QWidget()
        self._params_content.setVisible(False)  # matches setChecked(False) above
        outer_params_layout.addWidget(self._params_content)
        params_layout = QVBoxLayout(self._params_content)
        params_layout.setContentsMargins(0, 0, 0, 0)

        # pHash threshold
        phash_label = QLabel(t("scan_dialog.phash_label"))
        phash_desc = QLabel(t("scan_dialog.phash_desc"))
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
        color_label = QLabel(t("scan_dialog.color_label"))
        color_desc = QLabel(t("scan_dialog.color_desc"))
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

        right_top_layout.addWidget(self._params_group)
        # Trailing stretch so the params group hugs the top and any extra
        # vertical room in the right-top pane stays empty rather than
        # inflating the slider rows.
        right_top_layout.addStretch(1)

        right_splitter.addWidget(right_top)

        self._log_widget = QPlainTextEdit()
        self._log_widget.setReadOnly(True)
        self._log_widget.setMinimumHeight(150)
        self._log_widget.setPlaceholderText(t("scan_dialog.log_placeholder"))
        right_splitter.addWidget(self._log_widget)
        right_splitter.setSizes([340, 260])

        outer_splitter.addWidget(right_splitter)
        # Left column slightly wider — paths and the source-list table
        # benefit more from horizontal room than the slider rows do.
        outer_splitter.setSizes([550, 450])

        root.addWidget(outer_splitter, stretch=1)

        self._btn_scan = QPushButton(t("scan_dialog.start_button"))
        self._btn_scan.setDefault(True)
        self._btn_scan.clicked.connect(self._start_scan)
        self._btn_close = QPushButton(t("scan_dialog.close_button"))
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
            t("scan_dialog.save_dialog_title"),
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

        # Advanced settings collapsed/expanded state (#163). Default = collapsed.
        # Read explicitly as bool — settings.get returns the raw stored value,
        # which may be a YAML-derived bool already.
        expanded = bool(self.settings.get("ui.scan_dialog.advanced_expanded", False))
        self._params_group.setChecked(expanded)

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

    def _on_advanced_toggled(self, expanded: bool) -> None:
        """Show/hide the content + persist expanded state (#163).

        Hiding the child container is what actually reclaims the
        vertical space — Qt's checkable QGroupBox alone only disables
        children, leaving them visible and space-occupying.

        Saves the boolean ``ui.scan_dialog.advanced_expanded`` on every
        toggle so the user's last state survives the next dialog open
        without depending on the source-list save path firing.
        """
        self._params_content.setVisible(expanded)
        self.settings.set("ui.scan_dialog.advanced_expanded", expanded)
        try:
            self.settings.save()
        except OSError:
            pass  # Non-fatal — see _save_to_settings rationale

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
            QMessageBox.warning(
                self,
                t("scan_dialog.no_sources_title"),
                t("scan_dialog.no_sources_body"),
            )
            return
        output = self._output_field.text().strip()
        if not output:
            QMessageBox.warning(
                self,
                t("scan_dialog.no_output_title"),
                t("scan_dialog.no_output_body"),
            )
            return

        # Gate (#142): host can prompt if the loaded manifest has pending
        # decisions that this scan would replace. Default callback always
        # returns True so freshly-launched / unconnected dialogs are
        # unchanged.
        if not self._should_proceed():
            return

        self._save_to_settings()
        sources, recursive_map, source_priority = self._build_sources()

        self._log_widget.clear()
        self._log(t("scan_dialog.log_starting"))
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
        self._btn_close.setText(t("scan_dialog.close_load_button"))
        self._btn_close.clicked.disconnect()
        self._btn_close.clicked.connect(self._load_and_close)

    def _on_failed(self, error: str) -> None:
        """Handle scan failure: log the error and re-enable the scan button."""
        self._log(t("scan_dialog.log_error", error=error))
        self._btn_scan.setEnabled(True)
        QMessageBox.critical(self, t("scan_dialog.scan_failed_title"), error)
        # No manifest was produced; Close is the canonical exit. Pull focus
        # there so the user has an obvious next action (focus ring + Enter
        # dismisses) instead of a UI that looks identical to pre-scan (#86).
        self._btn_close.setFocus()

    def _on_completed_empty(self) -> None:
        """Empty input is benign — re-enable Start Scan, no modal."""
        self._btn_scan.setEnabled(True)
        # Same rationale as _on_failed (#86): no manifest produced, Close is
        # the way out, focus gives the user a visible signal that the scan
        # ended. Start Scan stays enabled so the user can fix sources and
        # retry without dismissing the dialog.
        self._btn_close.setFocus()

    def _load_and_close(self) -> None:
        """Call the completion callback and close the dialog."""
        if self._on_complete and self.manifest_path:
            self._on_complete(self.manifest_path)
        self.accept()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop any running scan worker before closing.

        Kept (rather than removed per #97's audit) because the override has a
        concrete job: a user clicking the title-bar X mid-scan must trigger
        worker.requestInterruption() so the QThread shuts down cleanly. Without
        this hook, the dialog dismisses but the worker keeps running on a
        detached thread until the process exits — that's the partial-state
        leak s03_cancel_scan was written to catch.
        """
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(3000)
        super().closeEvent(event)
