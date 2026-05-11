"""FileOperationsHandler: Handles file-related operations like manifest import/export and decisions."""

from __future__ import annotations

import re
from typing import Any, Callable, Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox
from loguru import logger

from app.views.components.status_messages import pluralize, report_count, t_pluralize
from app.views.constants import (
    LOCK_SENTINEL,
    REMOVE_FROM_LIST_DECISION,
    REMOVE_FROM_LIST_SENTINEL,
    UNLOCK_SENTINEL,
)
from infrastructure.i18n import t

# Single source of truth for the QFileDialog filter string used wherever
# the app opens or saves a manifest. Keeping this centralized avoids the
# scan-dialog vs. save-decisions mismatch that previously rejected .db
# in one place and accepted it in the other.
MANIFEST_FILE_FILTER = "SQLite Files (*.sqlite *.db);;All Files (*)"

# Maps SelectDialog field names → PhotoRecord attribute names.
_FIELD_TO_ATTR: dict[str, str] = {
    "File Name":     "file_path",      # basename extracted in _get_record_field
    "Folder":        "folder_path",
    "Action":        "user_decision",
    "Size (Bytes)":  "file_size_bytes",
    "Creation Date": "creation_date",
    "Shot Date":     "shot_date",
}


def _get_record_field(rec: Any, field: str) -> str | None:
    """Return the string value of a record's field, or None if unavailable."""
    from pathlib import Path

    attr = _FIELD_TO_ATTR.get(field)
    if attr is None:
        return None
    val = getattr(rec, attr, None)
    if val is None:
        return None
    if field == "File Name":
        return Path(str(val)).name
    return str(val)


def _decision_display_label(decision: str) -> str:
    """Return a human-friendly label for ``decision`` for confirm-dialog
    bodies. Mirrors the labels offered by :func:`settable_decisions` so
    the confirm body reads the same as the menu item that triggered it.
    """
    if decision == "delete":
        return t("decision.delete")
    if decision == "":
        return t("decision.keep")
    if decision == REMOVE_FROM_LIST_DECISION:
        return t("decision.remove_from_list")
    return decision


def build_match_fn(
    groups: list, sample_cap: int = 50
) -> Callable[[str, str], tuple[int, int, list[str]]]:
    """Return a closure that counts regex matches across the records.

    The closure returned by this function powers the ActionDialog's live
    preview pane. Calling it with a (field, pattern) pair returns a tuple
    (matched, total, sample_basenames) where:
      - matched: total number of records whose `field` value matches `pattern`
        (case-insensitive) under the same `_FIELD_TO_ATTR` map that
        `set_decision_by_regex` will use, so the preview is byte-for-byte
        consistent with what Apply will affect.
      - total: total number of records iterated. Records whose field is
        unavailable (no `_FIELD_TO_ATTR` entry, or the attr is None) count
        toward `total` but cannot match.
      - sample_basenames: at most `sample_cap` basenames of matching files,
        for display in the preview list. Iteration continues past the cap
        so the matched count is always accurate.

    On `re.error` returns (0, total, []) — the dialog handles invalid-regex
    feedback through its own validation row, so the closure stays silent.
    """

    def _match(field: str, pattern: str) -> tuple[int, int, list[str]]:
        from pathlib import Path

        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            total = sum(len(g.items) for g in groups)
            return (0, total, [])

        matched = 0
        total = 0
        samples: list[str] = []
        for grp in groups:
            for rec in grp.items:
                total += 1
                value = _get_record_field(rec, field)
                if value is None:
                    continue
                if rx.search(value):
                    matched += 1
                    if len(samples) < sample_cap:
                        path_val = getattr(rec, "file_path", None)
                        samples.append(
                            Path(str(path_val)).name if path_val else value
                        )
        return (matched, total, samples)

    return _match


class UIUpdateCallback(Protocol):
    """Protocol for UI update callbacks."""

    def refresh_tree(self, groups: list) -> None:
        """Refresh the tree view with new groups."""
        ...

    def show_group_counts(self, count: int) -> None:
        """Show group count (legacy compatibility)."""
        ...

    def show_groups_summary(self, groups: list) -> None:
        """Show groups summary (legacy compatibility)."""
        ...


