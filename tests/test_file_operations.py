"""Tests for FileOperationsHandler.set_decision and related operations.

These tests exercise the set_decision workflow against a real SQLite manifest DB,
using mocks for Qt widgets and the VM layer.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.models import PhotoGroup, PhotoRecord


# ── helpers ────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE migration_manifest (
    id               INTEGER PRIMARY KEY,
    source_path      TEXT NOT NULL,
    source_label     TEXT NOT NULL DEFAULT 'test',
    dest_path        TEXT,
    action           TEXT NOT NULL DEFAULT 'MOVE',
    source_hash      TEXT,
    phash            TEXT,
    hamming_distance INTEGER,
    group_id         TEXT,
    reason           TEXT,
    executed         INTEGER NOT NULL DEFAULT 0,
    user_decision    TEXT    NOT NULL DEFAULT ''
);
"""


def _make_db(tmp_path: Path, rows: list[dict]) -> Path:
    db = tmp_path / "manifest.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript(_DDL)
        for r in rows:
            conn.execute(
                "INSERT INTO migration_manifest (source_path, action) VALUES (?, ?)",
                (r["source_path"], r.get("action", "MOVE")),
            )
        conn.commit()
    return db


def _rec(path: str, group: int = 1, decision: str = "") -> PhotoRecord:
    return PhotoRecord(
        group_number=group,
        is_mark=False,
        is_locked=False,
        folder_path="",
        file_path=path,
        capture_date=None,
        modified_date=None,
        file_size_bytes=0,
        user_decision=decision,
    )


def _make_handler(vm, manifest_path: str | None, checked_paths=None, highlighted_items=None):
    """Build a FileOperationsHandler with all Qt deps mocked."""
    from app.views.handlers.file_operations import FileOperationsHandler

    ui_updater = MagicMock()
    status_reporter = MagicMock()
    parent = MagicMock()
    parent.menu_controller = MagicMock()

    handler = FileOperationsHandler(
        vm=vm,
        settings=MagicMock(),
        parent_widget=parent,
        ui_updater=ui_updater,
        status_reporter=status_reporter,
        checked_paths_provider=checked_paths,
        highlighted_items_provider=highlighted_items,
    )
    if manifest_path:
        handler._manifest_path = manifest_path
    return handler, ui_updater, status_reporter


def _read_decision(db: Path, path: str) -> str:
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT user_decision FROM migration_manifest WHERE source_path = ?",
            (path,),
        ).fetchone()
    return row[0] if row else ""


# ── set_decision ───────────────────────────────────────────────────────────

