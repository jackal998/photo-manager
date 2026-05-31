"""ExecuteActionDialog — review and confirm planned file operations."""

from __future__ import annotations

import dataclasses
import os

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTreeView,
    QVBoxLayout,
)
from loguru import logger

from app.views.components.status_messages import report_count
from app.views.constants import (
    COL_GROUP,
    COL_NAME,
    LOCK_SENTINEL,
    PATH_ROLE,
    REMOVE_FROM_LIST_DECISION,
    REMOVE_FROM_LIST_SENTINEL,
    SORT_ROLE,
    UNLOCK_SENTINEL,
    settable_decisions,
)
from app.views.preview_pane import PreviewPane
from app.views.tree_model_builder import build_model
from app.views.window_state import (
    QSETTINGS_KEY_EXECUTE_ACTION_DIALOG_GEOM,
    QSETTINGS_KEY_EXECUTE_ACTION_DIALOG_SPLITTER_STATE,
    restore_splitter_state,
    restore_widget_geometry,
    save_splitter_state,
    save_widget_geometry,
)
from infrastructure.i18n import t

# Internal verdict codes used by _ask_lock_confirm to normalize the
# LockedRowsConfirmDialog result for the dialog's callers. Kept
# separate from the dialog class's own constants so this file doesn't
# import the dialog module at the top — callers inside trigger paths
# bring it in lazily.
_DIALOG_VERDICT_PROCEED = 1       # Unlock & Apply — caller unlocks + applies
_DIALOG_VERDICT_SKIP_LOCKED = 2   # Apply to Unlocked Only — caller filters out locked
_DIALOG_VERDICT_CANCEL = 3        # Cancel — caller aborts

# #502 — type-filter QComboBox values stored as Qt UserData. The combo's
# label text is localised via t() at build time; the value is the canonical
# decision string used internally (or ``None`` for "All"). Keeping the
# value column language-agnostic means the filter predicate never has to
# round-trip through the locale catalogue, and switching language at runtime
# doesn't break in-flight filter state.
_TYPE_FILTER_ALL: str | None = None  # sentinel — show every decision


