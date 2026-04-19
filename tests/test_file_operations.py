"""Tests for FileOperationsHandler.set_decision and batch_set_decision.

These tests exercise the set_decision/batch_set_decision workflow against a
real SQLite manifest DB, using mocks for Qt widgets and the VM layer.
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
    hamming_distance INTEGER,
    duplicate_of     TEXT,
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


def _make_handler(vm, manifest_path: str | None, checked_paths=None):
    """Build a FileOperationsHandler with all Qt deps mocked."""
    from app.views.handlers.file_operations import FileOperationsHandler

    ui_updater = MagicMock()
    status_reporter = MagicMock()
    parent = MagicMock()
    parent.menu_controller = MagicMock()

    handler = FileOperationsHandler(
        vm=vm,
        repo=MagicMock(),
        delete_service=MagicMock(),
        settings=MagicMock(),
        parent_widget=parent,
        ui_updater=ui_updater,
        status_reporter=status_reporter,
        checked_paths_provider=checked_paths,
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


# ── batch_set_decision ─────────────────────────────────────────────────────

class TestBatchSetDecision:
    def test_sets_decision_for_checked_paths(self, tmp_path):
        recs = [_rec("/a.jpg"), _rec("/b.jpg"), _rec("/c.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [
            {"source_path": "/a.jpg"},
            {"source_path": "/b.jpg"},
            {"source_path": "/c.jpg"},
        ])
        handler, _, _ = _make_handler(vm, str(db), checked_paths=lambda: ["/a.jpg", "/c.jpg"])

        handler.batch_set_decision("delete")

        assert recs[0].user_decision == "delete"
        assert recs[1].user_decision == ""      # unchecked — untouched
        assert recs[2].user_decision == "delete"

    def test_updates_sqlite_for_checked_paths(self, tmp_path):
        recs = [_rec("/a.jpg"), _rec("/b.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}, {"source_path": "/b.jpg"}])
        handler, _, _ = _make_handler(vm, str(db), checked_paths=lambda: ["/a.jpg"])

        handler.batch_set_decision("keep")

        assert _read_decision(db, "/a.jpg") == "keep"
        assert _read_decision(db, "/b.jpg") == ""

    def test_refreshes_tree(self, tmp_path):
        recs = [_rec("/a.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, ui_updater, _ = _make_handler(vm, str(db), checked_paths=lambda: ["/a.jpg"])

        handler.batch_set_decision("delete")

        ui_updater.refresh_tree.assert_called_once()

    def test_reports_count_in_status(self, tmp_path):
        recs = [_rec("/a.jpg"), _rec("/b.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}, {"source_path": "/b.jpg"}])
        handler, _, status_reporter = _make_handler(
            vm, str(db), checked_paths=lambda: ["/a.jpg", "/b.jpg"]
        )

        handler.batch_set_decision("delete")

        msg = status_reporter.show_status.call_args[0][0]
        assert "2" in msg

    @patch("PySide6.QtWidgets.QMessageBox.information")
    def test_no_manifest_shows_message(self, mock_info, tmp_path):
        vm = SimpleNamespace(groups=[])
        handler, _, _ = _make_handler(vm, manifest_path=None)

        handler.batch_set_decision("delete")


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
        # No manifest_path set — CSV workflow
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

    def test_remove_via_toolbar_checked_paths_updates_db(self, tmp_path):
        """remove_from_list_toolbar with checked_paths writes 'removed' to SQLite."""
        from app.viewmodels.main_vm import MainVM
        from unittest.mock import MagicMock

        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}, {"source_path": "/b.jpg"}])
        rec_a = _rec("/a.jpg", group=1)
        rec_b = _rec("/b.jpg", group=1)
        vm = MainVM(MagicMock())
        vm.groups = [PhotoGroup(group_number=1, items=[rec_a, rec_b])]
        handler, _, _ = _make_handler(vm, str(db))

        handler.remove_from_list_toolbar(checked_paths=["/a.jpg"], highlighted_items=[])

        assert _read_decision(db, "/a.jpg") == "removed"
        assert _read_decision(db, "/b.jpg") == ""

    @patch("PySide6.QtWidgets.QMessageBox.information")
    def test_no_checked_paths_shows_message(self, mock_info, tmp_path):
        recs = [_rec("/a.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])
        handler, _, _ = _make_handler(vm, str(db), checked_paths=lambda: [])

        handler.batch_set_decision("delete")

        mock_info.assert_called_once()
        assert recs[0].user_decision == ""

    def test_works_across_multiple_groups(self, tmp_path):
        recs_g1 = [_rec("/a.jpg", group=1), _rec("/b.jpg", group=1)]
        recs_g2 = [_rec("/c.jpg", group=2)]
        vm = SimpleNamespace(groups=[
            PhotoGroup(group_number=1, items=recs_g1),
            PhotoGroup(group_number=2, items=recs_g2),
        ])
        db = _make_db(tmp_path, [
            {"source_path": "/a.jpg"},
            {"source_path": "/b.jpg"},
            {"source_path": "/c.jpg"},
        ])
        handler, _, _ = _make_handler(
            vm, str(db), checked_paths=lambda: ["/a.jpg", "/c.jpg"]
        )

        handler.batch_set_decision("keep")

        assert recs_g1[0].user_decision == "keep"
        assert recs_g1[1].user_decision == ""
        assert recs_g2[0].user_decision == "keep"

    def test_provider_with_gather_method(self, tmp_path):
        """checked_paths_provider can be an object with gather_checked_paths()."""
        recs = [_rec("/a.jpg")]
        vm = SimpleNamespace(groups=[PhotoGroup(group_number=1, items=recs)])
        db = _make_db(tmp_path, [{"source_path": "/a.jpg"}])

        provider = SimpleNamespace(gather_checked_paths=lambda: ["/a.jpg"])
        handler, _, _ = _make_handler(vm, str(db), checked_paths=provider)

        handler.batch_set_decision("delete")

        assert recs[0].user_decision == "delete"