class TestSetDecision:
    def test_sets_decision_in_memory(self, tmp_path):
        rec = _rec("/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, ui_updater, _ = _make_handler(vm, str(db))

        handler.set_decision([{"type": "file", "path": "/a.jpg"}], "delete")

        assert rec.user_decision == "delete"

    def test_sets_decision_in_sqlite(self, tmp_path):
        rec = _rec("/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))

        handler.set_decision([{"type": "file", "path": "/a.jpg"}], "keep")

        assert _read_decision(db, "/a.jpg") == "keep"

    def test_overwrites_existing_decision(self, tmp_path):
        rec = _rec("/a.jpg", decision="delete")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))

        handler.set_decision([{"type": "file", "path": "/a.jpg"}], "keep")

        assert rec.user_decision == "keep"
        assert _read_decision(db, "/a.jpg") == "keep"

    def test_skips_non_file_items(self, tmp_path):
        rec = _rec("/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))

        handler.set_decision([{"type": "group", "path": "/a.jpg"}], "delete")

        assert rec.user_decision == ""

    def test_refreshes_tree(self, tmp_path):
        rec = _rec("/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, ui_updater, _ = _make_handler(vm, str(db))

        handler.set_decision([{"type": "file", "path": "/a.jpg"}], "delete")

        ui_updater.refresh_tree.assert_called_once()

    def test_reports_status(self, tmp_path):
        rec = _rec("/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, status_reporter = _make_handler(vm, str(db))

        handler.set_decision([{"type": "file", "path": "/a.jpg"}], "delete")

        status_reporter.show_status.assert_called_once()
        assert "delete" in status_reporter.show_status.call_args[0][0]

    def test_no_manifest_noop(self, tmp_path):
        """set_decision silently returns when no manifest is loaded."""
        rec = _rec("/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        handler, ui_updater, _ = _make_handler(vm, manifest_path=None)

        handler.set_decision([{"type": "file", "path": "/a.jpg"}], "delete")

        assert rec.user_decision == ""
        ui_updater.refresh_tree.assert_not_called()

    def test_sets_multiple_items(self, tmp_path):
        recs = [_rec("/a.jpg"), _rec("/b.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}, {"source_path": "/b.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))

        handler.set_decision(
            [{"type": "file", "path": "/a.jpg"}, {"type": "file", "path": "/b.jpg"}],
            "keep",
        )

        assert recs[0].user_decision == "keep"
        assert recs[1].user_decision == "keep"
        assert _read_decision(db, "/a.jpg") == "keep"
        assert _read_decision(db, "/b.jpg") == "keep"



# ── remove_from_list (DB sync) ─────────────────────────────────────────────

class TestRemoveFromList:
    def test_remove_items_updates_db_when_manifest_loaded(self, tmp_path):
        """remove_items_from_list writes user_decision='removed' to SQLite."""
        from app.viewmodels.main_vm import MainVM
        from unittest.mock import MagicMock

        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}, {"source_path": "/b.jpg"}])
        rec_a = _rec("/a.jpg", group=1)
        rec_b = _rec("/b.jpg", group=1)
        vm = MainVM(MagicMock())
        vm.groups = [PhotoGroup(group_number=1, items=[rec_a, rec_b])]
        handler, _, _ = _make_handler(vm, str(db))

        handler.remove_items_from_list([{"type": "file", "path": "/a.jpg"}])

        assert _read_decision(db, "/a.jpg") == "removed"
        assert _read_decision(db, "/b.jpg") == ""

    def test_remove_items_noop_when_no_manifest(self, tmp_path):
        """remove_items_from_list does NOT write to DB when no manifest is loaded."""
        from app.viewmodels.main_vm import MainVM
        from unittest.mock import MagicMock

        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        rec_a = _rec("/a.jpg", group=1)
        vm = MainVM(MagicMock())
        vm.groups = [PhotoGroup(group_number=1, items=[rec_a])]
        # No manifest_path set — nothing to write to
        handler, _, _ = _make_handler(vm, manifest_path=None)

        handler.remove_items_from_list([{"type": "file", "path": "/a.jpg"}])

        # DB row unchanged (no manifest to write to)
        assert _read_decision(db, "/a.jpg") == ""

    def test_remove_group_marks_all_files_in_group(self, tmp_path):
        """Removing a whole group marks every file in that group as 'removed'."""
        from app.viewmodels.main_vm import MainVM
        from unittest.mock import MagicMock

        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}, {"source_path": "/b.jpg"}])
        rec_a = _rec("/a.jpg", group=5)
        rec_b = _rec("/b.jpg", group=5)
        vm = MainVM(MagicMock())
        vm.groups = [PhotoGroup(group_number=5, items=[rec_a, rec_b])]
        handler, _, _ = _make_handler(vm, str(db))

        handler.remove_items_from_list([{"type": "group", "group_number": 5}])

        assert _read_decision(db, "/a.jpg") == "removed"
        assert _read_decision(db, "/b.jpg") == "removed"

    def test_remove_via_toolbar_highlighted_updates_db(self, tmp_path):
        """remove_from_list_toolbar with highlighted items writes 'removed' to SQLite."""
        from app.viewmodels.main_vm import MainVM
        from unittest.mock import MagicMock

        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}, {"source_path": "/b.jpg"}])
        rec_a = _rec("/a.jpg", group=1)
        rec_b = _rec("/b.jpg", group=1)
        vm = MainVM(MagicMock())
        vm.groups = [PhotoGroup(group_number=1, items=[rec_a, rec_b])]
        handler, _, _ = _make_handler(vm, str(db))

        handler.remove_from_list_toolbar([{"type": "file", "path": "/a.jpg"}])

        assert _read_decision(db, "/a.jpg") == "removed"
        assert _read_decision(db, "/b.jpg") == ""

    @patch("PySide6.QtWidgets.QMessageBox.information")
    def test_no_items_shows_message(self, mock_info, tmp_path):
        recs = [_rec("/a.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))

        handler.remove_from_list_toolbar([])

        mock_info.assert_called_once()
        assert recs[0].user_decision == ""


# ── set_decision_to_highlighted ───────────────────────────────────────────────

class TestSetDecisionToHighlighted:
    def test_sets_decision_for_highlighted_files(self, tmp_path):
        recs = [_rec("/a.jpg"), _rec("/b.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}, {"source_path": "/b.jpg"}])
        hl_provider = lambda: [{"type": "file", "path": "/a.jpg"}]
        handler, _, _ = _make_handler(vm, str(db), highlighted_items=hl_provider)

        handler.set_decision_to_highlighted("keep")

        assert recs[0].user_decision == "keep"
        assert recs[1].user_decision == ""

    def test_updates_sqlite_for_highlighted_files(self, tmp_path):
        recs = [_rec("/a.jpg"), _rec("/b.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}, {"source_path": "/b.jpg"}])
        hl_provider = lambda: [{"type": "file", "path": "/a.jpg"}]
        handler, _, _ = _make_handler(vm, str(db), highlighted_items=hl_provider)

        handler.set_decision_to_highlighted("delete")

        assert _read_decision(db, "/a.jpg") == "delete"
        assert _read_decision(db, "/b.jpg") == ""

    @patch("PySide6.QtWidgets.QMessageBox.information")
    def test_skips_group_type_items(self, _mock, tmp_path):
        recs = [_rec("/a.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        hl_provider = lambda: [{"type": "group", "group_number": 1}]
        handler, _, _ = _make_handler(vm, str(db), highlighted_items=hl_provider)

        handler.set_decision_to_highlighted("delete")

        assert recs[0].user_decision == ""


# ── _get_record_field — Action field mapping ──────────────────────────────────

class TestGetRecordFieldActionMapping:
    """_get_record_field("Action", rec) must read user_decision, not action.

    The "Action" column in the tree (COL_ACTION=2) displays user_decision.
    Select/Unselect reads the visual tree model so it always matched correctly;
    Set Action uses _get_record_field which previously mapped to rec.action
    (scanner classification) — causing no matches for values like "delete".
    """

    def _make_rec(self, action: str, user_decision: str) -> PhotoRecord:
        return PhotoRecord(
            group_number=1,
            is_mark=False,
            is_locked=False,
            folder_path="/photos",
            file_path="/photos/a.jpg",
            capture_date=None,
            modified_date=None,
            file_size_bytes=1000,
            action=action,
            user_decision=user_decision,
        )

    def test_action_field_reads_user_decision(self):
        from app.views.handlers.file_operations import _get_record_field
        rec = self._make_rec(action="MOVE", user_decision="delete")
        assert _get_record_field(rec, "Action") == "delete"

    def test_action_field_does_not_read_scanner_action(self):
        from app.views.handlers.file_operations import _get_record_field
        rec = self._make_rec(action="MOVE", user_decision="delete")
        assert _get_record_field(rec, "Action") != "MOVE"

    def test_action_field_empty_when_undecided(self):
        from app.views.handlers.file_operations import _get_record_field
        rec = self._make_rec(action="REVIEW_DUPLICATE", user_decision="")
        # Empty user_decision returns None (falsy guard in _get_record_field)
        result = _get_record_field(rec, "Action")
        assert result == "" or result is None

    def test_set_decision_by_regex_matches_user_decision(self, tmp_path):
        """Regression: set_decision_by_regex with field=Action must match user_decision."""
        db = _make_db(tmp_path, [
            {"source_path": "/a.jpg", "action": "MOVE"},
            {"source_path": "/b.jpg", "action": "EXACT"},
        ])
        rec_a = _rec("/a.jpg", decision="delete")
        rec_a.action = "MOVE"
        rec_b = _rec("/b.jpg", decision="")
        rec_b.action = "EXACT"
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec_a, rec_b])])
        handler, _, _ = _make_handler(vm, str(db))

        # Set action="" (clear) for files where user_decision currently == "delete"
        handler.set_decision_by_regex("Action", "^delete$", "")

        assert rec_a.user_decision == "", "delete→'' clear should work"
        assert rec_b.user_decision == "", "undecided should be unchanged"

    @patch("PySide6.QtWidgets.QMessageBox.information")
    def test_no_manifest_noop(self, _mock, tmp_path):
        recs = [_rec("/a.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        hl_provider = lambda: [{"type": "file", "path": "/a.jpg"}]
        handler, ui_updater, _ = _make_handler(vm, manifest_path=None, highlighted_items=hl_provider)

        handler.set_decision_to_highlighted("delete")

        assert recs[0].user_decision == ""
        ui_updater.refresh_tree.assert_not_called()

    def test_no_highlighted_files_shows_message(self, tmp_path):
        recs = [_rec("/a.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        hl_provider = lambda: []
        handler, _, _ = _make_handler(vm, str(db), highlighted_items=hl_provider)

        with patch("PySide6.QtWidgets.QMessageBox.information"):
            handler.set_decision_to_highlighted("delete")

        assert recs[0].user_decision == ""

    def test_provider_with_get_selected_items_method(self, tmp_path):
        """highlighted_items_provider can be an object with get_selected_items()."""
        recs = [_rec("/a.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        provider = SimpleNamespace(get_selected_items=lambda: [{"type": "file", "path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db), highlighted_items=provider)

        handler.set_decision_to_highlighted("keep")

        assert recs[0].user_decision == "keep"


# ── save_manifest_decisions ───────────────────────────────────────────────────

class TestSaveManifestDecisions:
    @patch("PySide6.QtWidgets.QMessageBox.information")
    def test_saves_to_same_path_in_place(self, _mock, tmp_path):
        """Saving to the same file writes decisions without copying."""
        recs = [_rec("/a.jpg", decision="keep")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))

        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(str(db), "")):
            handler.save_manifest_decisions()

        assert _read_decision(db, "/a.jpg") == "keep"
        assert handler._manifest_path == str(db)

    @patch("PySide6.QtWidgets.QMessageBox.information")
    def test_saves_to_different_path_copies_and_writes(self, _mock, tmp_path):
        """Saving to a new path copies the source manifest and writes decisions."""
        recs = [_rec("/a.jpg", decision="delete")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        new_path = str(tmp_path / "exported.sqlite")
        handler, _, _ = _make_handler(vm, str(db))

        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=(new_path, "")):
            handler.save_manifest_decisions()

        assert _read_decision(Path(new_path), "/a.jpg") == "delete"
        assert handler._manifest_path == new_path

    def test_dialog_cancel_is_noop(self, tmp_path):
        """Cancelling the save dialog leaves the manifest unchanged."""
        recs = [_rec("/a.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))

        with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName", return_value=("", "")):
            handler.save_manifest_decisions()

        assert _read_decision(db, "/a.jpg") == ""

    def test_no_manifest_shows_message(self, tmp_path):
        """With no manifest open, shows an info dialog and returns."""
        vm = SimpleNamespace(groups=[])
        handler, _, _ = _make_handler(vm, manifest_path=None)

        with patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
            handler.save_manifest_decisions()

        mock_info.assert_called_once()


# ── batch SQL verification ─────────────────────────────────────────────────

class TestBatchSQLCalls:
    """Verify set_decision uses batch_update_decisions, not per-row updates."""

    def test_set_decision_calls_batch_update_once(self, tmp_path):
        recs = [_rec("/a.jpg"), _rec("/b.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}, {"source_path": "/b.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))

        with patch(
            "infrastructure.manifest_repository.ManifestRepository.batch_update_decisions"
        ) as mock_batch:
            handler.set_decision(
                [{"type": "file", "path": "/a.jpg"}, {"type": "file", "path": "/b.jpg"}],
                "delete",
            )

        mock_batch.assert_called_once()
        batch_arg = mock_batch.call_args[0][1]
        assert batch_arg == {"/a.jpg": "delete", "/b.jpg": "delete"}


# ── constructor ────────────────────────────────────────────────────────────

class TestConstructor:
    def test_handler_constructed_without_delete_service(self):
        """FileOperationsHandler no longer requires a delete_service argument."""
        from app.views.handlers.file_operations import FileOperationsHandler
        from types import SimpleNamespace
        vm = SimpleNamespace(groups=[])
        handler = FileOperationsHandler(
            vm=vm,
            settings=MagicMock(),
            parent_widget=MagicMock(),
            ui_updater=MagicMock(),
            status_reporter=MagicMock(),
        )
        assert handler is not None
        assert not hasattr(handler, "deleter")


# ── manifest-load callbacks ────────────────────────────────────────────────


class TestManifestLoadCallbacks:
    """Cover _on_manifest_loaded / _on_manifest_failed / _set_manifest_actions_enabled.

    These run as the worker's signals fire on the GUI thread; they're plain
    callables with simple signatures, so unit-test them directly with mocks.
    """

    def test_on_manifest_loaded_updates_vm_and_ui(self, tmp_path):
        from app.views.handlers.file_operations import FileOperationsHandler
        from types import SimpleNamespace

        vm = SimpleNamespace(groups=[], group_count=0)
        ui = MagicMock()
        status = MagicMock()
        parent = MagicMock()
        parent.menu_controller = MagicMock()
        handler = FileOperationsHandler(
            vm=vm, settings=MagicMock(),
            parent_widget=parent, ui_updater=ui, status_reporter=status,
        )

        groups = [
            PhotoGroup(group_number=1, items=[_rec("/a.jpg"), _rec("/b.jpg")]),
            PhotoGroup(group_number=2, items=[_rec("/c.jpg")]),
        ]
        # Stand in for the path the worker reports back.
        path = str(tmp_path / "m.sqlite")
        # group_count is read off the VM, not derived — wire it.
        vm.group_count = len(groups)

        handler._on_manifest_loaded(groups, path)

        assert vm.groups is groups
        assert handler._manifest_path == path
        ui.refresh_tree.assert_called_once_with(groups)
        ui.show_group_counts.assert_called_once_with(2)
        ui.show_groups_summary.assert_called_once_with(groups)
        # Status text mentions group count and total file count (3).
        status.show_status.assert_called_once()
        status_msg = status.show_status.call_args[0][0]
        assert "2" in status_msg and "3" in status_msg

    def test_on_manifest_failed_logs_and_disables_actions(self):
        from app.views.handlers.file_operations import FileOperationsHandler
        from types import SimpleNamespace

        vm = SimpleNamespace(groups=[])
        status = MagicMock()
        parent = MagicMock()
        parent.menu_controller = MagicMock()
        handler = FileOperationsHandler(
            vm=vm, settings=MagicMock(),
            parent_widget=parent, ui_updater=MagicMock(), status_reporter=status,
        )

        with patch("PySide6.QtWidgets.QMessageBox.critical") as crit:
            handler._on_manifest_failed("disk on fire")

        crit.assert_called_once()
        # Critical dialog body carries the error text.
        assert "disk on fire" in crit.call_args[0][2]
        # Status updated to a failure message.
        status.show_status.assert_called_once()
        # Manifest-dependent actions disabled.
        assert parent.menu_controller.enable_action.called
        for call in parent.menu_controller.enable_action.call_args_list:
            assert call[0][1] is False

    def test_set_manifest_actions_enabled_toggles_each_action(self):
        from app.views.handlers.file_operations import FileOperationsHandler
        from types import SimpleNamespace

        vm = SimpleNamespace(groups=[])
        parent = MagicMock()
        parent.menu_controller = MagicMock()
        handler = FileOperationsHandler(
            vm=vm, settings=MagicMock(), parent_widget=parent,
            ui_updater=MagicMock(), status_reporter=MagicMock(),
        )

        handler._set_manifest_actions_enabled(True)
        called = {c[0][0] for c in parent.menu_controller.enable_action.call_args_list}
        assert called == {
            "save_manifest", "execute_action",
            "set_action_hl_delete", "set_action_hl_keep",
        }
        for c in parent.menu_controller.enable_action.call_args_list:
            assert c[0][1] is True

    def test_set_manifest_actions_enabled_swallows_attribute_error(self):
        """The except AttributeError branch — parent without menu_controller still works."""
        from app.views.handlers.file_operations import FileOperationsHandler
        from types import SimpleNamespace

        vm = SimpleNamespace(groups=[])
        parent = MagicMock()
        # Make every enable_action call raise AttributeError so the except catches.
        parent.menu_controller.enable_action.side_effect = AttributeError("no such action")
        handler = FileOperationsHandler(
            vm=vm, settings=MagicMock(), parent_widget=parent,
            ui_updater=MagicMock(), status_reporter=MagicMock(),
        )

        # Must not raise.
        handler._set_manifest_actions_enabled(True)
