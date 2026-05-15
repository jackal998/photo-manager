"""ExecuteActionDialog — review and confirm planned file operations."""

from __future__ import annotations

import os

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QMenu,
    QPushButton,
    QTreeView,
    QVBoxLayout,
)
from loguru import logger

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
from app.views.tree_model_builder import build_model
from app.views.window_state import (
    QSETTINGS_KEY_EXECUTE_ACTION_DIALOG_GEOM,
    restore_widget_geometry,
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


class ExecuteActionDialog(QDialog):
    """Shows groups with decisions for final review; executes file decisions on confirm."""

    def __init__(
        self,
        groups: list,
        manifest_path: str | None,
        parent=None,
        settings: object | None = None,
    ) -> None:
        super().__init__(parent)
        # settings is optional so existing tests / callers that don't
        # need Phase B persistence can pass None. Threaded into the
        # inner regex dialog via _show_select_dialog so its mode +
        # recent-patterns survive across runs even when reached via
        # the Execute Action route.
        self._settings = settings
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
        self._missing_paths: list[str] = []
        self._src_model = None
        self._build_ui()
        # #215 — restore last saved geometry. ``setMinimumSize`` above
        # acts as the floor; the off-screen guard inside
        # ``restore_widget_geometry`` falls back to that default when a
        # previously-saved rect would land on a disconnected monitor.
        restore_widget_geometry(self, QSETTINGS_KEY_EXECUTE_ACTION_DIALOG_GEOM)

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

    def _complete_delete_groups(self) -> list[int]:
        """Return group_numbers where every record has user_decision='delete'."""
        result = []
        for group in self._groups:
            items = getattr(group, "items", [])
            if not items:
                continue
            if all(getattr(rec, "user_decision", "") == "delete" for rec in items):
                result.append(group.group_number)
        return sorted(result)

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._summary_label = QLabel()
        self._update_summary()
        layout.addWidget(self._summary_label)

        select_btn = QPushButton(t("execute_dialog.select_button"))
        select_btn.clicked.connect(self._show_select_dialog)
        layout.addWidget(select_btn)

        self._tree = QTreeView()
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.setAlternatingRowColors(True)
        # #211 — multi-row highlight feeds the scoped-execute feature.
        # Matches the main result tree (tree_controller.py:45).
        self._tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._rebuild_tree_model()
        layout.addWidget(self._tree)

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
        self._btn_box.rejected.connect(self.reject)
        layout.addWidget(self._btn_box)

        self._refresh_warning_banner()

    def _rebuild_tree_model(self) -> None:
        groups = self._groups_with_decisions()
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
        decided = self._decided_records()
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
        self._refresh_warning_banner()

    def _refresh_warning_banner(self) -> None:
        complete = self._complete_delete_groups()
        if complete:
            # Each group number is wrapped in an anchor; the linkActivated
            # connection in _build_ui dispatches the href to _on_jump_to_group.
            group_list = ", ".join(f'<a href="{g}">{g}</a>' for g in complete)
            self._warning_label.setText(
                t("execute_dialog.warning_complete_groups", groups=group_list)
            )
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
        """Re-label the Execute button based on tree selection state (#211).

        When ≥1 file row is highlighted, the button reads
        ``execute_button_highlighted`` so the user sees that Execute is
        scoped to the highlight. Empty selection reverts to the default
        ``execute_button`` label.
        """
        btn = self._btn_box.button(QDialogButtonBox.Ok)
        if btn is None:
            return
        if self._selected_file_paths():
            btn.setText(t("execute_dialog.execute_button_highlighted"))
        else:
            btn.setText(t("execute_dialog.execute_button"))

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
        self._refresh_ui_after_decision_change()

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
        self._refresh_ui_after_decision_change()

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

    # ------------------------------------------------------------------ set action by regex

    def _show_select_dialog(self) -> None:
        from app.views.dialogs.select_dialog import ActionDialog
        from app.views.handlers.file_operations import build_match_fn

        # Internal English keys; ActionDialog displays localized labels but
        # emits the English name back via setActionRequested.
        fields = ["Action", "Lock", "File Name", "Folder", "Size (Bytes)", "Creation Date", "Shot Date"]
        # Build the live-preview match_fn from this dialog's groups —
        # which alias the main window's vm.groups (see _remove_from_list_paths
        # docstring), so both surfaces preview against the same data.
        match_fn = build_match_fn(self._groups) if self._groups else None
        dlg = ActionDialog(
            fields=fields, parent=self, match_fn=match_fn,
            settings=self._settings,
            # #209 — pass the raw groups so the dialog can rank
            # records for Top-N within group and run threshold
            # comparisons against numeric/date fields.
            groups=self._groups,
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
            self._refresh_ui_after_decision_change()
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

        self._refresh_ui_after_decision_change()
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
                    self._manifest_path, {p: locked for p in paths}
                )
            except Exception as exc:
                logger.warning("Failed to persist batch lock state: {}", exc)

    # ------------------------------------------------------------------ execute

    def _on_execute_requested(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        # #211 — when the tree has a non-empty selection, scope this
        # Execute pass to only the highlighted file rows. The lock
        # guard, complete-group confirm, and downstream _on_execute
        # iteration all narrow to this set. Empty selection keeps the
        # pre-#211 "act on every decided row" behaviour.
        selected = self._selected_file_paths()
        scope: set[str] | None = selected if selected else None
        # Stash on self so _on_execute (separate method) can read it
        # without changing its signature.
        self._execute_scope = scope

        def _in_scope(path: str) -> bool:
            return scope is None or path in scope

        # Pre-execute scan for locked rows with decision='delete'.
        # These can exist if the user set the decision FIRST and then
        # locked the row — under the new model (#182) the user must
        # explicitly choose to unlock-and-delete or skip-locked before
        # any destructive action runs. This replaces the silent
        # filter at delete_service:50 (which never fired in the GUI
        # path anyway — _on_execute deletes directly, see below).
        total_delete_count = sum(
            1
            for group in self._groups
            for rec in getattr(group, "items", [])
            if getattr(rec, "user_decision", "") == "delete"
            and _in_scope(rec.file_path)
        )
        locked_delete_paths = [
            rec.file_path
            for group in self._groups
            for rec in getattr(group, "items", [])
            if getattr(rec, "user_decision", "") == "delete"
            and getattr(rec, "is_locked", False)
            and _in_scope(rec.file_path)
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

        # Complete-group confirm: under scoping, a group is only
        # "fully deleted by this click" when every delete-decision row
        # of that group is in scope. Otherwise the confirm wording
        # ("EVERY file deleted") would misrepresent what this click
        # does. The amber banner is unrelated — it reflects the user's
        # stored decision state, not this click's effect.
        complete = self._complete_delete_groups_in_scope(scope)
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
        self._on_execute()

    def _complete_delete_groups_in_scope(
        self, scope: set[str] | None
    ) -> list[int]:
        """Like :meth:`_complete_delete_groups`, but restricted to groups
        whose every member is in ``scope``. With ``scope=None`` this is
        the original behaviour. With a non-empty scope, only groups
        whose delete set lies entirely inside the highlighted subset
        are returned — those are the groups this Execute click will
        actually empty on disk.
        """
        if scope is None:
            return self._complete_delete_groups()
        result = []
        for group in self._groups:
            items = getattr(group, "items", [])
            if not items:
                continue
            if not all(
                getattr(rec, "user_decision", "") == "delete" for rec in items
            ):
                continue
            if all(rec.file_path in scope for rec in items):
                result.append(group.group_number)
        return sorted(result)

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

    def _on_execute(self) -> None:
        # #211 — _on_execute_requested stashes the selection scope on
        # self before delegating here. None means "no scoping" (execute
        # every decided row, pre-#211 behaviour); a non-empty set means
        # "only act on these file paths".
        scope: set[str] | None = getattr(self, "_execute_scope", None)

        def _in_scope(path: str) -> bool:
            return scope is None or path in scope

        # Batch-persist all current decisions before executing
        if self._manifest_path:
            batch = {
                rec.file_path: rec.user_decision
                for group in self._groups
                for rec in getattr(group, "items", [])
                if getattr(rec, "user_decision", "")
                and _in_scope(rec.file_path)
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
        for group in self._groups:
            for rec in getattr(group, "items", []):
                if not _in_scope(rec.file_path):
                    continue
                decision = getattr(rec, "user_decision", "") or ""
                if decision == "delete":
                    self._delete_file(rec.file_path)
                elif decision == "keep":
                    self.executed_paths.append(rec.file_path)
                elif decision == REMOVE_FROM_LIST_DECISION:
                    deferred_remove_paths.append(rec.file_path)

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

        self.accept()

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

    def done(self, result: int) -> None:
        """Persist geometry on every close path (#215).

        ``done()`` funnels ``accept()``, ``reject()`` and the X-button
        path so this one hook catches every dismissal.
        """
        save_widget_geometry(self, QSETTINGS_KEY_EXECUTE_ACTION_DIALOG_GEOM)
        super().done(result)


class ExecuteRunner(ExecuteActionDialog):
    """Headless wrapper around ``ExecuteActionDialog``'s destructive flow.

    #165 prototype — when option B is engaged (Execute Mode), the main
    window owns the tree, preview, banner, and Execute button. The
    destructive pipeline itself (pre-execute lock-confirm scan,
    complete-group "all files will be deleted" prompt, send2trash
    loop, missing-files report) still lives on ``ExecuteActionDialog``
    where ~1500 lines of tests cover it. ``ExecuteRunner`` subclasses
    the dialog, skips ``_build_ui`` so no widgets are constructed, and
    exposes ``run(scope)`` as a deliberate entry point that bypasses
    the tree-selection-driven scope computation.

    A follow-up after option B is approved would extract the pipeline
    methods onto ``ExecuteRunner`` directly and shrink the parent class
    to a deprecated shell — the inheritance is the prototype shortcut
    that keeps the existing test surface validating the runner for
    free without forking 1500 lines of coverage.
    """

    def _build_ui(self) -> None:  # type: ignore[override]
        # No widgets — the main window owns the Execute-mode UI.
        # _on_execute / _on_execute_requested still work because every
        # piece of state they touch (self._groups, self._manifest_path,
        # self.deleted_paths, …) is initialised by the base ``__init__``
        # before this hook is invoked.
        pass

    def _refresh_ui_after_decision_change(self) -> None:  # type: ignore[override]
        # The dialog calls this from the lock-confirm pathway to
        # re-render its own tree. The main window's tree is the source
        # of truth in Execute mode; refresh happens there after run()
        # returns. No-op here so we don't hit AttributeError on
        # self._btn_box / self._tree.
        pass

    def done(self, result: int) -> None:  # type: ignore[override]
        # Skip geometry persistence (we never showed a window) — but
        # keep the QDialog state machine consistent.
        from PySide6.QtWidgets import QDialog
        QDialog.done(self, result)

    def run(self, scope: set[str] | None = None) -> bool:
        """Fire the destructive pipeline with an optional path scope.

        Args:
            scope: Set of file_paths to limit the destructive run to.
                ``None`` (the default) means "every decided row in
                ``self._groups``" — the same default as the dialog
                flow's empty-selection branch.

        Returns:
            ``True`` if the destructive loop ran. ``False`` if the
            user cancelled at the lock-confirm or all-files-will-be-
            deleted prompt.
        """
        from PySide6.QtWidgets import QMessageBox

        self._execute_scope = scope

        def _in_scope(path: str) -> bool:
            return scope is None or path in scope

        # Pre-execute scan for locked rows with decision='delete' —
        # mirrors the dialog's _on_execute_requested but skips the
        # _selected_file_paths() call (no tree on this instance).
        total_delete_count = sum(
            1
            for group in self._groups
            for rec in getattr(group, "items", [])
            if getattr(rec, "user_decision", "") == "delete"
            and _in_scope(rec.file_path)
        )
        locked_delete_paths = [
            rec.file_path
            for group in self._groups
            for rec in getattr(group, "items", [])
            if getattr(rec, "user_decision", "") == "delete"
            and getattr(rec, "is_locked", False)
            and _in_scope(rec.file_path)
        ]
        if locked_delete_paths:
            verdict = self._ask_lock_confirm(
                paths=locked_delete_paths,
                decision_for_label="delete",
                affected_count=total_delete_count,
            )
            if verdict == _DIALOG_VERDICT_CANCEL:
                return False
            if verdict == _DIALOG_VERDICT_PROCEED:
                self._batch_set_lock(locked_delete_paths, locked=False)
            else:
                # Skip-Locked branch — clear the decision so _on_execute's
                # iteration skips locked rows but leaves lock state intact.
                self._clear_decision_on(locked_delete_paths)

        # Complete-group confirm — re-uses the in-scope variant so the
        # wording reflects what THIS click will empty on disk, not just
        # what was decided.
        complete = self._complete_delete_groups_in_scope(scope)
        if complete:
            group_list = ", ".join(str(g) for g in complete)
            reply = QMessageBox.question(
                self.parent(),
                t("execute_dialog.confirm_all_title"),
                t("execute_dialog.confirm_all_body", groups=group_list),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return False

        # Destructive loop — reads self._execute_scope back via
        # getattr(self, '_execute_scope', None), which we stashed above.
        self._on_execute()
        return True
