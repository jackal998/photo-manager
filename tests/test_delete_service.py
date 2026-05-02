"""Tests for infrastructure.delete_service.DeleteService."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from core.models import PhotoGroup, PhotoRecord
from infrastructure.delete_service import DeleteService


def _rec(path: str, is_locked: bool = False) -> PhotoRecord:
    return PhotoRecord(
        group_number=1,
        is_mark=True,
        is_locked=is_locked,
        folder_path=str(Path(path).parent),
        file_path=path,
        capture_date=None,
        modified_date=None,
        file_size_bytes=0,
    )


# ── plan_delete ────────────────────────────────────────────────────────────

class TestPlanDelete:
    def test_locked_items_excluded(self):
        groups = [PhotoGroup(group_number=1, items=[
            _rec("/a/keep.jpg", is_locked=True),
            _rec("/a/del.jpg", is_locked=False),
        ])]
        svc = DeleteService()
        plan = svc.plan_delete(groups, ["/a/keep.jpg", "/a/del.jpg"])
        assert "/a/keep.jpg" not in plan.delete_paths
        assert "/a/del.jpg" in plan.delete_paths

    def test_all_locked_empty_plan(self):
        groups = [PhotoGroup(group_number=1, items=[
            _rec("/a/f1.jpg", is_locked=True),
            _rec("/a/f2.jpg", is_locked=True),
        ])]
        svc = DeleteService()
        plan = svc.plan_delete(groups, ["/a/f1.jpg", "/a/f2.jpg"])
        assert plan.delete_paths == []

    def test_group_summary_full_delete_flag(self):
        groups = [PhotoGroup(group_number=1, items=[
            _rec("/a/f1.jpg"),
            _rec("/a/f2.jpg"),
        ])]
        svc = DeleteService()
        plan = svc.plan_delete(groups, ["/a/f1.jpg", "/a/f2.jpg"])
        summary = plan.group_summaries[0]
        assert summary.is_full_delete is True

    def test_group_summary_partial_delete(self):
        groups = [PhotoGroup(group_number=1, items=[
            _rec("/a/f1.jpg"),
            _rec("/a/f2.jpg"),
        ])]
        svc = DeleteService()
        plan = svc.plan_delete(groups, ["/a/f1.jpg"])
        summary = plan.group_summaries[0]
        assert summary.is_full_delete is False
        assert summary.selected_count == 1
        assert summary.total_count == 2

    def test_unselected_paths_not_in_plan(self):
        groups = [PhotoGroup(group_number=1, items=[
            _rec("/a/f1.jpg"),
            _rec("/a/f2.jpg"),
        ])]
        svc = DeleteService()
        plan = svc.plan_delete(groups, ["/a/f1.jpg"])
        assert "/a/f2.jpg" not in plan.delete_paths


# ── delete_to_recycle ──────────────────────────────────────────────────────

class TestDeleteToRecycle:
    def test_successful_delete(self, tmp_path):
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")
        svc = DeleteService()
        with patch("infrastructure.delete_service.send2trash") as mock_trash:
            result = svc.delete_to_recycle([str(f)])
        mock_trash.assert_called_once()
        assert str(f) in result.success_paths
        assert result.failed == []

    def test_missing_file_goes_to_failed(self, tmp_path):
        svc = DeleteService()
        result = svc.delete_to_recycle(["/does/not/exist/x.jpg"])
        assert len(result.failed) == 1
        assert result.success_paths == []

    def test_handle_releaser_called(self, tmp_path):
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")
        released = []
        svc = DeleteService()
        svc.set_handle_releaser(lambda: released.append(True))
        with patch("infrastructure.delete_service.send2trash"):
            svc.delete_to_recycle([str(f)])
        assert released == [True]

    def test_handle_releaser_exception_swallowed(self, tmp_path):
        """A failing handle_releaser must not block the delete."""
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")
        svc = DeleteService()
        svc.set_handle_releaser(lambda: (_ for _ in ()).throw(OSError("boom")))
        with patch("infrastructure.delete_service.send2trash") as mock_trash:
            result = svc.delete_to_recycle([str(f)])
        mock_trash.assert_called_once()
        assert str(f) in result.success_paths

    def test_falls_back_to_original_path_when_normalized_fails(self, tmp_path):
        """Method 1 (normalized) raises OSError → Method 2 (original path) succeeds."""
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")
        call_count = [0]

        def trash_fail_then_succeed(p):
            call_count[0] += 1
            if call_count[0] == 1:
                raise OSError("simulated normalized-path failure")
            # second call (original path) succeeds

        svc = DeleteService()
        with patch(
            "infrastructure.delete_service.send2trash",
            side_effect=trash_fail_then_succeed,
        ):
            result = svc.delete_to_recycle([str(f)])
        assert str(f) in result.success_paths
        assert result.failed == []
        assert call_count[0] == 2

    def test_falls_back_to_absolute_path_when_first_two_fail(self, tmp_path):
        """Method 1 + 2 raise OSError → Method 3 (absolute path) succeeds."""
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")
        call_count = [0]

        def trash(p):
            call_count[0] += 1
            if call_count[0] < 3:
                raise OSError(f"failure #{call_count[0]}")

        svc = DeleteService()
        with patch(
            "infrastructure.delete_service.send2trash",
            side_effect=trash,
        ):
            result = svc.delete_to_recycle([str(f)])
        assert str(f) in result.success_paths
        assert call_count[0] == 3

    def test_all_three_methods_failing_records_failure(self, tmp_path):
        """When every fallback path raises, the file is recorded as failed."""
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")

        svc = DeleteService()
        with patch(
            "infrastructure.delete_service.send2trash",
            side_effect=OSError("permanent failure"),
        ):
            result = svc.delete_to_recycle([str(f)])
        assert result.success_paths == []
        assert len(result.failed) == 1
        assert result.failed[0][0] == str(f)
        assert "Multiple delete failures" in result.failed[0][1]

    def test_runtime_error_on_normalized_path_recorded_as_failure(self, tmp_path):
        """RuntimeError on Method 1 short-circuits to failure (no fallback)."""
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")

        svc = DeleteService()
        with patch(
            "infrastructure.delete_service.send2trash",
            side_effect=RuntimeError("kernel said no"),
        ):
            result = svc.delete_to_recycle([str(f)])
        assert result.success_paths == []
        assert len(result.failed) == 1
        assert "Unexpected error" in result.failed[0][1]


# ── execute_delete ─────────────────────────────────────────────────────────

class TestExecuteDelete:
    def test_writes_audit_log(self, tmp_path):
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")
        groups = [PhotoGroup(group_number=1, items=[_rec(str(f))])]
        svc = DeleteService()
        from core.services.interfaces import DeletePlan, DeletePlanGroupSummary
        plan = DeletePlan(
            delete_paths=[str(f)],
            group_summaries=[DeletePlanGroupSummary(1, 1, 1, True)],
        )
        log_dir = str(tmp_path / "logs")
        with patch("infrastructure.delete_service.send2trash"):
            result = svc.execute_delete(groups, plan, log_dir=log_dir)
        assert result.log_path is not None
        assert Path(result.log_path).exists()

    def test_failed_delete_recorded_in_log(self, tmp_path):
        groups = [PhotoGroup(group_number=1, items=[_rec("/ghost.jpg")])]
        svc = DeleteService()
        from core.services.interfaces import DeletePlan, DeletePlanGroupSummary
        plan = DeletePlan(
            delete_paths=["/ghost.jpg"],
            group_summaries=[DeletePlanGroupSummary(1, 1, 1, True)],
        )
        log_dir = str(tmp_path / "logs")
        result = svc.execute_delete(groups, plan, log_dir=log_dir)
        assert len(result.failed) == 1
        assert Path(result.log_path).exists()
