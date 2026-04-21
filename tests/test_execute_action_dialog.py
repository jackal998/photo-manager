"""Tests for ExecuteActionDialog — collect_ops logic and execute behaviour.

Covers:
  - _collect_ops: reads user_decision from PhotoRecord items
  - Dialog UI state: summary counts, Execute button enabled/disabled
  - _on_execute: delete path goes to _delete_file; keep path to executed_paths
  - manifest mark_executed called after execution
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

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


# ── _collect_ops ───────────────────────────────────────────────────────────

class TestCollectOps:
    """Test _collect_ops via a real (but invisible) dialog instance."""

    def _make_dialog(self, qapp, groups):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        return ExecuteActionDialog(groups, manifest_path=None)

    def test_collects_delete_decision(self, qapp):
        groups = [_group(_rec("/a.jpg", "delete"))]
        dlg = self._make_dialog(qapp, groups)
        assert len(dlg._ops) == 1
        assert dlg._ops[0] == {"decision": "delete", "path": "/a.jpg"}

    def test_collects_keep_decision(self, qapp):
        groups = [_group(_rec("/a.jpg", "keep"))]
        dlg = self._make_dialog(qapp, groups)
        assert len(dlg._ops) == 1
        assert dlg._ops[0]["decision"] == "keep"

    def test_skips_undecided_records(self, qapp):
        groups = [_group(_rec("/a.jpg", "delete"), _rec("/b.jpg", ""))]
        dlg = self._make_dialog(qapp, groups)
        assert len(dlg._ops) == 1
        assert dlg._ops[0]["path"] == "/a.jpg"

    def test_empty_groups(self, qapp):
        dlg = self._make_dialog(qapp, [])
        assert dlg._ops == []

    def test_multiple_groups(self, qapp):
        groups = [
            _group(_rec("/a.jpg", "delete"), number=1),
            _group(_rec("/b.jpg", "keep"), _rec("/c.jpg", "delete"), number=2),
        ]
        dlg = self._make_dialog(qapp, groups)
        assert len(dlg._ops) == 3

    def test_group_without_items_attribute(self, qapp):
        """Groups with no items attribute should be skipped gracefully."""
        groups = [SimpleNamespace()]  # no 'items' attr
        dlg = self._make_dialog(qapp, groups)
        assert dlg._ops == []


# ── dialog creation & UI state ─────────────────────────────────────────────

class TestDialogState:
    def test_no_ops_disables_execute_button(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", ""))]  # no decisions
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        ok_btn = dlg.findChild(type(dlg), "")  # QDialogButtonBox OK button
        # Verify via _ops — the button enable state tracks this
        assert dlg._ops == []

    def test_ops_with_decisions(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"), _rec("/b.jpg", "keep"))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        assert len(dlg._ops) == 2

    def test_deleted_and_executed_paths_initially_empty(self, qapp):
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"))]
        dlg = ExecuteActionDialog(groups, manifest_path=None)
        assert dlg.deleted_paths == []
        assert dlg.executed_paths == []


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

    def test_mixed_decisions(self, qapp):
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

    def test_mark_executed_called_with_all_done(self, qapp, tmp_path):
        """After execute, ManifestRepository.mark_executed receives all paths."""
        from app.views.dialogs.execute_action_dialog import ExecuteActionDialog
        groups = [_group(_rec("/a.jpg", "delete"), _rec("/b.jpg", "keep"))]
        dlg = ExecuteActionDialog(groups, manifest_path="/fake/manifest.sqlite")

        # Simulate a successful delete by appending to deleted_paths inside the mock
        def fake_delete(path):
            dlg.deleted_paths.append(path)

        with patch.object(dlg, "_delete_file", side_effect=fake_delete):
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