class StatusReporter(Protocol):
    """Protocol for status reporting callback."""

    def show_status(self, message: str, timeout: int = 3000) -> None:
        """Show status message."""
        ...


class FileOperationsHandler:
    """Handles file-related operations including manifest import/export and user decisions."""

    def __init__(
        self,
        vm: Any,
        settings: Any,
        parent_widget: QObject,
        ui_updater: UIUpdateCallback,
        status_reporter: StatusReporter,
        checked_paths_provider: object | None = None,
        highlighted_items_provider: object | None = None,
    ) -> None:
        self.vm = vm
        self.settings = settings
        self.parent = parent_widget
        self.ui_updater = ui_updater
        self.status_reporter = status_reporter
        self.checked_paths_provider = checked_paths_provider
        self.highlighted_items_provider = highlighted_items_provider
        # Dirty since last load / save / execute. Decisions auto-persist
        # to SQLite, so leaving the app without an explicit Save isn't
        # a data-loss risk; the dirty flag is purely a UX cue for the
        # exit prompt. Cleared on import, save_silent, save, and
        # successful execute.
        self._is_dirty: bool = False

    def is_dirty(self) -> bool:
        """Return True if decisions have been set / changed since the
        last load / save / execute."""
        return self._is_dirty

    def _mark_dirty(self) -> None:
        self._is_dirty = True

    def _mark_clean(self) -> None:
        self._is_dirty = False

    def import_manifest(self) -> None:
        """Open a migration_manifest.sqlite in a background worker (non-blocking)."""
        path, _ = QFileDialog.getOpenFileName(
            self.parent, t("file_op.open_manifest_title"), "", MANIFEST_FILE_FILTER
        )
        if not path:
            return
        self._start_manifest_load(path)

    def _start_manifest_load(self, path: str) -> None:
        """Begin a background load for the manifest at *path*."""
        from app.views.workers.manifest_load_worker import ManifestLoadWorker

        self.status_reporter.show_status(t("file_op.opening_status"), 0)
        # Only disable manifest-gated actions if no prior manifest is loaded.
        # When manifest A is already in memory and the user is opening B, leave
        # A's actions enabled during the load — they're the user's safety net
        # if B fails (#108). _on_manifest_loaded re-asserts enabled on success;
        # _on_manifest_failed leaves them alone when a prior manifest exists.
        if not getattr(self, "_manifest_path", None):
            self._set_manifest_actions_enabled(False)

        default_sort = getattr(self.vm, "_default_sort", [])
        worker = ManifestLoadWorker(path, default_sort, parent=self.parent)
        worker.progress.connect(lambda msg: self.status_reporter.show_status(msg, 0))
        worker.finished.connect(lambda groups: self._on_manifest_loaded(groups, path))
        worker.failed.connect(self._on_manifest_failed)
        worker.start()
        # Keep reference so the worker is not garbage-collected
        self._load_worker = worker

    def _on_manifest_loaded(self, groups: list, path: str) -> None:
        self.vm.groups = groups
        self._manifest_path = path
        self.ui_updater.refresh_tree(groups)
        self.ui_updater.show_group_counts(self.vm.group_count)
        self.ui_updater.show_groups_summary(groups)
        self._set_manifest_actions_enabled(True)
        # Fresh load — no in-session edits yet.
        self._mark_clean()

        n_groups = self.vm.group_count
        n_items = sum(len(g.items) for g in groups)
        logger.info("Opened manifest: {} | groups={} items={}", path, n_groups, n_items)
        pairs = t_pluralize(n_groups, "status.noun_pair_singular", "status.noun_pair_plural")
        files = t_pluralize(n_items, "status.noun_file_singular", "status.noun_file_plural")
        self.status_reporter.show_status(
            t("status.manifest_loaded_pairs", pairs=pairs, files=files)
        )

    def _on_manifest_failed(self, error: str) -> None:
        logger.error("Open manifest failed: {}", error)
        QMessageBox.critical(self.parent, t("file_op.open_error_title"), error)
        self.status_reporter.show_status(t("file_op.open_failed_status"))
        # Only disable on failure if no prior manifest was loaded (#108). If a
        # valid manifest is still in memory (self._manifest_path is set), the
        # user is back to reviewing it after dismissing the error — leave its
        # actions enabled rather than stranding them disabled.
        if not getattr(self, "_manifest_path", None):
            self._set_manifest_actions_enabled(False)

    def _set_manifest_actions_enabled(self, enabled: bool) -> None:
        try:
            self.parent.menu_controller.set_manifest_actions(enabled)
        except AttributeError:
            pass

    def save_manifest_decisions_silent(self) -> bool:
        """Persist current decisions to the loaded manifest path with no
        file picker.

        Used by the exit-prompt's "Save & leave" branch — the user
        already chose to save, no need to show another modal asking
        where. Returns True on success, False if there's no manifest
        loaded or the save raised. Failure leaves dirty=True so the
        caller can decide whether to abort the close.
        """
        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            return False
        try:
            from infrastructure.manifest_repository import ManifestRepository
            ManifestRepository().save(manifest_path, self.vm.groups)
            self._mark_clean()
            return True
        except Exception as ex:
            logger.exception("Silent manifest save failed: {}", ex)
            return False

    def save_manifest_decisions(self) -> None:
        """Export current decisions to a (possibly new) manifest file."""
        import os
        import shutil
        import sqlite3

        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            QMessageBox.information(
                self.parent,
                t("file_op.save_no_manifest_title"),
                t("file_op.save_no_manifest_body"),
            )
            return

        save_path, _ = QFileDialog.getSaveFileName(
            self.parent,
            t("file_op.save_dialog_title"),
            manifest_path,
            MANIFEST_FILE_FILTER,
        )
        if not save_path:
            return

        try:
            if os.path.normcase(os.path.normpath(save_path)) != os.path.normcase(
                os.path.normpath(manifest_path)
            ):
                # #91: scanner writes the manifest in WAL mode and may still
                # hold an active connection with uncheckpointed writes in the
                # -wal sibling. shutil.copy2 only copies the main .sqlite, so
                # without a checkpoint the destination ends up with no schema.
                ckpt_conn = sqlite3.connect(manifest_path)
                try:
                    ckpt_conn.execute("PRAGMA wal_checkpoint(FULL)")
                finally:
                    ckpt_conn.close()
                shutil.copy2(manifest_path, save_path)

            from infrastructure.manifest_repository import ManifestRepository
            updated = ManifestRepository().save(save_path, self.vm.groups)
            self._manifest_path = save_path
            logger.info("Manifest decisions saved to {}: {} rows updated", save_path, updated)
            # Dropped the redundant QMessageBox here — the status-bar write below
            # already reports success and modal noise broke the "all completed
            # actions report via status bar only" convention.
            report_count(
                self.status_reporter,
                t("status.verb_saved"),
                updated,
                t("status.noun_decision_singular"),
                plural=t("status.noun_decision_plural"),
            )
            self._mark_clean()

        except Exception as ex:
            logger.exception("Save manifest failed: {}", ex)
            QMessageBox.critical(self.parent, t("file_op.save_error_title"), str(ex))
            self.status_reporter.show_status(t("file_op.save_failed_status"))

    def remove_from_list_toolbar(self, highlighted_items: list[dict]) -> None:
        """Remove highlighted items from the list via toolbar."""
        try:
            if highlighted_items:
                logger.info(
                    "Removing {} highlighted items from list via toolbar", len(highlighted_items)
                )
                file_items = [item for item in highlighted_items if item.get("type") == "file"]
                group_items = [item for item in highlighted_items if item.get("type") == "group"]

                paths_for_db: list[str] = [item["path"] for item in file_items]
                for item in group_items:
                    for g in self.vm.groups:
                        if g.group_number == item["group_number"]:
                            paths_for_db.extend(r.file_path for r in g.items)
                            break

                if file_items:
                    self.vm.remove_from_list([item["path"] for item in file_items])

                for item in group_items:
                    self.vm.remove_group_from_list(item["group_number"])

                self.ui_updater.refresh_tree(self.vm.groups)
                self._sync_removed_to_db(paths_for_db)
                self._mark_dirty()
                report_count(
                    self.status_reporter,
                    t("status.verb_removed"),
                    len(highlighted_items),
                    t("status.noun_item_from_list_singular"),
                    plural=t("status.noun_item_from_list_plural"),
                )
                return

            QMessageBox.information(
                self.parent,
                t("file_op.remove_title"),
                t("file_op.remove_no_selection"),
            )

        except Exception as e:
            logger.error("Remove from list via toolbar failed: {}", e)
            QMessageBox.critical(
                self.parent,
                t("file_op.remove_error_title"),
                t("file_op.remove_failed_body", error=str(e)),
            )

    def remove_items_from_list(self, items: list[dict]) -> None:
        """Remove multiple items (files and/or groups) from the list."""
        try:
            file_paths: list[str] = []
            group_numbers: list[int] = []

            for item in items:
                if item["type"] == "file":
                    file_paths.append(item["path"])
                elif item["type"] == "group":
                    group_numbers.append(item["group_number"])

            paths_for_db: list[str] = list(file_paths)
            for gn in group_numbers:
                for g in self.vm.groups:
                    if g.group_number == gn:
                        paths_for_db.extend(r.file_path for r in g.items)
                        break

            if file_paths:
                logger.info("Removing {} files from list", len(file_paths))
                self.vm.remove_from_list(file_paths)

            if group_numbers:
                logger.info("Removing {} groups from list", len(group_numbers))
                for group_num in group_numbers:
                    self.vm.remove_group_from_list(group_num)

            self.ui_updater.refresh_tree(self.vm.groups)
            self._sync_removed_to_db(paths_for_db)
            self._mark_dirty()

            total_removed = len(file_paths) + len(group_numbers)
            report_count(
                self.status_reporter,
                t("status.verb_removed"),
                total_removed,
                t("status.noun_item_from_list_singular"),
                plural=t("status.noun_item_from_list_plural"),
            )

        except Exception as e:
            logger.error("Remove items from list failed: {}", e)
            QMessageBox.critical(
                self.parent,
                t("file_op.remove_error_title"),
                t("file_op.remove_failed_body", error=str(e)),
            )

    def _sync_removed_to_db(self, file_paths: list[str]) -> None:
        """Mark file_paths as removed in the manifest DB (manifest workflow only)."""
        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path or not file_paths:
            return
        try:
            from infrastructure.manifest_repository import ManifestRepository
            ManifestRepository().remove_from_review(manifest_path, file_paths)
        except Exception as exc:
            logger.warning("Failed to sync removed paths to manifest: {}", exc)

    def set_decision(self, items: list[dict], new_decision: str) -> None:
        """Set user_decision for the given file items in memory and in SQLite.

        Note: this is the SHARED dispatcher. Single-row right-click calls
        this directly and intentionally bypasses lock-protection — the
        skip-locked pre-filter lives in the bulk paths
        (``set_decision_by_regex`` / ``set_decision_to_highlighted``)
        that call into here. See photo-manager#164.
        """
        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            return
        batch: dict[str, str] = {}
        for item in items:
            if item.get("type") != "file":
                continue
            file_path = item["path"]
            for group in self.vm.groups:
                for rec in group.items:
                    if rec.file_path == file_path:
                        rec.user_decision = new_decision
                        break
            batch[file_path] = new_decision
        if batch:
            from infrastructure.manifest_repository import ManifestRepository
            ManifestRepository().batch_update_decisions(manifest_path, batch)
            self._mark_dirty()
        self.ui_updater.refresh_tree(self.vm.groups)
        self.status_reporter.show_status(
            t("file_op.decision_set_status", decision=new_decision)
        )

    def set_locked_state(self, items: list[dict], locked: bool) -> None:
        """Flip ``is_locked`` for the given file items, in memory and SQLite.

        Lock state is orthogonal to ``user_decision`` and lives on its own
        column. This is the dispatcher for both single-row right-click
        Lock/Unlock and bulk regex/multi-select lock/unlock — see
        photo-manager#164.
        """
        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            return
        batch: dict[str, bool] = {}
        for item in items:
            if item.get("type") != "file":
                continue
            file_path = item["path"]
            for group in self.vm.groups:
                for rec in group.items:
                    if rec.file_path == file_path:
                        rec.is_locked = locked
                        break
            batch[file_path] = locked
        if batch:
            from infrastructure.manifest_repository import ManifestRepository
            ManifestRepository().batch_update_lock_state(manifest_path, batch)
            self._mark_dirty()
        self.ui_updater.refresh_tree(self.vm.groups)
        report_count(
            self.status_reporter,
            t("file_op.locked_verb") if locked else t("file_op.unlocked_verb"),
            len(batch),
            t("file_op.noun_row_singular"),
            t("file_op.noun_row_plural"),
        )

    def set_decision_to_highlighted(self, new_decision: str) -> None:
        """Set user_decision for tree-highlighted (activated) file rows.

        Routes through :meth:`set_decision_with_lock_check` so locked
        rows in the selection surface the unified confirm dialog
        (#182). Lock/Unlock sentinels remain free (idempotent
        application to all selected rows).
        """
        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            QMessageBox.information(
                self.parent,
                t("file_op.set_action_no_manifest_title"),
                t("file_op.set_action_no_manifest_body"),
            )
            return
        items: list[dict] = []
        provider = self.highlighted_items_provider
        if provider is not None:
            if callable(provider):
                items = provider()
            elif hasattr(provider, "get_selected_items"):
                items = provider.get_selected_items()
        file_items = [it for it in items if it.get("type") == "file"]
        if not file_items:
            QMessageBox.information(
                self.parent,
                t("file_op.set_action_no_selection_title"),
                t("file_op.set_action_no_selection_body"),
            )
            return
        self.set_decision_with_lock_check(file_items, new_decision)

    def _locked_paths_in(self, file_items: list[dict]) -> list[str]:
        """Return the paths in ``file_items`` whose record is locked.

        Order preserved relative to ``file_items`` so the dialog's
        truncated basename list ("first 5 …and N more") reads as the
        same order the user is looking at in the tree.
        """
        item_paths_in_order = [
            it["path"] for it in file_items if it.get("type") == "file"
        ]
        locked_paths: set[str] = set()
        for group in self.vm.groups:
            for rec in group.items:
                if rec.is_locked:
                    locked_paths.add(rec.file_path)
        return [p for p in item_paths_in_order if p in locked_paths]

    def set_decision_with_lock_check(
        self, items: list[dict], new_decision: str
    ) -> None:
        """Apply ``new_decision`` to ``items``, surfacing the unified
        :class:`LockedRowsConfirmDialog` when any item is locked.

        Single entry point for every path that would change a
        user_decision under #182's new semantic (single-row
        right-click, bulk multi-select, bulk regex). Lock / unlock
        sentinels short-circuit the dialog — locking IS the explicit
        freeze, unlocking IS the explicit escape, neither needs an
        extra confirm. See photo-manager#175 for the prior hybrid
        behavior and #182 for the redesign rationale.
        """
        file_items = [it for it in items if it.get("type") == "file"]
        if not file_items:
            return

        # Lock / unlock — idempotent, applied to all file_items.
        if new_decision == LOCK_SENTINEL:
            self.set_locked_state(file_items, locked=True)
            return
        if new_decision == UNLOCK_SENTINEL:
            self.set_locked_state(file_items, locked=False)
            return

        locked_paths = self._locked_paths_in(file_items)
        # REMOVE_FROM_LIST_SENTINEL is translated to its deferred
        # decision value before applying; do it once here so both the
        # dialog body (action label) and the eventual set_decision()
        # call see a consistent string.
        resolved_decision = (
            REMOVE_FROM_LIST_DECISION
            if new_decision == REMOVE_FROM_LIST_SENTINEL
            else new_decision
        )

        if not locked_paths:
            # Fast path — no locked rows touched, no dialog needed.
            self.set_decision(file_items, resolved_decision)
            return

        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )

        verdict = LockedRowsConfirmDialog.ask(
            self.parent,
            action_label=_decision_display_label(resolved_decision),
            affected_count=len(file_items),
            locked_paths=locked_paths,
        )

        if verdict == LockedRowsConfirmDialog.CANCEL:
            return

        if verdict == LockedRowsConfirmDialog.APPLY_ALL_UNLOCKED:
            locked_set = set(locked_paths)
            locked_items = [it for it in file_items if it["path"] in locked_set]
            # Unlock first so the subsequent set_decision sees fresh
            # state (and any UI refresh inside set_locked_state shows
            # the unlocked rows before the decision toast fires).
            self.set_locked_state(locked_items, locked=False)
            self.set_decision(file_items, resolved_decision)
            return

        # APPLY_UNLOCKED_ONLY — skip the locked subset, apply to the rest.
        locked_set = set(locked_paths)
        unlocked_items = [it for it in file_items if it["path"] not in locked_set]
        if unlocked_items:
            self.set_decision(unlocked_items, resolved_decision)
        if locked_set:
            self.status_reporter.show_status(
                t(
                    "file_op.decision_set_with_skipped_status",
                    decision=resolved_decision,
                    set_count=len(unlocked_items),
                    skipped=len(locked_set),
                )
            )

    def set_decision_by_regex(self, field: str, pattern: str, new_decision: str) -> None:
        """Find all file rows where field matches regex and route by action.

        Args:
            field: Field name (e.g. "File Name", "Folder", "Action").
            pattern: Regex pattern (case-insensitive).
            new_decision: ``"delete"`` / ``""`` set the corresponding
                user_decision; :data:`REMOVE_FROM_LIST_SENTINEL`
                attaches the deferred remove decision; the
                :data:`LOCK_SENTINEL` / :data:`UNLOCK_SENTINEL`
                sentinels flip ``is_locked`` for matched rows
                (idempotent — applied to all matched, no confirm
                dialog). Destructive decisions route through
                :meth:`set_decision_with_lock_check` so any locked
                rows in the matched set surface the unified
                :class:`LockedRowsConfirmDialog` (#182).
        """
        import re as _re

        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            QMessageBox.information(
                self.parent,
                t("file_op.set_action_no_manifest_title"),
                t("file_op.set_action_no_manifest_body"),
            )
            return

        try:
            rx = _re.compile(pattern, _re.IGNORECASE)
        except _re.error as exc:
            QMessageBox.warning(self.parent, t("file_op.invalid_regex_title"), str(exc))
            return

        matching: list[dict] = []
        for group in self.vm.groups:
            for rec in group.items:
                value = _get_record_field(rec, field)
                if value is not None and rx.search(value):
                    matching.append({"type": "file", "path": rec.file_path})

        if not matching:
            QMessageBox.information(
                self.parent,
                t("file_op.set_action_no_match_title"),
                t("file_op.set_action_no_match_body"),
            )
            return

        # All destructive + lock/unlock routing now goes through the
        # shared entry point so the dialog flow is identical to
        # single-row right-click and bulk multi-select.
        self.set_decision_with_lock_check(matching, new_decision)

    def execute_action(self) -> None:
        """Open the Execute Action review dialog and run planned operations."""
        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            QMessageBox.information(
                self.parent,
                t("file_op.execute_no_manifest_title"),
                t("file_op.execute_no_manifest_body"),
            )
            return
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        dlg = ExecuteActionDialog(
            self.vm.groups, manifest_path, self.parent,
            settings=self.settings,
        )
        accepted = dlg.exec() == QDialog.Accepted
        # When the user removed rows via the immediate single-row
        # right-click (which mutates self._groups in place — an alias
        # of vm.groups), the main tree must re-render even if the
        # dialog was rejected. The deferred-remove path is committed
        # only via Execute (accepted=True branch below), so it doesn't
        # affect this path.
        if dlg.removed_from_list_paths and not accepted:
            self.ui_updater.refresh_tree(self.vm.groups)
        if accepted:
            if dlg.deleted_paths:
                self.vm.remove_deleted_and_prune(dlg.deleted_paths, prune_singles=False)
            if dlg.removed_from_list_paths:
                # Deferred-remove paths are still in vm.groups (we set
                # user_decision but didn't drop them in-place). Drop
                # them now so they vanish from the main tree.
                # Immediate-path entries are already gone — vm.remove_from_list
                # filters by path, so duplicates are harmless.
                self.vm.remove_from_list(dlg.removed_from_list_paths)
            self.ui_updater.refresh_tree(self.vm.groups)
            total = len(dlg.deleted_paths) + len(dlg.executed_paths)
            report_count(
                self.status_reporter,
                t("status.verb_executed"),
                total,
                t("status.noun_action_singular"),
                plural=t("status.noun_action_plural"),
            )
            # Execute is the canonical "commit" — decisions have been
            # applied to disk (or to the review list); no need to nag
            # the user about saving on the way out.
            self._mark_clean()