class ExecuteActionDialog(QDialog):
    """Shows groups with decisions for final review; executes file decisions on confirm."""

    def __init__(
        self,
        groups: list,
        manifest_path: str | None,
        parent=None,
        settings: object | None = None,
        task_runner: object | None = None,
        status_reporter: object | None = None,
    ) -> None:
        super().__init__(parent)
        # settings is optional so existing tests / callers that don't
        # need Phase B persistence can pass None. Threaded into the
        # inner regex dialog via _show_select_dialog so its mode +
        # recent-patterns survive across runs even when reached via
        # the Execute Action route.
        self._settings = settings
        # task_runner is optional so unit tests that don't need a real
        # image-loading pipeline can omit it. When absent, the dialog
        # falls back to the pre-#165 single-column layout — no preview
        # pane, no splitter. When present, the tree is wrapped in a
        # horizontal splitter alongside the PreviewPane.
        self._task_runner = task_runner
        # status_reporter is optional so unit tests can omit it. When
        # present, the regex-apply path emits "Decision set" so the
        # main window's status bar gets the same confirmation the s14
        # main-menu route already produces (#316).
        self._status_reporter = status_reporter
        self._preview: PreviewPane | None = None
        self._splitter: QSplitter | None = None
        self.setWindowTitle(t("execute_dialog.title"))
        self.setMinimumSize(900, 560)
        # #139 — QDialog.exec() sets WA_ShowModal but leaves windowModality
        # at the QWidget default (Qt.NonModal). Without explicit modality,
        # Qt does NOT set the OS-level owner relationship or disable the
        # parent on Windows, so a real mouse click on the parent's menu
        # bar steals foreground and opens the menu while this dialog is
        # mid-review. ApplicationModal blocks input to all windows in
        # the app until this dialog is dismissed; this is the right
        # choice for a destructive-confirmation review modal where any
        # menu-bar action could create inconsistent state.
        self.setWindowModality(Qt.ApplicationModal)
        self._groups = groups
        self._manifest_path = manifest_path
        self.deleted_paths: list[str] = []
        self.executed_paths: list[str] = []
        # Paths removed from the review list during this dialog session
        # (via the new "remove from list" action). The parent inspects
        # this after exec() so it can refresh the main tree — vm.groups
        # is already updated in place because self._groups aliases it.
        self.removed_from_list_paths: list[str] = []
        # #444 — set when _set_decision_by_regex (or its lock/unlock
        # branch) writes a non-empty batch. Decisions/locks are mutated
        # in place on self._groups (== vm.groups), but the main tree's
        # rendered cells don't observe that mutation. The parent reads
        # this flag after exec() to fire refresh_tree on the
        # reject-after-changes path that otherwise falls through with
        # stale cell text.
        self._decisions_changed: bool = False
        self._missing_paths: list[str] = []
        # (path, reason) pairs for files whose delete raised an exception
        # — kept separate from `_missing_paths` so the post-execute UI
        # can show "didn't exist" vs "tried and failed" distinctly (#68).
        self._failed_paths: list[tuple[str, str]] = []
        self._src_model = None
        self._build_ui()
        # #215 — restore last saved geometry. ``setMinimumSize`` above
        # acts as the floor; the off-screen guard inside
        # ``restore_widget_geometry`` falls back to that default when a
        # previously-saved rect would land on a disconnected monitor.
        restore_widget_geometry(self, QSETTINGS_KEY_EXECUTE_ACTION_DIALOG_GEOM)
        # #165 — splitter sizes round-trip independently of dialog
        # geometry (saveState vs saveGeometry are separate Qt blobs).
        # Only attempt restore when the preview-enabled layout actually
        # built a splitter; the runner=None branch has no splitter.
        if self._splitter is not None:
            restore_splitter_state(
                self._splitter,
                QSETTINGS_KEY_EXECUTE_ACTION_DIALOG_SPLITTER_STATE,
            )

    # ------------------------------------------------------------------ helpers

    def _groups_with_decisions(self) -> list:
        """Return only groups where ≥1 file has user_decision set."""
        return [
            g for g in self._groups
            if any(getattr(r, "user_decision", "") for r in getattr(g, "items", []))
        ]

    def _decided_records(self) -> list[tuple]:
        """Return (group, rec) pairs where user_decision is set."""
        return [
            (group, rec)
            for group in self._groups
            for rec in getattr(group, "items", [])
            if getattr(rec, "user_decision", "")
        ]

    def _get_type_filter_value(self) -> str | None:
        """Return the currently-selected type filter's decision value.

        ``None`` = "All decisions" sentinel (show everything). Otherwise
        the canonical decision string (``"delete"`` or
        :data:`REMOVE_FROM_LIST_DECISION`). Reads ``currentData()`` from
        the combo so the value column stays language-agnostic — the
        combo's visible text is localised but its userData is the
        internal decision string. See #502.

        Defensive: during ``__init__`` the combo isn't built yet, so
        early callers (e.g. the constructor's ``_update_summary``)
        gracefully see ``None`` and behave as if no filter is active.
        """
        if not hasattr(self, "_type_filter_combo"):
            return _TYPE_FILTER_ALL
        return self._type_filter_combo.currentData()

    def _apply_type_filter(self, groups: list) -> list:
        """Return ``groups`` filtered to only records matching the
        current type-filter combo selection. Pure — never mutates input.

        Per #430 the group-context contract requires the filter to be
        purely visual: ``self._groups`` (which aliases ``vm.groups``)
        must NOT be touched. We return a new list of shallow ``PhotoGroup``
        copies via :func:`dataclasses.replace` so downstream consumers
        (``build_model``, ``_decided_records`` callers) see a filtered
        view without any structural mutation. Groups that become empty
        after filtering are dropped entirely so the tree doesn't render
        an empty header row.

        Returns ``groups`` unchanged when the filter is "All" — avoids
        the per-group copy allocation on the default path.
        """
        filter_value = self._get_type_filter_value()
        if filter_value is _TYPE_FILTER_ALL:
            return groups
        filtered: list = []
        for group in groups:
            items = getattr(group, "items", [])
            matching = [
                rec for rec in items
                if getattr(rec, "user_decision", "") == filter_value
            ]
            if matching:
                filtered.append(dataclasses.replace(group, items=matching))
        return filtered

    def _hidden_pending_delete_count(self) -> int:
        """Return the number of delete-decision rows currently HIDDEN by
        the type filter — i.e. rows that would be committed by Execute
        on an unfiltered view but are not visible right now.

        Used by ``_refresh_warning_banner`` to surface the
        "hidden destructive" line when the user has filtered to a
        non-delete view but still has pending delete decisions in the
        full manifest. Without this signal, switching the filter to
        "Remove only" while pending deletes exist would visually erase
        the warning state and the user would assume nothing destructive
        is staged. See #502.
        """
        filter_value = self._get_type_filter_value()
        if filter_value is _TYPE_FILTER_ALL or filter_value == "delete":
            # Filter doesn't hide anything destructive: filter=All shows
            # delete rows; filter="delete" makes destructive rows the
            # visible subset. Both cases: hidden count is zero.
            return 0
        return sum(
            1
            for group in self._groups
            for rec in getattr(group, "items", [])
            if getattr(rec, "user_decision", "") == "delete"
        )

    def _complete_delete_groups(
        self, paths_filter: set[str] | None = None,
    ) -> list[int]:
        """Return group_numbers where every record has
        ``user_decision='delete'``.

        When ``paths_filter`` is set (partial-execute path), the
        check is narrowed: a group qualifies if every record WHOSE
        PATH IS IN THE FILTER has ``user_decision='delete'``, AND at
        least one such record exists. Records outside the filter
        are ignored — they're not part of this execution scope, so
        their decisions don't count for or against "complete".
        Without this narrowing, partial-execute would fire the
        complete-group confirm on groups where the unselected rows
        are kept (false positive) or never fire on groups where
        only the selected rows are delete (false negative).
        """
        result = []
        for group in self._groups:
            items = getattr(group, "items", [])
            if not items:
                continue
            if paths_filter is None:
                if all(
                    getattr(rec, "user_decision", "") == "delete" for rec in items
                ):
                    result.append(group.group_number)
            else:
                in_scope = [
                    rec for rec in items
                    if getattr(rec, "file_path", "") in paths_filter
                ]
                if in_scope and all(
                    getattr(rec, "user_decision", "") == "delete"
                    for rec in in_scope
                ):
                    result.append(group.group_number)
        return sorted(result)

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._summary_label = QLabel()
        self._update_summary()
        # #408 — cap the top section's vertical appetite so the tree
        # absorbs growth. Maximum policy uses the widget's sizeHint as
        # the ceiling, so multi-line summaries / large fonts still fit
        # without clipping (safer than a hard pixel cap).
        self._summary_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout.addWidget(self._summary_label)

        select_btn = QPushButton(t("execute_dialog.select_button"))
        select_btn.clicked.connect(self._show_select_dialog)
        select_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        layout.addWidget(select_btn)

        # #502 — type filter row. Combo + label live on one horizontal
        # row above the tree so they're discoverable without competing
        # for vertical space. Filter resets to "All" on every dialog
        # reopen (the combo is constructed fresh per dialog instance —
        # we don't persist this state). Lock/Unlock and Keep are
        # intentionally omitted: Lock/Unlock live on ``is_locked`` not
        # ``user_decision`` so filtering by them would surprise users
        # ("Lock only" would match nothing); Keep collides with the
        # undecided state (both are empty-string) which would make the
        # filter ambiguous.
        type_filter_row = QHBoxLayout()
        type_filter_row.addWidget(QLabel(t("execute_dialog.filter_label")))
        self._type_filter_combo = QComboBox()
        self._type_filter_combo.setObjectName("executeDialogTypeFilterCombo")
        self._type_filter_combo.addItem(
            t("execute_dialog.filter_all"), _TYPE_FILTER_ALL,
        )
        self._type_filter_combo.addItem(
            t("execute_dialog.filter_delete_only"), "delete",
        )
        self._type_filter_combo.addItem(
            t("execute_dialog.filter_remove_only"), REMOVE_FROM_LIST_DECISION,
        )
        self._type_filter_combo.currentIndexChanged.connect(
            self._on_type_filter_changed
        )
        type_filter_row.addWidget(self._type_filter_combo)
        type_filter_row.addStretch(1)
        layout.addLayout(type_filter_row)

        self._tree = QTreeView()
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.setAlternatingRowColors(True)
        # #211 — multi-row highlight feeds the scoped-execute feature.
        # Matches the main result tree (tree_controller.py:45).
        self._tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._rebuild_tree_model()
        # #165 — when a task_runner was threaded through the constructor,
        # wrap the tree in a horizontal splitter alongside an embedded
        # PreviewPane so the user can see what each file looks like
        # before confirming destructive action. Without a runner (the
        # original test/legacy path) keep the single-column layout
        # exactly as it was — no splitter, no preview.
        if self._task_runner is not None:
            self._preview = PreviewPane(self, self._task_runner)
            # #409 — the shared ImageTaskRunner emits its completion
            # signal on the receiver it was constructed with (the
            # MainWindow), which forwards only to the main window's
            # own PreviewPane. Without this connect, background-loaded
            # images never reach the dialog's preview and the pane
            # stays blank. Qt auto-disconnects when the dialog's
            # _preview child is destroyed on dialog close.
            self._task_runner._receiver.imageLoaded.connect(
                self._preview.on_image_loaded
            )
            self._splitter = QSplitter(Qt.Horizontal, self)
            self._splitter.addWidget(self._tree)
            self._splitter.addWidget(self._preview)
            # Tree wider than preview by default; the persisted splitter
            # state takes over once the user resizes the divider once.
            self._splitter.setStretchFactor(0, 3)
            self._splitter.setStretchFactor(1, 2)
            # #408 — explicit stretch=1 makes the tree/splitter the
            # primary growth absorber when the dialog is resized
            # vertically; top section stays compact.
            layout.addWidget(self._splitter, 1)
        else:
            layout.addWidget(self._tree, 1)

        # Warning banner for complete-group deletions
        self._warning_banner = QFrame()
        self._warning_banner.setFrameShape(QFrame.StyledPanel)
        self._warning_banner.setStyleSheet(
            "QFrame { background: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; }"
        )
        banner_layout = QVBoxLayout(self._warning_banner)
        banner_layout.setContentsMargins(8, 6, 8, 6)
        self._warning_label = QLabel()
        self._warning_label.setWordWrap(True)
        self._warning_label.setStyleSheet("color: #856404; font-weight: bold;")
        # Group numbers in the banner are rendered as HTML anchors so the
        # user can click one to jump straight to that group in the tree
        # (#166). RichText must be enabled before setText; linkActivated
        # is wired once and dispatches to _on_jump_to_group on click.
        self._warning_label.setTextFormat(Qt.RichText)
        self._warning_label.linkActivated.connect(self._on_jump_to_group)
        banner_layout.addWidget(self._warning_label)
        self._warning_banner.setVisible(False)
        layout.addWidget(self._warning_banner)

        has_decisions = bool(self._decided_records())
        # Dismiss-label convention across all three primary modals is "Close":
        # ScanDialog uses "Close" (relabeled to "Close & Load" post-scan to
        # signal the mode change); ActionDialog uses "Close" because Apply
        # already committed each regex; this dialog reuses the same label so
        # users moving between modals see one dismiss verb. The destructive
        # intent is reinforced at the "All Files Will Be Deleted" confirmation
        # that fires on Execute — not at this dismiss button.
        self._btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._btn_box.button(QDialogButtonBox.Ok).setText(t("execute_dialog.execute_button"))
        self._btn_box.button(QDialogButtonBox.Cancel).setText(t("execute_dialog.close_button"))
        self._btn_box.button(QDialogButtonBox.Ok).setEnabled(has_decisions)
        self._btn_box.accepted.connect(self._on_execute_requested)
        # Improvement 1 in the partial-execute bundle: separate "Execute
        # selected" button that scopes execution to the rows currently
        # highlighted in the tree. Disabled until the selection contains
        # at least one decided file row. Driven by _on_selection_changed
        # below + _refresh_ui_after_decision_change. The button is a
        # SECOND action (not a relabel of Execute) — #410 explicitly
        # rejected the relabel pattern as ambiguous.
        self._btn_execute_selected = QPushButton(
            t("execute_dialog.execute_selected_button"), self,
        )
        self._btn_execute_selected.setEnabled(False)
        self._btn_execute_selected.clicked.connect(self._on_execute_selected_requested)
        self._btn_box.addButton(
            self._btn_execute_selected, QDialogButtonBox.ActionRole,
        )
        self._btn_box.rejected.connect(self.reject)
        layout.addWidget(self._btn_box)

        self._refresh_warning_banner()

    def _rebuild_tree_model(self) -> None:
        # #502 — apply type-filter on top of the decisions filter so the
        # rendered tree matches the combo selection. ``_apply_type_filter``
        # returns the input list unchanged when filter is "All", so the
        # default path costs nothing.
        groups = self._apply_type_filter(self._groups_with_decisions())
        model, proxy = build_model(groups)
        self._src_model = model
        self._tree.setModel(proxy if proxy is not None else model)
        self._tree.expandAll()
        # QTreeView.setModel installs a NEW QItemSelectionModel each
        # call, so the selectionChanged connection must be re-wired
        # after every rebuild — not once in _build_ui (#211).
        sel_model = self._tree.selectionModel()
        if sel_model is not None:
            sel_model.selectionChanged.connect(self._on_selection_changed)
        # _build_ui calls this BEFORE _btn_box exists; once the button
        # exists, refresh its label so a freshly-rebuilt (empty-selection)
        # tree reverts to the default "Execute" text.
        if hasattr(self, "_btn_box"):
            self._on_selection_changed()

    def _update_summary(self) -> None:
        # #502 — summary reflects the visible-after-type-filter scope,
        # not the global decision set. Matches the "visible = committed"
        # contract established by #410 / #485 — the counters at the top
        # of the dialog must agree with what Execute will actually
        # touch. Filter value None ("All") makes this identical to the
        # pre-#502 path.
        filter_value = self._get_type_filter_value()
        if filter_value is _TYPE_FILTER_ALL:
            decided = self._decided_records()
        else:
            decided = [
                (group, rec)
                for group in self._groups
                for rec in getattr(group, "items", [])
                if getattr(rec, "user_decision", "") == filter_value
            ]
        n_delete = sum(1 for _, rec in decided if rec.user_decision == "delete")
        if decided:
            self._summary_label.setText(
                t("execute_dialog.summary_decided", count=len(decided), n_delete=n_delete)
            )
        else:
            self._summary_label.setText(t("execute_dialog.summary_none"))

    def _refresh_ui_after_decision_change(self) -> None:
        """Rebuild tree, update summary, and sync Execute button + warning banner."""
        self._rebuild_tree_model()
        self._update_summary()
        self._btn_box.button(QDialogButtonBox.Ok).setEnabled(bool(self._decided_records()))
        self._refresh_execute_selected_state()
        self._refresh_warning_banner()

    def _on_type_filter_changed(self, _index: int) -> None:
        """Type-filter combo changed — refresh tree, summary, banner.

        Same three-way refresh as ``_refresh_ui_after_decision_change``
        minus the Execute-button enabled state (the global decision set
        didn't change, only the visible scope did, so the button stays
        enabled as long as ANY decisions exist anywhere). See #502.
        """
        self._rebuild_tree_model()
        self._update_summary()
        self._refresh_execute_selected_state()
        self._refresh_warning_banner()

    def _refresh_execute_selected_state(self) -> None:
        """Sync the "Execute selected" button's enabled state. Enabled
        only when ≥1 currently-highlighted file row has a decision set;
        otherwise the button would no-op confusingly. Called from
        ``_on_selection_changed`` (selection delta) and
        ``_refresh_ui_after_decision_change`` (decision delta).
        """
        selected = self._selected_file_paths()
        if not selected:
            self._btn_execute_selected.setEnabled(False)
            return
        any_with_decision = any(
            getattr(rec, "user_decision", "")
            for group in self._groups
            for rec in getattr(group, "items", [])
            if getattr(rec, "file_path", "") in selected
        )
        self._btn_execute_selected.setEnabled(any_with_decision)

    def _refresh_warning_banner(self) -> None:
        # #502 — banner is composed of up to two parts:
        # (a) complete-delete-groups warning (the pre-#502 content) — fires
        #     when at least one group is fully delete-decided WITHIN the
        #     currently-visible scope. The visible scope is governed by
        #     the type filter; ``_complete_delete_groups`` with no
        #     argument uses the unfiltered groups, so we narrow it by
        #     passing a ``paths_filter`` derived from the visible records
        #     when a non-"All" filter is active.
        # (b) hidden-destructive line — fires when the type filter is set
        #     to anything other than "All" or "Delete only" AND there are
        #     pending delete-decision rows that the filter is hiding.
        #     Without (b), switching to "Remove only" while pending deletes
        #     exist would visually clear the warning state and the user
        #     would assume nothing destructive is staged.
        filter_value = self._get_type_filter_value()
        if filter_value is _TYPE_FILTER_ALL:
            complete = self._complete_delete_groups()
        else:
            visible_paths = {
                rec.file_path
                for group in self._groups
                for rec in getattr(group, "items", [])
                if getattr(rec, "user_decision", "") == filter_value
                and getattr(rec, "file_path", "")
            }
            complete = (
                self._complete_delete_groups(paths_filter=visible_paths)
                if visible_paths else []
            )
        hidden_pending_delete = self._hidden_pending_delete_count()

        parts: list[str] = []
        if complete:
            # Each group number is wrapped in an anchor; the linkActivated
            # connection in _build_ui dispatches the href to _on_jump_to_group.
            group_list = ", ".join(f'<a href="{g}">{g}</a>' for g in complete)
            parts.append(
                t("execute_dialog.warning_complete_groups", groups=group_list)
            )
        if hidden_pending_delete > 0:
            parts.append(
                t(
                    "execute_dialog.warning_hidden_destructive",
                    count=hidden_pending_delete,
                )
            )

        if parts:
            self._warning_label.setText("<br>".join(parts))
            self._warning_banner.setVisible(True)
        else:
            self._warning_banner.setVisible(False)

    # ------------------------------------------------------------------ selection scoping

    def _selected_file_paths(self) -> set[str]:
        """Return the set of file paths currently highlighted in the tree.

        Filters to leaf (file) rows — file rows have a valid parent
        index, group header rows do not. PATH_ROLE lives on COL_NAME
        (see ``tree_model_builder.build_model``), so we resolve every
        selected index back to its row's COL_NAME sibling.

        An empty set means "no scoping" — the caller should fall back
        to the pre-#211 "execute every decided row" behaviour.
        """
        sel_model = self._tree.selectionModel()
        if sel_model is None:
            return set()
        paths: set[str] = set()
        for idx in sel_model.selectedIndexes():
            if not idx.parent().isValid():
                continue  # group header row, not a file
            path = idx.sibling(idx.row(), COL_NAME).data(PATH_ROLE)
            if path:
                paths.add(path)
        return paths

    def _on_selection_changed(self, *_args) -> None:
        """Drive the embedded preview pane AND the "Execute selected"
        enabled state on tree selection changes.

        #165 — exactly one file row selected → show_single, anything
        else → clear. Multi-select intentionally clears rather than
        showing the first row, so the user isn't misled into thinking
        the preview reflects "the" selection.

        #410 history: the OLD Execute-button-relabel-on-selection
        branch was removed in favour of pre-dialog scope filtering at
        the Action menu. The current bundle's "Execute selected" is a
        SECOND button (not a relabel) — see ``_build_ui`` button box
        setup — so the relabel rejection still stands.
        """
        # Improvement 1: enabled-state of "Execute selected" tracks
        # both the selection set AND the decided-records of those
        # selected paths. Calling the dedicated helper keeps the
        # selection logic in one place.
        self._refresh_execute_selected_state()
        if self._preview is None:
            return
        selected = self._selected_file_paths()
        if len(selected) == 1:
            path = next(iter(selected))
            self._preview.show_single(
                path,
                {
                    "name": os.path.basename(path),
                    "folder": os.path.dirname(path),
                },
            )
        else:
            self._preview.clear()

    def _on_jump_to_group(self, href: str) -> None:
        """Scroll the dialog tree to the group identified by ``href``.

        ``href`` is the group_number rendered into the banner anchor by
        :meth:`_refresh_warning_banner`. The lookup matches against the
        SORT_ROLE value set on each group row by
        :func:`app.views.tree_model_builder.build_model`. Mirrors the
        scrollTo + selectionModel.select pattern used by
        ``MainWindow._reselect_by_path``.
        """
        try:
            target = int(href)
        except (TypeError, ValueError):
            return
        model = self._tree.model()
        if model is None:
            return
        for row in range(model.rowCount()):
            idx = model.index(row, COL_GROUP)
            if not idx.isValid():
                continue
            if idx.data(SORT_ROLE) == target:
                self._tree.scrollTo(idx, QAbstractItemView.PositionAtTop)
                self._tree.setCurrentIndex(idx)
                self._tree.selectionModel().select(
                    idx,
                    QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
                )
                return

    # ------------------------------------------------------------------ context menu

    def _on_tree_context_menu(self, pos) -> None:
        index = self._tree.indexAt(pos)
        if not index.isValid():
            return
        # File rows have a parent; group rows are at the root level
        if not index.parent().isValid():
            return
        path = index.sibling(index.row(), COL_NAME).data(PATH_ROLE)
        if not path:
            return
        menu = QMenu(self)
        set_menu = menu.addMenu(t("execute_dialog.set_action_menu"))
        # include_remove=True surfaces "remove from list" alongside the
        # decision options. Single-row right-click takes the silent
        # path (no confirmation prompt) — the threshold gate lives in
        # the regex flow, where one click can cull dozens of rows.
        for label, value in settable_decisions(include_remove=True):
            act = set_menu.addAction(label)
            act.triggered.connect(
                lambda _checked=False, _v=value, _p=path: self._set_decision(_p, _v)
            )
        # Lock / Unlock — the escape hatch the user reaches for at execute
        # time when a previously-locked row needs to actually go through.
        # Single-row override is intentional: no skip-locked filter here.
        # See photo-manager#164.
        lock_act = menu.addAction(t("context_menu.lock"))
        lock_act.triggered.connect(
            lambda _checked=False, _p=path: self._set_lock(_p, True)
        )
        unlock_act = menu.addAction(t("context_menu.unlock"))
        unlock_act.triggered.connect(
            lambda _checked=False, _p=path: self._set_lock(_p, False)
        )
        # Right-click parity with the main file list — the regex dialog
        # was previously only reachable via the dedicated toolbar button.
        # Discoverability matters more than menu purity; add it here too.
        menu.addSeparator()
        regex_act = menu.addAction(t("execute_dialog.set_action_by_regex_menu"))
        regex_act.triggered.connect(self._show_select_dialog)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _set_lock(self, path: str, locked: bool) -> None:
        """Single-row Lock/Unlock from the Execute dialog right-click.

        Persists immediately and refreshes the tree so the lock glyph
        updates without waiting for an Execute pass. See photo-manager#164.
        """
        for group in self._groups:
            for rec in getattr(group, "items", []):
                if rec.file_path == path:
                    rec.is_locked = locked
                    break
        if self._manifest_path:
            try:
                from infrastructure.manifest_repository import ManifestRepository
                ManifestRepository().batch_update_lock_state(
                    self._manifest_path, {path: locked}
                )
            except Exception as exc:
                logger.warning("Failed to persist lock state: {}", exc)
        # #444 — single-row lock/unlock mutates rec.is_locked in place
        # on vm.groups; main tree must re-render the lock glyph on close.
        self._decisions_changed = True
        self._refresh_ui_after_decision_change()
        # #318 — match the main-window route's confirmation. Without
        # this emit, single-row Lock/Unlock from the Execute Action
        # dialog left the status bar at its prior baseline.
        if self._status_reporter is not None:
            report_count(
                self._status_reporter,
                t("file_op.locked_verb") if locked else t("file_op.unlocked_verb"),
                1,
                t("file_op.noun_row_singular"),
                t("file_op.noun_row_plural"),
            )

    def _set_decision(self, path: str, decision: str) -> None:
        if decision == LOCK_SENTINEL:
            self._set_lock(path, True)
            return
        if decision == UNLOCK_SENTINEL:
            self._set_lock(path, False)
            return
        if decision == REMOVE_FROM_LIST_SENTINEL:
            # Single-row right-click — always confirm before removing,
            # for symmetry with the regex flow. Set+execute is a bigger
            # commitment than delete/keep, even on one row.
            from PySide6.QtWidgets import QMessageBox
            # The remove-from-list confirm fires regardless of lock —
            # the lock confirm wraps a DECISION change, but
            # remove-from-list is a deferred remove with its own
            # confirm. If the target is locked, surface the lock
            # confirm FIRST and short-circuit on cancel; the existing
            # remove-from-list confirm then runs as before.
            if self._row_is_locked(path):
                verdict = self._ask_lock_confirm(
                    paths=[path],
                    decision_for_label=REMOVE_FROM_LIST_DECISION,
                )
                if verdict != _DIALOG_VERDICT_PROCEED:
                    return
                # User chose Unlock & Apply — unlock the row before
                # the remove-from-list confirm fires.
                self._set_lock(path, False)
            reply = QMessageBox.question(
                self,
                t("file_op.remove_confirm_title"),
                t("file_op.remove_confirm_body", count=1),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            self._remove_from_list_paths([path])
            return
        # Destructive decision (delete / "" keep) — route through the
        # unified lock confirm if the target row is locked.
        if self._row_is_locked(path):
            verdict = self._ask_lock_confirm(
                paths=[path], decision_for_label=decision
            )
            if verdict != _DIALOG_VERDICT_PROCEED:
                return
            self._set_lock(path, False)
        for group in self._groups:
            for rec in getattr(group, "items", []):
                if rec.file_path == path:
                    rec.user_decision = decision
                    break
        # #444 — single-row decision change mutates vm.groups in place;
        # main tree must re-render the Action cell on close.
        self._decisions_changed = True
        self._refresh_ui_after_decision_change()
        # #318 — match the main-window route's confirmation.
        # #425 — interpolate the localised label, not the raw value.
        from app.views.handlers.file_operations import _decision_display_label
        if self._status_reporter is not None:
            self._status_reporter.show_status(
                t("file_op.decision_set_status", decision=_decision_display_label(decision))
            )

    def _row_is_locked(self, path: str) -> bool:
        for group in self._groups:
            for rec in getattr(group, "items", []):
                if rec.file_path == path:
                    return bool(rec.is_locked)
        return False

    def _ask_lock_confirm(
        self, *, paths: list[str], decision_for_label: str, affected_count: int | None = None
    ) -> int:
        """Show the locked-rows confirm dialog for ``paths`` (all locked).

        Returns one of the ``_DIALOG_VERDICT_*`` constants. For
        single-row entry points (degenerate single-locked case) the
        "Apply to Unlocked Only" button is disabled by construction —
        the helper still surfaces the dialog so the user has a
        deliberate stop sign rather than a silent override.
        """
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        from app.views.handlers.file_operations import _decision_display_label

        verdict = LockedRowsConfirmDialog.ask(
            self,
            action_label=_decision_display_label(decision_for_label),
            affected_count=affected_count if affected_count is not None else len(paths),
            locked_paths=paths,
        )
        if verdict == LockedRowsConfirmDialog.APPLY_ALL_UNLOCKED:
            return _DIALOG_VERDICT_PROCEED
        if verdict == LockedRowsConfirmDialog.APPLY_UNLOCKED_ONLY:
            return _DIALOG_VERDICT_SKIP_LOCKED
        return _DIALOG_VERDICT_CANCEL

    def _remove_from_list_paths(self, paths: list[str]) -> None:
        """Drop ``paths`` from self._groups (in place) and the manifest.

        ``self._groups`` aliases ``vm.groups`` (passed by reference at
        construction). In-place mutation here means the main window's
        viewmodel is already up to date when the dialog closes — the
        parent only needs to re-render. Empty groups are dropped from
        the list to avoid showing a header with no rows.
        """
        if not paths:
            return
        removed = set(paths)
        # Walk groups; strip matched records; drop groups that empty out.
        # We iterate over a copy and rebuild via list slicing so we
        # mutate the same list object self._groups points at — caller
        # aliasing depends on it.
        keep_groups = []
        for g in self._groups:
            kept_items = [it for it in getattr(g, "items", []) if it.file_path not in removed]
            if kept_items:
                # Mutate the existing group object so any other
                # references to it (vm-side) stay consistent.
                g.items = kept_items
                keep_groups.append(g)
        self._groups[:] = keep_groups  # in-place replacement preserves the alias
        if self._manifest_path:
            try:
                from infrastructure.manifest_repository import ManifestRepository
                ManifestRepository().remove_from_review(self._manifest_path, list(paths))
            except Exception as exc:
                logger.warning("Failed to sync removed paths to manifest: {}", exc)
        self.removed_from_list_paths.extend(paths)
        self._refresh_ui_after_decision_change()
        # #318 — match the main-window route's confirmation
        # (set_locked_state's report_count pattern, file_operations.py).
        if self._status_reporter is not None and paths:
            report_count(
                self._status_reporter,
                t("status.verb_removed"),
                len(paths),
                t("status.noun_item_from_list_singular"),
                plural=t("status.noun_item_from_list_plural"),
            )

    # ------------------------------------------------------------------ set action by regex

    def _show_select_dialog(self) -> None:
        from app.views.dialogs.select_dialog import ActionDialog
        from app.views.handlers.dialog_handler_helpers import (
            default_action_dialog_fields,
        )
        from app.views.handlers.file_operations import build_match_fn

        # Canonical field list — same source the main-window route uses
        # (dialog_handler.py:86). #392: the hard-coded list previously
        # omitted Score / Group Count / Similarity / Resolution, leaving
        # them silently unreachable from the Execute route. The downstream
        # _set_decision_by_regex already dispatched the __cmp__: /
        # __top_n__: pseudo-patterns correctly, so the only barrier was
        # this field list. ActionDialog displays localized labels but
        # emits the English name back via setActionRequested.
        fields = list(default_action_dialog_fields())
        # #443 — scope Select-by to the rendered subset. The dialog
        # only shows _groups_with_decisions(); the sub-dialog must
        # match/preview/dispatch against the same rows the user is
        # looking at, not the full manifest. Filtered list keeps the
        # original record references (no copy) so writes inside the
        # sub-dialog still reach vm.groups through the existing
        # aliasing contract.
        #
        # Fallback: when no decisions exist yet, the dialog tree is
        # empty and there's no rendered scope to narrow to — fall
        # back to self._groups so the user can seed initial decisions
        # via Select-by (the s43 numeric-threshold scenario depends on
        # this — ActionDialog inspects records to detect numeric
        # fields and an empty groups list would prevent the numeric
        # panel from surfacing).
        scoped_groups = self._groups_with_decisions() or self._groups
        match_fn = build_match_fn(scoped_groups) if scoped_groups else None
        dlg = ActionDialog(
            fields=fields, parent=self, match_fn=match_fn,
            settings=self._settings,
            # #209 — pass the raw groups so the dialog can rank
            # records for Top-N within group and run threshold
            # comparisons against numeric/date fields.
            groups=scoped_groups,
            context_id="execute",  # A8: isolate from main-window preference
        )
        dlg.setActionRequested.connect(self._set_decision_by_regex)
        dlg.exec()

    def _matched_paths_for_pattern(
        self, field: str, pattern: str
    ) -> list[str]:
        """Resolve ``pattern`` against ``self._groups`` and return matched
        file_paths, preserving the user's tree order (group-then-record).

        Handles three pattern shapes:
          * ``__cmp__:OP:VALUE`` — threshold comparison (#209)
          * ``__top_n__:N:asc|desc`` — top/bottom N within group (#209)
          * anything else — case-insensitive regex against the field
            value from ``_get_record_field``.

        Raises :class:`re.error` on an invalid regex; raises
        :class:`ValueError` on a malformed numeric pattern. Caller
        catches and surfaces a localized message.
        """
        import re as _re
        from app.views.dialogs.select_dialog import (
            PATTERN_CMP_PREFIX,
            PATTERN_TOP_N_PREFIX,
            decode_cmp_pattern,
            decode_top_n_pattern,
            select_paths_by_threshold,
            select_paths_top_n,
        )
        from app.views.handlers.file_operations import _get_record_field

        if pattern.startswith(PATTERN_CMP_PREFIX):
            decoded = decode_cmp_pattern(pattern)
            if decoded is None:
                raise ValueError(pattern)
            op, value_text = decoded
            return select_paths_by_threshold(
                self._groups, field, op, value_text
            )
        if pattern.startswith(PATTERN_TOP_N_PREFIX):
            decoded = decode_top_n_pattern(pattern)
            if decoded is None:
                raise ValueError(pattern)
            n, order = decoded
            return select_paths_top_n(self._groups, field, n, order)
        rx = _re.compile(pattern, _re.IGNORECASE)
        out: list[str] = []
        for group in self._groups:
            for rec in getattr(group, "items", []):
                value = _get_record_field(rec, field)
                if value is not None and rx.search(value):
                    out.append(rec.file_path)
        return out

    def _set_decision_by_regex(self, field: str, pattern: str, new_decision: str) -> None:
        """Find all file rows where field matches pattern and route by action.

        ``new_decision == REMOVE_FROM_LIST_SENTINEL`` removes the
        matched rows from the review list (mirrors the main-window
        regex flow). ``LOCK_SENTINEL`` / ``UNLOCK_SENTINEL`` flip
        ``is_locked`` for matched rows (idempotent — applied to all,
        no skip-locked pre-filter on this branch). For destructive
        decisions, already-locked rows are skipped — see
        photo-manager#164.

        Accepts the same regex strings as before, plus the numeric
        pseudo-patterns ``__cmp__:`` and ``__top_n__:`` emitted by the
        Set Action dialog when the user picks a numeric-capable field
        (#209). All three pattern shapes funnel through the same
        matched-paths set, so the lock-confirm / persist / refresh
        steps stay shared.
        """
        import re as _re
        from PySide6.QtWidgets import QMessageBox

        try:
            matched_for_op = self._matched_paths_for_pattern(field, pattern)
        except _re.error as exc:
            QMessageBox.warning(self, t("execute_dialog.invalid_regex_title"), str(exc))
            return
        except ValueError:
            # Malformed numeric pattern — treat as "no match" rather
            # than a hard error. The dialog UI prevents most invalid
            # patterns; a stray one shouldn't crash the apply flow.
            QMessageBox.information(
                self,
                t("execute_dialog.no_match_title"),
                t("execute_dialog.no_match_body"),
            )
            return

        # Lock / unlock route — applied to ALL matched, no skip filter.
        # The whole point of having unlock available here is that locked
        # rows need bulk-untangling at execute time.
        if new_decision in (LOCK_SENTINEL, UNLOCK_SENTINEL):
            target_locked = (new_decision == LOCK_SENTINEL)
            matched_set = set(matched_for_op)
            lock_batch: dict[str, bool] = {}
            for group in self._groups:
                for rec in getattr(group, "items", []):
                    if rec.file_path in matched_set:
                        rec.is_locked = target_locked
                        lock_batch[rec.file_path] = target_locked
            if not lock_batch:
                QMessageBox.information(
                    self,
                    t("execute_dialog.no_match_title"),
                    t("execute_dialog.no_match_body"),
                )
                return
            if self._manifest_path:
                try:
                    from infrastructure.manifest_repository import ManifestRepository
                    ManifestRepository().batch_update_lock_state(
                        self._manifest_path, lock_batch
                    )
                except Exception as exc:
                    logger.warning("Failed to persist lock state: {}", exc)
            # #444 — bulk lock/unlock counts as a row-state mutation
            # the main tree needs to re-render on close.
            self._decisions_changed = True
            self._refresh_ui_after_decision_change()
            # #318 — match the main-window route's bulk-lock
            # confirmation. The bulk path is higher-friction than the
            # single-row paths because there's no per-row visible
            # feedback for which N rows just got the flag flip.
            if self._status_reporter is not None and lock_batch:
                report_count(
                    self._status_reporter,
                    t("file_op.locked_verb") if target_locked
                    else t("file_op.unlocked_verb"),
                    len(lock_batch),
                    t("file_op.noun_row_singular"),
                    t("file_op.noun_row_plural"),
                )
            return

        # Bulk regex remove behaves like bulk regex delete/keep —
        # matched rows get REMOVE_FROM_LIST_DECISION and the user
        # reviews + commits via Execute.
        if new_decision == REMOVE_FROM_LIST_SENTINEL:
            new_decision = REMOVE_FROM_LIST_DECISION

        # Compute locked subset from the unified matched set. Order
        # preserved from matched_for_op so the lock-confirm dialog's
        # truncated list reads as the user's tree order.
        matched_paths: list[str] = list(matched_for_op)
        matched_set = set(matched_paths)
        locked_paths: list[str] = []
        for group in self._groups:
            for rec in getattr(group, "items", []):
                if rec.file_path in matched_set and rec.is_locked:
                    locked_paths.append(rec.file_path)

        if not matched_paths:
            QMessageBox.information(
                self,
                t("execute_dialog.no_match_title"),
                t("execute_dialog.no_match_body"),
            )
            return

        apply_paths = matched_paths
        if locked_paths:
            verdict = self._ask_lock_confirm(
                paths=locked_paths,
                decision_for_label=new_decision,
                affected_count=len(matched_paths),
            )
            if verdict == _DIALOG_VERDICT_CANCEL:
                return
            if verdict == _DIALOG_VERDICT_PROCEED:
                # Unlock the locked subset first; the apply loop below
                # then writes the decision to every matched row.
                self._batch_set_lock(locked_paths, locked=False)
            else:
                # _DIALOG_VERDICT_SKIP_LOCKED — apply only to unlocked.
                locked_set = set(locked_paths)
                apply_paths = [p for p in matched_paths if p not in locked_set]
                if not apply_paths:
                    # Degenerate case shouldn't occur (button is
                    # disabled when no unlocked rows) but guard
                    # defensively.
                    return

        batch: dict[str, str] = {}
        for group in self._groups:
            for rec in getattr(group, "items", []):
                if rec.file_path in apply_paths:
                    rec.user_decision = new_decision
                    batch[rec.file_path] = new_decision

        if self._manifest_path and batch:
            try:
                from infrastructure.manifest_repository import ManifestRepository
                ManifestRepository().batch_update_decisions(self._manifest_path, batch)
            except Exception as exc:
                logger.warning("Failed to persist batch decisions: {}", exc)

        # #444 — flip the sync flag whenever any decision actually
        # changed in this dialog's session. The parent uses this on
        # close to decide whether to refresh the main tree.
        if batch:
            self._decisions_changed = True

        self._refresh_ui_after_decision_change()
        # Mirror the s14 main-menu regex flow's status confirmation
        # (file_operations.set_decision_by_regex emits the same key).
        # Without this, the main window's status bar still shows the
        # initial "Loaded manifest" baseline after the user applies a
        # regex here — see #316.
        # #425 — interpolate the localised label, not the raw value.
        if batch and self._status_reporter is not None:
            from app.views.handlers.file_operations import _decision_display_label
            self._status_reporter.show_status(
                t("file_op.decision_set_status", decision=_decision_display_label(new_decision))
            )
        if locked_paths and len(apply_paths) < len(matched_paths):
            logger.info(
                "Set {} decisions, skipped {} locked rows",
                len(batch), len(matched_paths) - len(apply_paths),
            )

    def _batch_set_lock(self, paths: list[str], locked: bool) -> None:
        """Flip ``is_locked`` for ``paths`` in-memory and persist.

        Internal helper for the lock-confirm flow — distinct from
        :meth:`_set_lock` which targets a single path and refreshes
        the UI; this helper deliberately skips the UI refresh because
        the caller will do its own refresh after the subsequent
        decision-set pass.
        """
        if not paths:
            return
        path_set = set(paths)
        for group in self._groups:
            for rec in getattr(group, "items", []):
                if rec.file_path in path_set:
                    rec.is_locked = locked
        if self._manifest_path:
            try:
                from infrastructure.manifest_repository import ManifestRepository
                ManifestRepository().batch_update_lock_state(
                    self._manifest_path, dict.fromkeys(paths, locked)
                )
            except Exception as exc:
                logger.warning("Failed to persist batch lock state: {}", exc)

    # ------------------------------------------------------------------ execute

    def _on_execute_selected_requested(self) -> None:
        """Improvement 1 — "Execute selected" button entry point.

        Resolves the currently-highlighted file paths and dispatches to
        the standard execute pipeline with ``paths_filter`` set. Empty
        selection short-circuits (the button is normally disabled in
        that state, but a race between selection-cleared and
        clicked-signal can land here harmlessly).
        """
        selected = self._selected_file_paths()
        if not selected:
            return
        self._on_execute_requested(paths_filter=selected)

    def _on_execute_requested(
        self, paths_filter: set[str] | None = None,
    ) -> None:
        from PySide6.QtWidgets import QMessageBox

        # #410 — scope narrowing for the FULL-execute path is the
        # handler's concern (pre-dialog "(only selected)" Action-menu
        # entry pre-filters ``self._groups``). The PARTIAL-execute
        # path (``paths_filter is not None``) is in-dialog scoping
        # added by Improvement 1: the dialog still sees every group
        # it was constructed with, but only acts on records whose
        # path is in the filter.

        # #502 — when the type filter is set to anything other than
        # "All", AND ``paths_filter`` together to land at the effective
        # commit scope: ``visible-by-type ∩ selected``. Resolved BEFORE
        # the ``in_scope`` closure is defined so the lock-confirm
        # pre-execute scan + the complete-group confirm both see the
        # same narrowed view — otherwise a "Lock only" filter could
        # spuriously trigger the destructive-row lock-confirm gate. The
        # symmetry: "All" + no selection → ``effective_filter is None``
        # (unchanged behaviour); "Delete only" + no selection →
        # ``effective_filter`` is every delete path; "Delete only" +
        # selection → intersection of the two.
        type_filter_value = self._get_type_filter_value()
        if type_filter_value is not _TYPE_FILTER_ALL:
            type_paths = {
                rec.file_path
                for group in self._groups
                for rec in getattr(group, "items", [])
                if getattr(rec, "user_decision", "") == type_filter_value
                and getattr(rec, "file_path", "")
            }
            if paths_filter is None:
                paths_filter = type_paths
            else:
                paths_filter = paths_filter & type_paths

        def in_scope(rec) -> bool:
            if paths_filter is None:
                return True
            return getattr(rec, "file_path", "") in paths_filter

        # Pre-execute scan for locked rows with decision='delete'.
        # These can exist if the user set the decision FIRST and then
        # locked the row — under the new model (#182) the user must
        # explicitly choose to unlock-and-delete or skip-locked before
        # any destructive action runs.
        total_delete_count = sum(
            1
            for group in self._groups
            for rec in getattr(group, "items", [])
            if getattr(rec, "user_decision", "") == "delete" and in_scope(rec)
        )
        locked_delete_paths = [
            rec.file_path
            for group in self._groups
            for rec in getattr(group, "items", [])
            if getattr(rec, "user_decision", "") == "delete"
            and getattr(rec, "is_locked", False)
            and in_scope(rec)
        ]
        if locked_delete_paths:
            verdict = self._ask_lock_confirm(
                paths=locked_delete_paths,
                decision_for_label="delete",
                affected_count=total_delete_count,
            )
            if verdict == _DIALOG_VERDICT_CANCEL:
                return
            if verdict == _DIALOG_VERDICT_PROCEED:
                # Unlock and proceed with the full delete set.
                self._batch_set_lock(locked_delete_paths, locked=False)
            else:
                # Skip Locked — clear the decision on locked rows so
                # _on_execute (which iterates decision='delete') skips
                # them, but leave is_locked=True so the user's
                # explicit lock survives.
                self._clear_decision_on(locked_delete_paths)
            self._refresh_ui_after_decision_change()

        # Complete-group confirm: for full execute, groups already
        # filtered to the action's scope by the handler. For partial
        # execute, the check is narrowed to in-scope records only —
        # see ``_complete_delete_groups``'s paths_filter parameter.
        complete = self._complete_delete_groups(paths_filter=paths_filter)
        if complete:
            group_list = ", ".join(str(g) for g in complete)
            reply = QMessageBox.question(
                self,
                t("execute_dialog.confirm_all_title"),
                t("execute_dialog.confirm_all_body", groups=group_list),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self._on_execute(paths_filter=paths_filter)

    def _clear_decision_on(self, paths: list[str]) -> None:
        """Reset ``user_decision`` to '' for ``paths``. Used by the
        pre-execute confirm's "Apply to Unlocked Only" branch — the
        user said "leave these locked rows alone," so we clear their
        delete decision so _on_execute's iteration skips them. Lock
        state stays untouched.
        """
        if not paths:
            return
        path_set = set(paths)
        batch: dict[str, str] = {}
        for group in self._groups:
            for rec in getattr(group, "items", []):
                if rec.file_path in path_set:
                    rec.user_decision = ""
                    batch[rec.file_path] = ""
        if self._manifest_path and batch:
            try:
                from infrastructure.manifest_repository import ManifestRepository
                ManifestRepository().batch_update_decisions(self._manifest_path, batch)
            except Exception as exc:
                logger.warning("Failed to persist cleared decisions: {}", exc)

    def _on_execute(self, paths_filter: set[str] | None = None) -> None:
        # #410 baseline: for the full-execute path, groups arrive
        # pre-filtered from the handler — this method acts on every
        # decided row it was given.
        # Improvement 1: when ``paths_filter`` is set (partial-execute
        # via the "Execute selected" button), the iteration narrows to
        # records whose path is in the filter. Records outside the
        # filter retain their decisions for a future Execute click.

        def in_scope(rec) -> bool:
            if paths_filter is None:
                return True
            return getattr(rec, "file_path", "") in paths_filter

        # Batch-persist current decisions before executing. For partial
        # execute the persist set is still the full decision picture —
        # rows outside the filter retain their decisions in the manifest
        # so a subsequent execute pass sees them. The filter only
        # narrows the ACTION pass below, not the manifest sync.
        if self._manifest_path:
            batch = {
                rec.file_path: rec.user_decision
                for group in self._groups
                for rec in getattr(group, "items", [])
                if getattr(rec, "user_decision", "")
            }
            if batch:
                try:
                    from infrastructure.manifest_repository import ManifestRepository
                    ManifestRepository().batch_update_decisions(self._manifest_path, batch)
                except Exception as exc:
                    logger.warning("Failed to persist decisions before execute: {}", exc)

        # Collect deferred-remove paths separately from immediate ones
        # so we don't double-mark already-removed rows in SQLite. The
        # immediate single-row right-click path already calls
        # remove_from_review at click time.
        deferred_remove_paths: list[str] = []
        # Track in-scope records we executed so we can clear their
        # decision in-memory below. Without this, a subsequent
        # "Execute selected" or "Execute" click would re-process them
        # (and _delete_file would hit "file not found" on the now-
        # deleted files).
        executed_in_scope: list[tuple] = []
        for group in self._groups:
            for rec in getattr(group, "items", []):
                if not in_scope(rec):
                    continue
                decision = getattr(rec, "user_decision", "") or ""
                if decision == "delete":
                    self._delete_file(rec.file_path)
                    executed_in_scope.append((group, rec))
                elif decision == "keep":
                    self.executed_paths.append(rec.file_path)
                    executed_in_scope.append((group, rec))
                elif decision == REMOVE_FROM_LIST_DECISION:
                    deferred_remove_paths.append(rec.file_path)
                    executed_in_scope.append((group, rec))

        if self._manifest_path:
            all_done = self.deleted_paths + self.executed_paths
            if all_done:
                try:
                    from infrastructure.manifest_repository import ManifestRepository
                    ManifestRepository().mark_executed(self._manifest_path, all_done)
                except Exception as exc:
                    logger.warning("Failed to mark executed in manifest: {}", exc)

            if deferred_remove_paths:
                try:
                    from infrastructure.manifest_repository import ManifestRepository
                    ManifestRepository().remove_from_review(
                        self._manifest_path, deferred_remove_paths
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to mark deferred remove rows: {}", exc
                    )
                # Surface to the caller so it can drop these from
                # vm.groups; the immediate-path entries are already
                # gone from there via in-place mutation.
                self.removed_from_list_paths.extend(deferred_remove_paths)

        if self._missing_paths:
            from PySide6.QtWidgets import QMessageBox
            missing_list = "\n".join(self._missing_paths[:20])
            suffix = (
                t("execute_dialog.files_not_found_more", n=len(self._missing_paths) - 20)
                if len(self._missing_paths) > 20
                else ""
            )
            QMessageBox.warning(
                self,
                t("execute_dialog.files_not_found_title"),
                t("execute_dialog.files_not_found_body", missing=missing_list, suffix=suffix),
            )

        if self._failed_paths:
            from PySide6.QtWidgets import QMessageBox
            failed_lines = [f"{p} — {reason}" for p, reason in self._failed_paths[:20]]
            failed_list = "\n".join(failed_lines)
            suffix = (
                t("execute_dialog.files_failed_more", n=len(self._failed_paths) - 20)
                if len(self._failed_paths) > 20
                else ""
            )
            QMessageBox.warning(
                self,
                t("execute_dialog.files_failed_title"),
                t("execute_dialog.files_failed_body", failed=failed_list, suffix=suffix),
            )

        # Full execute → close the dialog (existing behaviour).
        # Partial execute → keep the dialog open so the user can
        # continue reviewing the remaining un-executed rows. Clear the
        # in-memory user_decision on the records we just executed so a
        # subsequent click doesn't reprocess them (the deletion already
        # ran for delete-decision rows; running it again would hit
        # "file not found").
        if paths_filter is None:
            self.accept()
            return
        for _group, rec in executed_in_scope:
            try:
                rec.user_decision = ""
            except Exception:
                # Pydantic-frozen or read-only record — extremely
                # defensive; PhotoRecord is a mutable dataclass today.
                logger.warning(
                    "Could not clear user_decision on executed rec {}", rec
                )
        self._decisions_changed = True
        self._refresh_ui_after_decision_change()

    def _delete_file(self, path: str) -> None:
        if not os.path.exists(path):
            self._missing_paths.append(path)
            logger.warning("File not found, skipping delete: {}", path)
            return
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
            self._failed_paths.append((path, str(exc)))

    def done(self, result: int) -> None:
        """Persist geometry on every close path (#215).

        ``done()`` funnels ``accept()``, ``reject()`` and the X-button
        path so this one hook catches every dismissal.

        #165 — when the preview-enabled layout built a splitter, save
        its state on the same close path so divider position survives
        across opens.
        """
        save_widget_geometry(self, QSETTINGS_KEY_EXECUTE_ACTION_DIALOG_GEOM)
        if self._splitter is not None:
            save_splitter_state(
                self._splitter,
                QSETTINGS_KEY_EXECUTE_ACTION_DIALOG_SPLITTER_STATE,
            )
        super().done(result)
