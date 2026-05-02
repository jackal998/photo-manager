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

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

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
        from app.views.dialogs.execute_action_dialog import _SETTABLE_DECISIONS
        assert isinstance(_SETTABLE_DECISIONS, list)
        assert all(isinstance(t, tuple) and len(t) == 2 for t in _SETTABLE_DECISIONS)

    def test_keep_remove_action_value_is_empty_string(self, qapp):
        from app.views.dialogs.execute_action_dialog import _SETTABLE_DECISIONS
        keep_entry = next((t for t in _SETTABLE_DECISIONS if "keep" in t[0].lower()), None)
        assert keep_entry is not None, "No 'keep' entry in _SETTABLE_DECISIONS"
        assert keep_entry[1] == "", f"Expected '' but got {keep_entry[1]!r}"

    def test_delete_decision_value_is_delete(self, qapp):
        from app.views.dialogs.execute_action_dialog import _SETTABLE_DECISIONS
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
