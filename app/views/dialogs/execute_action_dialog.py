"""ExecuteActionDialog — review and confirm planned file operations."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    COL_NAME,
    PATH_ROLE,
    REMOVE_FROM_LIST_DECISION,
    REMOVE_FROM_LIST_SENTINEL,
    settable_decisions,
)
from app.views.tree_model_builder import build_model
from infrastructure.i18n import t


class ExecuteActionDialog(QDialog):
    """Shows groups with decisions for final review; executes file decisions on confirm."""

    def __init__(self, groups: list, manifest_path: str | None, parent=None) -> None:
        super().__init__(parent)
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
            group_list = ", ".join(str(g) for g in complete)
            self._warning_label.setText(
                t("execute_dialog.warning_complete_groups", groups=group_list)
            )
            self._warning_banner.setVisible(True)
        else:
            self._warning_banner.setVisible(False)

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
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _set_decision(self, path: str, decision: str) -> None:
        if decision == REMOVE_FROM_LIST_SENTINEL:
            # Single-row right-click — always confirm before removing,
            # for symmetry with the regex flow. Set+execute is a bigger
            # commitment than delete/keep, even on one row.
            from PySide6.QtWidgets import QMessageBox
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
        for group in self._groups:
            for rec in getattr(group, "items", []):
                if rec.file_path == path:
                    rec.user_decision = decision
                    break
        self._refresh_ui_after_decision_change()

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

        # Internal English keys; ActionDialog displays localized labels but
        # emits the English name back via setActionRequested.
        fields = ["Action", "File Name", "Folder", "Size (Bytes)", "Creation Date", "Shot Date"]
        dlg = ActionDialog(fields=fields, parent=self)
        dlg.setActionRequested.connect(self._set_decision_by_regex)
        dlg.exec()

    def _set_decision_by_regex(self, field: str, pattern: str, new_decision: str) -> None:
        """Find all file rows where field matches pattern and route by action.

        ``new_decision == REMOVE_FROM_LIST_SENTINEL`` removes the
        matched rows from the review list (mirrors the main-window
        regex flow); otherwise sets ``user_decision``.
        """
        import re as _re
        from PySide6.QtWidgets import QMessageBox
        from app.views.handlers.file_operations import _get_record_field

        try:
            rx = _re.compile(pattern, _re.IGNORECASE)
        except _re.error as exc:
            QMessageBox.warning(self, t("execute_dialog.invalid_regex_title"), str(exc))
            return

        # Bulk regex remove now mirrors bulk regex delete/keep:
        # matched rows get user_decision = REMOVE_FROM_LIST_DECISION
        # and the user reviews + commits via Execute. Single-row
        # right-click (_set_decision) stays immediate.
        if new_decision == REMOVE_FROM_LIST_SENTINEL:
            new_decision = REMOVE_FROM_LIST_DECISION

        batch: dict[str, str] = {}
        for group in self._groups:          # search full list, not just displayed
            for rec in getattr(group, "items", []):
                value = _get_record_field(rec, field)
                if value is not None and rx.search(value):
                    rec.user_decision = new_decision
                    batch[rec.file_path] = new_decision

        if not batch:
            QMessageBox.information(
                self,
                t("execute_dialog.no_match_title"),
                t("execute_dialog.no_match_body"),
            )
            return

        if self._manifest_path:
            try:
                from infrastructure.manifest_repository import ManifestRepository
                ManifestRepository().batch_update_decisions(self._manifest_path, batch)
            except Exception as exc:
                logger.warning("Failed to persist batch decisions: {}", exc)

        self._refresh_ui_after_decision_change()

    # ------------------------------------------------------------------ execute

    def _on_execute_requested(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        complete = self._complete_delete_groups()
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

    def _on_execute(self) -> None:
        # Batch-persist all current decisions before executing
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
        for group in self._groups:
            for rec in getattr(group, "items", []):
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
