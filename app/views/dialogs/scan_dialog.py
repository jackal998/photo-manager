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
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
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

from app.views.window_state import (
    QSETTINGS_KEY_SCAN_DIALOG_GEOM,
    restore_widget_geometry,
    save_widget_geometry,
)
from app.views.workers.scan_worker import ScanWorker
from infrastructure.i18n import t
from scanner.workers import default_hash_workers


@dataclass
class _SourceEntry:
    """One user-selected source folder with its scan options."""

    path: str
    recursive: bool = True


# #424 — scan progress UI: stage label, files-per-sec, ETA helpers.
# Pure functions kept module-private so unit tests can pin the
# formatting contract without instantiating QDialog.

# Receiver-side rolling window for ETA stability — matches the worker
# tracker's window so "ETA appears once ≥ 5s of throughput samples are
# available" lines up on both sides.
_ETA_MIN_SAMPLES_SECONDS = 5.0


def _format_throughput(files_per_sec: float) -> str:
    """Render files/sec for the dialog's third progress row.

    Returns ``"—"`` when the rate is zero or negative (stall, or
    sub-1s into a stage where the worker's deque only has one
    sample). Above 10 files/sec we drop the decimal — the precision
    isn't useful and a wobbling tenths digit on a 200/s rate looks
    busier than the underlying scan."""
    if files_per_sec <= 0.0:
        return "—"
    if files_per_sec >= 10.0:
        return f"{files_per_sec:.0f} files/sec"
    return f"{files_per_sec:.1f} files/sec"


def _format_eta(remaining: int, files_per_sec: float) -> str:
    """Render an ETA string from remaining-count and throughput.

    Returns ``"—"`` when (a) throughput is zero / negative (stall),
    (b) remaining is non-positive (stage already complete). The ≥5s-
    samples gate is enforced by the caller via the receiver's own
    stage-elapsed timer — the worker already drops sub-window
    throughput to 0, so this function trusts what it gets."""
    if files_per_sec <= 0.0 or remaining <= 0:
        return "—"
    seconds = remaining / files_per_sec
    if seconds < 1.0:
        return "<1s"
    if seconds < 60.0:
        return f"~{int(seconds)}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"~{minutes}m {secs:02d}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"~{hours}h {mins:02d}m"


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


