"""Tests for app.viewmodels.main_vm.MainVM."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from core.models import PhotoGroup, PhotoRecord
from app.viewmodels.main_vm import MainVM


def _rec(
    path: str,
    group: int = 1,
    is_mark: bool = False,
    is_locked: bool = False,
    user_decision: str = "",
) -> PhotoRecord:
    return PhotoRecord(
        group_number=group,
        is_mark=is_mark,
        is_locked=is_locked,
        folder_path="/folder",
        file_path=path,
        capture_date=datetime(2024, 1, 1),
        modified_date=datetime(2024, 1, 1),
        file_size_bytes=1024,
        user_decision=user_decision,
    )


def _mock_repo(*records: PhotoRecord):
    repo = MagicMock()
    repo.load.return_value = iter(list(records))
    return repo


def _load(*records: PhotoRecord) -> MainVM:
    """Helper: build a MainVM and load the given records via load_from_repo."""
    repo = _mock_repo(*records)
    vm = MainVM()
    vm.load_from_repo(repo, "/manifest.sqlite")
    return vm


# ── load_from_repo ─────────────────────────────────────────────────────────

class TestLoadFromRepo:
    def test_loads_from_manifest_repo(self):
        vm = _load(_rec("/x.jpg", 5), _rec("/y.jpg", 5))
        assert vm.group_count == 1
        assert vm.groups[0].group_number == 5

    def test_records_grouped_by_group_number(self):
        vm = _load(_rec("/a.jpg", 1), _rec("/b.jpg", 1), _rec("/c.jpg", 2))
        assert vm.group_count == 2
        group1 = next(g for g in vm.groups if g.group_number == 1)
        assert len(group1.items) == 2


# ── remove_from_list ───────────────────────────────────────────────────────

class TestRemoveFromList:
    def test_removes_specified_path(self):
        vm = _load(_rec("/a.jpg", 1), _rec("/b.jpg", 1))
        vm.remove_from_list(["/a.jpg"])
        paths = [r.file_path for g in vm.groups for r in g.items]
        assert "/a.jpg" not in paths
        assert "/b.jpg" in paths

    def test_empty_group_dropped(self):
        vm = _load(_rec("/a.jpg", 1))
        vm.remove_from_list(["/a.jpg"])
        assert vm.group_count == 0

    def test_noop_on_empty_list(self):
        vm = _load(_rec("/a.jpg", 1))
        vm.remove_from_list([])
        assert vm.group_count == 1


# ── remove_deleted_and_prune ───────────────────────────────────────────────

class TestRemoveDeletedAndPrune:
    def test_group_with_one_remaining_item_pruned(self):
        """Default (prune_singles=True): drops groups reduced to 1 item."""
        vm = _load(_rec("/a.jpg", 1), _rec("/b.jpg", 1))
        vm.remove_deleted_and_prune(["/a.jpg"])
        assert vm.group_count == 0

    def test_prune_singles_false_keeps_single_item_group(self):
        """prune_singles=False: manifest workflow keeps groups reduced to 1 item."""
        vm = _load(_rec("/a.jpg", 1), _rec("/b.jpg", 1))
        vm.remove_deleted_and_prune(["/a.jpg"], prune_singles=False)
        assert vm.group_count == 1
        assert vm.groups[0].items[0].file_path == "/b.jpg"

    def test_prune_singles_false_still_drops_empty_groups(self):
        """prune_singles=False must still drop groups where ALL items are deleted."""
        vm = _load(_rec("/a.jpg", 1), _rec("/b.jpg", 1))
        vm.remove_deleted_and_prune(["/a.jpg", "/b.jpg"], prune_singles=False)
        assert vm.group_count == 0

    def test_prune_singles_false_standalone_group_survives(self):
        """Standalone single-item groups (KEEP/UNDATED/MOVE) persist after unrelated delete."""
        vm = _load(
            _rec("/pair_cand.jpg", 1), _rec("/pair_ref.jpg", 1),
            _rec("/standalone.jpg", 2),
        )
        vm.remove_deleted_and_prune(["/pair_cand.jpg"], prune_singles=False)
        assert vm.group_count == 2

    def test_group_with_two_remaining_items_kept(self):
        vm = _load(_rec("/a.jpg", 1), _rec("/b.jpg", 1), _rec("/c.jpg", 1))
        vm.remove_deleted_and_prune(["/a.jpg"])
        assert vm.group_count == 1
        assert len(vm.groups[0].items) == 2

    def test_noop_on_empty_deleted(self):
        vm = _load(_rec("/a.jpg", 1))
        vm.remove_deleted_and_prune([])
        assert vm.group_count == 1


# ── update_marks_from_checked_paths ───────────────────────────────────────

class TestUpdateMarks:
    def test_marks_checked_paths(self):
        vm = _load(_rec("/a.jpg", 1), _rec("/b.jpg", 1))
        vm.update_marks_from_checked_paths(["/a.jpg"])
        group = vm.groups[0]
        a = next(r for r in group.items if r.file_path == "/a.jpg")
        b = next(r for r in group.items if r.file_path == "/b.jpg")
        assert a.is_mark is True
        assert b.is_mark is False

    def test_empty_checked_unmarks_all(self):
        vm = _load(_rec("/a.jpg", 1, is_mark=True))
        vm.update_marks_from_checked_paths([])
        assert vm.groups[0].items[0].is_mark is False


# ── remove_group_from_list ─────────────────────────────────────────────────

class TestRemoveGroupFromList:
    def test_removes_group(self):
        vm = _load(_rec("/a.jpg", 1), _rec("/b.jpg", 2))
        vm.remove_group_from_list(1)
        assert vm.group_count == 1
        assert vm.groups[0].group_number == 2

    def test_noop_for_unknown_group(self):
        vm = _load(_rec("/a.jpg", 1))
        vm.remove_group_from_list(999)
        assert vm.group_count == 1


# ── user_decision preserved through load ──────────────────────────────────

class TestUserDecisionPreserved:
    def test_user_decision_survives_load_from_repo(self):
        rec = _rec("/a.jpg", group=1, user_decision="delete")
        repo = _mock_repo(rec)
        vm = MainVM()
        vm.load_from_repo(repo, "/manifest.sqlite")
        assert vm.groups[0].items[0].user_decision == "delete"

    def test_user_decision_empty_by_default(self):
        rec = _rec("/a.jpg", group=1)
        repo = _mock_repo(rec)
        vm = MainVM()
        vm.load_from_repo(repo, "/manifest.sqlite")
        assert vm.groups[0].items[0].user_decision == ""

    def test_multiple_user_decisions_preserved(self):
        recs = [
            _rec("/a.jpg", group=1, user_decision="delete"),
            _rec("/b.jpg", group=1, user_decision="keep"),
            _rec("/c.jpg", group=2, user_decision=""),
        ]
        repo = _mock_repo(*recs)
        vm = MainVM()
        vm.load_from_repo(repo, "/manifest.sqlite")
        by_path = {r.file_path: r for g in vm.groups for r in g.items}
        assert by_path["/a.jpg"].user_decision == "delete"
        assert by_path["/b.jpg"].user_decision == "keep"
        assert by_path["/c.jpg"].user_decision == ""


# ── group_count ────────────────────────────────────────────────────────────

class TestGroupCount:
    def test_zero_before_load(self):
        vm = MainVM()
        assert vm.group_count == 0

    def test_reflects_loaded_groups(self):
        vm = _load(_rec("/a.jpg", 1), _rec("/b.jpg", 2), _rec("/c.jpg", 3))
        assert vm.group_count == 3
