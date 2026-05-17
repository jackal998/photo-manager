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
    save_widget_geometry,
    window_state_qsettings,
)
from infrastructure.i18n import t


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

        # Initialize thumbnail size from settings
        self._thumb_size: int = 512
        if self._settings is not None:
            try:
                self._thumb_size = int(self._settings.get("thumbnail_size", 512) or 512)
            except Exception:
                self._thumb_size = 512

    def _setup_components(self) -> None:
        """Setup all extracted components and controllers."""
        # Create tree view first
        self.tree = QTreeView()

        # Initialize controllers
        self.tree_controller = TreeController(self.tree)
        self.menu_controller = MenuController(self, settings=self._settings)
        self.layout_manager = LayoutManager(self)

        # Status reporter and UI updater implementations
        self.status_reporter = StatusReporterImpl(self)
        self.ui_updater = UIUpdaterImpl(self)

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

        # Tree data provider for dialog handler
        self.tree_data_provider = TreeDataProviderImpl(self.tree, self.tree_controller)

        # Initialize dialog handler. records_provider lets the regex
        # dialog build its live-preview match function from the current
        # manifest state at open time (no caching — picks up any
        # in-memory changes since the last open). settings is threaded
        # through so the regex dialog can persist Phase B preferences
        # (Beginner/Regex mode + recent-patterns history).
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
            self._vm.load_from_repo(ManifestRepository(), manifest_path)
            self.file_operations._manifest_path = manifest_path
            self.show_groups_summary(self._vm.groups)
            self.refresh_tree(self._vm.groups)
            try:
                self.menu_controller.set_manifest_actions(True)
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
        from PySide6.QtCore import QItemSelectionModel

        from app.views.main_window_helpers import find_paths_in_model

        matches = find_paths_in_model(self.tree.model(), target_paths)
        if not matches:
            return
        sel_model = self.tree.selectionModel()
        if sel_model is None:
            return

        sel_model.clearSelection()
        for name_idx in matches:
            sel_model.select(
                name_idx,
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
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

    def on_open_action_dialog(self) -> None:
        """Handle open Set Action by Field/Regex dialog."""
        self.dialog_handler.show_action_dialog()

    # PRESERVED: Tree selection change handler

    def on_tree_selection_changed(self, *_: Any) -> None:
        """Handle tree selection changes for preview updates.

        Args:
            *_: Selection change arguments (ignored)
        """
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
        if not self.file_operations.is_dirty():
            super().closeEvent(event)
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(t("exit.confirm_title"))
        box.setText(t("exit.confirm_body"))
        btn_save = box.addButton(t("exit.button_save_leave"), QMessageBox.AcceptRole)
        btn_leave = box.addButton(t("exit.button_leave"), QMessageBox.DestructiveRole)
        btn_back = box.addButton(t("exit.button_back"), QMessageBox.RejectRole)
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

    # ------------------------------------------------------------------ live language switch

    def _capture_relocalize_state(self) -> dict:
        """Snapshot the bits of UI state worth carrying across a live
        language switch — window geometry, splitter sizes, and the
        selected file row's path. Tree expansion isn't preserved
        because ``TreeController.refresh_model`` always expands all
        groups by default; preview doesn't need preservation because
        re-selecting the same row triggers it. vm-side state
        (manifest, decisions) survives automatically because vm
        outlives the window."""
        state: dict = {
            "geometry": bytes(self.saveGeometry()),
            "splitter_state": None,
            "selected_path": None,
            "thumb_size": self._thumb_size,
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
        # Re-select the previously-selected row by file_path. The tree
        # is already populated by refresh_tree at construction time.
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
