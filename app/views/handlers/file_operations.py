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
from app.views.window_state import (
    QSETTINGS_KEY_SAVE_MANIFEST_DIALOG_GEOM,
    restore_widget_geometry,
    save_widget_geometry,
)
from infrastructure.i18n import t

# Single source of truth for the QFileDialog filter string used wherever
# the app opens or saves a manifest. Keeping this centralized avoids the
# scan-dialog vs. save-decisions mismatch that previously rejected .db
# in one place and accepted it in the other.
MANIFEST_FILE_FILTER = "SQLite Files (*.sqlite *.db);;All Files (*)"

# Maps SelectDialog field names → PhotoRecord attribute names.
# Resolution is a composite of pixel_width × pixel_height — the attr
# mapping below points at pixel_width as a placeholder so the dict
# lookup succeeds; the actual rendering is handled inline in
# _get_record_field. Score's numeric ranking goes through the
# numeric-condition panel (`_numeric_value_for`); the mapping below
# is only consulted on the regex fallback path. #238.
_FIELD_TO_ATTR: dict[str, str] = {
    "File Name":     "file_path",      # basename extracted in _get_record_field
    "Folder":        "folder_path",
    "Action":        "user_decision",
    "Lock":          "is_locked",      # bool → "Locked"/"" in _get_record_field (#182)
    "Size (Bytes)":  "file_size_bytes",
    "Creation Date": "creation_date",
    "Shot Date":     "shot_date",
    "Score":         "score",          # float ∈ [0, 1]; None for passenger MOVs (#238)
    "Resolution":    "pixel_width",    # placeholder; rendered as "WxH" in _get_record_field (#238)
}


def _get_record_field(rec: Any, field: str) -> str | None:
    """Return the string value of a record's field, or None if unavailable.

    The ``Lock`` field maps a boolean ``is_locked`` to the string
    ``"Locked"`` (truthy) or ``""`` (falsy) so users can regex-match
    locked rows with ``^Locked$`` and unlocked rows with ``^$``. The
    rendered string matches what the COL_LOCK column shows in the tree
    (🔒 glyph for locked, empty for unlocked) — same conceptual values,
    different presentation.

    The ``Resolution`` field formats ``pixel_width × pixel_height``
    using the same ``×`` (U+00D7) glyph the tree's COL_RESOLUTION cell
    uses (see ``tree_model_builder.build_model``). Returns None when
    either dimension is missing — matches the tree's empty-cell
    rendering for that case. Users regex-match ``^1920×1080$`` style
    (#238).
    """
    from pathlib import Path

    attr = _FIELD_TO_ATTR.get(field)
    if attr is None:
        return None
    if field == "Resolution":
        px_w = getattr(rec, "pixel_width", None)
        px_h = getattr(rec, "pixel_height", None)
        if not px_w or not px_h:
            return None
        return f"{px_w}×{px_h}"
    val = getattr(rec, attr, None)
    if field == "Lock":
        # bool conversion explicitly — getattr can return False which
        # is not None and shouldn't short-circuit to "None" via str().
        return "Locked" if bool(val) else ""
    if val is None:
        return None
    if field == "File Name":
        return Path(str(val)).name
    return str(val)


def _decision_display_label(decision: str) -> str:
    """Return a human-friendly label for ``decision`` for confirm-dialog
    bodies AND status-bar messages. Mirrors the labels offered by
    :func:`settable_decisions` so the confirm body reads the same as
    the menu item that triggered it.

    Both ``""`` (canonical keep) and ``"keep"`` (legacy literal from
    pre-#425 auto-select writes) map to ``t("decision.keep")`` so the
    label is consistent regardless of which value got persisted.
    """
    if decision == "delete":
        return t("decision.delete")
    if decision == "" or decision == "keep":
        # "" is the canonical keep state; "keep" is the legacy literal
        # back-compat path (#425 — older manifests may carry it).
        return t("decision.keep")
    if decision == REMOVE_FROM_LIST_DECISION:
        return t("decision.remove_from_list")
    return decision


