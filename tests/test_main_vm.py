"""Tests for app.viewmodels.main_vm.MainVM."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from core.models import PhotoGroup, PhotoRecord
from app.viewmodels.main_vm import MainVM


def _rec(path: str, group: int = 1, is_mark: bool = False, is_locked: bool = False) -> PhotoRecord:
    return PhotoRecord(
        group_number=group,
        is_mark=is_mark,
        is_locked=is_locked,
        folder_path="/folder",
        file_path=path,
        capture_date=datetime(2024, 1, 1),
        modified_date=datetime(2024, 1, 1),
        file_size_bytes=1024,
    )


def _mock_repo(*records: PhotoRecord):
    repo = MagicMock()
    repo.load.return_value = iter(list(records))
    return repo


# ── load_csv ───────────────────────────────────────────────────────────────

class TestLoadCsv:
    def test_groups_populated(self):
        repo = _mock_repo(_rec("/a.jpg", 1), _rec("/b.jpg", 2))
        vm = MainVM(repo)
        vm.load_csv("/some/file.csv")
        assert len(vm.groups) == 2

    def test_source_csv_path_set(self):
        repo = _mock_repo(_rec("/a.jpg", 1))
        vm = MainVM(repo)
        vm.load_csv("/some/file.csv")
        assert vm.get_source_csv_path() == "/some/file.csv"

    def test_records_grouped_by_group_number(self):
        repo = _mock_repo(
            _rec("/a.jpg", 1), _rec("/b.jpg", 1),
            _rec("/c.jpg", 2),
        )
        vm = MainVM(repo)
        vm.load_csv("/f.csv")
        assert vm.group_count == 2
        group1 = next(g for g in vm.groups if g.group_number == 1)
        assert len(group1.items) == 2


# ── load_from_repo ─────────────────────────────────────────────────────────

class TestLoadFromRepo:
    def test_loads_from_manifest_repo(self):
        csv_repo = _mock_repo()
        manifest_repo = _mock_repo(_rec("/x.jpg", 5), _rec("/y.jpg", 5))
        vm = MainVM(csv_repo)
        vm.load_from_repo(manifest_repo, "/manifest.sqlite")
        assert vm.group_count == 1
        assert vm.groups[0].group_number == 5

    def test_source_csv_path_cleared(self):
        csv_repo = _mock_repo(_rec("/a.jpg", 1))
        vm = MainVM(csv_repo)
        vm.load_csv("/some.csv")
        manifest_repo = _mock_repo(_rec("/x.jpg", 1))
        vm.load_from_repo(manifest_repo, "/manifest.sqlite")
        assert vm.get_source_csv_path() is None


# ── remove_from_list ───────────────────────────────────────────────────────

class TestRemoveFromList:
    def test_removes_specified_path(self):
        repo = _mock_repo(_rec("/a.jpg", 1), _rec("/b.jpg", 1))
        vm = MainVM(repo)
        vm.load_csv("/f.csv")
        vm.remove_from_list(["/a.jpg"])
        paths = [r.file_path for g in vm.groups for r in g.items]
        assert "/a.jpg" not in paths
        assert "/b.jpg" in paths

    def test_empty_group_dropped(self):
        repo = _mock_repo(_rec("/a.jpg", 1))
        vm = MainVM(repo)
        vm.load_csv("/f.csv")
        vm.remove_from_list(["/a.jpg"])
        assert vm.group_count == 0

    def test_noop_on_empty_list(self):
        repo = _mock_repo(_rec("/a.jpg", 1))
        vm = MainVM(repo)
        vm.load_csv("/f.csv")
        vm.remove_from_list([])
        assert vm.group_count == 1


# ── remove_deleted_and_prune ───────────────────────────────────────────────

class TestRemoveDeletedAndPrune:
    def test_group_with_one_remaining_item_pruned(self):
        repo = _mock_repo(_rec("/a.jpg", 1), _rec("/b.jpg", 1))
        vm = MainVM(repo)
        vm.load_csv("/f.csv")
        vm.remove_deleted_and_prune(["/a.jpg"])
        # Only one item left in the group → group dropped
        assert vm.group_count == 0

    def test_group_with_two_remaining_items_kept(self):
        repo = _mock_repo(_rec("/a.jpg", 1), _rec("/b.jpg", 1), _rec("/c.jpg", 1))
        vm = MainVM(repo)
        vm.load_csv("/f.csv")
        vm.remove_deleted_and_prune(["/a.jpg"])
        assert vm.group_count == 1
        assert len(vm.groups[0].items) == 2

    def test_noop_on_empty_deleted(self):
        repo = _mock_repo(_rec("/a.jpg", 1))
        vm = MainVM(repo)
        vm.load_csv("/f.csv")
        vm.remove_deleted_and_prune([])
        assert vm.group_count == 1


# ── update_marks_from_checked_paths ───────────────────────────────────────

class TestUpdateMarks:
    def test_marks_checked_paths(self):
        repo = _mock_repo(_rec("/a.jpg", 1), _rec("/b.jpg", 1))
        vm = MainVM(repo)
        vm.load_csv("/f.csv")
        vm.update_marks_from_checked_paths(["/a.jpg"])
        group = vm.groups[0]
        a = next(r for r in group.items if r.file_path == "/a.jpg")
        b = next(r for r in group.items if r.file_path == "/b.jpg")
        assert a.is_mark is True
        assert b.is_mark is False

    def test_empty_checked_unmarks_all(self):
        repo = _mock_repo(_rec("/a.jpg", 1, is_mark=True))
        vm = MainVM(repo)
        vm.load_csv("/f.csv")
        vm.update_marks_from_checked_paths([])
        assert vm.groups[0].items[0].is_mark is False


# ── remove_group_from_list ─────────────────────────────────────────────────

class TestRemoveGroupFromList:
    def test_removes_group(self):
        repo = _mock_repo(_rec("/a.jpg", 1), _rec("/b.jpg", 2))
        vm = MainVM(repo)
        vm.load_csv("/f.csv")
        vm.remove_group_from_list(1)
        assert vm.group_count == 1
        assert vm.groups[0].group_number == 2

    def test_noop_for_unknown_group(self):
        repo = _mock_repo(_rec("/a.jpg", 1))
        vm = MainVM(repo)
        vm.load_csv("/f.csv")
        vm.remove_group_from_list(999)
        assert vm.group_count == 1


# ── group_count ────────────────────────────────────────────────────────────

class TestGroupCount:
    def test_zero_before_load(self):
        vm = MainVM(MagicMock())
        assert vm.group_count == 0

    def test_reflects_loaded_groups(self):
        repo = _mock_repo(_rec("/a.jpg", 1), _rec("/b.jpg", 2), _rec("/c.jpg", 3))
        vm = MainVM(repo)
        vm.load_csv("/f.csv")
        assert vm.group_count == 3
