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
        # #425 — second rec uses legacy "keep" literal to prove the vm
        # round-trips back-compat manifest data unchanged (new manifests
        # use "" canonical; old ones may still carry "keep").
        recs = [
            _rec("/a.jpg", group=1, user_decision="delete"),
            _rec("/b.jpg", group=1, user_decision="keep"),  # back-compat
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


# ── pending_decision_count (#142 — re-scan confirmation gate) ─────────────


class TestPendingDecisionCount:
    """Counter used by MainWindow._confirm_no_pending_decisions to decide
    whether to prompt before letting a re-scan replace the loaded
    manifest.

    Empty user_decision = default (untouched). Any non-empty string
    counts as "user has acted on this row".
    """

    def test_zero_before_load(self):
        vm = MainVM()
        assert vm.pending_decision_count == 0

    def test_zero_when_all_records_undecided(self):
        vm = _load(
            _rec("/a.jpg", 1, user_decision=""),
            _rec("/b.jpg", 1, user_decision=""),
            _rec("/c.jpg", 2, user_decision=""),
        )
        assert vm.pending_decision_count == 0

    def test_counts_delete_decisions(self):
        vm = _load(
            _rec("/a.jpg", 1, user_decision="delete"),
            _rec("/b.jpg", 1, user_decision=""),
        )
        assert vm.pending_decision_count == 1

    def test_counts_legacy_keep_literal_as_pending(self):
        """#425 — back-compat: legacy "keep" literal counts as pending
        (any non-empty user_decision = "user has acted"). New manifests
        use "" canonical for keep, which correctly DOES NOT count as
        pending (an auto-selected or right-click-keep'd row is undecided
        from the manifest-replacement POV — there's nothing to lose).
        """
        vm = _load(
            _rec("/a.jpg", 1, user_decision="keep"),  # legacy literal
            _rec("/b.jpg", 1, user_decision=""),
        )
        assert vm.pending_decision_count == 1

    def test_counts_mixed_decision_kinds(self):
        # #425 — second rec uses the legacy "keep" literal (back-compat).
        # Canonical "" is intentionally excluded from the pending count
        # because empty = undecided/keep state has nothing to lose.
        vm = _load(
            _rec("/a.jpg", 1, user_decision="delete"),
            _rec("/b.jpg", 1, user_decision="keep"),  # back-compat
            _rec("/c.jpg", 2, user_decision=""),
            _rec("/d.jpg", 2, user_decision="delete"),
        )
        assert vm.pending_decision_count == 3

    def test_counts_across_multiple_groups(self):
        vm = _load(
            _rec("/g1a.jpg", 1, user_decision="delete"),
            _rec("/g2a.jpg", 2, user_decision="delete"),
            _rec("/g3a.jpg", 3, user_decision="delete"),
        )
        assert vm.pending_decision_count == 3

    def test_treats_any_non_empty_string_as_decided(self):
        """Defensive: if a future decision kind is added (e.g. 'review',
        'move'), the gate still fires. Only the empty string means
        'untouched'."""
        vm = _load(
            _rec("/a.jpg", 1, user_decision="review"),
            _rec("/b.jpg", 1, user_decision="move"),
        )
        assert vm.pending_decision_count == 2


# ── removed_from_list_paths session bookkeeping ──────────────────────────


class TestRemovedFromListPaths:
    """``removed_from_list_paths`` lives on the VM so the Execute Action
    dialog can pick up paths that were removed via the main-window
    right-click flow before the dialog was opened."""

    def test_defaults_empty(self):
        vm = MainVM()
        assert vm.removed_from_list_paths == []

    def test_load_from_repo_clears(self):
        vm = MainVM()
        vm.removed_from_list_paths = ["/stale.jpg"]
        repo = _mock_repo(_rec("/a.jpg", 1))
        vm.load_from_repo(repo, "/manifest.sqlite")
        # Carrying paths forward into a freshly-loaded manifest is a
        # bug class — the strings would refer to a different on-disk
        # state. Always reset on load.
        assert vm.removed_from_list_paths == []


# ── Within-group score-DESC default sort (#187 — PR 5) ─────────────────────


def _scored_rec(path: str, *, score: float | None, group: int = 1) -> PhotoRecord:
    return PhotoRecord(
        group_number=group,
        is_mark=False,
        is_locked=False,
        folder_path="/folder",
        file_path=path,
        capture_date=datetime(2024, 1, 1),
        modified_date=datetime(2024, 1, 1),
        file_size_bytes=1024,
        score=score,
    )


class TestWithinGroupScoreSort:
    """The MainVM's _group_records prepends ("score", False) as the design
    default for #187 so the highest-scoring copy lands at the top of each
    group. User-configured sorts act as secondary tiebreakers; an explicit
    user sort on the score field (either direction) suppresses the prepend.
    """

    def test_default_sort_orders_by_score_desc(self):
        """No user-configured sort — score-DESC is the implicit default."""
        repo = _mock_repo(
            _scored_rec("/low.jpg", score=0.3),
            _scored_rec("/high.jpg", score=0.9),
            _scored_rec("/mid.jpg", score=0.6),
        )
        vm = MainVM()
        vm.load_from_repo(repo, "/manifest.sqlite")
        paths = [r.file_path for r in vm.groups[0].items]
        assert paths == ["/high.jpg", "/mid.jpg", "/low.jpg"]

    def test_none_score_sorts_to_bottom_under_desc(self):
        """A row with score=None (Live Photo MOV passenger, isolated, or
        old manifest) must sort below real scores under descending order.
        Previously this would TypeError because SortService substituted
        ``""`` for None on a numeric field — the regression-test contract."""
        repo = _mock_repo(
            _scored_rec("/unscored.jpg", score=None),
            _scored_rec("/scored.jpg", score=0.5),
        )
        vm = MainVM()
        vm.load_from_repo(repo, "/manifest.sqlite")
        paths = [r.file_path for r in vm.groups[0].items]
        assert paths == ["/scored.jpg", "/unscored.jpg"]

    def test_user_configured_score_sort_overrides_default(self):
        """A user who explicitly configures ``score`` (any direction) takes
        full control — the implicit DESC prepend is skipped so the user's
        chosen direction wins."""
        repo = _mock_repo(
            _scored_rec("/low.jpg", score=0.3),
            _scored_rec("/high.jpg", score=0.9),
        )
        vm = MainVM(default_sort=[("score", True)])   # explicit ascending
        vm.load_from_repo(repo, "/manifest.sqlite")
        paths = [r.file_path for r in vm.groups[0].items]
        assert paths == ["/low.jpg", "/high.jpg"]

    def test_user_configured_non_score_sort_becomes_tiebreaker(self):
        """A user's sort on a non-score field acts as the secondary key:
        score-DESC first, user's field second."""
        repo = _mock_repo(
            _scored_rec("/path_b.jpg", score=0.8),
            _scored_rec("/path_a.jpg", score=0.8),  # tied score
            _scored_rec("/path_c.jpg", score=0.5),
        )
        vm = MainVM(default_sort=[("file_path", True)])
        vm.load_from_repo(repo, "/manifest.sqlite")
        paths = [r.file_path for r in vm.groups[0].items]
        # Score-DESC primary: the two 0.8s before the 0.5. Then path-ASC
        # as tiebreaker on the 0.8 rows: a before b.
        assert paths == ["/path_a.jpg", "/path_b.jpg", "/path_c.jpg"]
