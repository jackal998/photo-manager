"""Refactored MainWindow using extracted components.

This module contains the refactored MainWindow that uses specialized controllers
and handlers while preserving all existing public interfaces for backward compatibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTreeView,
)
from loguru import logger

from app.views.components.decision_tree_view import DecisionTreeView
from app.views.components.empty_state import build_empty_state_widget
from app.views.components.menu_controller import MenuController
from app.views.components.status_messages import plural_form, pluralize
from app.views.components.status_reporter_impl import StatusReporterImpl

# Import extracted components
from app.views.components.tree_controller import TreeController
from app.views.constants import COL_CREATION_DATE, COL_FOLDER, COL_GROUP, COL_NAME, COL_SHOT_DATE, COL_SIZE_BYTES, PATH_ROLE
from app.views.handlers.action_handlers import ActionHandlersImpl
from app.views.handlers.context_menu import ContextMenuHandler
from app.views.handlers.dialog_handler import DialogHandler
from app.views.handlers.file_operations import FileOperationsHandler
from app.views.image_tasks import ImageTaskRunner
from app.views.layout.layout_manager import LayoutManager
from app.views.preview_pane import PreviewPane
from app.views.window_state import (
    QSETTINGS_KEY_COLUMN_HEADER_STATE,
    QSETTINGS_KEY_MAIN_SPLITTER_STATE,
    QSETTINGS_KEY_MAIN_WINDOW_GEOM,
    qsettings_path,
    window_state_qsettings,
)
from infrastructure.i18n import t


# Button specs for the "Unsaved Changes" QMessageBox built in
# :meth:`MainWindow.closeEvent`. Tuple order is the order buttons appear
# in the dialog (left → right), which determines Tab traversal and is
# load-bearing for the qa batch runner's close-window dance — see
# :func:`qa.scenarios._close_window_helper.click_leave_button` and the
# matching L1 test in tests/test_main_window.py.
#
# Each entry: (name, translation_key, ButtonRole). ``name`` is the
# internal identifier closeEvent uses to look the button up after
# constructing them; it intentionally does NOT depend on locale or
# button text. ``"leave"`` is the only name external code (the qa
# helper) needs to know about.
EXIT_DIALOG_BUTTONS: tuple[tuple[str, str, QMessageBox.ButtonRole], ...] = (
    ("save", "exit.button_save_leave", QMessageBox.AcceptRole),
    ("leave", "exit.button_leave", QMessageBox.DestructiveRole),
    ("back", "exit.button_back", QMessageBox.RejectRole),
)


class MainWindow(QMainWindow):
    """Main application window with refactored architecture.

    This class maintains all existing public interfaces while using extracted
    components for better maintainability and testability.
    """

    # PRESERVED: Critical signal for ImageTaskRunner
    imageLoaded = Signal(str, str, object)  # token, path, QImage

    def __init__(
        self,
        vm: Any,
        image_service: Any | None = None,
        settings: Any | None = None,
    ) -> None:
        super().__init__()
        self._initialize_services(vm, image_service, settings)

        # Setup components
        self._setup_components()

        # Setup UI
        self._setup_ui()

        # Connect signals
        self._connect_signals()

        # Setup window properties
        self._setup_window_properties()

        # Restore persisted geometry + splitter state (#141). Runs last so
        # that it overrides ``setup_initial_window_size``'s half-screen
        # default when a previous launch saved geometry. Tolerates
        # missing/corrupt blobs by leaving the defaults in place.
        self._restore_geometry()

        # Point 1: MainWindow fully initialised — baseline before any manifest load.
        try:
            from scripts.memory_probe import snapshot, _ENABLED  # type: ignore[import]
            if _ENABLED:
                snapshot("mainwindow_init_done", point=1)
        except ImportError:
            pass

    # ------------------------------------------------------------------ window-state persistence

    # QSettings keys — kept as class-level aliases for back-compat with
    # any existing callers; the canonical definitions live in
    # :mod:`app.views.window_state` (single source of truth shared with
    # the dialogs and the column-header state from #214).
    QSETTINGS_KEY_GEOMETRY = QSETTINGS_KEY_MAIN_WINDOW_GEOM
    QSETTINGS_KEY_SPLITTER_STATE = QSETTINGS_KEY_MAIN_SPLITTER_STATE
    QSETTINGS_KEY_COLUMN_STATE = QSETTINGS_KEY_COLUMN_HEADER_STATE

    # Backwards-compat shims for callers/tests that historically
    # reached for these classmethods. The real implementations live
    # in :mod:`app.views.window_state`.
    @staticmethod
    def _qsettings_path() -> Path:
        return qsettings_path()

    @classmethod
    def _window_state_qsettings(cls) -> QSettings:
        return window_state_qsettings()

    def _restore_geometry(self) -> None:
        """Restore window geometry + splitter state from QSettings, if any.

        Each step is independently guarded — a corrupt splitter blob
        shouldn't leave the user with an unrestored window position.
        """
        store = self._window_state_qsettings()
        geom = store.value(self.QSETTINGS_KEY_GEOMETRY)
        if geom:
            try:
                self.restoreGeometry(geom)
            except Exception:
                pass
        sp_state = store.value(self.QSETTINGS_KEY_SPLITTER_STATE)
        if sp_state:
            try:
                splitter = self.layout_manager.get_splitter()
                if splitter is not None:
                    splitter.restoreState(sp_state)
            except Exception:
                pass

    def _save_geometry(self) -> None:
        """Persist window geometry + splitter state + column layout to QSettings."""
        try:
            store = self._window_state_qsettings()
            store.setValue(self.QSETTINGS_KEY_GEOMETRY, self.saveGeometry())
            splitter = self.layout_manager.get_splitter()
            if splitter is not None:
                store.setValue(
                    self.QSETTINGS_KEY_SPLITTER_STATE, splitter.saveState()
                )
            self.tree_controller.save_column_state(
                store, self.QSETTINGS_KEY_COLUMN_STATE
            )
            # Flush before the process tears down — QSettings is lazy by
            # default and a Qt-driven exit doesn't always destruct the
            # store cleanly enough to hit the auto-flush.
            store.sync()
        except Exception:
            # Never let geometry persistence fail the close. The next
            # launch just falls back to the half-screen default.
            pass

    def _save_column_state_only(self) -> None:
        """Save just the column layout to QSettings.

        Wired to the tree-header ``sectionMoved`` / ``sectionResized``
        signals so a single drag or resize is persisted immediately —
        users who never close cleanly (force quit, OS crash) still get
        their layout back on next launch. We don't write geometry or
        splitter state here because those have their own signal paths
        and writing them on every column drag is needless I/O.
        """
        try:
            store = self._window_state_qsettings()
            self.tree_controller.save_column_state(
                store, self.QSETTINGS_KEY_COLUMN_STATE
            )
            store.sync()
        except Exception:
            pass

    def _initialize_services(
        self,
        vm: Any,
        image_service: Any | None,
        settings: Any | None,
    ) -> None:
        self._vm = vm
        self._img = image_service
        self._settings = settings

        # #468 — defense-in-depth flag for closeEvent. Set/cleared by
        # ScanDialog.scan_started / scan_finished signals wired up in
        # :meth:`on_scan_sources`. Stays False whenever no scan dialog
        # is open; the closeEvent guard short-circuits on True.
        self.scan_running: bool = False

        # Initialize thumbnail size from settings
        self._thumb_size: int = 512
        if self._settings is not None:
            try:
                self._thumb_size = int(self._settings.get("thumbnail_size", 512) or 512)
            except Exception:
                self._thumb_size = 512

    def _setup_components(self) -> None:
        """Setup all extracted components and controllers."""
        # Create tree view first. DecisionTreeView is a QTreeView subclass that
        # catches bare 'd' / 'k' presses in keyPressEvent and emits
        # ``decisionRequested(str)`` — see app/views/components/decision_tree_view.py.
        # (QShortcut on the tree silently fails to match K under this app's
        # runtime state; root cause is still open as a #626 follow-up.)
        self.tree = DecisionTreeView()

        # Initialize controllers
        self.tree_controller = TreeController(self.tree)
        self.menu_controller = MenuController(self, settings=self._settings)
        self.layout_manager = LayoutManager(self)

        # Status reporter and UI updater implementations
        self.status_reporter = StatusReporterImpl(self)
        self.ui_updater = UIUpdaterImpl(self)

        # #622 Phase 1 — flush ImageService's startup-queued status messages.
        # main.py constructs ImageService BEFORE MainWindow, so the
        # legacy-thumbs-wipe notice produced during _migrate_legacy_disk_cache
        # has no reporter to talk to at that point. The setter below attaches
        # one now and synchronously flushes the queued message. 8 s timeout
        # gives the user a beat to read it before the status bar reverts.
        if self._img is not None and hasattr(self._img, "set_status_reporter"):
            try:
                reporter = self.status_reporter
                self._img.set_status_reporter(
                    lambda msg, _r=reporter: _r.show_status(msg, timeout=8000)
                )
            except Exception:
                pass

        # #165 — runner needs to exist before the file operations
        # handler so the handler can forward it to ExecuteActionDialog's
        # embedded PreviewPane. The PreviewPane in the main window
        # (built later in _setup_ui) reuses this same runner instance.
        self._runner = ImageTaskRunner(service=self._img, receiver=self)

        # Initialize file operations handler
        self.file_operations = FileOperationsHandler(
            vm=self._vm,
            settings=self._settings,
            parent_widget=self,
            ui_updater=self.ui_updater,
            status_reporter=self.status_reporter,
            checked_paths_provider=None,
            highlighted_items_provider=self.tree_controller,
            task_runner=self._runner,
        )

        # Wire the tree's d/k key emit to the existing set-decision path (#615).
        # set_decision_to_highlighted handles no-manifest / no-selection / locked
        # rows uniformly, so the keyboard path and the right-click menu path
        # share one entry point — no new ActionHandlersImpl proxy needed.
        self.tree.decisionRequested.connect(
            self.file_operations.set_decision_to_highlighted
        )

        # Tree data provider for dialog handler
        self.tree_data_provider = TreeDataProviderImpl(self.tree, self.tree_controller)

        # Initialize dialog handler. records_provider lets the regex
        # dialog build its live-preview match function from the current
        # manifest state at open time (no caching — picks up any
        # in-memory changes since the last open). settings is threaded
        # through so the regex dialog can persist Phase B preferences
        # (Simple/Regex mode + recent-patterns history).
        self.dialog_handler = DialogHandler(
            parent_widget=self,
            tree_data_provider=self.tree_data_provider,
            action_handler=self._apply_action_by_regex,
            records_provider=lambda: self._vm.groups,
            settings=self._settings,
        )

        # Action handlers for context menu
        self.action_handlers = ActionHandlersImpl(
            file_operations=self.file_operations,
            dialog_handler=self.dialog_handler,
        )

        # Initialize context menu handler
        self.context_menu_handler = ContextMenuHandler(
            tree_view=self.tree,
            tree_item_provider=self.tree_controller,
            action_handlers=self.action_handlers,
            parent_widget=self,
        )

    def _setup_ui(self) -> None:
        """Setup the main UI components and layout."""
        self.setWindowTitle(t("main_window.title"))

        # Setup tree properties
        self.tree_controller.setup_tree_properties()

        # Create layout sections
        center_widget, center_layout = self.layout_manager.create_tree_section()
        # First-run hint — visible until the first manifest loads. Once a
        # manifest is loaded (even if it produces zero groups), the user has
        # discovered the menu and the wrapper is hidden permanently (#42).
        # Wraps the hint label + primary-action buttons (#137) so the
        # visibility toggle hides everything in one call. Builder lives in
        # ``app.views.components.empty_state`` so the wiring stays
        # unit-testable without cascading the full view stack.
        (
            self._empty_state_widget,
            self._empty_state_label,
            self._empty_state_scan_button,
            self._empty_state_open_button,
        ) = build_empty_state_widget(
            label_text=t("main_window.empty_state"),
            scan_button_text=t("main_window.empty_state_scan_button"),
            scan_handler=self.on_scan_sources,
            open_button_text=t("main_window.empty_state_open_button"),
            open_handler=self.on_open_manifest,
        )
        center_layout.addWidget(self._empty_state_widget)

        center_layout.addWidget(self.tree)
        self.tree.setVisible(False)

        right_widget, right_layout = self.layout_manager.create_preview_section()

        # Preview pane consumes the runner created in _setup_components
        # (#165 — runner lifecycle moved up so FileOperationsHandler
        # can forward it to ExecuteActionDialog's embedded preview).
        self._preview = PreviewPane(right_widget, self._runner, thumb_size=self._thumb_size)
        right_layout.addWidget(self._preview)

        # Wire the tree's P key to the preview's play/pause toggle.
        # PR #624 killed video autoplay; this is the no-mouse path to
        # control playback while reviewing on the keyboard. Single-view
        # only — grid-mode P is a defensible no-op (no unambiguous
        # "focused" player). See decision_tree_view.playPauseRequested.
        self.tree.playPauseRequested.connect(self._preview.toggle_play_pause)

        # Setup main layout with splitter
        central = self.layout_manager.setup_main_layout(center_widget, right_widget)
        self.setCentralWidget(central)

        # Connect splitter signals
        self.layout_manager.connect_splitter_signals(self._preview.refit)

        # Setup initial window size
        self.layout_manager.setup_initial_window_size()

        # Setup menus
        self.menu_controller.setup_menus()

        # Setup context menu
        self.context_menu_handler.setup_context_menu()

    def _connect_signals(self) -> None:
        """Connect all signal/slot relationships."""
        # Menu action handlers
        handlers = {
            "scan_sources": self.on_scan_sources,
            "open_manifest": self.on_open_manifest,
            "save_manifest": self.on_save_manifest,
            "execute_action": self.on_execute_action,
            "execute_action_selected_only": self.on_execute_action_selected_only,
            "action_by_regex": self.on_open_action_dialog,
            "remove_from_list": self._remove_from_list_toolbar,
            "exit": self.close,
            "open_latest_log": self._open_latest_log,
            "open_latest_delete_log": self._open_latest_delete_log,
            "open_log_directory": self._open_log_directory,
            "open_delete_log_directory": self._open_delete_log_directory,
        }
        self.menu_controller.connect_actions(handlers)

        # Tree header click handler
        self.tree_controller.setup_header_behavior(self._on_header_clicked)

        # Persist column order + width on every drag/resize (#214). Saving
        # on each signal — rather than only at closeEvent — survives force
        # quits and OS crashes; the call is debounced enough by Qt that a
        # drag-in-progress doesn't flood QSettings I/O.
        self.tree_controller.connect_layout_change_signal(
            self._save_column_state_only
        )

        # Tree row double-click handler (#143). File rows open in the OS
        # default viewer; group rows toggle expand (handled inside the
        # controller). Hands off to the shared opener helper so right-click
        # Open Folder and double-click share the same OS-cascade impl.
        from app.views.handlers.file_opener import open_file_in_default_viewer
        self.tree_controller.setup_double_click(open_file_in_default_viewer)

        # Image loading signal
        self.imageLoaded.connect(self._on_image_loaded)

        # Full-res viewer: double-click on a preview tile/image → open modal
        self._preview.requestFullRes.connect(self.on_open_full_res_viewer)

    def _setup_window_properties(self) -> None:
        """Setup window properties and status bar."""
        # Persistent baseline label. Qt's QStatusBar.showMessage shows a
        # *temporary* message; once it expires (or a menu hover with an
        # empty statusTip overwrites it) the bar goes empty unless a
        # widget added via addWidget is there to fall back to (#138, #140).
        self._status_baseline = QLabel(t("main_window.status_ready"))
        self.statusBar().addWidget(self._status_baseline, 1)

    def set_status_baseline(self, text: str) -> None:
        """Update the persistent status-bar baseline text and surface it.

        Transient action messages set via showMessage(text, timeout)
        temporarily hide the baseline; it reappears once the temp message
        clears. When a caller updates the baseline (e.g. after a manifest
        load), they want the new text visible NOW — so we also clear any
        active temp message. Otherwise the worker's in-progress
        "Loaded N groups." or similar would keep covering the baseline
        indefinitely (its timeout was 0, persistent).
        """
        self._status_baseline.setText(text)
        self.statusBar().clearMessage()

    # PRESERVED: All public methods with exact signatures

    def clear_preview(self) -> None:
        """#431 — drop preview-pane content (called from
        FileOperationsHandler._on_manifest_loaded before refresh_tree
        so a fresh manifest can't keep showing the previous manifest's
        last-selected row). Defensive: ``clear()`` is a no-op when the
        pane is already empty, and the attribute is guarded so an
        early teardown sequence doesn't crash."""
        preview = getattr(self, "_preview", None)
        if preview is not None and hasattr(preview, "clear"):
            preview.clear()

    def refresh_tree(self, groups: list) -> None:
        """Refresh tree view with new groups data.

        Args:
            groups: List of group objects to display
        """
        # First manifest load — hide the first-run hint and reveal the tree.
        # Stays hidden afterwards even if a later load produces zero groups,
        # because the user has clearly already discovered the entry point.
        if self._empty_state_widget.isVisible():
            self._empty_state_widget.setVisible(False)
            self.tree.setVisible(True)

        self.tree_controller.refresh_model(groups)

        # Restore the saved column layout AFTER refresh_model's
        # ResizeToContents→Interactive cycle (#214). Calling earlier is
        # silently wiped by the auto-size step. Runs every refresh
        # because each refresh_model rebuild reapplies the auto-sized
        # defaults — without restoring after each one, the user's saved
        # widths would be lost on any re-scan / re-open mid-session.
        try:
            store = self._window_state_qsettings()
            self.tree_controller.restore_column_state(
                store, self.QSETTINGS_KEY_COLUMN_STATE
            )
        except Exception:
            pass

        # Reconnect selection handler after model reset
        self.tree_controller.reconnect_selection_handler(self.on_tree_selection_changed)

        # Adjust splitter for tree content
        self.layout_manager.adjust_splitter_for_tree(self.tree_controller.calculate_tree_width)

    def show_group_counts(self, group_count: int) -> None:
        """Show group counts (preserved for backward compatibility).

        Args:
            group_count: Number of groups
        """
        # No-op: groups sidebar removed; keep method to avoid breaking callers
        pass

    def show_groups_summary(self, groups: list) -> None:
        """Show groups summary (preserved for backward compatibility).

        Args:
            groups: List of groups
        """
        # No-op: groups sidebar removed; keep method to avoid breaking callers
        pass

    # PRESERVED: Menu action handlers

    def on_scan_sources(self) -> None:
        """Open the Scan Sources dialog."""
        from app.views.dialogs.scan_dialog import ScanDialog
        dlg = ScanDialog(
            settings=self._settings,
            on_scan_complete=self._load_manifest_after_scan,
            parent=self,
            should_proceed=self._confirm_no_pending_decisions,
        )
        # #468 — track worker liveness on the receiver side so
        # closeEvent can guard against a force-quit mid-scan. Lambdas
        # (rather than method refs) so the flag value is bound at emit
        # time and the slots stay anonymous — no teardown wiring needed
        # since the dialog is owned by ``dlg.exec()``'s scope.
        dlg.scan_started.connect(lambda: setattr(self, "scan_running", True))
        dlg.scan_finished.connect(lambda: setattr(self, "scan_running", False))
        dlg.exec()

    def _confirm_no_pending_decisions(self) -> bool:
        """Prompt before a re-scan replaces a manifest with pending decisions.

        Issue #142: a re-scan replaces the in-memory manifest (and may
        overwrite the on-disk file if the output path matches) without
        warning. If the user has acted on rows since loading, that work is
        silently lost.

        Returns ``True`` to proceed with the scan, ``False`` to cancel.
        Returns ``True`` immediately when there's nothing at risk
        (no manifest loaded, or no decisions made yet), so this is also
        the right behaviour for first-time scans.
        """
        pending = self._vm.pending_decision_count
        if pending == 0:
            return True

        pending_phrase = pluralize(
            pending,
            t("status.noun_pending_decision_singular"),
            t("status.noun_pending_decision_plural"),
        )
        reply = QMessageBox.question(
            self,
            t("main_window.discard_pending_title"),
            t("main_window.discard_pending_body", pending=pending_phrase),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def _load_manifest_from_path(self, manifest_path: str) -> None:
        """Load a manifest directly (called after scan completes or from Open Manifest)."""
        from app.views.main_window_helpers import count_isolated_rows
        from infrastructure.manifest_repository import ManifestRepository
        try:
            # #616: release in-memory image cache RAM from the previous
            # manifest BEFORE swapping vm.groups. The async
            # Open-Manifest path goes through ``_on_manifest_loaded``
            # which clears via the UIUpdateCallback proxy; this sync
            # path (post-scan + relocalize) doesn't, so it needs an
            # explicit clear here. Disk cache is preserved. ``getattr``
            # so test scaffolds that mock MainWindow without ``_img``
            # don't AttributeError into the broad-except below.
            img = getattr(self, "_img", None)
            if img is not None and hasattr(img, "clear_cache"):
                img.clear_cache()
            self._vm.load_from_repo(ManifestRepository(), manifest_path)
            self.file_operations._manifest_path = manifest_path
            self.show_groups_summary(self._vm.groups)
            self.refresh_tree(self._vm.groups)
            try:
                self.menu_controller.set_manifest_actions(True)
                # #410: set_manifest_actions enables the (only selected)
                # entry too, but it should stay disabled until a file row
                # is selected. Refresh applies the additional gate.
                self._refresh_execute_selected_only_enabled()
            except AttributeError:
                pass
            n = self._vm.group_count
            # Surface isolated files in the status bar so users whose scan
            # produced zero near-duplicate groups don't see an empty review
            # pane with no explanation. Isolated = total manifest rows
            # minus rows that ended up in any group.
            grouped = sum(len(g.items) for g in self._vm.groups)
            isolated = count_isolated_rows(manifest_path, grouped)
            parts = [pluralize(
                n,
                t("status.noun_group_singular"),
                t("status.noun_group_plural"),
            )]
            if isolated:
                # Preserve thousands separator on isolated count — typical
                # libraries can have tens of thousands of un-grouped files.
                isolated_form = plural_form(
                    isolated,
                    t("status.noun_isolated_file_singular"),
                    t("status.noun_isolated_file_plural"),
                )
                parts.append(f"{isolated:,} {isolated_form}")
            self.set_status_baseline(
                t("main_window.status_loaded", parts=", ".join(parts))
            )
        except Exception as exc:
            QMessageBox.critical(self, t("main_window.load_error_title"), str(exc))

    def _load_manifest_after_scan(self, manifest_path: str) -> None:
        """Load the manifest produced by a scan and apply tree selection
        to any rows the worker pre-decided as keepers (#239).

        Distinct from :meth:`_load_manifest_from_path` (the Open-Manifest
        path) so re-opening an existing manifest that happens to carry
        ``action="KEEP"`` rows doesn't clobber whatever selection the
        user had in mind. The scan-complete path is the only place the
        user has explicitly asked auto-select to choose for them.
        """
        from app.views.main_window_helpers import extract_keeper_paths

        self._load_manifest_from_path(manifest_path)
        keeper_paths = extract_keeper_paths(self._vm.groups)
        if keeper_paths:
            self._select_rows_by_paths(keeper_paths)

    def _select_rows_by_paths(self, target_paths: set[str]) -> None:
        """Apply tree selection to every row whose PATH_ROLE is in
        ``target_paths``. Scrolls to the first match so the user sees
        the selection state, not just the count in the status bar.
        Used by :meth:`_load_manifest_after_scan` for the auto-select
        post-scan highlight (#239). Generalises :meth:`_reselect_by_path`
        from single- to multi-target selection.
        """
        from PySide6.QtCore import QItemSelection, QItemSelectionModel

        from app.views.main_window_helpers import find_paths_in_model

        matches = find_paths_in_model(self.tree.model(), target_paths)
        if not matches:
            return
        sel_model = self.tree.selectionModel()
        if sel_model is None:
            return

        # Batch the selection into one .select() call. The per-index loop this
        # replaces emitted selectionChanged once per match, and the wired
        # handler (on_tree_selection_changed → _preview.show_single) is a
        # heavy image/video load on the UI thread — N=hundreds of keepers
        # froze the window after Close & Load. See #344.
        selection = QItemSelection()
        for name_idx in matches:
            selection.select(name_idx, name_idx)
        sel_model.select(
            selection,
            QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
        )
        self.tree.scrollTo(matches[0])

    def on_open_manifest(self) -> None:
        """Handle Open Manifest action."""
        self.file_operations.import_manifest()

    def on_save_manifest(self) -> None:
        """Handle Save Manifest Decisions action."""
        self.file_operations.save_manifest_decisions()

    def on_execute_action(self) -> None:
        """Handle Execute Action — open review dialog and run planned operations."""
        self.file_operations.execute_action()

    def on_execute_action_selected_only(self) -> None:
        """#410: Execute Action — selected only.

        Opens the review dialog pre-filtered to groups containing
        the currently-selected file rows in the main tree. Replaces
        the older in-dialog selection-then-execute scoping path
        (`_on_selection_changed`'s button relabel + `_on_execute_requested`'s
        `_selected_file_paths()` branch, both removed in #410). Scope is
        a kwarg through the handler — not stored as global state — so the
        dialog is unaware of how its groups were filtered.
        """
        self.file_operations.execute_action(selected_only=True)

    def _refresh_execute_selected_only_enabled(self) -> None:
        """#410: gate execute_action_selected_only on (manifest_loaded AND
        ≥1 file row selected). Manifest-loaded is read off the sibling
        execute_action entry (MANIFEST_ACTIONS already toggles it); the
        file-row selection check uses tree_controller.get_selected_items()
        which already filters by type for callers throughout the codebase."""
        execute_action = self.menu_controller.actions.get("execute_action")
        manifest_loaded = bool(execute_action and execute_action.isEnabled())
        items = self.tree_controller.get_selected_items() if manifest_loaded else []
        has_file_selection = any(item.get("type") == "file" for item in items)
        selected_only = self.menu_controller.actions.get("execute_action_selected_only")
        if selected_only is not None:
            selected_only.setEnabled(manifest_loaded and has_file_selection)

    def on_open_action_dialog(self) -> None:
        """Handle open Set Action by Field dialog."""
        self.dialog_handler.show_action_dialog()

    def on_open_full_res_viewer(self, path: str) -> None:
        """Open the full-resolution viewer dialog for `path` (non-modal).

        Connected to ``preview_pane.requestFullRes`` — fires when the user
        double-clicks a preview tile or the single-view image label.

        Passes the app-level ImageService via DI so the dialog reuses the
        existing disk cache and skips constructing a second bare instance
        (which would re-run ``_migrate_legacy_disk_cache`` on every open).
        """
        from app.views.dialogs.full_res_viewer import FullResViewerDialog

        dlg = FullResViewerDialog(path, parent=self, service=self._img)
        dlg.show()

    # PRESERVED: Tree selection change handler

    def on_tree_selection_changed(self, *_: Any) -> None:
        """Handle tree selection changes for preview updates.

        Args:
            *_: Selection change arguments (ignored)
        """
        # #410: re-gate the "(only selected)" menu entry on every
        # selection change. Runs BEFORE the early-return so the
        # entry also flips back to disabled on a full deselect.
        # Defensive getattr: fake_self test stubs that exercise only
        # the preview-pane branch don't always inject this helper —
        # see tests/test_main_window.py's on_tree_selection_changed
        # suite. On a real MainWindow it's always present.
        refresh = getattr(self, "_refresh_execute_selected_only_enabled", None)
        if callable(refresh):
            refresh()
        # Delegate to existing preview logic using tree controller
        indexes = self.tree.selectionModel().selectedRows()
        if not indexes:
            return

        idx = indexes[0]
        view_model = self.tree.model()
        src_model = self.tree_controller.model
        proxy = self.tree_controller.proxy

        if proxy is not None and hasattr(proxy, "mapToSource"):
            src_idx = proxy.mapToSource(idx)
            model = src_model
            idx = src_idx
        else:
            model = view_model

        # Determine if group or child
        if idx.parent().isValid():
            # Child row selected -> single preview
            name_index = model.index(idx.row(), COL_NAME, idx.parent())
            folder_index = model.index(idx.row(), COL_FOLDER, idx.parent())
            name = model.data(name_index)
            folder = model.data(folder_index)
            path = model.data(name_index, 32)  # PATH_ROLE
            if not path:
                if not folder or not name:
                    return
                path = str(Path(folder) / name)
            # Optional date/size info for single preview header
            try:
                size_index = model.index(idx.row(), COL_SIZE_BYTES, idx.parent())
                creation_index = model.index(idx.row(), COL_CREATION_DATE, idx.parent())
                shot_index = model.index(idx.row(), COL_SHOT_DATE, idx.parent())
                size_txt = model.data(size_index) or ""
                creation_txt = model.data(creation_index) or ""
                shot_txt = model.data(shot_index) or ""
                self._preview.show_single(
                    path,
                    {
                        "name": name,
                        "folder": folder,
                        "size": size_txt,
                        "creation": creation_txt,
                        "shot": shot_txt,
                    },
                )
            except Exception:
                self._preview.show_single(path)
        else:
            # Group level selected -> grid thumbnails
            group_items: list[tuple[str, str, str, str, str, str]] = []
            parent_item = model.itemFromIndex(model.index(idx.row(), COL_GROUP))
            if parent_item is not None:
                rows = parent_item.rowCount()
                for r in range(rows):
                    name_item = parent_item.child(r, COL_NAME)
                    folder_item = parent_item.child(r, COL_FOLDER)
                    name = model.itemFromIndex(name_item.index()).text() if name_item else ""
                    folder = model.itemFromIndex(folder_item.index()).text() if folder_item else ""
                    size_txt = (
                        model.itemFromIndex(
                            parent_item.child(r, COL_SIZE_BYTES).index()
                        ).text()
                        if parent_item.child(r, COL_SIZE_BYTES)
                        else ""
                    )
                    creation_txt = (
                        model.itemFromIndex(parent_item.child(r, COL_CREATION_DATE).index()).text()
                        if parent_item.child(r, COL_CREATION_DATE)
                        else ""
                    )
                    shot_txt = (
                        model.itemFromIndex(parent_item.child(r, COL_SHOT_DATE).index()).text()
                        if parent_item.child(r, COL_SHOT_DATE)
                        else ""
                    )
                    if name and folder:
                        p = name_item.data(32) if name_item else None  # PATH_ROLE
                        if not p:
                            p = str(Path(folder) / name)
                        group_items.append((p, name, folder, size_txt, creation_txt, shot_txt))
            self._preview.show_grid(group_items)
            # Request autoplay for all videos after loading tiles
            try:
                self._preview.autoplay_all_videos_when_ready()
            except Exception:
                pass

    # PRESERVED: Image loading slot

    def _on_image_loaded(self, token: str, path: str, image: Any) -> None:
        """Handle image loading completion.

        Args:
            token: Image loading token
            path: Image file path
            image: Loaded image object
        """
        self._preview.on_image_loaded(token, path, image)

    # Private methods

    def _remove_from_list_toolbar(self) -> None:
        """Handle remove from list toolbar action."""
        highlighted_items = self.tree_controller.get_selected_items()
        self.file_operations.remove_from_list_toolbar(highlighted_items)

    # ------------------------------------------------------------------ close-with-dirty-check

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Prompt the user when there are unsaved decisions before closing.

        Decisions auto-persist to the loaded manifest as soon as
        they're set, so "Leave" never loses data. The prompt's value
        is purely about offering an explicit save (e.g. before a
        Save-As to another path) and giving the user a back-out.
        """
        # #141: persist current geometry + splitter state before any
        # branch. Saving up-front is fine — a Back click leaves the
        # window open with the same geometry that was just saved, so
        # there's nothing to undo; if the user resizes after Back, the
        # next close-attempt re-saves.
        self._save_geometry()
        # Dispatch to the close-disposition logic, which sets accept/ignore
        # on the event. We then check event.isAccepted() ONCE at the end
        # and force-quit the QApplication on accept (see _force_quit_on_accept).
        self._dispatch_close(event)
        self._force_quit_on_accept(event)

    def _dispatch_close(self, event) -> None:
        """Set accept/ignore on ``event`` based on app state + user choice.

        Pulled out of ``closeEvent`` so the post-hook (``_force_quit_on_accept``)
        runs at a SINGLE exit point regardless of which close-branch fired.
        """
        # #468 — defense-in-depth guard against closing the main
        # window while a scan worker is alive. Today the ScanDialog is
        # modal and Qt cascades the close, so ``ScanDialog.closeEvent``
        # always runs first and interrupts the worker. If the dialog
        # were ever changed to non-modal / detached, or worker
        # ownership moved up here, that cascade would silently break
        # and the worker would survive the main-window close. When the
        # flag is True we prompt before letting the close through —
        # Yes accepts (existing cascade interrupts the worker), No
        # (or Esc / window-X) keeps the user in the app.
        if self.scan_running:
            reply = QMessageBox.question(
                self,
                t("exit.scan_running_title"),
                t("exit.scan_running_body"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            event.accept()
            return
        if not self.file_operations.is_dirty():
            super().closeEvent(event)
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(t("exit.confirm_title"))
        box.setText(t("exit.confirm_body"))
        # Iterate over the module-level spec so the qa helper and the
        # closeEvent body cannot disagree about button order/roles.
        buttons_by_name = {
            name: box.addButton(t(key), role)
            for name, key, role in EXIT_DIALOG_BUTTONS
        }
        btn_save = buttons_by_name["save"]
        btn_leave = buttons_by_name["leave"]
        btn_back = buttons_by_name["back"]
        # Default to Back so an accidental Enter/Esc keeps the user in the app.
        box.setDefaultButton(btn_back)
        box.exec()

        clicked = box.clickedButton()
        if clicked is btn_save:
            if self.file_operations.save_manifest_decisions_silent():
                event.accept()
            else:
                # Save failed — better to keep the user in the app
                # than silently lose the prompt's protection.
                QMessageBox.critical(
                    self,
                    t("file_op.save_error_title"),
                    t("file_op.save_failed_status"),
                )
                event.ignore()
        elif clicked is btn_leave:
            event.accept()
        else:
            # Back, Esc, or window-X (rejection); stay in the app.
            event.ignore()

    def _force_quit_on_accept(self, event) -> None:
        """Force ``QApplication.quit()`` when the user accepts the close.

        Why this exists — the user-observable "can't-close" bug
        (2026-06-08, post-#610): in some launch contexts (Windows session
        host, RDP, child of cmd.exe etc.), Qt's ``lastWindowClosed``
        signal does NOT fire when the MainWindow is hidden by
        ``closeEvent``. Symptom: ``app.exec()`` never returns,
        ``aboutToQuit`` never fires, the python.exe process stays alive
        and idle (0 % CPU, ~25 threads, ~40 000 handles) until
        force-killed. Captured PID 31776 (300 MB RAM, 62 s CPU, 0 visible
        windows, 24 threads, 41 599 handles) on the user's box mid-saga;
        all worker / exiftool subprocesses had cleaned up correctly —
        the leak was the parent itself.

        Mechanism — the MainWindow has no ``Qt::WA_DeleteOnClose``, so a
        close-accept just hides it. Qt 6's ``lastWindowClosed`` semantics
        + a residue of internal helper-window top-levels
        (``QTreeViewThemeHelperWindow``, ``ThemeChangeObserverWindow``,
        IME helpers) combined with ProcessPoolExecutor internal manager
        threads sometimes leaves ``app.exec()`` parked. Calling
        ``QApplication.instance().quit()`` directly is the smallest
        possible fix: it forces ``quit()`` to fire, which always emits
        ``aboutToQuit`` and unwinds ``app.exec()`` regardless of
        ``lastWindowClosed`` state.

        Only quits on accept — ignored close events (Back / Esc /
        scan-running-No / save-failure) stay in the app as before.

        Gated on ``self._relocalizing`` — the live language switch at
        ``_handle_language_switch`` calls ``self.close()`` on the OLD
        window AFTER constructing + showing the new one. That programmatic
        close also goes through ``closeEvent`` and would otherwise trigger
        the force-quit, killing the app mid-switch (caught by qa-batch
        run #612 — s22_language_switch + s58_language_switch_preserves_manifest
        both failed before this guard was added).
        """
        if not event.isAccepted():
            return
        if getattr(self, "_relocalizing", False):
            return
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # ------------------------------------------------------------------ live language switch

    def _capture_relocalize_state(self) -> dict:
        """Snapshot the bits of UI state worth carrying across a live
        language switch — window geometry, splitter sizes, the
        selected file row's path, and the loaded manifest path so the
        new window can re-load it. Tree expansion isn't preserved
        because ``TreeController.refresh_model`` always expands all
        groups by default; preview doesn't need preservation because
        re-selecting the same row triggers it. vm holds the in-memory
        groups but the freshly-constructed MainWindow never calls
        ``refresh_tree`` on its own, so without ``manifest_path`` the
        user would land on the empty-state hint despite
        ``language.confirm_body`` promising the manifest stays intact
        (#428)."""
        state: dict = {
            "geometry": bytes(self.saveGeometry()),
            "splitter_state": None,
            "selected_path": None,
            "thumb_size": self._thumb_size,
            "manifest_path": getattr(self.file_operations, "_manifest_path", None),
        }
        try:
            splitter = self.layout_manager.get_splitter()
            if splitter is not None:
                state["splitter_state"] = bytes(splitter.saveState())
        except Exception:
            pass
        try:
            from app.views.main_window_helpers import extract_first_selected_file_path

            items = self.tree_controller.get_selected_items()
            state["selected_path"] = extract_first_selected_file_path(items)
        except Exception:
            pass
        return state

    def _apply_relocalize_state(self, state: dict) -> None:
        """Best-effort restore of the snapshot from
        ``_capture_relocalize_state``. Each step is independently
        guarded — a failure to restore selection shouldn't strand
        the user with a broken window."""
        try:
            geom = state.get("geometry")
            if geom:
                self.restoreGeometry(geom)
        except Exception:
            pass
        try:
            splitter = self.layout_manager.get_splitter()
            sp_state = state.get("splitter_state")
            if splitter is not None and sp_state:
                splitter.restoreState(sp_state)
        except Exception:
            pass
        # #428 — restore the manifest BEFORE re-selecting. The freshly
        # built MainWindow has no tree rows of its own; vm still holds
        # the groups in memory but the new window never auto-renders
        # them. Calling _load_manifest_from_path re-reads from the
        # SQLite path, repopulates the tree, refreshes status-bar +
        # menu-action gates, and makes _reselect_by_path's walk find
        # anything. Done unconditionally when a path was captured —
        # the vm-in-memory state and on-disk state are kept in sync
        # by file_operations.save_manifest_decisions_silent() being
        # called in relocalize() before the swap.
        manifest_path = state.get("manifest_path")
        if manifest_path:
            try:
                self._load_manifest_from_path(manifest_path)
            except Exception:
                pass
            # refresh_tree's "hide empty-state widget" branch is gated
            # on ``self._empty_state_widget.isVisible()``, which Qt
            # returns False for during the relocalize swap because the
            # new MainWindow hasn't been show()'n yet. Without the
            # explicit flip below the guard misses, leaving the tree
            # hidden and the empty-state hint visible after show() —
            # the user-visible half of the #428 regression. The
            # initial-load flow doesn't hit this because the user
            # discovers the menu on an already-shown window.
            try:
                self._empty_state_widget.setVisible(False)
                self.tree.setVisible(True)
            except Exception:
                pass
        # Re-select the previously-selected row by file_path. The tree
        # is now populated either by refresh_tree at construction time
        # (no manifest case) or by the _load_manifest_from_path call
        # above (#428).
        target = state.get("selected_path")
        if target:
            try:
                self._reselect_by_path(target)
            except Exception:
                pass

    def _reselect_by_path(self, target_path: str) -> None:
        """Walk the tree and select the row whose PATH_ROLE matches."""
        from PySide6.QtCore import QItemSelectionModel

        from app.views.main_window_helpers import find_path_in_model

        name_idx = find_path_in_model(self.tree.model(), target_path)
        if name_idx is None:
            return
        self.tree.scrollTo(name_idx)
        self.tree.selectionModel().select(
            name_idx,
            QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
        )

    def relocalize(self) -> None:
        """Rebuild the window in the locale persisted to settings.

        Triggered by ``MenuController._on_language_chosen`` after it
        writes ``ui.locale``. We snapshot a few preservable bits of UI
        state, swap the translator singletons, build a fresh
        MainWindow via the same factory used at startup, restore the
        snapshot, and dispose of self. The new window picks up
        translated strings naturally because every view module reads
        them through ``t()`` at construction time.
        """
        # Local import avoids a module-level cycle (main imports
        # MainWindow at module level; this is a runtime call).
        from main import install_locale_translators, make_main_window

        # #428 — flush pending decisions to disk BEFORE the swap. The
        # new MainWindow re-loads the manifest from SQLite (see
        # _apply_relocalize_state), so any in-memory decisions the
        # user set after the last save would otherwise be silently
        # discarded by the reload. The save is a no-op when nothing
        # is dirty, and the silent helper returns False on
        # save-failure rather than raising — we proceed with the swap
        # either way because dropping the language switch entirely
        # after the user already clicked "Yes" on the confirm prompt
        # is the worse UX failure mode.
        try:
            if self.file_operations.is_dirty():
                self.file_operations.save_manifest_decisions_silent()
        except Exception:
            pass

        saved = self._capture_relocalize_state()

        app = QApplication.instance()
        if app is not None and self._settings is not None:
            install_locale_translators(app, self._settings)

        new_win = make_main_window(self._vm, self._img, self._settings)
        new_win._apply_relocalize_state(saved)
        new_win.show()
        # Close + delete this window. The new one owns the same vm /
        # image_service / settings; nothing of ours needs to outlive
        # the close.
        #
        # #612 — flag this as a programmatic close so ``_force_quit_on_accept``
        # does NOT call ``QApplication.quit()`` here. Without the guard the
        # old window's closeEvent would force-quit the app mid-language-switch
        # (s22_language_switch + s58_language_switch_preserves_manifest both
        # failed on the first attempt of #612's CI).
        self._relocalizing = True
        self.close()
        self.deleteLater()

    def _apply_action_by_regex(self, field: str, pattern: str, action_value: str) -> None:
        """Apply an action to all files matching field/regex from the ActionDialog."""
        self.file_operations.set_decision_by_regex(field, pattern, action_value)

    def _on_header_clicked(self, logical_index: int) -> None:
        """Handle tree header clicks to maintain sort state.

        Args:
            logical_index: Clicked column index
        """
        try:
            current_order = self.tree.header().sortIndicatorOrder()
            self.tree_controller.update_sort_state(logical_index, current_order)
            logger.debug("Sort state updated - Column: {}, Order: {}", logical_index, current_order)
        except Exception as e:
            logger.error("Failed to track header click: {}", e)

    def _open_latest_log(self) -> None:
        """Open the latest log file."""
        from infrastructure.logging import open_latest_log

        if not open_latest_log():
            QMessageBox.warning(
                self, "Log File Not Found", "No log files found in the log directory."
            )

    def _open_latest_delete_log(self) -> None:
        """Open the latest delete log file."""
        from infrastructure.logging import open_latest_delete_log

        if not open_latest_delete_log():
            QMessageBox.warning(
                self,
                "Delete Log Not Found",
                "No delete log files found in the delete log directory.",
            )

    def _open_log_directory(self) -> None:
        """Open the log directory in file explorer."""
        from infrastructure.logging import open_log_directory

        if not open_log_directory():
            QMessageBox.warning(
                self, "Log Directory Not Found", "Log directory could not be opened."
            )

    def _open_delete_log_directory(self) -> None:
        """Open the delete log directory in file explorer."""
        from infrastructure.logging import open_delete_log_directory

        if not open_delete_log_directory():
            QMessageBox.warning(
                self, "Delete Log Directory Not Found", "Delete log directory could not be opened."
            )


# Helper implementation classes


class UIUpdaterImpl:
    """Implementation of UIUpdateCallback protocol."""

    def __init__(self, main_window):
        self.window = main_window

    def refresh_tree(self, groups: list) -> None:
        """Refresh tree view."""
        self.window.refresh_tree(groups)

    def show_group_counts(self, count: int) -> None:
        """Show group counts (legacy)."""
        self.window.show_group_counts(count)

    def show_groups_summary(self, groups: list) -> None:
        """Show groups summary (legacy)."""
        self.window.show_groups_summary(groups)

    def clear_preview(self) -> None:
        """Drop preview-pane content (#431).

        Proxies ``MainWindow.clear_preview``. The ``UIUpdateCallback``
        Protocol declares this and ``_on_manifest_loaded`` calls it, but
        the proxy was missing here — so every Open-Manifest load hit an
        AttributeError inside the worker's ``finished`` slot and aborted
        the app. See test_probe_uiupdater_impl_proxies_every_protocol_method.
        """
        self.window.clear_preview()

    def clear_image_cache(self) -> None:
        """Drop the in-memory image cache on manifest unload (#616).

        Proxies through to ``MainWindow._img.clear_cache()``. Guarded
        against ``_img is None`` because some test scaffolds construct
        a MainWindow without the image service wired up. The
        ``hasattr`` check is belt-and-braces: a stub image service in
        future tests shouldn't crash this proxy.
        """
        img = getattr(self.window, "_img", None)
        if img is not None and hasattr(img, "clear_cache"):
            img.clear_cache()


class TreeDataProviderImpl:
    """Implementation of TreeDataProvider protocol."""

    def __init__(self, tree_view: QTreeView, tree_controller: TreeController):
        self.tree = tree_view
        self.controller = tree_controller

    def get_selection_model(self):
        """Get tree selection model."""
        return self.tree.selectionModel()

    def get_view_model(self):
        """Get tree view model."""
        return self.tree.model()

    def get_source_model(self):
        """Get source model."""
        return self.controller.model

    def get_proxy_model(self):
        """Get proxy model."""
        return self.controller.proxy


# ActionHandlersImpl moved to app/views/handlers/action_handlers.py so
# the context-menu bridge is unit-testable without cascade-importing
# this 400+ line QMainWindow file. The import lives at the top of this
# module; the class itself is no longer defined here.