def build_match_fn(
    groups: list, sample_cap: int = 50
) -> Callable[[str, str], tuple[int, int, list[tuple[str, str]]]]:
    """Return a closure that counts regex matches across the records.

    The closure returned by this function powers the ActionDialog's live
    preview pane. Calling it with a (field, pattern) pair returns a tuple
    (matched, total, samples) where:
      - matched: total number of records whose `field` value matches `pattern`
        (case-insensitive) under the same `_FIELD_TO_ATTR` map that
        `set_decision_by_regex` will use, so the preview is byte-for-byte
        consistent with what Apply will affect.
      - total: total number of records iterated. Records whose field is
        unavailable (no `_FIELD_TO_ATTR` entry, or the attr is None) count
        toward `total` but cannot match.
      - samples: at most ``sample_cap`` ``(basename, matched_field_str)``
        tuples for matching files. The dialog displays ``matched_field_str``
        in the preview list so the user can see *why* a non-File-Name
        regex matched (A2 from #347: pre-Wave-4 the preview showed
        basenames for Folder / Size / Score / Date / Lock / Action /
        Resolution regexes too, leaving the match-span highlighter
        silently no-op because ``rx.search(basename)`` returned None).
        For the File Name field the two strings are equal and the
        previous one-string sample shape is preserved at render time.
        Iteration continues past the cap so the matched count is always
        accurate.

    On `re.error` returns (0, total, []) — the dialog handles invalid-regex
    feedback through its own validation row, so the closure stays silent.
    """

    def _match(field: str, pattern: str) -> tuple[int, int, list[tuple[str, str]]]:
        from pathlib import Path

        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            total = sum(len(g.items) for g in groups)
            return (0, total, [])

        matched = 0
        total = 0
        samples: list[tuple[str, str]] = []
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
                        basename = (
                            Path(str(path_val)).name if path_val else value
                        )
                        samples.append((basename, value))
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

    def clear_preview(self) -> None:
        """Drop any preview-pane content (#431).

        Called from ``_on_manifest_loaded`` so a fresh manifest doesn't
        leave the previous manifest's last-selected file rendered in
        the preview pane. The dialog-scope ``ExecuteActionDialog``
        already does this on close — this is the matching cleanup for
        the main-window-scope path.
        """
        ...


