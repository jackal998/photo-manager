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
    user_decision    TEXT    NOT NULL DEFAULT '',
    is_locked        INTEGER NOT NULL DEFAULT 0
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


def _rec(
    path: str, group: int = 1, decision: str = "", locked: bool = False
) -> PhotoRecord:
    return PhotoRecord(
        group_number=group,
        is_mark=False,
        is_locked=locked,
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


def _read_locked(db: Path, path: str) -> bool:
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT is_locked FROM migration_manifest WHERE source_path = ?",
            (path,),
        ).fetchone()
    return bool(row[0]) if row else False


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


# ── remove-from-list lock guard (#208) ───────────────────────────────────────


class TestRemoveFromListLockGuard:
    """Lock guard on remove_items_from_list and remove_from_list_toolbar (#208).

    Locked files must surface LockedRowsConfirmDialog before removal,
    mirroring the guard already present in the execute-dialog remove path.
    """

    def _setup(self, tmp_path):
        from app.viewmodels.main_vm import MainVM
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}, {"source_path": "/b.jpg"}])
        rec_a = _rec("/a.jpg", locked=False)
        rec_b = _rec("/b.jpg", locked=True)
        vm = MainVM(MagicMock())
        vm.groups = [PhotoGroup(group_number=1, items=[rec_a, rec_b])]
        handler, _, _ = _make_handler(vm, str(db))
        return handler, rec_a, rec_b, db

    # -- remove_items_from_list -----------------------------------------------

    def test_cancel_does_not_remove_locked_file(self, tmp_path):
        from app.views.dialogs.locked_rows_confirm_dialog import LockedRowsConfirmDialog
        handler, rec_a, rec_b, db = self._setup(tmp_path)
        with patch.object(LockedRowsConfirmDialog, "ask", return_value=LockedRowsConfirmDialog.CANCEL):
            handler.remove_items_from_list([{"type": "file", "path": "/b.jpg"}])
        assert rec_b in handler.vm.groups[0].items
        assert _read_decision(db, "/b.jpg") == ""

    def test_apply_unlocked_only_skips_locked_file(self, tmp_path):
        from app.views.dialogs.locked_rows_confirm_dialog import LockedRowsConfirmDialog
        handler, rec_a, rec_b, db = self._setup(tmp_path)
        items = [
            {"type": "file", "path": "/a.jpg"},
            {"type": "file", "path": "/b.jpg"},
        ]
        with patch.object(
            LockedRowsConfirmDialog, "ask",
            return_value=LockedRowsConfirmDialog.APPLY_UNLOCKED_ONLY,
        ):
            handler.remove_items_from_list(items)
        remaining = [r.file_path for r in handler.vm.groups[0].items]
        assert "/a.jpg" not in remaining
        assert "/b.jpg" in remaining
        assert _read_decision(db, "/a.jpg") == "removed"
        assert _read_decision(db, "/b.jpg") == ""

    def test_apply_all_unlocked_unlocks_then_removes_all(self, tmp_path):
        from app.views.dialogs.locked_rows_confirm_dialog import LockedRowsConfirmDialog
        handler, rec_a, rec_b, db = self._setup(tmp_path)
        items = [
            {"type": "file", "path": "/a.jpg"},
            {"type": "file", "path": "/b.jpg"},
        ]
        with patch.object(
            LockedRowsConfirmDialog, "ask",
            return_value=LockedRowsConfirmDialog.APPLY_ALL_UNLOCKED,
        ):
            handler.remove_items_from_list(items)
        assert handler.vm.groups == []
        assert rec_b.is_locked is False
        assert _read_decision(db, "/a.jpg") == "removed"
        assert _read_decision(db, "/b.jpg") == "removed"

    def test_no_locked_items_skips_dialog(self, tmp_path):
        from app.views.dialogs.locked_rows_confirm_dialog import LockedRowsConfirmDialog
        handler, rec_a, rec_b, _ = self._setup(tmp_path)
        rec_b.is_locked = False  # no locked items
        with patch.object(LockedRowsConfirmDialog, "ask") as ask:
            handler.remove_items_from_list([{"type": "file", "path": "/a.jpg"}])
        ask.assert_not_called()

    # -- remove_from_list_toolbar ---------------------------------------------

    def test_toolbar_cancel_does_not_remove_locked_file(self, tmp_path):
        from app.views.dialogs.locked_rows_confirm_dialog import LockedRowsConfirmDialog
        handler, rec_a, rec_b, db = self._setup(tmp_path)
        with patch.object(LockedRowsConfirmDialog, "ask", return_value=LockedRowsConfirmDialog.CANCEL):
            handler.remove_from_list_toolbar([{"type": "file", "path": "/b.jpg"}])
        assert rec_b in handler.vm.groups[0].items

    def test_toolbar_apply_unlocked_only_skips_locked(self, tmp_path):
        from app.views.dialogs.locked_rows_confirm_dialog import LockedRowsConfirmDialog
        handler, rec_a, rec_b, db = self._setup(tmp_path)
        items = [
            {"type": "file", "path": "/a.jpg"},
            {"type": "file", "path": "/b.jpg"},
        ]
        with patch.object(
            LockedRowsConfirmDialog, "ask",
            return_value=LockedRowsConfirmDialog.APPLY_UNLOCKED_ONLY,
        ):
            handler.remove_from_list_toolbar(items)
        remaining = [r.file_path for r in handler.vm.groups[0].items]
        assert "/a.jpg" not in remaining
        assert "/b.jpg" in remaining

    def test_toolbar_apply_all_unlocked_removes_all(self, tmp_path):
        from app.views.dialogs.locked_rows_confirm_dialog import LockedRowsConfirmDialog
        handler, rec_a, rec_b, db = self._setup(tmp_path)
        items = [
            {"type": "file", "path": "/a.jpg"},
            {"type": "file", "path": "/b.jpg"},
        ]
        with patch.object(
            LockedRowsConfirmDialog, "ask",
            return_value=LockedRowsConfirmDialog.APPLY_ALL_UNLOCKED,
        ):
            handler.remove_from_list_toolbar(items)
        assert handler.vm.groups == []
        assert rec_b.is_locked is False

    def test_group_with_locked_item_expands_correctly(self, tmp_path):
        """Removing a group that contains a locked file surfaces the dialog
        with the group's file paths (not just the group item itself)."""
        from app.views.dialogs.locked_rows_confirm_dialog import LockedRowsConfirmDialog
        handler, rec_a, rec_b, db = self._setup(tmp_path)
        with patch.object(
            LockedRowsConfirmDialog, "ask",
            return_value=LockedRowsConfirmDialog.CANCEL,
        ) as ask:
            handler.remove_items_from_list([{"type": "group", "group_number": 1}])
        ask.assert_called_once()
        _, kwargs = ask.call_args
        assert kwargs["affected_count"] == 2
        assert "/b.jpg" in kwargs["locked_paths"]


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

    @patch("PySide6.QtWidgets.QMessageBox.information")
    def test_saves_when_source_has_uncheckpointed_wal(self, _mock, tmp_path):
        """Regression for #91: source manifest with uncheckpointed WAL writes
        must still produce a populated copy at the new path. Without the fix,
        shutil.copy2 captured only the empty 4KB main .sqlite and save()
        failed with 'no such table: migration_manifest'."""
        recs = [_rec("/a.jpg", decision="delete")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])

        db = tmp_path / "manifest.sqlite"
        # Build the manifest in WAL mode and KEEP the connection open —
        # mimics the scanner having just finished writing without checkpoint.
        src_conn = sqlite3.connect(str(db))
        try:
            src_conn.execute("PRAGMA journal_mode = WAL")
            src_conn.executescript(_DDL)
            src_conn.execute(
                "INSERT INTO migration_manifest (source_path, action) VALUES (?, ?)",
                ("/a.jpg", "MOVE"),
            )
            src_conn.commit()
            wal_path = Path(str(db) + "-wal")
            assert wal_path.exists() and wal_path.stat().st_size > 0, (
                "test setup: data should be in -wal sibling"
            )

            new_path = str(tmp_path / "exported.sqlite")
            handler, _, _ = _make_handler(vm, str(db))

            with patch(
                "PySide6.QtWidgets.QFileDialog.getSaveFileName",
                return_value=(new_path, ""),
            ):
                handler.save_manifest_decisions()

            assert _read_decision(Path(new_path), "/a.jpg") == "delete"
            assert handler._manifest_path == new_path
        finally:
            src_conn.close()

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

    @patch("PySide6.QtWidgets.QMessageBox.information")
    def test_dialog_uses_canonical_title(self, _mock, tmp_path):
        """The Save Manifest dialog must open with caption 'Save Manifest Decisions'.

        Drift in this literal at file_operations.py would silently break QA
        scenario s12 (which finds the dialog by exact title) and confuse users
        who see a renamed title. Asserts the call args of getSaveFileName so
        the title literal can't drift unnoticed (#129 — replacement coverage
        for s12 which cannot run on hosted Windows runners).
        """
        recs = [_rec("/a.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))

        with patch(
            "PySide6.QtWidgets.QFileDialog.getSaveFileName",
            return_value=("", ""),  # cancel; we only care about call args
        ) as mock_dlg:
            handler.save_manifest_decisions()

        mock_dlg.assert_called_once()
        title = mock_dlg.call_args.args[1]
        assert title == "Save Manifest Decisions", (
            f"dialog title drift: expected 'Save Manifest Decisions', got {title!r}"
        )


# ── load → decide → save round-trip ────────────────────────────────────────


class TestSaveManifestLoadRoundTrip:
    """Real sqlite -> ManifestRepository.load() -> save_manifest_decisions ->
    real sqlite. Catches drift between the load and save sides that synthetic
    SimpleNamespace fixtures miss — covers the end-to-end signal s12 catches
    on a local desktop but cannot run on CI due to the IFileSaveDialog
    limitation (#129)."""

    @staticmethod
    def _seed_grouped_manifest(tmp_path: Path) -> Path:
        """Build a real grouped manifest with two rows in one near-duplicate group.

        Mirrors the shape ManifestLoadWorker sees in production: one
        REVIEW_DUPLICATE row + one Ref-tier row (MOVE) sharing a group_id.
        Singletons would be filtered out by load(), so the pair is required.
        """
        from PIL import Image
        cand = tmp_path / "cand.jpg"
        ref = tmp_path / "ref.jpg"
        for p in (cand, ref):
            Image.new("RGB", (16, 16), (128, 64, 32)).save(p, "JPEG")
        db = tmp_path / "src.sqlite"
        with sqlite3.connect(db) as conn:
            conn.executescript(_DDL)
            gid = "/group/a"
            conn.execute(
                "INSERT INTO migration_manifest "
                "(source_path, source_label, action, hamming_distance, "
                "group_id, reason) VALUES (?, 'src', 'REVIEW_DUPLICATE', 5, ?, 'nd')",
                (str(cand), gid),
            )
            conn.execute(
                "INSERT INTO migration_manifest "
                "(source_path, source_label, action, group_id, reason) "
                "VALUES (?, 'src', 'MOVE', ?, 'unique')",
                (str(ref), gid),
            )
            conn.commit()
        return db

    @patch("PySide6.QtWidgets.QMessageBox.information")
    def test_load_decide_save_roundtrip(self, _mock, tmp_path):
        from collections import defaultdict
        from infrastructure.manifest_repository import ManifestRepository

        src_db = self._seed_grouped_manifest(tmp_path)

        # Load via the real repository — exercises auto-migration, group
        # filtering, and PhotoRecord construction from real DB rows.
        records = list(ManifestRepository().load(str(src_db)))
        assert len(records) == 2, (
            f"expected 2 grouped records from load(), got {len(records)}"
        )

        # Re-group by group_number so save sees real PhotoGroup objects
        # whose items came out of the load path (not hand-built fixtures).
        by_gn: dict[int, list] = defaultdict(list)
        for rec in records:
            by_gn[rec.group_number].append(rec)
        groups = [
            PhotoGroup(group_number=gn, items=items)
            for gn, items in by_gn.items()
        ]

        # Inject decisions on the loaded records.
        cand_rec = next(r for r in records if r.file_path.endswith("cand.jpg"))
        ref_rec = next(r for r in records if r.file_path.endswith("ref.jpg"))
        cand_rec.user_decision = "delete"
        ref_rec.user_decision = "keep"

        # Save to a NEW path via the real handler (dialog mocked).
        new_path = str(tmp_path / "exported.sqlite")
        vm = SimpleNamespace(groups=groups)
        handler, _, _ = _make_handler(vm, str(src_db))
        with patch(
            "PySide6.QtWidgets.QFileDialog.getSaveFileName",
            return_value=(new_path, ""),
        ):
            handler.save_manifest_decisions()

        # Verify the loaded -> decided -> saved chain preserved decisions.
        assert _read_decision(Path(new_path), cand_rec.file_path) == "delete"
        assert _read_decision(Path(new_path), ref_rec.file_path) == "keep"
        assert handler._manifest_path == new_path


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
        # Successful load updates the persistent baseline (#138, #140), not
        # a transient temp message that would disappear after a few seconds
        # or when the user opens a menu.
        status.set_baseline.assert_called_once()
        status_msg = status.set_baseline.call_args[0][0]
        assert "2" in status_msg and "3" in status_msg

    def test_on_manifest_failed_logs_and_disables_actions(self):
        """No prior manifest loaded → failure disables actions (first-load case)."""
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
        # No prior manifest — failure disables actions via the shared controller.
        parent.menu_controller.set_manifest_actions.assert_called_once_with(False)

    def test_on_manifest_failed_preserves_prior_manifest_actions(self, tmp_path):
        """#108: failed load with a prior manifest loaded leaves its actions enabled.

        Reproduces the bug from #108: user has manifest A loaded and clicks
        Open Manifest…, picks a corrupt file, the load fails. Before the fix,
        the failure callback unconditionally disabled actions, stranding the
        user's still-valid manifest A inaccessible. After the fix, actions
        stay enabled because A is still in memory.
        """
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
        # Pretend manifest A is already loaded.
        handler._manifest_path = str(tmp_path / "manifest_a.sqlite")

        with patch("PySide6.QtWidgets.QMessageBox.critical"):
            handler._on_manifest_failed("disk on fire")

        # Status still reports the failure.
        status.show_status.assert_called_once()
        # But actions are NOT toggled — A's enabled state is preserved.
        parent.menu_controller.set_manifest_actions.assert_not_called()

    def test_start_manifest_load_disables_actions_when_no_prior_manifest(self, tmp_path):
        """First-ever Open Manifest: optimistic disable while load is in flight."""
        from app.views.handlers.file_operations import FileOperationsHandler
        from types import SimpleNamespace

        vm = SimpleNamespace(groups=[], _default_sort=[])
        parent = MagicMock()
        parent.menu_controller = MagicMock()
        handler = FileOperationsHandler(
            vm=vm, settings=MagicMock(),
            parent_widget=parent, ui_updater=MagicMock(), status_reporter=MagicMock(),
        )

        with patch("app.views.workers.manifest_load_worker.ManifestLoadWorker") as worker_cls:
            worker_cls.return_value = MagicMock()
            handler._start_manifest_load(str(tmp_path / "new.sqlite"))

        # No prior manifest — actions get disabled while the worker runs.
        parent.menu_controller.set_manifest_actions.assert_called_once_with(False)

    def test_start_manifest_load_preserves_prior_manifest_actions(self, tmp_path):
        """#108: Open Manifest while A is loaded leaves A's actions enabled during the load.

        Without this gating, the user momentarily loses access to A's actions
        between picking B in the file dialog and B's worker firing finished /
        failed. If B fails, A's actions never come back (covered by the
        sibling _on_manifest_failed test above). If B succeeds, the flicker
        is at least visible. Either way, prior-loaded A should stay enabled.
        """
        from app.views.handlers.file_operations import FileOperationsHandler
        from types import SimpleNamespace

        vm = SimpleNamespace(groups=[], _default_sort=[])
        parent = MagicMock()
        parent.menu_controller = MagicMock()
        handler = FileOperationsHandler(
            vm=vm, settings=MagicMock(),
            parent_widget=parent, ui_updater=MagicMock(), status_reporter=MagicMock(),
        )
        # Pretend manifest A is already loaded.
        handler._manifest_path = str(tmp_path / "manifest_a.sqlite")

        with patch("app.views.workers.manifest_load_worker.ManifestLoadWorker") as worker_cls:
            worker_cls.return_value = MagicMock()
            handler._start_manifest_load(str(tmp_path / "manifest_b.sqlite"))

        # Prior manifest exists — start_manifest_load must NOT pre-emptively disable.
        parent.menu_controller.set_manifest_actions.assert_not_called()

    def test_set_manifest_actions_enabled_delegates_to_controller(self):
        """_set_manifest_actions_enabled forwards to MenuController.set_manifest_actions."""
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
        parent.menu_controller.set_manifest_actions.assert_called_once_with(True)

        parent.menu_controller.set_manifest_actions.reset_mock()
        handler._set_manifest_actions_enabled(False)
        parent.menu_controller.set_manifest_actions.assert_called_once_with(False)

    def test_set_manifest_actions_enabled_swallows_attribute_error(self):
        """If parent has no menu_controller, the helper must not raise."""
        from app.views.handlers.file_operations import FileOperationsHandler
        from types import SimpleNamespace

        vm = SimpleNamespace(groups=[])
        parent = MagicMock()
        # Make set_manifest_actions raise AttributeError so the except catches.
        parent.menu_controller.set_manifest_actions.side_effect = AttributeError("no controller")
        handler = FileOperationsHandler(
            vm=vm, settings=MagicMock(), parent_widget=parent,
            ui_updater=MagicMock(), status_reporter=MagicMock(),
        )

        # Must not raise.
        handler._set_manifest_actions_enabled(True)


# ── remove_*_from_list (toolbar + items) error branches ──────────────────


class TestRemoveFromListErrorBranches:
    """Cover the try/except trailers in remove_from_list_toolbar / remove_items_from_list."""

    def test_remove_from_list_toolbar_no_selection_shows_info(self):
        from app.views.handlers.file_operations import FileOperationsHandler

        vm = SimpleNamespace(groups=[])
        handler = FileOperationsHandler(
            vm=vm, settings=MagicMock(), parent_widget=MagicMock(),
            ui_updater=MagicMock(), status_reporter=MagicMock(),
        )
        with patch("PySide6.QtWidgets.QMessageBox.information") as info:
            handler.remove_from_list_toolbar([])
        info.assert_called_once()

    def test_remove_from_list_toolbar_exception_handled(self):
        """If vm.remove_from_list raises, surface as critical dialog, don't crash."""
        from app.views.handlers.file_operations import FileOperationsHandler

        vm = MagicMock()
        vm.groups = []
        vm.remove_from_list.side_effect = RuntimeError("vm broke")
        handler = FileOperationsHandler(
            vm=vm, settings=MagicMock(), parent_widget=MagicMock(),
            ui_updater=MagicMock(), status_reporter=MagicMock(),
        )
        with patch("PySide6.QtWidgets.QMessageBox.critical") as crit:
            handler.remove_from_list_toolbar(
                [{"type": "file", "path": "/a.jpg"}]
            )
        crit.assert_called_once()
        assert "Remove from list failed" in crit.call_args[0][2]

    def test_remove_items_from_list_exception_handled(self):
        from app.views.handlers.file_operations import FileOperationsHandler

        vm = MagicMock()
        vm.groups = []
        vm.remove_from_list.side_effect = RuntimeError("vm broke")
        handler = FileOperationsHandler(
            vm=vm, settings=MagicMock(), parent_widget=MagicMock(),
            ui_updater=MagicMock(), status_reporter=MagicMock(),
        )
        with patch("PySide6.QtWidgets.QMessageBox.critical") as crit:
            handler.remove_items_from_list(
                [{"type": "file", "path": "/a.jpg"}]
            )
        crit.assert_called_once()


# ── set_decision_by_regex (action-by-field/regex bulk apply) ──────────────


class TestSetDecisionByRegex:
    def test_no_manifest_loaded_shows_info(self):
        from app.views.handlers.file_operations import FileOperationsHandler

        vm = SimpleNamespace(groups=[])
        handler = FileOperationsHandler(
            vm=vm, settings=MagicMock(), parent_widget=MagicMock(),
            ui_updater=MagicMock(), status_reporter=MagicMock(),
        )
        with patch("PySide6.QtWidgets.QMessageBox.information") as info:
            handler.set_decision_by_regex("File Name", ".*", "delete")
        info.assert_called_once()

    def test_invalid_regex_warns_user(self, tmp_path):
        rec = _rec("/photos/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/photos/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))
        with patch("PySide6.QtWidgets.QMessageBox.warning") as warn:
            handler.set_decision_by_regex("File Name", "[unclosed", "delete")
        warn.assert_called_once()
        assert warn.call_args[0][1] == "Invalid Regex"

    def test_no_match_shows_info(self, tmp_path):
        rec = _rec("/photos/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/photos/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))
        with patch("PySide6.QtWidgets.QMessageBox.information") as info:
            handler.set_decision_by_regex("File Name", "wont_match_xyz", "delete")
        info.assert_called_once()
        assert "No files matched" in info.call_args[0][2]

    def test_matching_files_get_decision_set(self, tmp_path):
        rec_match = _rec("/photos/IMG_keep.jpg")
        rec_skip = _rec("/photos/other.jpg")
        vm = SimpleNamespace(groups=[
            PhotoGroup(group_number=1, items=[rec_match, rec_skip]),
        ])
        db = _make_db(tmp_path, [
            {"source_path": "/photos/IMG_keep.jpg"},
            {"source_path": "/photos/other.jpg"},
        ])
        handler, _, _ = _make_handler(vm, str(db))

        handler.set_decision_by_regex("File Name", r"IMG_keep", "keep")

        assert rec_match.user_decision == "keep"
        assert rec_skip.user_decision == ""


# ── set_decision_by_regex with REMOVE_FROM_LIST_SENTINEL ──────────────────


class TestSetDecisionByRegexRemoveFromList:
    """Regex 'remove from list' branch — deferred semantics.

    The bulk regex flow no longer removes rows immediately. Like
    bulk delete and bulk keep, it sets ``user_decision`` on every
    matched row and the user reviews + commits via Execute Action.
    No confirmation prompt fires (matches the delete/keep regex
    feel); single-row right-click in the execute dialog is the only
    path that still removes immediately, with its own confirm
    prompt (covered in test_execute_action_dialog).
    """

    def test_match_writes_remove_decision_no_immediate_drop(self, tmp_path):
        """Matched rows stay in vm.groups and have
        user_decision='remove_from_list' written. No prompt fires."""
        from app.views.constants import REMOVE_FROM_LIST_DECISION, REMOVE_FROM_LIST_SENTINEL
        from app.viewmodels.main_vm import MainVM

        db = _make_db(tmp_path, [
            {"source_path": "/photos/keep.jpg"},
            {"source_path": "/photos/match.jpg"},
        ])
        rec_keep = _rec("/photos/keep.jpg")
        rec_match = _rec("/photos/match.jpg")
        vm = MainVM(MagicMock())
        vm.groups = [PhotoGroup(group_number=1, items=[rec_keep, rec_match])]
        handler, ui_updater, _ = _make_handler(vm, str(db))

        with patch("PySide6.QtWidgets.QMessageBox.question") as q:
            handler.set_decision_by_regex(
                "File Name", r"^match", REMOVE_FROM_LIST_SENTINEL
            )

        # No prompt — deferred path matches delete/keep regex feel.
        q.assert_not_called()
        # Both rows still present in memory; only the matched one's
        # user_decision was changed.
        all_paths = [r.file_path for g in vm.groups for r in g.items]
        assert "/photos/match.jpg" in all_paths
        assert "/photos/keep.jpg" in all_paths
        assert rec_match.user_decision == REMOVE_FROM_LIST_DECISION
        assert rec_keep.user_decision == ""
        # SQLite reflects the same.
        assert _read_decision(db, "/photos/match.jpg") == REMOVE_FROM_LIST_DECISION
        assert _read_decision(db, "/photos/keep.jpg") == ""
        ui_updater.refresh_tree.assert_called()

    def test_remove_decision_marks_handler_dirty(self, tmp_path):
        """Setting the deferred remove decision must flip the dirty
        flag — the exit prompt depends on it (Item 2)."""
        from app.views.constants import REMOVE_FROM_LIST_SENTINEL
        from app.viewmodels.main_vm import MainVM

        db = _make_db(tmp_path, [{"source_path": "/photos/m.jpg"}])
        vm = MainVM(MagicMock())
        vm.groups = [PhotoGroup(group_number=1, items=[_rec("/photos/m.jpg")])]
        handler, _, _ = _make_handler(vm, str(db))
        assert handler.is_dirty() is False

        handler.set_decision_by_regex("File Name", r"^m", REMOVE_FROM_LIST_SENTINEL)
        assert handler.is_dirty() is True

    def test_bulk_regex_no_match_shows_info(self, tmp_path):
        """A pattern that matches nothing still shows the no-match info
        dialog (unchanged from prior behaviour)."""
        from app.views.constants import REMOVE_FROM_LIST_SENTINEL

        rec = _rec("/photos/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/photos/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))

        with patch("PySide6.QtWidgets.QMessageBox.information") as info, \
             patch("PySide6.QtWidgets.QMessageBox.question") as q:
            handler.set_decision_by_regex(
                "File Name", "wont_match", REMOVE_FROM_LIST_SENTINEL
            )

        info.assert_called_once()
        assert "No files matched" in info.call_args[0][2]
        q.assert_not_called()


# ── execute_action / save_manifest guards ─────────────────────────────────


class TestEntryPointGuards:
    def test_execute_action_no_manifest_shows_info(self):
        from app.views.handlers.file_operations import FileOperationsHandler

        vm = SimpleNamespace(groups=[])
        handler = FileOperationsHandler(
            vm=vm, settings=MagicMock(), parent_widget=MagicMock(),
            ui_updater=MagicMock(), status_reporter=MagicMock(),
        )
        with patch("PySide6.QtWidgets.QMessageBox.information") as info:
            handler.execute_action()
        info.assert_called_once()
        assert "No manifest loaded" in info.call_args[0][2]

    def test_save_manifest_no_manifest_shows_info(self):
        from app.views.handlers.file_operations import FileOperationsHandler

        vm = SimpleNamespace(groups=[])
        handler = FileOperationsHandler(
            vm=vm, settings=MagicMock(), parent_widget=MagicMock(),
            ui_updater=MagicMock(), status_reporter=MagicMock(),
        )
        with patch("PySide6.QtWidgets.QMessageBox.information") as info:
            handler.save_manifest_decisions()
        info.assert_called_once()
        assert "No manifest open" in info.call_args[0][2]


# ── Item 2 — dirty-tracking flag + silent save ─────────────────────────────


class TestDirtyTracking:
    """The is_dirty flag drives the exit prompt. False positives are
    acceptable (prompt fires when nothing actually changed); false
    negatives are not (close without prompting after real changes).
    These tests pin the transitions so a future regression doesn't
    silently un-flip the flag.
    """

    def test_initial_state_is_clean(self):
        from app.views.handlers.file_operations import FileOperationsHandler

        h = FileOperationsHandler(
            vm=MagicMock(), settings=MagicMock(), parent_widget=MagicMock(),
            ui_updater=MagicMock(), status_reporter=MagicMock(),
        )
        assert h.is_dirty() is False

    def test_set_decision_marks_dirty(self, tmp_path):
        rec = _rec("/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))
        assert handler.is_dirty() is False

        handler.set_decision([{"type": "file", "path": "/a.jpg"}], "delete")
        assert handler.is_dirty() is True

    def test_remove_items_marks_dirty(self, tmp_path):
        from app.viewmodels.main_vm import MainVM

        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        vm = MainVM(MagicMock())
        vm.groups = [PhotoGroup(group_number=1, items=[_rec("/a.jpg")])]
        handler, _, _ = _make_handler(vm, str(db))

        handler.remove_items_from_list([{"type": "file", "path": "/a.jpg"}])
        assert handler.is_dirty() is True

    def test_remove_from_toolbar_marks_dirty(self, tmp_path):
        from app.viewmodels.main_vm import MainVM

        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        vm = MainVM(MagicMock())
        vm.groups = [PhotoGroup(group_number=1, items=[_rec("/a.jpg")])]
        handler, _, _ = _make_handler(vm, str(db))

        handler.remove_from_list_toolbar([{"type": "file", "path": "/a.jpg"}])
        assert handler.is_dirty() is True

    def test_silent_save_clears_dirty(self, tmp_path):
        rec = _rec("/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))
        handler.set_decision([{"type": "file", "path": "/a.jpg"}], "delete")
        assert handler.is_dirty() is True

        ok = handler.save_manifest_decisions_silent()
        assert ok is True
        assert handler.is_dirty() is False

    def test_silent_save_returns_false_when_no_manifest(self):
        from app.views.handlers.file_operations import FileOperationsHandler

        h = FileOperationsHandler(
            vm=MagicMock(), settings=MagicMock(), parent_widget=MagicMock(),
            ui_updater=MagicMock(), status_reporter=MagicMock(),
        )
        # No manifest_path → can't save anywhere; returns False, leaves
        # dirty alone (caller decides whether to abort the close).
        h._mark_dirty()
        assert h.save_manifest_decisions_silent() is False
        assert h.is_dirty() is True

    def test_manifest_load_clears_dirty(self, tmp_path):
        from app.viewmodels.main_vm import MainVM

        vm = MainVM(MagicMock())
        vm.groups = [PhotoGroup(group_number=1, items=[_rec("/a.jpg")])]
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))
        # Simulate prior in-session changes.
        handler._mark_dirty()
        assert handler.is_dirty() is True

        # _on_manifest_loaded resets vm.groups and the dirty flag.
        handler._on_manifest_loaded([], "/some/new/path.sqlite")
        assert handler.is_dirty() is False

    def test_full_save_clears_dirty(self, tmp_path):
        """The interactive Save Manifest Decisions… path also marks
        clean on success (via the existing save_manifest_decisions
        method). Using mock for the QFileDialog so the test runs
        offscreen."""
        rec = _rec("/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))
        handler._mark_dirty()

        save_path = str(tmp_path / "saved.sqlite")
        with patch(
            "PySide6.QtWidgets.QFileDialog.getSaveFileName",
            return_value=(save_path, ""),
        ):
            handler.save_manifest_decisions()
        assert handler.is_dirty() is False


# ── build_match_fn (powers ActionDialog live preview) ──────────────────────

class TestBuildMatchFn:
    """The closure must agree byte-for-byte with set_decision_by_regex.

    These tests pin its contract: same field map, same case-insensitive
    flag, same skip-on-None-field semantics. If they drift, the dialog's
    preview will lie to the user about what Apply will do.
    """

    def _rec_with_folder(self, path: str, folder: str = "/photos") -> PhotoRecord:
        rec = _rec(path)
        rec.folder_path = folder
        return rec

    def test_returns_matched_total_samples(self):
        from app.views.handlers.file_operations import build_match_fn

        recs = [
            _rec("/photos/IMG_001.jpg"),
            _rec("/photos/IMG_002.jpg"),
            _rec("/photos/note.txt"),
        ]
        groups = [PhotoGroup(group_number=1, items=recs)]

        match_fn = build_match_fn(groups)
        matched, total, samples = match_fn("File Name", r"^IMG_\d+\.jpg$")

        assert matched == 2
        assert total == 3
        assert samples == ["IMG_001.jpg", "IMG_002.jpg"]

    def test_invalid_regex_returns_zero_with_total(self):
        """A live-preview must not crash on a partial regex; it returns
        zero matches and lets the dialog's validation row show why."""
        from app.views.handlers.file_operations import build_match_fn

        recs = [_rec("/a.jpg"), _rec("/b.jpg")]
        groups = [PhotoGroup(group_number=1, items=recs)]

        match_fn = build_match_fn(groups)
        matched, total, samples = match_fn("File Name", "(unclosed")

        assert matched == 0
        assert total == 2
        assert samples == []

    def test_sample_cap_truncates_but_count_is_full(self):
        """Sample collection stops at sample_cap so the preview list
        stays bounded; matched count must still be the true total."""
        from app.views.handlers.file_operations import build_match_fn

        recs = [_rec(f"/dir/file_{i:03d}.jpg") for i in range(100)]
        groups = [PhotoGroup(group_number=1, items=recs)]

        match_fn = build_match_fn(groups, sample_cap=50)
        matched, total, samples = match_fn("File Name", r"\.jpg$")

        assert matched == 100
        assert total == 100
        assert len(samples) == 50

    def test_uses_get_record_field_for_basename(self):
        """File Name field must extract basename, not the full path —
        otherwise users can't write `^IMG` to anchor at the filename
        (the path starts with `/photos/`)."""
        from app.views.handlers.file_operations import build_match_fn

        recs = [_rec("/photos/IMG_x.jpg"), _rec("/photos/note.txt")]
        groups = [PhotoGroup(group_number=1, items=recs)]

        match_fn = build_match_fn(groups)
        matched, _total, _samples = match_fn("File Name", r"^IMG")

        assert matched == 1

    def test_folder_field_returns_folder(self):
        """And Folder must NOT use the basename — pinning the field-map
        routing matches what set_decision_by_regex does."""
        from app.views.handlers.file_operations import build_match_fn

        recs = [
            self._rec_with_folder("/a.jpg", folder="/photos/2023"),
            self._rec_with_folder("/b.jpg", folder="/photos/2024"),
        ]
        groups = [PhotoGroup(group_number=1, items=recs)]

        match_fn = build_match_fn(groups)
        matched, _, samples = match_fn("Folder", r"2023$")

        assert matched == 1
        # Sample is the basename of the matching record's file_path,
        # not the folder string — so users see WHICH file matched.
        assert samples == ["a.jpg"]

    def test_unmapped_field_skips_without_match(self):
        """Group Count / Similarity have no _FIELD_TO_ATTR entry — they
        cannot match any regex, but records still count toward total."""
        from app.views.handlers.file_operations import build_match_fn

        recs = [_rec("/a.jpg"), _rec("/b.jpg")]
        groups = [PhotoGroup(group_number=1, items=recs)]

        match_fn = build_match_fn(groups)
        matched, total, samples = match_fn("Group Count", r".*")

        assert matched == 0
        assert total == 2
        assert samples == []

    def test_empty_groups(self):
        from app.views.handlers.file_operations import build_match_fn

        match_fn = build_match_fn([])
        matched, total, samples = match_fn("File Name", r".*")

        assert matched == 0
        assert total == 0
        assert samples == []

    def test_case_insensitive(self):
        """Must match set_decision_by_regex's re.IGNORECASE flag —
        otherwise the preview undercounts vs. what Apply will do."""
        from app.views.handlers.file_operations import build_match_fn

        recs = [_rec("/photos/IMG_001.JPG"), _rec("/photos/img_002.jpg")]
        groups = [PhotoGroup(group_number=1, items=recs)]

        match_fn = build_match_fn(groups)
        matched, _total, _samples = match_fn("File Name", r"\.jpg$")

        assert matched == 2


# ---------------------------------------------------------------------------
# Lock state — set_locked_state, regex lock action, skip-locked (photo-manager#164)
# ---------------------------------------------------------------------------

class TestSetLockedState:
    """``set_locked_state`` flips the orthogonal ``is_locked`` flag in
    memory and SQLite. Single-row right-click goes through here directly,
    so it must NOT skip locked rows — only bulk paths skip."""

    def test_locks_in_memory(self, tmp_path):
        rec = _rec("/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))
        handler.set_locked_state([{"type": "file", "path": "/a.jpg"}], True)
        assert rec.is_locked is True

    def test_locks_in_sqlite(self, tmp_path):
        rec = _rec("/a.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))
        handler.set_locked_state([{"type": "file", "path": "/a.jpg"}], True)
        assert _read_locked(db, "/a.jpg") is True

    def test_unlocks(self, tmp_path):
        rec = _rec("/a.jpg", locked=True)
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))
        # Pre-set DB lock to mirror the in-memory state
        handler.set_locked_state([{"type": "file", "path": "/a.jpg"}], True)
        assert _read_locked(db, "/a.jpg") is True
        handler.set_locked_state([{"type": "file", "path": "/a.jpg"}], False)
        assert rec.is_locked is False
        assert _read_locked(db, "/a.jpg") is False

    def test_idempotent_relock(self, tmp_path):
        """Locking an already-locked row is a no-op (no error, same state)."""
        rec = _rec("/a.jpg", locked=True)
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))
        handler.set_locked_state([{"type": "file", "path": "/a.jpg"}], True)
        handler.set_locked_state([{"type": "file", "path": "/a.jpg"}], True)
        assert rec.is_locked is True


class TestSetDecisionIsSilentDispatcher:
    """``set_decision`` is the low-level silent dispatcher. It does NOT
    check locks — that's the job of :meth:`set_decision_with_lock_check`
    and its callers (single-row right-click, bulk regex, bulk
    multi-select). Pinning the silent contract here so the wrapper
    can be refactored without breaking the underlying primitive.
    See photo-manager#182.
    """

    def test_set_decision_writes_decision_regardless_of_lock(self, tmp_path):
        rec = _rec("/locked.jpg", locked=True)
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[rec])])
        db = _make_db(tmp_path, [{"source_path": "/locked.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))

        # Direct call — bypasses the lock-confirm wrapper. The wrapper
        # is what the real call sites use; this test pins the primitive.
        handler.set_decision([{"type": "file", "path": "/locked.jpg"}], "delete")

        assert rec.user_decision == "delete"
        assert _read_decision(db, "/locked.jpg") == "delete"


class TestSetDecisionByRegexLockConfirm:
    """Bulk regex with a destructive decision routes through the
    LockedRowsConfirmDialog when any matched row is locked (#182).
    Each verdict (Unlock & Apply All / Apply to Unlocked Only / Cancel)
    drives a different outcome. Lock/unlock sentinels short-circuit
    the dialog and stay idempotent."""

    def _setup_mixed(self, tmp_path):
        unlocked = _rec("/free.jpg")
        locked = _rec("/pinned.jpg", locked=True)
        vm = SimpleNamespace(groups=[PhotoGroup(
            group_number=1, items=[unlocked, locked])])
        db = _make_db(tmp_path, [
            {"source_path": "/free.jpg"},
            {"source_path": "/pinned.jpg"},
        ])
        handler, _, _ = _make_handler(vm, str(db))
        return handler, vm, db, unlocked, locked

    def test_apply_unlocked_only_writes_only_to_unlocked(self, tmp_path):
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        handler, _, db, unlocked, locked = self._setup_mixed(tmp_path)

        with patch.object(
            LockedRowsConfirmDialog,
            "ask",
            return_value=LockedRowsConfirmDialog.APPLY_UNLOCKED_ONLY,
        ):
            handler.set_decision_by_regex("File Name", r"\.jpg$", "delete")

        assert unlocked.user_decision == "delete"
        assert locked.user_decision == ""
        assert locked.is_locked is True  # lock not flipped
        assert _read_decision(db, "/free.jpg") == "delete"
        assert _read_decision(db, "/pinned.jpg") == ""

    def test_apply_all_unlocked_unlocks_then_applies(self, tmp_path):
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        handler, _, db, unlocked, locked = self._setup_mixed(tmp_path)

        with patch.object(
            LockedRowsConfirmDialog,
            "ask",
            return_value=LockedRowsConfirmDialog.APPLY_ALL_UNLOCKED,
        ):
            handler.set_decision_by_regex("File Name", r"\.jpg$", "delete")

        assert unlocked.user_decision == "delete"
        assert locked.user_decision == "delete"
        assert locked.is_locked is False  # unlocked as part of the action
        assert _read_decision(db, "/free.jpg") == "delete"
        assert _read_decision(db, "/pinned.jpg") == "delete"

    def test_cancel_changes_nothing(self, tmp_path):
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        handler, _, db, unlocked, locked = self._setup_mixed(tmp_path)

        with patch.object(
            LockedRowsConfirmDialog,
            "ask",
            return_value=LockedRowsConfirmDialog.CANCEL,
        ):
            handler.set_decision_by_regex("File Name", r"\.jpg$", "delete")

        assert unlocked.user_decision == ""
        assert locked.user_decision == ""
        assert locked.is_locked is True
        assert _read_decision(db, "/free.jpg") == ""
        assert _read_decision(db, "/pinned.jpg") == ""

    def test_all_locked_dialog_still_offers_unlock_apply(self, tmp_path):
        """Degenerate case: every matched row is locked. The dialog
        is still shown so the user can choose Unlock & Apply All or
        Cancel; the 'Apply to Unlocked Only' button is disabled at
        construction (covered in test_locked_rows_confirm_dialog).
        Here we just verify the call site reaches the dialog and
        respects an Unlock & Apply verdict."""
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )

        a = _rec("/pinned_a.jpg", locked=True)
        b = _rec("/pinned_b.jpg", locked=True)
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[a, b])])
        db = _make_db(tmp_path, [
            {"source_path": "/pinned_a.jpg"},
            {"source_path": "/pinned_b.jpg"},
        ])
        handler, _, _ = _make_handler(vm, str(db))

        with patch.object(
            LockedRowsConfirmDialog,
            "ask",
            return_value=LockedRowsConfirmDialog.APPLY_ALL_UNLOCKED,
        ) as ask:
            handler.set_decision_by_regex("File Name", r"\.jpg$", "delete")
            ask.assert_called_once()

        assert a.user_decision == "delete"
        assert b.user_decision == "delete"
        assert a.is_locked is False
        assert b.is_locked is False

    def test_no_locked_rows_no_dialog(self, tmp_path):
        """Fast path: when nothing is locked, the dialog never opens
        and the bulk apply runs directly (today's behavior preserved
        for the common case)."""
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )

        a = _rec("/a.jpg")
        b = _rec("/b.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[a, b])])
        db = _make_db(tmp_path, [
            {"source_path": "/a.jpg"},
            {"source_path": "/b.jpg"},
        ])
        handler, _, _ = _make_handler(vm, str(db))

        with patch.object(LockedRowsConfirmDialog, "ask") as ask:
            handler.set_decision_by_regex("File Name", r"\.jpg$", "delete")
            ask.assert_not_called()

        assert a.user_decision == "delete"
        assert b.user_decision == "delete"

    def test_lock_regex_action_locks_all_matched_idempotently(self, tmp_path):
        """LOCK_SENTINEL applies to all matched rows including already-
        locked ones (no skip-filter on this branch)."""
        from app.views.constants import LOCK_SENTINEL

        already = _rec("/already_locked.jpg", locked=True)
        fresh = _rec("/fresh.jpg")
        vm = SimpleNamespace(groups=[PhotoGroup(
            group_number=1, items=[already, fresh])])
        db = _make_db(tmp_path, [
            {"source_path": "/already_locked.jpg"},
            {"source_path": "/fresh.jpg"},
        ])
        handler, _, _ = _make_handler(vm, str(db))

        handler.set_decision_by_regex("File Name", r"\.jpg$", LOCK_SENTINEL)

        assert already.is_locked is True
        assert fresh.is_locked is True
        assert _read_locked(db, "/already_locked.jpg") is True
        assert _read_locked(db, "/fresh.jpg") is True

    def test_unlock_regex_action_unlocks_all_matched(self, tmp_path):
        """UNLOCK_SENTINEL is the bulk escape hatch for the user who
        locked too aggressively earlier."""
        from app.views.constants import UNLOCK_SENTINEL

        a = _rec("/a.jpg", locked=True)
        b = _rec("/b.jpg", locked=True)
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=[a, b])])
        db = _make_db(tmp_path, [
            {"source_path": "/a.jpg"},
            {"source_path": "/b.jpg"},
        ])
        handler, _, _ = _make_handler(vm, str(db))
        # Persist the initial locked state so the unlock has something to flip
        handler.set_locked_state(
            [{"type": "file", "path": "/a.jpg"}, {"type": "file", "path": "/b.jpg"}],
            True,
        )

        handler.set_decision_by_regex("File Name", r"\.jpg$", UNLOCK_SENTINEL)

        assert a.is_locked is False
        assert b.is_locked is False
        assert _read_locked(db, "/a.jpg") is False
        assert _read_locked(db, "/b.jpg") is False


