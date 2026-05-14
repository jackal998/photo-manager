"""Tests for ExecuteActionDialog — tree-based review and execute behaviour.

Covers:
  - _decided_records: reads user_decision from PhotoRecord items
  - Dialog UI state: summary label, Execute button enabled/disabled
  - _on_execute: delete path goes to _delete_file; keep path to executed_paths
  - manifest batch_update_decisions and mark_executed called on execute
  - _complete_delete_groups: detection of fully-deleted groups
  - _set_decision: updates rec.user_decision and refreshes warning banner
  - Warning banner visibility based on complete-delete groups
"""

from __future__ import annotations

from unittest.mock import patch


from core.models import PhotoGroup, PhotoRecord


# ── helpers ────────────────────────────────────────────────────────────────

def _rec(path: str, decision: str = "") -> PhotoRecord:
    return PhotoRecord(
        group_number=1,
        is_mark=False,
        is_locked=False,
        folder_path="",
        file_path=path,
        capture_date=None,
        modified_date=None,
        file_size_bytes=0,
        user_decision=decision,
    )


def _group(*records: PhotoRecord, number: int = 1) -> PhotoGroup:
    return PhotoGroup(group_number=number, items=list(records))


# ── dialog UI state ────────────────────────────────────────────────────────

class TestDialogState:
    def test_no_decisions_disables_execute_button(self, qapp):
        from PySide6.QtWidgets import QDialogButtonBox
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", ""))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        btn = dlg._btn_box.button(QDialogButtonBox.Ok)
        assert not btn.isEnabled()

    def test_decisions_enable_execute_button(self, qapp):
        from PySide6.QtWidgets import QDialogButtonBox
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"), _rec("/b.jpg", "keep"))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        btn = dlg._btn_box.button(QDialogButtonBox.Ok)
        assert btn.isEnabled()

    def test_deleted_and_executed_paths_initially_empty(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        assert dlg.deleted_paths == []
        assert dlg.executed_paths == []

    def test_tree_view_has_model_on_init(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        assert dlg._tree.model() is not None

    def test_window_modality_is_application_modal(self, qapp):
        """#139 — QDialog.exec() alone leaves windowModality at NonModal,
        which means Qt does NOT set OS-level WS_DISABLED on the parent
        on Windows. Real mouse clicks on the parent window's menu bar
        then steal foreground and open menus while this dialog is mid-
        review. Pin ApplicationModal explicitly so the OS-level owner
        relationship and WS_DISABLED are both established."""
        from PySide6.QtCore import Qt
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        assert dlg.windowModality() == Qt.ApplicationModal

    def test_empty_groups_tree_still_created(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        dlg = ExecuteActionDialog([], manifest_path=None)
        assert dlg._tree.model() is not None


# ── _decided_records ───────────────────────────────────────────────────────

class TestDecidedRecords:
    def test_counts_decided_records(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"), _rec("/b.jpg", "keep"), _rec("/c.jpg", ""))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        assert len(dlg._decided_records()) == 2

    def test_no_decided_records_when_all_undecided(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", ""), _rec("/b.jpg", ""))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        assert dlg._decided_records() == []


# ── _set_decision ──────────────────────────────────────────────────────────

class TestSetDecision:
    def test_updates_record_user_decision(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        rec = _rec("/a.jpg", "delete")
        groups = [_group(rec)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        dlg._set_decision("/a.jpg", "keep")
        assert rec.user_decision == "keep"

    def test_refreshes_warning_banner(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        rec = _rec("/a.jpg", "delete")
        groups = [_group(rec)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        # Initially banner is visible (complete delete group)
        assert dlg._warning_banner.isVisibleTo(dlg)
        # After setting to keep, no longer complete delete
        dlg._set_decision("/a.jpg", "keep")
        assert not dlg._warning_banner.isVisibleTo(dlg)


# ── lock / unlock at execute stage (photo-manager#164) ─────────────────────

class TestExecuteDialogLock:
    """The execute stage is the user's last chance to override a lock.
    Single-row right-click Lock/Unlock and bulk regex lock/unlock both
    flip ``is_locked`` in memory and persist to SQLite. Bulk regex on
    destructive actions skips locked rows; on lock/unlock applies to all.
    """

    def test_set_lock_via_single_row(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        rec = _rec("/a.jpg", "")
        groups = [_group(rec, _rec("/b.jpg", ""))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        dlg._set_lock("/a.jpg", True)
        assert rec.is_locked is True

    def test_set_decision_routes_lock_sentinel(self, qapp):
        """``_set_decision`` recognises LOCK_SENTINEL and routes to
        ``_set_lock`` so the right-click Set Action submenu's lock
        entry just works without separate dispatch logic."""
        from app.views.constants import LOCK_SENTINEL
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        rec = _rec("/a.jpg", "")
        groups = [_group(rec)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        dlg._set_decision("/a.jpg", LOCK_SENTINEL)
        assert rec.is_locked is True

    def test_set_decision_routes_unlock_sentinel(self, qapp):
        from app.views.constants import UNLOCK_SENTINEL
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        rec = _rec("/a.jpg", "")
        rec.is_locked = True
        groups = [_group(rec)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        dlg._set_decision("/a.jpg", UNLOCK_SENTINEL)
        assert rec.is_locked is False

    def test_regex_destructive_apply_unlocked_only_skips_locked(self, qapp):
        """Bulk regex with a destructive new_decision routes through
        the lock-confirm dialog (#182). When the user picks
        'Apply to Unlocked Only', locked rows are skipped."""
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        unlocked = _rec("/free.jpg", "")
        locked = _rec("/pinned.jpg", "")
        locked.is_locked = True
        groups = [_group(unlocked, locked)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        with patch.object(
            LockedRowsConfirmDialog,
            "ask",
            return_value=LockedRowsConfirmDialog.APPLY_UNLOCKED_ONLY,
        ):
            dlg._set_decision_by_regex("File Name", r"\.jpg$", "delete")
        assert unlocked.user_decision == "delete"
        assert locked.user_decision == ""  # protected — user chose to skip
        assert locked.is_locked is True

    def test_regex_destructive_apply_all_unlocks_then_writes(self, qapp):
        """'Unlock & Apply to All' unlocks the locked subset, then
        applies the decision to every matched row."""
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        unlocked = _rec("/free.jpg", "")
        locked = _rec("/pinned.jpg", "")
        locked.is_locked = True
        groups = [_group(unlocked, locked)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        with patch.object(
            LockedRowsConfirmDialog,
            "ask",
            return_value=LockedRowsConfirmDialog.APPLY_ALL_UNLOCKED,
        ):
            dlg._set_decision_by_regex("File Name", r"\.jpg$", "delete")
        assert unlocked.user_decision == "delete"
        assert locked.user_decision == "delete"
        assert locked.is_locked is False  # unlocked as part of action

    def test_regex_destructive_cancel_changes_nothing(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        unlocked = _rec("/free.jpg", "")
        locked = _rec("/pinned.jpg", "")
        locked.is_locked = True
        groups = [_group(unlocked, locked)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        with patch.object(
            LockedRowsConfirmDialog,
            "ask",
            return_value=LockedRowsConfirmDialog.CANCEL,
        ):
            dlg._set_decision_by_regex("File Name", r"\.jpg$", "delete")
        assert unlocked.user_decision == ""
        assert locked.user_decision == ""
        assert locked.is_locked is True

    def test_regex_lock_action_locks_all_matched(self, qapp):
        """LOCK_SENTINEL applies to all matched rows including
        already-locked (idempotent — see photo-manager#164)."""
        from app.views.constants import LOCK_SENTINEL
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        a = _rec("/a.jpg", "")
        b = _rec("/b.jpg", "")
        groups = [_group(a, b)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        dlg._set_decision_by_regex("File Name", r"\.jpg$", LOCK_SENTINEL)
        assert a.is_locked is True
        assert b.is_locked is True

    def test_regex_unlock_action_unlocks_all_matched(self, qapp):
        """UNLOCK_SENTINEL is the bulk escape hatch at execute time."""
        from app.views.constants import UNLOCK_SENTINEL
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        a = _rec("/a.jpg", "")
        a.is_locked = True
        b = _rec("/b.jpg", "")
        b.is_locked = True
        groups = [_group(a, b)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        dlg._set_decision_by_regex("File Name", r"\.jpg$", UNLOCK_SENTINEL)
        assert a.is_locked is False
        assert b.is_locked is False

    def test_single_row_destructive_on_locked_routes_through_dialog(self, qapp):
        """Single-row right-click on a locked row no longer silently
        overrides the lock (#182 retires the override path). The
        unified confirm fires with affected_count=1, locked_paths=[that
        row]; verdict drives the outcome."""
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        rec = _rec("/pinned.jpg", "")
        rec.is_locked = True
        groups = [_group(rec)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)

        # Cancel → row unchanged.
        with patch.object(
            LockedRowsConfirmDialog,
            "ask",
            return_value=LockedRowsConfirmDialog.CANCEL,
        ):
            dlg._set_decision("/pinned.jpg", "delete")
        assert rec.user_decision == ""
        assert rec.is_locked is True

        # Unlock & Apply → row unlocked + decision set.
        with patch.object(
            LockedRowsConfirmDialog,
            "ask",
            return_value=LockedRowsConfirmDialog.APPLY_ALL_UNLOCKED,
        ):
            dlg._set_decision("/pinned.jpg", "delete")
        assert rec.user_decision == "delete"
        assert rec.is_locked is False

    def test_single_row_destructive_on_unlocked_no_dialog(self, qapp):
        """Fast path: single-row right-click on an unlocked row never
        opens the lock-confirm dialog."""
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        rec = _rec("/free.jpg", "")
        groups = [_group(rec)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        with patch.object(LockedRowsConfirmDialog, "ask") as ask:
            dlg._set_decision("/free.jpg", "delete")
            ask.assert_not_called()
        assert rec.user_decision == "delete"

    def test_regex_destructive_all_locked_uses_dialog(self, qapp):
        """All-matches-locked still surfaces the lock-confirm dialog
        (with 'Apply to Unlocked Only' disabled at construction) so
        the user can choose Unlock & Apply or Cancel. The retired
        ``file_op.set_action_all_locked_*`` QMessageBox.information
        toast is gone (#182)."""
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        a = _rec("/a.jpg", "")
        b = _rec("/b.jpg", "")
        a.is_locked = True
        b.is_locked = True
        groups = [_group(a, b)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        with patch.object(
            LockedRowsConfirmDialog,
            "ask",
            return_value=LockedRowsConfirmDialog.CANCEL,
        ) as ask:
            dlg._set_decision_by_regex("File Name", r"\.jpg$", "delete")
            ask.assert_called_once()
        # User cancelled → no decisions applied, locks untouched.
        assert a.user_decision == ""
        assert b.user_decision == ""
        assert a.is_locked is True
        assert b.is_locked is True


# ── _on_execute ────────────────────────────────────────────────────────────

class TestOnExecute:
    def test_delete_decision_calls_delete_file(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)

        with patch.object(dlg, "_delete_file") as mock_del:
            dlg._on_execute()

        mock_del.assert_called_once_with("/a.jpg")

    def test_keep_decision_adds_to_executed_paths(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "keep"))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)

        with patch.object(dlg, "_delete_file") as mock_del:
            dlg._on_execute()

        mock_del.assert_not_called()
        assert "/a.jpg" in dlg.executed_paths

    def test_undecided_records_skipped(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(
            _rec("/del.jpg", "delete"),
            _rec("/keep.jpg", "keep"),
            _rec("/undecided.jpg", ""),
        )]
        dlg = ExecuteActionDialog(groups, manifest_path=None)

        with patch.object(dlg, "_delete_file"):
            dlg._on_execute()

        assert "/keep.jpg" in dlg.executed_paths
        assert "/undecided.jpg" not in dlg.executed_paths

    def test_batch_update_decisions_called_before_execute(self, qapp, tmp_path):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"), _rec("/b.jpg", "keep"))]
        dlg = ExecuteActionDialog(groups, manifest_path="/fake/manifest.sqlite")

        def fake_delete(path):
            dlg.deleted_paths.append(path)

        with patch.object(dlg, "_delete_file", side_effect=fake_delete):
            with patch(
                "infrastructure.manifest_repository.ManifestRepository.batch_update_decisions"
            ) as mock_batch:
                with patch(
                    "infrastructure.manifest_repository.ManifestRepository.mark_executed"
                ):
                    dlg._on_execute()

        mock_batch.assert_called_once()
        batch_arg = mock_batch.call_args[0][1]
        assert "/a.jpg" in batch_arg
        assert "/b.jpg" in batch_arg

    def test_mark_executed_called_with_all_done(self, qapp, tmp_path):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"), _rec("/b.jpg", "keep"))]
        dlg = ExecuteActionDialog(groups, manifest_path="/fake/manifest.sqlite")

        def fake_delete(path):
            dlg.deleted_paths.append(path)

        with patch.object(dlg, "_delete_file", side_effect=fake_delete):
            with patch(
                "infrastructure.manifest_repository.ManifestRepository.batch_update_decisions"
            ):
                with patch(
                    "infrastructure.manifest_repository.ManifestRepository.mark_executed"
                ) as mock_mark:
                    dlg._on_execute()

        mock_mark.assert_called_once()
        called_paths = set(mock_mark.call_args[0][1])
        assert "/a.jpg" in called_paths
        assert "/b.jpg" in called_paths

    def test_mark_executed_not_called_when_no_manifest(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "keep"))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)

        with patch(
            "infrastructure.manifest_repository.ManifestRepository.mark_executed"
        ) as mock_mark:
            dlg._on_execute()

        mock_mark.assert_not_called()


# ── _delete_file ───────────────────────────────────────────────────────────

class TestDeleteFile:
    def test_successful_delete_appends_to_deleted_paths(self, qapp, tmp_path):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")
        groups = [_group(_rec(str(f), "delete"))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)

        with patch("send2trash.send2trash") as mock_trash:
            dlg._delete_file(str(f))

        mock_trash.assert_called_once_with(str(f))
        assert str(f) in dlg.deleted_paths

    def test_failed_delete_not_added_to_deleted_paths(self, qapp, tmp_path):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = []
        dlg = ExecuteActionDialog(groups, manifest_path=None)

        with patch("send2trash.send2trash", side_effect=OSError("no disk")):
            with patch("os.remove", side_effect=OSError("no disk")):
                dlg._delete_file("/nonexistent/file.jpg")

        assert dlg.deleted_paths == []

    def test_falls_back_to_os_remove_when_send2trash_missing(self, qapp, tmp_path):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")
        groups = []
        dlg = ExecuteActionDialog(groups, manifest_path=None)

        with patch.dict("sys.modules", {"send2trash": None}):
            with patch("os.remove") as mock_remove:
                dlg._delete_file(str(f))

        mock_remove.assert_called_once_with(str(f))
        assert str(f) in dlg.deleted_paths


# ── _complete_delete_groups ────────────────────────────────────────────────

class TestGroupDeletionCheck:
    def test_complete_delete_groups_detects_full_group(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [
            _group(_rec("/a.jpg", "delete"), _rec("/b.jpg", "delete"), number=1),
            _group(_rec("/c.jpg", "delete"), _rec("/d.jpg", "keep"), number=2),
        ]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        complete = dlg._complete_delete_groups()
        assert 1 in complete
        assert 2 not in complete

    def test_complete_delete_groups_empty_when_none(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"), _rec("/b.jpg", "keep"), number=1)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        assert dlg._complete_delete_groups() == []

    def test_complete_delete_groups_multiple(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [
            _group(_rec("/a.jpg", "delete"), number=1),
            _group(_rec("/b.jpg", "delete"), number=2),
            _group(_rec("/c.jpg", "keep"), number=3),
        ]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        complete = dlg._complete_delete_groups()
        assert set(complete) == {1, 2}

    def test_warning_banner_visible_when_complete_group(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"), _rec("/b.jpg", "delete"), number=1)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        assert dlg._warning_banner.isVisibleTo(dlg)

    def test_warning_banner_hidden_when_no_complete_group(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"), _rec("/b.jpg", "keep"), number=1)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        assert not dlg._warning_banner.isVisibleTo(dlg)

    def test_undecided_records_excluded_from_complete_check(self, qapp):
        """A group with one delete and one undecided is NOT a complete-delete group."""
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"), _rec("/b.jpg", ""), number=1)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        # /b.jpg has no decision → not all records are "delete" → NOT complete
        complete = dlg._complete_delete_groups()
        assert 1 not in complete


# ── banner jump-to (#166) ──────────────────────────────────────────────────

class TestBannerJumpTo:
    """Each group number in the warning banner is rendered as an HTML
    anchor; clicking one scrolls + selects that group in the dialog
    tree. These tests pin the rendering and the lookup path.
    """

    def _two_complete_groups(self):
        return [
            _group(_rec("/g1a.jpg", "delete"), _rec("/g1b.jpg", "delete"), number=1),
            _group(_rec("/g2a.jpg", "delete"), _rec("/g2b.jpg", "delete"), number=3),
        ]

    def test_banner_renders_group_numbers_as_anchors(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        dlg = ExecuteActionDialog(self._two_complete_groups(), manifest_path=None)
        text = dlg._warning_label.text()
        # Both group numbers should appear inside anchor tags
        assert '<a href="1">1</a>' in text
        assert '<a href="3">3</a>' in text

    def test_jump_to_selects_target_group_row(self, qapp):
        from app.views.constants import COL_GROUP, SORT_ROLE
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        dlg = ExecuteActionDialog(self._two_complete_groups(), manifest_path=None)
        # Click the "3" anchor — tree's current index should land on the
        # group row whose SORT_ROLE group_number is 3.
        dlg._on_jump_to_group("3")
        idx = dlg._tree.currentIndex()
        assert idx.isValid()
        # currentIndex may be on any column of the selected row; resolve to
        # COL_GROUP via sibling so the SORT_ROLE lookup is unambiguous.
        group_idx = idx.sibling(idx.row(), COL_GROUP)
        assert group_idx.data(SORT_ROLE) == 3

    def test_jump_to_ignores_invalid_href(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        dlg = ExecuteActionDialog(self._two_complete_groups(), manifest_path=None)
        # Non-integer hrefs must not raise and must leave selection
        # untouched. (linkActivated is a typed signal, but defensive
        # against future template drift.)
        dlg._on_jump_to_group("not-an-int")
        # No assertion on selection state — the contract is "don't raise".

    def test_jump_to_unknown_group_is_noop(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        dlg = ExecuteActionDialog(self._two_complete_groups(), manifest_path=None)
        # Group 99 isn't in the tree → no change in selection / no raise.
        before = dlg._tree.currentIndex()
        dlg._on_jump_to_group("99")
        after = dlg._tree.currentIndex()
        assert after.row() == before.row()
        assert after.column() == before.column()


# ── _delete_file: missing file handling ────────────────────────────────────

class TestMissingFileHandling:
    def test_missing_file_added_to_missing_paths(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        dlg = ExecuteActionDialog([], manifest_path=None)
        dlg._delete_file("/nonexistent/photo.jpg")
        assert "/nonexistent/photo.jpg" in dlg._missing_paths
        assert "/nonexistent/photo.jpg" not in dlg.deleted_paths

    def test_missing_file_not_trashed(self, qapp):
        from unittest.mock import patch
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        dlg = ExecuteActionDialog([], manifest_path=None)
        with patch("send2trash.send2trash") as mock_trash:
            dlg._delete_file("/nonexistent/photo.jpg")
        mock_trash.assert_not_called()


# ── group filtering ────────────────────────────────────────────────────────

class TestGroupFiltering:
    """Only groups with ≥1 decided file should appear in the tree."""

    def _src_model(self, dlg):
        model = dlg._tree.model()
        return model.sourceModel() if hasattr(model, "sourceModel") else model

    def test_only_decided_groups_shown(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [
            _group(_rec("/a.jpg", "delete"), number=1),
            _group(_rec("/b.jpg", ""), number=2),
        ]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        assert self._src_model(dlg).rowCount() == 1

    def test_undecided_groups_not_in_tree(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [
            _group(_rec("/a.jpg", ""), number=1),
            _group(_rec("/b.jpg", ""), number=2),
        ]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        assert self._src_model(dlg).rowCount() == 0

    def test_all_decided_groups_shown(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [
            _group(_rec("/a.jpg", "delete"), number=1),
            _group(_rec("/b.jpg", "delete"), number=2),
        ]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        assert self._src_model(dlg).rowCount() == 2

    def test_decided_records_still_uses_all_groups(self, qapp):
        """_decided_records() must iterate self._groups, not the filtered display list."""
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [
            _group(_rec("/a.jpg", "delete"), number=1),
            _group(_rec("/b.jpg", ""), number=2),
        ]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        decided = dlg._decided_records()
        assert len(decided) == 1
        assert decided[0][1].file_path == "/a.jpg"


# ── Select by Field/Regex button ───────────────────────────────────────────

class TestSelectByRegexButton:
    def test_select_by_regex_button_exists(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        from PySide6.QtWidgets import QPushButton
        groups = [_group(_rec("/a.jpg", "delete"))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        buttons = dlg.findChildren(QPushButton)
        button_texts = [b.text() for b in buttons]
        assert any("Field/Regex" in t for t in button_texts), (
            f"No 'Select by Field/Regex' button found. Buttons: {button_texts}"
        )


# ── _set_decision_by_regex ─────────────────────────────────────────────────

class TestSetDecisionByRegex:
    def test_set_delete_by_filename_regex(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(
            _rec("/photos/IMG_001.jpg", ""),
            _rec("/photos/RAW_001.dng", ""),
        )]
        dlg = ExecuteActionDialog(groups, manifest_path=None)

        dlg._set_decision_by_regex("File Name", r"^IMG_", "delete")

        assert groups[0].items[0].user_decision == "delete"
        assert groups[0].items[1].user_decision == ""

    def test_set_empty_clears_decision(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(
            _rec("/a.jpg", "delete"),
            _rec("/b.jpg", "delete"),
        )]
        dlg = ExecuteActionDialog(groups, manifest_path=None)

        dlg._set_decision_by_regex("File Name", r"^a\.jpg$", "")

        assert groups[0].items[0].user_decision == ""
        assert groups[0].items[1].user_decision == "delete"

    def test_action_field_matches_user_decision_not_scanner_action(self, qapp):
        """Action field regex matches user_decision (the bug fix must be in effect)."""
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        rec_a = _rec("/a.jpg", "delete")
        rec_a.action = "MOVE"
        rec_b = _rec("/b.jpg", "")
        rec_b.action = "EXACT"
        groups = [_group(rec_a, rec_b)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)

        # Clear "delete" using Action field — must match user_decision, not rec.action
        dlg._set_decision_by_regex("Action", "^delete$", "")

        assert groups[0].items[0].user_decision == "", "should have cleared 'delete'"
        assert groups[0].items[1].user_decision == ""

    @patch("PySide6.QtWidgets.QMessageBox.information")
    def test_no_match_shows_information(self, mock_info, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", ""))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        dlg._set_decision_by_regex("File Name", r"^no_match_xyz$", "delete")
        mock_info.assert_called_once()

    @patch("PySide6.QtWidgets.QMessageBox.warning")
    def test_invalid_regex_shows_warning(self, mock_warn, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", ""))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        dlg._set_decision_by_regex("File Name", "[invalid(", "delete")
        mock_warn.assert_called_once()

    def test_tree_updates_after_set_decision(self, qapp):
        """After setting a decision by regex, the tree gains the newly-decided group."""
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [
            _group(_rec("/a.jpg", ""), number=1),
            _group(_rec("/b.jpg", ""), number=2),
        ]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        model_before = dlg._tree.model()
        src_before = model_before.sourceModel() if hasattr(model_before, "sourceModel") else model_before
        assert src_before.rowCount() == 0   # nothing decided yet

        dlg._set_decision_by_regex("File Name", r"^a\.jpg$", "delete")

        model_after = dlg._tree.model()
        src_after = model_after.sourceModel() if hasattr(model_after, "sourceModel") else model_after
        assert src_after.rowCount() == 1    # group 1 now visible


# ── context menu uses _SETTABLE_DECISIONS tuples ───────────────────────────

class TestContextMenuDecisions:
    def test_settable_decisions_constant_exists(self, qapp):
        from app.views.constants import settable_decisions
        _SETTABLE_DECISIONS = settable_decisions()
        assert isinstance(_SETTABLE_DECISIONS, list)
        assert all(isinstance(t, tuple) and len(t) == 2 for t in _SETTABLE_DECISIONS)

    def test_keep_remove_action_value_is_empty_string(self, qapp):
        from app.views.constants import settable_decisions
        _SETTABLE_DECISIONS = settable_decisions()
        keep_entry = next((t for t in _SETTABLE_DECISIONS if "keep" in t[0].lower()), None)
        assert keep_entry is not None, "No 'keep' entry in _SETTABLE_DECISIONS"
        assert keep_entry[1] == "", f"Expected '' but got {keep_entry[1]!r}"

    def test_delete_decision_value_is_delete(self, qapp):
        from app.views.constants import settable_decisions
        _SETTABLE_DECISIONS = settable_decisions()
        del_entry = next((t for t in _SETTABLE_DECISIONS if t[1] == "delete"), None)
        assert del_entry is not None


# ── _on_execute_requested (confirmation gate) ─────────────────────────────


class TestOnExecuteRequestedConfirmation:
    """Tests for the confirmation prompt that fires before destructive execute."""

    def test_no_complete_delete_groups_calls_through(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog

        # Group with one delete + one keep — not "complete delete"
        rec_d = _rec("/a.jpg", "delete")
        rec_k = _rec("/b.jpg", "keep")
        g = _group(rec_d, rec_k)
        dlg = ExecuteActionDialog([g], manifest_path=None)
        try:
            with patch.object(dlg, "_on_execute") as on_exec:
                dlg._on_execute_requested()
            on_exec.assert_called_once()
        finally:
            dlg.close()

    def test_complete_delete_group_yes_continues(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        from PySide6.QtWidgets import QMessageBox as _QMB

        g = _group(_rec("/a.jpg", "delete"), _rec("/b.jpg", "delete"))
        dlg = ExecuteActionDialog([g], manifest_path=None)
        try:
            with (
                patch("PySide6.QtWidgets.QMessageBox.question", return_value=_QMB.Yes) as q,
                patch.object(dlg, "_on_execute") as on_exec,
            ):
                dlg._on_execute_requested()
            q.assert_called_once()
            on_exec.assert_called_once()
        finally:
            dlg.close()

    def test_complete_delete_group_no_aborts(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        from PySide6.QtWidgets import QMessageBox as _QMB

        g = _group(_rec("/a.jpg", "delete"), _rec("/b.jpg", "delete"))
        dlg = ExecuteActionDialog([g], manifest_path=None)
        try:
            with (
                patch("PySide6.QtWidgets.QMessageBox.question", return_value=_QMB.No),
                patch.object(dlg, "_on_execute") as on_exec,
            ):
                dlg._on_execute_requested()
            on_exec.assert_not_called()
        finally:
            dlg.close()


# ── pre-execute lock-confirm scan (#182) ──────────────────────────────────


class TestExecuteRequestedLockConfirm:
    """When the user clicks Execute and at least one row has
    user_decision='delete' AND is_locked=True (locked AFTER decision was
    set), the unified lock-confirm dialog fires BEFORE the
    'All Files Will Be Deleted' QMessageBox. Verdict drives:
      - APPLY_ALL_UNLOCKED  → unlock + proceed to execute the full set
      - APPLY_UNLOCKED_ONLY → clear decision on locked rows, proceed
      - CANCEL              → bail before any destructive action
    """

    def _locked_delete_setup(self):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        unlocked = _rec("/free.jpg", "delete")
        locked = _rec("/pinned.jpg", "delete")
        locked.is_locked = True
        groups = [_group(unlocked, locked)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        return dlg, unlocked, locked

    def test_no_locked_delete_skips_lock_confirm(self, qapp):
        """Fast path: no locked-with-delete rows → no lock-confirm
        dialog, proceed directly to the existing all-delete confirm."""
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        groups = [_group(_rec("/a.jpg", "delete"), _rec("/b.jpg", "keep"))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        try:
            with (
                patch.object(LockedRowsConfirmDialog, "ask") as ask,
                patch.object(dlg, "_on_execute") as on_exec,
            ):
                dlg._on_execute_requested()
                ask.assert_not_called()
                on_exec.assert_called_once()
        finally:
            dlg.close()

    def test_apply_all_unlocked_unlocks_then_executes(self, qapp):
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        from PySide6.QtWidgets import QMessageBox as _QMB
        dlg, unlocked, locked = self._locked_delete_setup()
        try:
            with (
                patch.object(
                    LockedRowsConfirmDialog,
                    "ask",
                    return_value=LockedRowsConfirmDialog.APPLY_ALL_UNLOCKED,
                ),
                patch("PySide6.QtWidgets.QMessageBox.question", return_value=_QMB.Yes),
                patch.object(dlg, "_on_execute") as on_exec,
            ):
                dlg._on_execute_requested()
            assert locked.is_locked is False
            assert locked.user_decision == "delete"  # still slated for delete
            assert unlocked.user_decision == "delete"
            on_exec.assert_called_once()
        finally:
            dlg.close()

    def test_apply_all_unlocked_runs_send2trash_on_both_paths(self, qapp, tmp_path):
        """End-to-end integration: lock-confirm → Unlock & Apply All →
        all-delete confirm → _on_execute → send2trash actually fires
        for BOTH the previously-locked file and the unlocked one.

        Layer-1 destructive guard (mocks send2trash). The layer-3
        sibling is s36_lock_confirm_destructive_execute, which fires
        real send2trash on a disposable fixture. This test exists so
        a regression in the pre-execute scan → unlock → execute chain
        fails CI even before qa-batch runs.
        """
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        from PySide6.QtWidgets import QMessageBox as _QMB

        # Real on-disk files so _delete_file's os.path.exists check
        # passes — without that, paths land in _missing_paths and
        # send2trash never gets called.
        f_free = tmp_path / "free.jpg"
        f_locked = tmp_path / "locked.jpg"
        f_free.write_bytes(b"x")
        f_locked.write_bytes(b"x")
        unlocked = _rec(str(f_free), "delete")
        locked = _rec(str(f_locked), "delete")
        locked.is_locked = True
        groups = [_group(unlocked, locked)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)

        try:
            with (
                patch.object(
                    LockedRowsConfirmDialog,
                    "ask",
                    return_value=LockedRowsConfirmDialog.APPLY_ALL_UNLOCKED,
                ),
                patch("PySide6.QtWidgets.QMessageBox.question", return_value=_QMB.Yes),
                # Patch the imported reference inside _delete_file —
                # the function does `import send2trash` and calls
                # `send2trash.send2trash(path)`, so patching the
                # attribute on the module catches the real call site.
                patch("send2trash.send2trash") as fake_send2trash,
            ):
                dlg._on_execute_requested()

            # Both files reached send2trash — proves the lock-confirm
            # didn't accidentally short-circuit the unlocked path,
            # and the unlock+execute happened for the locked one.
            called_paths = {
                call.args[0] for call in fake_send2trash.call_args_list
            }
            assert str(f_free) in called_paths
            assert str(f_locked) in called_paths
            # Both rows ended up in deleted_paths (post-execute audit).
            assert str(f_free) in dlg.deleted_paths
            assert str(f_locked) in dlg.deleted_paths
            # Locked row was unlocked as part of the verdict.
            assert locked.is_locked is False
        finally:
            dlg.close()

    def test_apply_unlocked_only_clears_decision_on_locked(self, qapp):
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        from PySide6.QtWidgets import QMessageBox as _QMB
        dlg, unlocked, locked = self._locked_delete_setup()
        try:
            with (
                patch.object(
                    LockedRowsConfirmDialog,
                    "ask",
                    return_value=LockedRowsConfirmDialog.APPLY_UNLOCKED_ONLY,
                ),
                # No complete-delete groups any more (locked row's decision was
                # cleared), so the all-delete QMessageBox shouldn't fire — but
                # patch it defensively in case the group has only one row
                # and the predicate still resolves true.
                patch("PySide6.QtWidgets.QMessageBox.question", return_value=_QMB.Yes),
                patch.object(dlg, "_on_execute") as on_exec,
            ):
                dlg._on_execute_requested()
            assert locked.is_locked is True            # lock preserved
            assert locked.user_decision == ""          # decision cleared
            assert unlocked.user_decision == "delete"
            on_exec.assert_called_once()
        finally:
            dlg.close()

    def test_cancel_aborts_before_any_destructive_action(self, qapp):
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        dlg, unlocked, locked = self._locked_delete_setup()
        try:
            with (
                patch.object(
                    LockedRowsConfirmDialog,
                    "ask",
                    return_value=LockedRowsConfirmDialog.CANCEL,
                ),
                patch.object(dlg, "_on_execute") as on_exec,
            ):
                dlg._on_execute_requested()
            assert locked.is_locked is True
            assert locked.user_decision == "delete"
            assert unlocked.user_decision == "delete"
            on_exec.assert_not_called()
        finally:
            dlg.close()

    def test_lock_confirm_receives_total_delete_count_not_just_locked_count(self, qapp):
        """#207 — when 1 of 3 delete-decision rows is locked, the lock-confirm dialog
        must receive affected_count=3 (total deletes), not affected_count=1 (locked
        only). Without this, unlocked_count=0 and the dialog fires the all-locked
        branch, disabling "Apply to Unlocked Only"."""
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        from app.views.dialogs.locked_rows_confirm_dialog import LockedRowsConfirmDialog

        r1 = _rec("/a.jpg", "delete")
        r2 = _rec("/b.jpg", "delete")
        r3 = _rec("/c.jpg", "delete")
        r3.is_locked = True
        groups = [_group(r1, r2, r3)]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        try:
            with patch.object(
                LockedRowsConfirmDialog,
                "ask",
                return_value=LockedRowsConfirmDialog.CANCEL,
            ) as ask:
                dlg._on_execute_requested()
            ask.assert_called_once()
            _, kwargs = ask.call_args
            assert kwargs["affected_count"] == 3
            assert kwargs["locked_paths"] == ["/c.jpg"]
        finally:
            dlg.close()


# ── _set_decision_by_regex persist-failure swallow ────────────────────────


class TestSetDecisionByRegexPersistFailure:
    def test_persistence_failure_does_not_block_in_memory_update(self, qapp, tmp_path):
        """When batch_update_decisions raises, the in-memory rec.user_decision
        is still set — failure is logged but the dialog stays usable."""
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog

        rec = _rec("/photos/IMG.jpg")
        g = _group(rec)
        # Manifest path that doesn't exist → batch_update_decisions raises.
        dlg = ExecuteActionDialog([g], manifest_path=str(tmp_path / "missing.sqlite"))
        try:
            dlg._set_decision_by_regex("File Name", r"IMG", "delete")
            assert rec.user_decision == "delete"
        finally:
            dlg.close()


# ── Remove-from-list branch (regex + single-row right-click) ──────────────


class TestRemoveFromListBranch:
    """The execute-action dialog routes the REMOVE_FROM_LIST_SENTINEL
    to a separate path that mutates self._groups in place (preserving
    the alias to vm.groups), syncs the manifest, and accumulates
    removed paths for the parent to read after exec()."""

    def test_single_row_right_click_prompts_and_removes_when_confirmed(self, qapp, tmp_path):
        """Single-row right-click + confirm: drops the row from the
        in-memory group AND records it in removed_from_list_paths so
        the parent can refresh the main tree on close."""
        from PySide6.QtWidgets import QMessageBox

        from app.views.constants import REMOVE_FROM_LIST_SENTINEL
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog

        rec_a = _rec("/a.jpg", "delete")
        rec_b = _rec("/b.jpg", "delete")
        groups = [_group(rec_a, rec_b)]
        # tmp_path/missing means remove_from_review will raise; the
        # logger swallow lets the in-memory removal still happen, which
        # is the behavior we want to assert.
        dlg = ExecuteActionDialog(groups, manifest_path=str(tmp_path / "missing.sqlite"))
        try:
            with patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ) as q:
                dlg._set_decision("/a.jpg", REMOVE_FROM_LIST_SENTINEL)
            q.assert_called_once()
            remaining = [r.file_path for g in dlg._groups for r in g.items]
            assert remaining == ["/b.jpg"]
            assert dlg.removed_from_list_paths == ["/a.jpg"]
        finally:
            dlg.close()

    def test_single_row_right_click_decline_keeps_row(self, qapp, tmp_path):
        """Decline path: prompt fires, user clicks No, row stays."""
        from PySide6.QtWidgets import QMessageBox

        from app.views.constants import REMOVE_FROM_LIST_SENTINEL
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog

        rec_a = _rec("/a.jpg", "delete")
        groups = [_group(rec_a)]
        dlg = ExecuteActionDialog(groups, manifest_path=str(tmp_path / "missing.sqlite"))
        try:
            with patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.No,
            ):
                dlg._set_decision("/a.jpg", REMOVE_FROM_LIST_SENTINEL)
            # Row still present, removed_from_list_paths empty.
            assert dlg._groups[0].items == [rec_a]
            assert dlg.removed_from_list_paths == []
        finally:
            dlg.close()

    def test_empty_groups_dropped_from_list(self, qapp, tmp_path):
        """When every record in a group is removed, the group itself
        must disappear — otherwise the tree shows an empty header."""
        from PySide6.QtWidgets import QMessageBox

        from app.views.constants import REMOVE_FROM_LIST_SENTINEL
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog

        rec_a = _rec("/a.jpg", "delete")
        groups = [_group(rec_a)]
        dlg = ExecuteActionDialog(groups, manifest_path=str(tmp_path / "missing.sqlite"))
        try:
            with patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ):
                dlg._set_decision("/a.jpg", REMOVE_FROM_LIST_SENTINEL)
            assert dlg._groups == []
        finally:
            dlg.close()

    def test_groups_alias_to_caller_is_preserved(self, qapp, tmp_path):
        """self._groups is constructed from the caller's list; the in-place
        slice replacement (self._groups[:] = ...) must keep that alias so
        vm.groups (the caller's list) reflects the removal automatically."""
        from PySide6.QtWidgets import QMessageBox

        from app.views.constants import REMOVE_FROM_LIST_SENTINEL
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog

        rec_a = _rec("/a.jpg", "delete")
        rec_b = _rec("/b.jpg", "delete")
        caller_groups = [_group(rec_a, rec_b)]
        dlg = ExecuteActionDialog(caller_groups, manifest_path=str(tmp_path / "missing.sqlite"))
        try:
            with patch(
                "PySide6.QtWidgets.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ):
                dlg._set_decision("/a.jpg", REMOVE_FROM_LIST_SENTINEL)
            # Caller's list reflects the removal because we mutated in place.
            assert caller_groups is dlg._groups
            remaining = [r.file_path for g in caller_groups for r in g.items]
            assert remaining == ["/b.jpg"]
        finally:
            dlg.close()

    def test_regex_remove_writes_decision_no_prompt(self, qapp, tmp_path):
        """Regex 'remove from list' is now deferred — no prompt fires,
        matched rows just get user_decision='remove_from_list' set.
        The actual removal happens at Execute time."""
        from app.views.constants import REMOVE_FROM_LIST_DECISION, REMOVE_FROM_LIST_SENTINEL
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog

        rec_a = _rec("/a.jpg", "delete")
        rec_b = _rec("/b.jpg", "delete")
        groups = [_group(rec_a, rec_b)]
        dlg = ExecuteActionDialog(groups, manifest_path=str(tmp_path / "missing.sqlite"))
        try:
            with patch("PySide6.QtWidgets.QMessageBox.question") as q:
                dlg._set_decision_by_regex(
                    "File Name", r"^a\.jpg$", REMOVE_FROM_LIST_SENTINEL
                )
            q.assert_not_called()
            # Row stays in groups; decision is updated.
            assert rec_a.user_decision == REMOVE_FROM_LIST_DECISION
            assert rec_b.user_decision == "delete"
            assert dlg.removed_from_list_paths == [], (
                "Bulk regex must not append to removed_from_list_paths "
                "before Execute — that list is for executed removals."
            )
        finally:
            dlg.close()

    def test_regex_remove_no_match_shows_info(self, qapp, tmp_path):
        """Zero matches → no-match info dialog, no prompt, no decision change."""
        from app.views.constants import REMOVE_FROM_LIST_SENTINEL
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog

        rec = _rec("/a.jpg", "delete")
        dlg = ExecuteActionDialog([_group(rec)], manifest_path=str(tmp_path / "missing.sqlite"))
        try:
            with patch("PySide6.QtWidgets.QMessageBox.information") as info, \
                 patch("PySide6.QtWidgets.QMessageBox.question") as q:
                dlg._set_decision_by_regex(
                    "File Name", "wont_match", REMOVE_FROM_LIST_SENTINEL
                )
            info.assert_called_once()
            q.assert_not_called()
            assert rec.user_decision == "delete"
            assert dlg.removed_from_list_paths == []
        finally:
            dlg.close()

    def test_on_execute_handles_remove_from_list_decision(self, qapp, tmp_path):
        """When _on_execute encounters user_decision='remove_from_list',
        it should NOT delete the file (no recycle-bin call) but should
        accumulate the path in removed_from_list_paths so the parent
        can drop it from vm.groups, AND mark it in remove_from_review
        in the manifest."""
        import sqlite3

        from app.views.constants import REMOVE_FROM_LIST_DECISION
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog

        # Build a real SQLite manifest so remove_from_review can write.
        db = tmp_path / "manifest.sqlite"
        with sqlite3.connect(db) as conn:
            conn.executescript("""
                CREATE TABLE migration_manifest (
                    id INTEGER PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    source_label TEXT NOT NULL DEFAULT 'test',
                    dest_path TEXT,
                    action TEXT NOT NULL DEFAULT 'MOVE',
                    source_hash TEXT,
                    phash TEXT,
                    hamming_distance INTEGER,
                    group_id TEXT,
                    reason TEXT,
                    executed INTEGER NOT NULL DEFAULT 0,
                    user_decision TEXT NOT NULL DEFAULT ''
                );
            """)
            conn.execute(
                "INSERT INTO migration_manifest (source_path) VALUES (?)",
                ("/a.jpg",),
            )
            conn.commit()

        rec_a = _rec("/a.jpg", REMOVE_FROM_LIST_DECISION)
        groups = [_group(rec_a)]
        dlg = ExecuteActionDialog(groups, manifest_path=str(db))
        try:
            with patch.object(dlg, "_delete_file") as delete_file:
                dlg._on_execute()
            # No file delete attempted for remove decisions.
            delete_file.assert_not_called()
            # Path landed in removed_from_list_paths so the parent
            # can drop it from vm.groups.
            assert dlg.removed_from_list_paths == ["/a.jpg"]
            # The manifest row was marked removed.
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "SELECT user_decision FROM migration_manifest WHERE source_path = ?",
                    ("/a.jpg",),
                ).fetchone()
            assert row and row[0] == "removed"
        finally:
            dlg.close()