class StatusReporter(Protocol):
    """Protocol for status reporting callback."""

    def show_status(self, message: str, timeout: int = 3000) -> None:
        """Show transient status message (auto-clears after timeout)."""
        ...

    def set_baseline(self, message: str) -> None:
        """Update the persistent baseline shown between transient messages."""
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
        task_runner: object | None = None,
    ) -> None:
        self.vm = vm
        self.settings = settings
        self.parent = parent_widget
        self.ui_updater = ui_updater
        self.status_reporter = status_reporter
        self.checked_paths_provider = checked_paths_provider
        self.highlighted_items_provider = highlighted_items_provider
        # #165 — forwarded into ExecuteActionDialog so its embedded
        # PreviewPane can request thumbnails via the same runner the
        # main window uses. Optional for handler-level unit tests that
        # never reach the dialog.
        self.task_runner = task_runner
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
        self.status_reporter.set_baseline(
            t("status.manifest_loaded_pairs", pairs=pairs, files=files)
        )
        # #431: drop the previous manifest's preview content. Runs
        # AFTER set_baseline so any cost (Qt widget cleanup) can't
        # delay the status update that callers / qa scenarios poll
        # for. Side effect is visual-only — the stale image lingers
        # for the time clear takes to run, which is still much faster
        # than refresh_tree.
        self.ui_updater.clear_preview()

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
            # #410: execute_action_selected_only carries an additional
            # selection-gate beyond MANIFEST_ACTIONS. Refresh after the
            # bulk toggle so the entry reflects (manifest_loaded AND has
            # selection), not just the manifest state.
            refresh = getattr(
                self.parent, "_refresh_execute_selected_only_enabled", None
            )
            if callable(refresh):
                refresh()
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

        # #230 — Use a non-native QFileDialog instance with an explicit
        # minimum size. The native Windows IFileSaveDialog opened with
        # the folder picker / breadcrumb clipped above the screen top,
        # and native dialogs ignore Qt-side setMinimumSize. Process-wide
        # opt-out lives at main.py:99 for CI; this is the production fix.
        dlg = QFileDialog(self.parent, t("file_op.save_dialog_title"))
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        dlg.setFileMode(QFileDialog.AnyFile)
        dlg.setNameFilter(MANIFEST_FILE_FILTER)
        dlg.setOption(QFileDialog.DontUseNativeDialog, True)
        dlg.setMinimumSize(800, 500)
        dlg.setDirectory(os.path.dirname(manifest_path))
        dlg.selectFile(os.path.basename(manifest_path))

        restore_widget_geometry(dlg, QSETTINGS_KEY_SAVE_MANIFEST_DIALOG_GEOM)
        try:
            accepted = dlg.exec() == QFileDialog.Accepted
        finally:
            save_widget_geometry(dlg, QSETTINGS_KEY_SAVE_MANIFEST_DIALOG_GEOM)

        if not accepted:
            return
        save_path = dlg.selectedFiles()[0]

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

                # Lock guard (#208): surface LockedRowsConfirmDialog if any
                # item being removed is locked.
                all_paths, locked_paths = self._collect_locked_paths_for_removal(
                    highlighted_items
                )
                if locked_paths:
                    from app.views.dialogs.locked_rows_confirm_dialog import (
                        LockedRowsConfirmDialog,
                    )
                    verdict = LockedRowsConfirmDialog.ask(
                        self.parent,
                        action_label=_decision_display_label(REMOVE_FROM_LIST_DECISION),
                        affected_count=len(all_paths),
                        locked_paths=locked_paths,
                    )
                    if verdict == LockedRowsConfirmDialog.CANCEL:
                        return
                    if verdict == LockedRowsConfirmDialog.APPLY_UNLOCKED_ONLY:
                        locked_set = set(locked_paths)
                        unlocked = [p for p in all_paths if p not in locked_set]
                        if unlocked:
                            self.vm.remove_from_list(unlocked)
                            self._sync_removed_to_db(unlocked)
                            self._mark_dirty()
                            self.ui_updater.refresh_tree(self.vm.groups)
                            report_count(
                                self.status_reporter,
                                t("status.verb_removed"),
                                len(unlocked),
                                t("status.noun_item_from_list_singular"),
                                plural=t("status.noun_item_from_list_plural"),
                            )
                        return
                    # APPLY_ALL_UNLOCKED: unlock the locked subset in memory
                    # and in SQLite, then fall through to remove everything.
                    manifest_path = getattr(self, "_manifest_path", None)
                    locked_set = set(locked_paths)
                    for group in self.vm.groups:
                        for rec in group.items:
                            if rec.file_path in locked_set:
                                rec.is_locked = False
                    if manifest_path:
                        from infrastructure.manifest_repository import ManifestRepository
                        ManifestRepository().batch_update_lock_state(
                            manifest_path, {p: False for p in locked_paths}
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
                # #426: offer to prune any groups that collapsed to a
                # single item after this bulk remove.
                self._maybe_offer_singleton_prune()
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
            # Lock guard (#208): surface LockedRowsConfirmDialog if any
            # item being removed is locked.
            all_paths, locked_paths = self._collect_locked_paths_for_removal(items)
            if locked_paths:
                from app.views.dialogs.locked_rows_confirm_dialog import (
                    LockedRowsConfirmDialog,
                )
                verdict = LockedRowsConfirmDialog.ask(
                    self.parent,
                    action_label=_decision_display_label(REMOVE_FROM_LIST_DECISION),
                    affected_count=len(all_paths),
                    locked_paths=locked_paths,
                )
                if verdict == LockedRowsConfirmDialog.CANCEL:
                    return
                if verdict == LockedRowsConfirmDialog.APPLY_UNLOCKED_ONLY:
                    locked_set = set(locked_paths)
                    unlocked = [p for p in all_paths if p not in locked_set]
                    if unlocked:
                        self.vm.remove_from_list(unlocked)
                        self._sync_removed_to_db(unlocked)
                        self._mark_dirty()
                        self.ui_updater.refresh_tree(self.vm.groups)
                        report_count(
                            self.status_reporter,
                            t("status.verb_removed"),
                            len(unlocked),
                            t("status.noun_item_from_list_singular"),
                            plural=t("status.noun_item_from_list_plural"),
                        )
                        # #426: offer to prune singletons created by
                        # this partial remove too.
                        self._maybe_offer_singleton_prune()
                    return
                # APPLY_ALL_UNLOCKED: unlock the locked subset in memory
                # and in SQLite, then fall through to remove everything.
                manifest_path = getattr(self, "_manifest_path", None)
                locked_set = set(locked_paths)
                for group in self.vm.groups:
                    for rec in group.items:
                        if rec.file_path in locked_set:
                            rec.is_locked = False
                if manifest_path:
                    from infrastructure.manifest_repository import ManifestRepository
                    ManifestRepository().batch_update_lock_state(
                        manifest_path, {p: False for p in locked_paths}
                    )

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
            # #426: offer to prune any groups that collapsed to a
            # single item after this remove. Covers context-menu single
            # + bulk + regex-driven flows that all funnel through here.
            self._maybe_offer_singleton_prune()

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

    def _maybe_offer_singleton_prune(self) -> None:
        """#426 — after a destructive op, if any group is now down to a
        single item, offer to remove those singletons in one batch.

        Honors ``settings.get("ui.prune_singletons", "ask")``:
          * ``"ask"`` (default)  — fire the confirm dialog.
          * ``"always"`` — silently prune; never ask again.
          * ``"never"``  — silently keep; never ask again.

        Batched: one dialog per destructive op covering ALL singletons it
        produced, ONE ``vm.remove_from_list`` call + ONE
        ``_sync_removed_to_db`` call (perf-aware per the issue's
        ≤5000-singletons acceptance criterion). On a clean state with
        no singletons present, this is a fast O(N) no-op.
        """
        # Collect singletons from the current vm state.
        singleton_paths: list[str] = []
        for g in self.vm.groups:
            items = getattr(g, "items", [])
            if len(items) == 1:
                fp = getattr(items[0], "file_path", None)
                if fp:
                    singleton_paths.append(fp)
        if not singleton_paths:
            return

        pref = "ask"
        try:
            pref = self.settings.get("ui.prune_singletons", "ask") or "ask"
        except Exception:
            pref = "ask"
        if pref == "never":
            return
        if pref == "always":
            self._apply_singleton_prune(singleton_paths)
            return

        # pref == "ask" — show the dialog.
        from app.views.dialogs.singleton_prune_confirm_dialog import (
            SingletonPruneConfirmDialog,
        )
        verdict, remember = SingletonPruneConfirmDialog.ask(
            self.parent, count=len(singleton_paths)
        )
        if remember:
            new_pref = "always" if verdict == SingletonPruneConfirmDialog.REMOVE else "never"
            try:
                self.settings.set("ui.prune_singletons", new_pref)
                if hasattr(self.settings, "save"):
                    self.settings.save()
            except Exception as exc:
                logger.warning("Failed to persist ui.prune_singletons: {}", exc)
        if verdict == SingletonPruneConfirmDialog.REMOVE:
            self._apply_singleton_prune(singleton_paths)

    def _apply_singleton_prune(self, paths: list[str]) -> None:
        """Run the batched prune — one vm call, one DB sync, one refresh."""
        if not paths:
            return
        logger.info("Pruning {} singleton groups (#426)", len(paths))
        self.vm.remove_from_list(paths)
        self._sync_removed_to_db(paths)
        self._mark_dirty()
        self.ui_updater.refresh_tree(self.vm.groups)

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
        # #425 — pass the localised label, not the raw internal value:
        # the {decision} placeholder was previously interpolated with
        # "delete" / "" / "keep" verbatim, so zh_TW status reads showed
        # English "delete" inside an otherwise Mandarin sentence.
        self.status_reporter.show_status(
            t("file_op.decision_set_status", decision=_decision_display_label(new_decision))
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

    def _collect_locked_paths_for_removal(
        self, items: list[dict]
    ) -> tuple[list[str], list[str]]:
        """Expand items (files + groups) to file paths and return (all, locked).

        Used by the remove-from-list lock guard to find locked paths across
        both individual file items and group items (which expand to all their
        constituent files).
        """
        all_paths: list[str] = []
        for item in items:
            if item.get("type") == "file":
                all_paths.append(item["path"])
            elif item.get("type") == "group":
                gn = item.get("group_number")
                for g in self.vm.groups:
                    if g.group_number == gn:
                        all_paths.extend(r.file_path for r in g.items)
                        break
        locked_set: set[str] = {
            rec.file_path
            for group in self.vm.groups
            for rec in group.items
            if rec.is_locked
        }
        locked = [p for p in all_paths if p in locked_set]
        return all_paths, locked

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
                    # #425 — pass localised label, not raw internal value.
                    decision=_decision_display_label(resolved_decision),
                    set_count=len(unlocked_items),
                    skipped=len(locked_set),
                )
            )

    def set_decision_by_regex(self, field: str, pattern: str, new_decision: str) -> None:
        """Find all file rows where field matches pattern and route by action.

        Args:
            field: Field name (e.g. "File Name", "Folder", "Score").
            pattern: Regex pattern (case-insensitive) OR a numeric
                pseudo-pattern emitted by the Set Action dialog's
                numeric panel — ``__cmp__:OP:VALUE`` (#209 threshold
                comparison) or ``__top_n__:N:asc|desc`` (#209 top/bottom
                N per group). Pseudo-patterns are dispatched to
                :func:`select_paths_by_threshold` /
                :func:`select_paths_top_n` so the numeric Apply path
                works for every field the dialog dropdown exposes
                (Score / Group Count / Similarity / Size / Creation
                Date / Shot Date). Before #392 only the text regex
                branch existed here, so numeric Apply via the
                main-window route silently no-op'd.
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

        # #397 empty-pattern receiver guard. Dropped the ActionDialog's
        # Apply-button gate to let users see at-click failure modes —
        # but ``re.search("", anything)`` is truthy and would route a
        # destructive decision to EVERY row. Cheaper to early-reject
        # here than to surface a downstream regret. Numeric pseudo-
        # patterns (``__cmp__:...`` / ``__top_n__:...``) never satisfy
        # ``not pattern`` because they always carry the prefix, so this
        # guard catches only the actual empty-text case.
        if not pattern:
            QMessageBox.information(
                self.parent,
                t("file_op.set_action_no_match_title"),
                t("file_op.set_action_no_match_body"),
            )
            return

        try:
            matched_paths = self._matched_paths_for_pattern(field, pattern)
        except _re.error as exc:
            QMessageBox.warning(self.parent, t("file_op.invalid_regex_title"), str(exc))
            return
        except ValueError:
            # Malformed numeric pseudo-pattern — surface as "no match"
            # rather than a hard error. Dialog validation prevents most
            # invalid input; a stray malformed pattern shouldn't crash
            # the apply flow. Mirrors execute_action_dialog's UX.
            QMessageBox.information(
                self.parent,
                t("file_op.set_action_no_match_title"),
                t("file_op.set_action_no_match_body"),
            )
            return

        matching: list[dict] = [
            {"type": "file", "path": p} for p in matched_paths
        ]

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

    def _matched_paths_for_pattern(
        self, field: str, pattern: str
    ) -> list[str]:
        """Resolve ``pattern`` against ``self.vm.groups`` and return
        matched file_paths, preserving tree order (group-then-record).

        Handles three pattern shapes (mirrors
        :meth:`ExecuteActionDialog._matched_paths_for_pattern` so both
        ActionDialog open-routes — main-window and Execute — share
        identical match semantics):

          * ``__cmp__:OP:VALUE`` — threshold comparison (#209)
          * ``__top_n__:N:asc|desc`` — top/bottom N within group (#209)
          * anything else — case-insensitive regex against the field
            value from :func:`_get_record_field`.

        Raises :class:`re.error` on an invalid regex; raises
        :class:`ValueError` on a malformed numeric pattern. Caller
        catches and surfaces a localized message.
        """
        import re as _re
        # Lazy imports: select_dialog is a view module; importing it
        # at module top would pull Qt widgets into the handler import
        # graph. Same pattern as ExecuteActionDialog uses.
        from app.views.dialogs.select_dialog import (
            PATTERN_CMP_PREFIX,
            PATTERN_TOP_N_PREFIX,
            decode_cmp_pattern,
            decode_top_n_pattern,
            select_paths_by_threshold,
            select_paths_top_n,
        )

        if pattern.startswith(PATTERN_CMP_PREFIX):
            decoded = decode_cmp_pattern(pattern)
            if decoded is None:
                raise ValueError(pattern)
            op, value_text = decoded
            return select_paths_by_threshold(
                self.vm.groups, field, op, value_text
            )
        if pattern.startswith(PATTERN_TOP_N_PREFIX):
            decoded = decode_top_n_pattern(pattern)
            if decoded is None:
                raise ValueError(pattern)
            n, order = decoded
            return select_paths_top_n(self.vm.groups, field, n, order)
        rx = _re.compile(pattern, _re.IGNORECASE)
        out: list[str] = []
        for group in self.vm.groups:
            for rec in group.items:
                value = _get_record_field(rec, field)
                if value is not None and rx.search(value):
                    out.append(rec.file_path)
        return out

    def execute_action(self, selected_only: bool = False) -> None:
        """Open the Execute Action review dialog and run planned operations.

        #430: ``selected_only=True`` pre-filters the dialog's groups
        by **group membership**: selecting any row inside group G
        pulls ALL of G's items into the dialog so the user keeps the
        ref-row, near-dup tags, and score comparisons visible while
        triaging. Selecting a group header counts as selecting the
        whole group. Supersedes the earlier per-row filter (#410)
        which stripped peer context. Scope is a kwarg, NOT global
        state on the handler; the dialog itself is unaware of the
        filter (groups arrive already reduced).
        """
        manifest_path = getattr(self, "_manifest_path", None)
        if not manifest_path:
            QMessageBox.information(
                self.parent,
                t("file_op.execute_no_manifest_title"),
                t("file_op.execute_no_manifest_body"),
            )
            return
        groups = self.vm.groups
        if selected_only:
            tree_controller = getattr(self.parent, "tree_controller", None)
            selected_group_numbers: set[int] = set()
            if tree_controller is not None:
                # Build a path → group_number index once, so a multi-
                # row selection doesn't re-scan the full group list per
                # item (O(N) instead of O(N·M)).
                path_to_group: dict[str, int] = {}
                for g in groups:
                    gn = getattr(g, "group_number", 0)
                    for r in getattr(g, "items", []):
                        fp = getattr(r, "file_path", None)
                        if fp:
                            path_to_group[fp] = gn
                for item in tree_controller.get_selected_items():
                    if item.get("type") == "file":
                        path = item.get("path")
                        if path is None:
                            continue
                        gn = path_to_group.get(path)
                        if gn is not None:
                            selected_group_numbers.add(gn)
                    elif item.get("type") == "group":
                        gn = item.get("group_number")
                        if gn is not None:
                            selected_group_numbers.add(gn)
            groups = [
                g for g in groups
                if getattr(g, "group_number", 0) in selected_group_numbers
            ]
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        dlg = ExecuteActionDialog(
            groups, manifest_path, self.parent,
            settings=self.settings,
            task_runner=self.task_runner,
            status_reporter=self.status_reporter,
        )
        accepted = dlg.exec() == QDialog.Accepted
        # When the user removed rows via the immediate single-row
        # right-click (which mutates self._groups in place — an alias
        # of vm.groups), the main tree must re-render even if the
        # dialog was rejected. The deferred-remove path is committed
        # only via Execute (accepted=True branch below), so it doesn't
        # affect this path.
        # #444 — the same mutate-in-place + reject path applies when
        # decisions / lock state were changed via Select-by or the
        # right-click set-decision menu: the dialog persists to SQLite
        # and updates vm.groups, but the main tree never observes the
        # mutation without an explicit refresh.
        if not accepted and (dlg.removed_from_list_paths or dlg._decisions_changed):
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
            # #426: offer to prune any groups that just collapsed to a
            # single item. Runs LAST so the report_count / refresh sequence
            # above is unaffected — the prune itself does its own refresh.
            self._maybe_offer_singleton_prune()