# ── apply_best_copy_to_group (#187 PR 6) ──────────────────────────────────


def _scored_rec(
    path: str,
    *,
    score: float | None,
    group: int = 1,
    locked: bool = False,
    decision: str = "",
) -> PhotoRecord:
    """PhotoRecord factory carrying a score for the best-copy tests."""
    rec = _rec(path, group=group, decision=decision, locked=locked)
    rec.score = score
    return rec


class TestApplyBestCopyToGroup:
    """End-to-end exercise of FileOperationsHandler.apply_best_copy_to_group.

    The action mutates ``vm.groups`` in place AND writes the resolved
    KEEP/DELETE decisions to SQLite via batch_update_decisions. Locked
    rows are silently protected; Live Photo MOV passengers (score=None)
    are skipped. All paths use a real on-disk SQLite manifest so the
    batched UPDATE actually hits the DB — this catches a regression
    where the in-memory mutation works but the DB write is missing.
    """

    def test_highest_score_wins_keep_others_get_delete(self, tmp_path):
        big = _scored_rec("/x/big.jpg", score=0.9)
        mid = _scored_rec("/x/mid.jpg", score=0.6)
        low = _scored_rec("/x/low.jpg", score=0.3)
        vm = MagicMock()
        vm.groups = [PhotoGroup(group_number=1, items=[big, mid, low])]
        db = _make_db(tmp_path, [
            {"source_path": "/x/big.jpg"},
            {"source_path": "/x/mid.jpg"},
            {"source_path": "/x/low.jpg"},
        ])
        handler, _, _ = _make_handler(vm, str(db))

        handler.apply_best_copy_to_group(1)

        # In-memory: winner keep, others delete
        assert big.user_decision == ""
        assert mid.user_decision == "delete"
        assert low.user_decision == "delete"
        # SQLite round-trip
        assert _read_decision(db, "/x/big.jpg") == ""
        assert _read_decision(db, "/x/mid.jpg") == "delete"
        assert _read_decision(db, "/x/low.jpg") == "delete"

    def test_locked_rows_are_silently_protected(self, tmp_path):
        """A locked row in the group keeps its prior decision regardless
        of score — that's the whole point of the lock flag. The
        highest-scoring NON-locked row becomes the winner."""
        locked_top = _scored_rec(
            "/x/locked.jpg", score=0.95, locked=True, decision="delete"
        )
        unlocked_top = _scored_rec("/x/unlocked_a.jpg", score=0.80)
        unlocked_bot = _scored_rec("/x/unlocked_b.jpg", score=0.40)
        vm = MagicMock()
        vm.groups = [PhotoGroup(
            group_number=1,
            items=[locked_top, unlocked_top, unlocked_bot],
        )]
        db = _make_db(tmp_path, [
            {"source_path": "/x/locked.jpg"},
            {"source_path": "/x/unlocked_a.jpg"},
            {"source_path": "/x/unlocked_b.jpg"},
        ])
        handler, _, _ = _make_handler(vm, str(db))

        handler.apply_best_copy_to_group(1)

        # Locked row's in-memory decision is unchanged (action never
        # mutates it even though it has the highest score).
        assert locked_top.user_decision == "delete"
        # DB row was never written by this action — it retains the
        # initial value from _make_db ("" since _make_db doesn't seed
        # decisions). The contract is "no write occurred for the locked
        # path," and the initial-state value proves that.
        assert _read_decision(db, "/x/locked.jpg") == ""
        # Winner among the unlocked candidates:
        assert unlocked_top.user_decision == ""
        assert unlocked_bot.user_decision == "delete"
        # And the unlocked rows DID get the DB write.
        assert _read_decision(db, "/x/unlocked_a.jpg") == ""
        assert _read_decision(db, "/x/unlocked_b.jpg") == "delete"

    def test_mov_passenger_score_none_is_skipped(self, tmp_path):
        """Live Photo MOV passenger (score=None) is excluded from
        candidacy. The HEIC sibling drives the decision; the MOV
        inherits via pair-cluster logic, not this action."""
        heic = _scored_rec("/x/IMG_001.heic", score=0.85)
        mov_passenger = _scored_rec("/x/IMG_001.mov", score=None)
        vm = MagicMock()
        vm.groups = [PhotoGroup(group_number=1, items=[heic, mov_passenger])]
        db = _make_db(tmp_path, [
            {"source_path": "/x/IMG_001.heic"},
            {"source_path": "/x/IMG_001.mov"},
        ])
        handler, _, _ = _make_handler(vm, str(db))

        handler.apply_best_copy_to_group(1)

        # HEIC: only scored candidate → wins KEEP.
        assert heic.user_decision == ""
        # MOV passenger: untouched. user_decision stays at its initial "".
        assert mov_passenger.user_decision == ""

    def test_no_candidates_emits_status_no_db_change(self, tmp_path):
        """Group where every row is locked OR has score=None: the action
        is a no-op (no DB write, no decision mutation) and surfaces a
        quiet status message. Old manifests pre-#187 hit this path —
        a re-scan recovers."""
        locked = _scored_rec("/x/a.jpg", score=0.5, locked=True)
        unscored = _scored_rec("/x/b.jpg", score=None)
        vm = MagicMock()
        vm.groups = [PhotoGroup(group_number=1, items=[locked, unscored])]
        db = _make_db(tmp_path, [
            {"source_path": "/x/a.jpg"},
            {"source_path": "/x/b.jpg"},
        ])
        handler, _, status_reporter = _make_handler(vm, str(db))

        handler.apply_best_copy_to_group(1)

        # No decisions changed
        assert locked.user_decision == ""
        assert unscored.user_decision == ""
        assert _read_decision(db, "/x/a.jpg") == ""
        assert _read_decision(db, "/x/b.jpg") == ""
        # Status reporter got a message — verifying the user knows the
        # action ran but had nothing to do.
        status_reporter.show_status.assert_called_once()

    def test_unknown_group_number_silent_noop(self, tmp_path):
        """Defensive: a stale group_number from a re-grouped tree must
        not crash. PR 6's UX click-target is the group row, but the
        view-model could have re-grouped between menu open and click
        (e.g. concurrent remove-from-list)."""
        rec = _scored_rec("/x/a.jpg", score=0.5)
        vm = MagicMock()
        vm.groups = [PhotoGroup(group_number=1, items=[rec])]
        db = _make_db(tmp_path, [{"source_path": "/x/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db))

        # Group 999 doesn't exist — must not raise.
        handler.apply_best_copy_to_group(999)
        assert rec.user_decision == ""

    def test_no_manifest_path_returns_early(self, tmp_path):
        """Without a manifest path the action cannot write — early return
        prevents an exception cascade and keeps in-memory state clean."""
        rec = _scored_rec("/x/a.jpg", score=0.5)
        vm = MagicMock()
        vm.groups = [PhotoGroup(group_number=1, items=[rec])]
        handler, _, _ = _make_handler(vm, manifest_path=None)
        handler.apply_best_copy_to_group(1)
        assert rec.user_decision == ""  # unchanged

    def test_tie_break_is_deterministic_by_path(self, tmp_path):
        """When two rows tie on score, the path-lexicographic tie-break
        produces a stable result. Re-running the action must pick the
        same winner — otherwise users would see flapping decisions on
        consecutive clicks."""
        a = _scored_rec("/x/aa.jpg", score=0.8)
        b = _scored_rec("/x/bb.jpg", score=0.8)
        vm = MagicMock()
        vm.groups = [PhotoGroup(group_number=1, items=[b, a])]  # order shouldn't matter
        db = _make_db(tmp_path, [
            {"source_path": "/x/aa.jpg"},
            {"source_path": "/x/bb.jpg"},
        ])
        handler, _, _ = _make_handler(vm, str(db))

        handler.apply_best_copy_to_group(1)

        # /x/bb.jpg > /x/aa.jpg lexicographically; max-with-key picks bb.
        # The contract isn't WHICH one wins — only that it's deterministic.
        # Re-running with a fresh handler produces the same result.
        winner1 = "/x/bb.jpg" if b.user_decision == "" else "/x/aa.jpg"

        # Reset and re-run on fresh records to verify determinism.
        a2 = _scored_rec("/x/aa.jpg", score=0.8)
        b2 = _scored_rec("/x/bb.jpg", score=0.8)
        vm.groups = [PhotoGroup(group_number=1, items=[a2, b2])]  # different order
        handler2, _, _ = _make_handler(vm, str(db))
        handler2.apply_best_copy_to_group(1)
        winner2 = "/x/bb.jpg" if b2.user_decision == "" else "/x/aa.jpg"
        assert winner1 == winner2