def _tip(text: str) -> str:
    """Wrap tooltip text so Qt word-wraps it to a readable width.

    QToolTip only word-wraps a tooltip when it is *rich text*; a long plain
    string renders on one over-long line — and CJK (no spaces) never breaks,
    so zh_TW tooltips ran ~1400px wide off the edge. Embedding the (escaped)
    text in a fixed-width table cell forces rich-text mode and a ~360px wrap,
    giving a tidy multi-line block in every locale.
    """
    from html import escape

    return f"<table><tr><td width='360'>{escape(text)}</td></tr></table>"


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
    """Table listing the selected source folders, displayed sorted by path.

    Columns: path, Recursive checkbox, × remove.

    Display order is always re-derived from ``self._entries`` by sorting on
    path (case-insensitive). The underlying ``self._entries`` list stays
    insertion-ordered so ``add_entry``'s duplicate-path check keeps working.

    Signals:
        changed: Emitted whenever the list content changes.
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

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([
            t("scan_dialog.table_col_path"),
            t("scan_dialog.table_col_recursive"),
            "",
        ])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
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
        """Repopulate the table widget from ``self._entries``, sorted by path.

        Display order is derived fresh from ``self._entries`` at every
        rebuild by sorting case-insensitively on path. Per-row callbacks
        capture the entry's index in the canonical ``self._entries`` list
        so add/remove/toggle keep operating on the same entry regardless
        of where it lands in the sorted display.
        """
        self._table.setRowCount(0)
        sorted_entries = sorted(self._entries, key=lambda e: e.path.casefold())
        for display_row, entry in enumerate(sorted_entries):
            entry_idx = self._entries.index(entry)
            self._table.insertRow(display_row)

            self._table.setItem(display_row, 0, QTableWidgetItem(entry.path))

            check = QCheckBox()
            check.setChecked(entry.recursive)
            check.stateChanged.connect(
                lambda state, idx=entry_idx: self._on_recursive_changed(idx, state)
            )
            self._table.setCellWidget(display_row, 1, self._centered(check))

            rm_btn = QPushButton("×")
            rm_btn.setFixedWidth(26)
            rm_btn.setToolTip(t("scan_dialog.tooltip_remove"))
            rm_btn.clicked.connect(lambda _, idx=entry_idx: self._remove(idx))
            self._table.setCellWidget(display_row, 2, rm_btn)

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

    # #468 — defense-in-depth signals so MainWindow can track whether a
    # scan worker is alive without poking at ``self._worker`` from
    # outside the dialog. ``scan_started`` fires immediately before
    # ``self._worker.start()`` in :meth:`_start_scan`; ``scan_finished``
    # fires from every worker-exit path (success / failure /
    # completed-empty) and from :meth:`closeEvent` after the worker has
    # been interrupted + waited. The receiver-side "is the worker
    # running" state is the strict XOR of these two — no third terminal
    # state to wire.
    scan_started = Signal()
    scan_finished = Signal()

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
        self._dhash_slider: QSlider
        self._dhash_spin: QSpinBox
        self._color_slider: QSlider
        self._color_spin: QSpinBox
        self._auto_select_check: QCheckBox

        self._build_ui()
        self._load_from_settings()
        # #215 — restore last saved geometry (if any). Runs after the
        # widget tree is built so Qt has a layout to apply the rect
        # against; falls back to the setMinimumWidth/Height defaults
        # above when no saved geometry exists or it would land off-
        # screen (multi-monitor disconnect).
        restore_widget_geometry(self, QSETTINGS_KEY_SCAN_DIALOG_GEOM)

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
        phash_desc.setStyleSheet("color: #555;")
        phash_desc.setToolTip(_tip(t("scan_dialog.phash_tooltip")))
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
        # Small gap so the one-line description clears the slider frame.
        params_layout.addSpacing(6)
        params_layout.addLayout(phash_row)

        # dHash confidence threshold (#517) — the second, independent
        # perceptual hash that confirms a pHash near-dup match (high vs low
        # confidence). Sits directly below pHash; mirrors its 1–20 range.
        dhash_label = QLabel(t("scan_dialog.dhash_label"))
        dhash_desc = QLabel(t("scan_dialog.dhash_desc"))
        dhash_desc.setStyleSheet("color: #555;")
        dhash_desc.setToolTip(_tip(t("scan_dialog.dhash_tooltip")))
        dhash_row = QHBoxLayout()
        self._dhash_slider = QSlider(Qt.Orientation.Horizontal)
        self._dhash_slider.setRange(1, 20)
        self._dhash_slider.setValue(10)
        self._dhash_spin = QSpinBox()
        self._dhash_spin.setRange(1, 20)
        self._dhash_spin.setValue(10)
        self._dhash_spin.setFixedWidth(60)
        self._dhash_slider.valueChanged.connect(self._dhash_spin.setValue)
        self._dhash_spin.valueChanged.connect(self._dhash_slider.setValue)
        dhash_row.addWidget(self._dhash_slider, stretch=1)
        dhash_row.addWidget(self._dhash_spin)
        params_layout.addWidget(dhash_label)
        params_layout.addWidget(dhash_desc)
        params_layout.addSpacing(6)
        params_layout.addLayout(dhash_row)

        # Mean-color threshold
        color_label = QLabel(t("scan_dialog.color_label"))
        color_desc = QLabel(t("scan_dialog.color_desc"))
        color_desc.setStyleSheet("color: #555;")
        color_desc.setToolTip(_tip(t("scan_dialog.color_tooltip")))
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
        # Small gap so the one-line description clears the slider frame.
        params_layout.addSpacing(6)
        params_layout.addLayout(color_row)

        # Auto-select after scan (#212).
        # Opt-in: when on, the scan worker promotes the top-scored row in
        # each duplicate group to action="KEEP" before writing the
        # manifest. Other duplicates stay at their classifier action so
        # the user still confirms deletions explicitly. Persists via
        # ``ui.scan_dialog.auto_select_enabled`` on toggle (mirrors the
        # ``advanced_expanded`` save path) so the choice survives a
        # close/reopen.
        self._auto_select_check = QCheckBox(t("scan_dialog.auto_select_label"))
        auto_select_desc = QLabel(t("scan_dialog.auto_select_desc"))
        auto_select_desc.setStyleSheet("color: #555;")
        auto_select_desc.setToolTip(_tip(t("scan_dialog.auto_select_tooltip")))
        self._auto_select_check.toggled.connect(self._on_auto_select_toggled)
        params_layout.addWidget(self._auto_select_check)
        params_layout.addWidget(auto_select_desc)

        # Aggressive auto-select sub-option (#393).
        # Opt-in on top of the parent — disabled when the parent is off.
        # When on, every non-keeper row in a scored group receives
        # user_decision='delete' so the user opens Execute Action with
        # the full triage pre-populated. Indented one level to read as
        # a sub-option of the parent.
        self._auto_select_aggressive_check = QCheckBox(
            t("scan_dialog.auto_select_aggressive_label")
        )
        auto_select_aggressive_desc = QLabel(
            t("scan_dialog.auto_select_aggressive_desc")
        )
        auto_select_aggressive_desc.setStyleSheet("color: #555;")
        auto_select_aggressive_desc.setToolTip(
            _tip(t("scan_dialog.auto_select_aggressive_tooltip"))
        )
        self._auto_select_aggressive_check.toggled.connect(
            self._on_auto_select_aggressive_toggled
        )
        params_layout.addWidget(self._auto_select_aggressive_check)
        params_layout.addWidget(auto_select_aggressive_desc)

        # #551 Phase 3 — opt-in read-knee autotune. Default OFF: when enabled the
        # scan ramps reader concurrency per device at the start and settles on the
        # measured knee instead of the static NAS=8 / HDD=1 / else guess. Persists
        # via ``ui.scan_dialog.autotune_read_knee`` (mirrors auto_select). Never
        # changes which duplicates are found — only read speed (#551 Q5 determinism).
        # Added to params_layout (inside the existing params group), NOT a new
        # splitter child (#467 cold-launch fragility).
        self._autotune_read_knee_check = QCheckBox(
            t("scan_dialog.autotune_read_knee_label")
        )
        autotune_read_knee_desc = QLabel(t("scan_dialog.autotune_read_knee_desc"))
        autotune_read_knee_desc.setStyleSheet("color: #555;")
        autotune_read_knee_desc.setToolTip(
            _tip(t("scan_dialog.autotune_read_knee_tooltip"))
        )
        self._autotune_read_knee_check.toggled.connect(
            self._on_autotune_read_knee_toggled
        )
        params_layout.addWidget(self._autotune_read_knee_check)
        params_layout.addWidget(autotune_read_knee_desc)

        # #486/#560 — hash-pool calibration is now always-on (the per-scan
        # "auto" default), so there is no user-facing re-calibrate toggle. The
        # power-user ``scan.hash_pool`` = "thread"/"process" escape hatch in
        # settings.json still overrides; resolution lives in _resolve_hash_pool.

        right_top_layout.addWidget(self._params_group)
        # Trailing stretch so the params group hugs the top and any extra
        # vertical room in the right-top pane stays empty rather than
        # inflating the slider rows.
        right_top_layout.addStretch(1)

        right_splitter.addWidget(right_top)

        # #424 — receiver-side bookkeeping for the ≥5s-samples ETA gate.
        # Reset on every stage change so a freshly-started stage hides
        # the ETA until it accumulates enough samples — without this
        # the prior stage's settled throughput would leak into the new
        # stage's first emit and produce a misleading ETA.
        self._current_stage: str | None = None
        self._stage_started_at_monotonic: float = 0.0

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

        # #424 — Stage / throughput / ETA frame as a top-level row
        # under the outer_splitter, ABOVE the action buttons. Kept
        # outside the splitter so the right_splitter stays at its
        # original 2-widget configuration — the 3-widget variant
        # broke qa(2):s02 (cold-launch dialog show event blocked).
        # Initially hidden; revealed on the first stage_progress emit.
        self._progress_frame = QFrame()
        # objectName lets the layer-3 s02 driver locate this frame via
        # UIA and assert it's hidden after an empty/failed scan (#510).
        self._progress_frame.setObjectName("scanProgressFrame")
        self._progress_frame.setFrameShape(QFrame.StyledPanel)
        self._progress_frame.setVisible(False)
        pf_layout = QVBoxLayout(self._progress_frame)
        pf_layout.setContentsMargins(8, 6, 8, 6)
        self._stage_label = QLabel("")
        self._stage_label.setStyleSheet("font-weight: bold;")
        pf_layout.addWidget(self._stage_label)
        self._stage_progress_bar = QProgressBar()
        self._stage_progress_bar.setTextVisible(True)
        pf_layout.addWidget(self._stage_progress_bar)
        self._stage_rate_label = QLabel("")
        self._stage_rate_label.setStyleSheet("color: #555; font-family: monospace;")
        pf_layout.addWidget(self._stage_rate_label)
        root.addWidget(self._progress_frame)

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

        # Bare relative filenames (e.g. "migration_manifest.sqlite") confuse
        # Qt on Windows: getSaveFileName opens against the process CWD —
        # unpredictable — and can render a folder-picker-flavoured dialog
        # instead of the polished save-file UI (#216). Pass an absolute
        # path when we have one; pass "" otherwise so Qt falls back to
        # its remembered last-visited directory (matches the Open Manifest
        # flow in file_operations._on_open_manifest).
        text = self._output_field.text().strip()
        start = str(Path(text).resolve()) if text else ""
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
            # Migration shim (since the 2025 "sources.list" rollout):
            # users upgrading from pre-sources.list builds still carry
            # only the legacy sources.{iphone,takeout,jdrive} keys.
            # Removing this branch silently empties their source list
            # on first launch -- no error, no warning, just zero sources.
            # tests/test_settings_migration.py pins the contract; a PR
            # that intentionally drops this shim must drop that test
            # too and ship a migration story. See #258.
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

        # Auto-select keepers after scan (#212). Default = False so the
        # pre-#212 behaviour is preserved for users who haven't opted in.
        auto_select = bool(
            self.settings.get("ui.scan_dialog.auto_select_enabled", False)
        )
        self._auto_select_check.setChecked(auto_select)

        # Aggressive mode (#393). Default = False — destructive-leaning,
        # must be explicitly opted into. The checkbox is gated on the
        # parent: disabled when auto-select itself is off.
        aggressive = bool(
            self.settings.get(
                "ui.scan_dialog.auto_select_aggressive_delete", False
            )
        )
        self._auto_select_aggressive_check.setChecked(aggressive)
        self._auto_select_aggressive_check.setEnabled(auto_select)

        # Read-knee autotune (#551 Phase 3). Default = False — opt-in,
        # experimental read-speed tuning that never affects scan results.
        autotune_read_knee = bool(
            self.settings.get("ui.scan_dialog.autotune_read_knee", False)
        )
        self._autotune_read_knee_check.setChecked(autotune_read_knee)

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

    def _on_auto_select_toggled(self, enabled: bool) -> None:
        """Persist the auto-select checkbox on every toggle (#212).

        Same persistence shape as ``_on_advanced_toggled`` — write
        immediately on change so the user's choice survives the next
        dialog open without depending on the scan-start save path
        firing.

        Also gates the aggressive sub-option (#393): the aggressive
        checkbox is meaningless when the parent is off, so disable it
        in that state.
        """
        self.settings.set("ui.scan_dialog.auto_select_enabled", enabled)
        self._auto_select_aggressive_check.setEnabled(enabled)
        try:
            self.settings.save()
        except OSError:
            pass  # Non-fatal — see _save_to_settings rationale

    def _on_auto_select_aggressive_toggled(self, enabled: bool) -> None:
        """Persist the aggressive sub-option on every toggle (#393).

        Mirrors ``_on_auto_select_toggled`` — write through on every
        change so the user's choice survives a close/reopen cycle.
        """
        self.settings.set(
            "ui.scan_dialog.auto_select_aggressive_delete", enabled
        )
        try:
            self.settings.save()
        except OSError:
            pass  # Non-fatal — see _save_to_settings rationale

    def _on_autotune_read_knee_toggled(self, enabled: bool) -> None:
        """Persist the read-knee autotune opt-in on every toggle (#551 Phase 3).

        Same write-through shape as ``_on_auto_select_toggled`` so the choice
        survives a close/reopen without depending on the scan-start save path.
        """
        self.settings.set("ui.scan_dialog.autotune_read_knee", enabled)
        try:
            self.settings.save()
        except OSError:
            pass  # Non-fatal — see _save_to_settings rationale

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
    ) -> tuple[dict[str, str], dict[str, bool]]:
        """Build sources and recursive_map from the source list.

        Labels are auto-generated from folder basenames (internal only; not shown
        to the user — the full path is already visible in the table).

        Scan-order priority is not produced here: the scanner auto-infers it
        from iteration order when ``source_priority`` is omitted from the
        ``ScanWorker`` call, and the final dedup sort (group name → score →
        file name) does not depend on source order.

        Returns:
            A 2-tuple of (sources, recursive_map) dicts keyed by the
            auto-generated label.
        """
        entries = self._source_list.entries()
        used_labels: set[str] = set()
        sources: dict[str, str] = {}
        recursive_map: dict[str, bool] = {}

        for entry in entries:
            folder_name = Path(entry.path).name or "source"
            label = _auto_label(folder_name, used_labels)
            used_labels.add(label)
            sources[label] = entry.path
            recursive_map[label] = entry.recursive

        return sources, recursive_map

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
        sources, recursive_map = self._build_sources()

        self._log_widget.clear()
        self._log(t("scan_dialog.log_starting"))
        self._btn_scan.setEnabled(False)

        # #451 — exif_workers is a setting-only knob today (no UI). Default
        # 2 keeps the rollout conservative; raise via settings.json
        # ``scan.exif_workers`` if EXIF dominates after enabling
        # parallel hash workers. The worker clamps the value at
        # construction so user-supplied 99 doesn't blow up the box.
        exif_workers = self.settings.get("scan.exif_workers", 2)
        try:
            exif_workers = int(exif_workers)
        except (TypeError, ValueError):
            exif_workers = 2

        hash_pool, hash_pool_rates = self._resolve_hash_pool(sources, recursive_map)

        self._worker = ScanWorker(
            sources=sources,
            output_path=output,
            recursive_map=recursive_map,
            threshold=self._phash_slider.value(),
            dhash_threshold=self._dhash_slider.value(),
            mean_color_threshold=self._color_slider.value(),
            # #449 control removed — the hash-worker count is the NAS-aware
            # auto-pick (8 if any source is remote, else min(4, cpu_count)),
            # computed fresh from the current sources at scan time. The HASH
            # pool type (thread/process) is still chosen by the #486
            # re-calibration. No manual override.
            workers=default_hash_workers(
                [entry.path for entry in self._source_list.entries()]
            ),
            exif_workers=exif_workers,
            hash_pool=hash_pool,
            hash_pool_rates=hash_pool_rates,
            auto_select_enabled=self._auto_select_check.isChecked(),
            auto_select_aggressive_delete=(
                self._auto_select_aggressive_check.isChecked()
            ),
            # #551 Phase 3 — opt-in read-knee autotune (default OFF) + the
            # device_key-keyed knee cache so a knee measured on a prior scan of
            # this physical device is reused with no ramp. The worker ignores
            # both unless autotune_read_knee is True.
            autotune_read_knee=self._autotune_read_knee_check.isChecked(),
            autotune_knees=self.settings.get("scan.read_knee_cache", {}) or {},
        )
        self._worker.progress.connect(self._log)
        self._worker.stage_progress.connect(self._on_stage_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.completed_empty.connect(self._on_completed_empty)
        # Persist a fresh calibration (cache miss) under its fingerprint so
        # the next scan of the same library skips the re-measurement.
        self._worker.hash_pool_measured.connect(self._on_hash_pool_measured)
        # #551 Phase 3 — persist a freshly-measured read-knee (sole-ramping
        # device) under its device_key so the next scan of that device skips the
        # ramp and starts at the cached knee.
        self._worker.read_knee_measured.connect(self._on_read_knee_measured)
        # Reset the stage frame for the new scan: hide until the
        # first stage_progress fires, clear residual labels so the
        # frame can't briefly show prior-scan numbers.
        self._reset_progress_ui()
        # #468 — emit BEFORE start() so a connected slot that flips a
        # "scan_running" flag is set by the time the worker thread is
        # alive. Emit-after would race the worker's first signals.
        self.scan_started.emit()
        self._worker.start()

    def _resolve_hash_pool(self, sources: dict, recursive_map: dict):
        """#486/#560 — resolve the HASH-stage executor (+ for "auto", its
        cached calibration). Returns ``(hash_pool, hash_pool_rates)`` and sets
        ``self._hash_pool_fp`` for the post-scan cache write.

        Calibration is now **always-on**: ``scan.hash_pool`` defaults to
        ``"auto"`` and there is no user-facing toggle (#560 — the per-scan
        cost is low, so always-calibrate is the non-user-facing default).

        * **auto (default)** → fingerprint the machine + sources; on a cache
          hit reuse the rates (the worker re-projects them to the current file
          count); on a cache **miss** calibrate **silently** this scan (rates
          stay ``None`` so the worker measures + caches). No modal — the #554
          multi-device+NAS guard already short-circuits the one risky case
          (mixed HDD+NAS) to the per-device thread path inside the worker.
        * **thread / process** (settings.json power-user override) → use the
          explicit value as-is, no fingerprint, no calibration.

        Extracted from ``_start_scan`` so the resolution is unit-testable
        without launching the worker thread.
        """
        import os as _os
        from app.views.workers.scan_worker import hash_pool_fingerprint

        hash_pool = self.settings.get("scan.hash_pool", "auto")
        hash_pool_rates = None
        self._hash_pool_fp = None

        if hash_pool == "auto":
            self._hash_pool_fp = hash_pool_fingerprint(
                sources, recursive_map, _os.cpu_count() or 4
            )
            cache = self.settings.get("scan.hash_pool_cache", {}) or {}
            # Cache hit → reuse the measured rates; miss → leave rates None so
            # the worker calibrates silently this scan and caches the result.
            hash_pool_rates = cache.get(self._hash_pool_fp)

        return hash_pool, hash_pool_rates

    def _on_hash_pool_measured(self, rates: dict) -> None:
        """#486-PR3b — persist a fresh hash-pool calibration under its
        machine+sources fingerprint so the next scan of the same library
        reuses the measured rates instead of re-running the ~2s calibration.
        Fires only on a cache miss (the worker emits the rates it measured).
        """
        if not self._hash_pool_fp:
            return
        from app.views.workers.scan_worker import store_hash_pool_rates

        store_hash_pool_rates(self.settings, self._hash_pool_fp, rates)

    def _on_read_knee_measured(self, summary: dict) -> None:
        """#551 Phase 3 — persist a freshly-measured read-knee keyed by
        ``device_key`` so the next scan of that physical device reuses it with
        no ramp. Fires once per sole-ramping device when its ramp freezes (the
        worker only emits for a clean, uncontended measurement). ``store_read_knee``
        flushes settings to disk, so no extra save is needed here.
        """
        from scanner.autotune import store_read_knee

        device = summary.get("device")
        knee = summary.get("knee")
        if device and isinstance(knee, int):
            store_read_knee(self.settings, device, knee)

    def _reset_progress_ui(self) -> None:
        """Hide the stage frame and clear its labels.

        #510 — the progress frame is revealed on the first
        ``stage_progress`` emit and was previously only reset at the
        TOP of the NEXT scan's ``_start_scan``. The terminal handlers
        ``_on_completed_empty`` / ``_on_failed`` leave the dialog open
        without resetting, so the bar + "scanning…" label stayed stuck
        after an empty/failed scan — making a benign empty result (or a
        scan that aborted, e.g. #509) look frozen. Calling this from
        every terminal handler hides the frame the moment the scan
        ends. Mirrors the reset block that already lived in
        ``_start_scan``.
        """
        self._progress_frame.setVisible(False)
        self._current_stage = None
        self._stage_label.setText("")
        self._stage_rate_label.setText("")

    def _on_stage_progress(
        self, stage_name: str, completed: int, total: int, files_per_sec: float
    ) -> None:
        """#424 — receiver for ScanWorker.stage_progress.

        Renders the stage label, progress bar (determinate when
        ``total > 0``; indeterminate when ``total == 0`` — atomic
        stages CLASSIFY/SCORE/WRITE), and the throughput/ETA line.
        ETA is suppressed (``"—"``) until ≥5s have elapsed since the
        current stage started, matching the issue's acceptance
        criterion. Stage transitions reset that timer.
        """
        import time
        # Reveal the frame on the first signal of any scan; cheap and
        # idempotent so repeated emits don't churn the layout.
        if not self._progress_frame.isVisible():
            self._progress_frame.setVisible(True)
        # Stage change → reset the elapsed-time gate so ETA on a
        # fresh stage isn't seeded by the prior stage's throughput.
        if stage_name != self._current_stage:
            self._current_stage = stage_name
            self._stage_started_at_monotonic = time.monotonic()
        # Stage label — receivers translate canonical names via the
        # translations table (falls back to the raw name if a
        # locale didn't ship a mapping yet).
        label_key = f"scan_dialog.stage_{stage_name.lower()}"
        translated = t(label_key)
        # Fallback: t() returns the key unchanged when missing — use
        # the canonical name to avoid showing "scan_dialog.stage_walk"
        # in the UI for a locale that hasn't been updated.
        stage_display = translated if translated != label_key else stage_name
        if total > 0:
            self._stage_label.setText(f"{stage_display}  ({completed:,}/{total:,})")
            self._stage_progress_bar.setRange(0, total)
            self._stage_progress_bar.setValue(completed)
            self._stage_progress_bar.setFormat("%p%")
        else:
            # Indeterminate stage — render a moving stripe. #448 added a
            # live counter via ``completed`` for the WALK stage (no
            # known total, but a running file count); show it when
            # non-zero so the user sees the walker is making progress
            # rather than staring at a bare "…" for minutes.
            if completed > 0:
                self._stage_label.setText(f"{stage_display}  ({completed:,})")
            else:
                self._stage_label.setText(f"{stage_display}  …")
            self._stage_progress_bar.setRange(0, 0)
        # Throughput + ETA row.
        rate_txt = _format_throughput(files_per_sec)
        elapsed = time.monotonic() - self._stage_started_at_monotonic
        if elapsed < _ETA_MIN_SAMPLES_SECONDS or total <= 0:
            eta_txt = "—"
        else:
            remaining = max(0, total - completed)
            eta_txt = _format_eta(remaining, files_per_sec)
        self._stage_rate_label.setText(f"{rate_txt}  —  ETA {eta_txt}")

    def _log(self, msg: str) -> None:
        """Append ``msg`` to the progress log and scroll to the bottom."""
        self._log_widget.appendPlainText(msg)
        self._log_widget.verticalScrollBar().setValue(
            self._log_widget.verticalScrollBar().maximum()
        )

    def _on_finished(self, manifest_path: str) -> None:
        """Handle scan completion: switch Close button to Close & Load."""
        # #510 — reset the progress frame for symmetry with the other
        # terminal handlers (the dialog closes on load, so this is
        # mostly belt-and-suspenders here).
        self._reset_progress_ui()
        self.manifest_path = manifest_path
        self._btn_scan.setEnabled(True)
        # `&&` escapes the ampersand so Qt doesn't interpret it as a mnemonic
        # prefix and silently drop it on display (which produced the "Close
        # double-space Load" bug — #54).
        self._btn_close.setText(t("scan_dialog.close_load_button"))
        self._btn_close.clicked.disconnect()
        self._btn_close.clicked.connect(self._load_and_close)
        # #468 — worker thread has exited; clear the receiver-side flag.
        self.scan_finished.emit()

    def _on_failed(self, error: str) -> None:
        """Handle scan failure: log the error and re-enable the scan button."""
        # #510 — hide the stage frame so the bar + "scanning…" label
        # don't stay stuck on a failed scan that leaves the dialog open.
        self._reset_progress_ui()
        self._log(t("scan_dialog.log_error", error=error))
        self._btn_scan.setEnabled(True)
        QMessageBox.critical(self, t("scan_dialog.scan_failed_title"), error)
        # No manifest was produced; Close is the canonical exit. Pull focus
        # there so the user has an obvious next action (focus ring + Enter
        # dismisses) instead of a UI that looks identical to pre-scan (#86).
        self._btn_close.setFocus()
        # #468 — worker thread has exited; clear the receiver-side flag.
        self.scan_finished.emit()

    def _on_completed_empty(self) -> None:
        """Empty input is benign — re-enable Start Scan, no modal."""
        # #510 — hide the stage frame so an empty-sources scan doesn't
        # leave the bar + "scanning…" label stuck, looking frozen.
        self._reset_progress_ui()
        self._btn_scan.setEnabled(True)
        # Same rationale as _on_failed (#86): no manifest produced, Close is
        # the way out, focus gives the user a visible signal that the scan
        # ended. Start Scan stays enabled so the user can fix sources and
        # retry without dismissing the dialog.
        self._btn_close.setFocus()
        # #468 — worker thread has exited; clear the receiver-side flag.
        self.scan_finished.emit()

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
        was_running = bool(self._worker and self._worker.isRunning())
        if was_running:
            self._worker.requestInterruption()
            # #491 — capture wait()'s bool return so a timeout is visible
            # in the log. False here means the QThread didn't tear down
            # within 3 s and is now orphaned: it keeps running with its
            # ExiftoolProcess subprocess(es) alive until the parent
            # Python process itself exits (which is why the
            # KILL_ON_JOB_CLOSE Job Object from #460 doesn't catch it —
            # parent is still alive). Pre-#491 this was a silent path:
            # the dialog dismissed and the "must close CMD window"
            # symptom appeared with no log signal to diagnose with.
            finished = self._worker.wait(3000)
            if not finished:
                from loguru import logger
                logger.warning(
                    "scan_dialog: worker.wait(3000) timed out on close — "
                    "QThread may be orphaned (was probably stuck in a "
                    "stage without a cancel checkpoint)"
                )
        if was_running:
            # #468 — the worker started but never hit its own terminal
            # signals; clear the receiver-side flag here instead.
            self.scan_finished.emit()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        """Persist geometry on every close path (#215).

        ``done()`` is the funnel for ``accept()`` / ``reject()`` and
        the default ``closeEvent`` route (X button → QDialog reject),
        so one hook here catches every dismissal in one place.
        """
        save_widget_geometry(self, QSETTINGS_KEY_SCAN_DIALOG_GEOM)
        super().done(result)
