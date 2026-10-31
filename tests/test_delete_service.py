"""Tests for infrastructure.delete_service.DeleteService."""

from __future__ import annotations

from unittest.mock import patch

from infrastructure.delete_service import DeleteService


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

    def test_winerror_decoded_reason_surfaces_in_failure(self, tmp_path):
        """A send2trash OSError carrying a known winerror decodes to a
        plain-language reason instead of the generic 'Multiple delete
        failures: ...' string (#742)."""
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"fake")

        def trash_sharing_violation(p):
            exc = OSError(None, "OLE error 0x80270027", p)
            exc.winerror = -2144927705
            raise exc

        svc = DeleteService()
        with patch(
            "infrastructure.delete_service.send2trash",
            side_effect=trash_sharing_violation,
        ):
            result = svc.delete_to_recycle([str(f)])
        assert result.success_paths == []
        assert len(result.failed) == 1
        assert result.failed[0][0] == str(f)
        assert result.failed[0][1] == "file is in use by another process"

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


# ── write_delete_log ───────────────────────────────────────────────────────


class TestWriteDeleteLog:
    """write_delete_log writes one audit row per path, successes and failures alike."""

    def test_success_and_failure_rows_are_read_back(self, tmp_path):
        import csv

        from infrastructure.logging import write_delete_log

        log_path = write_delete_log(
            [(1, "/a/ok.jpg", True, ""), (2, "/a/bad.jpg", False, "access denied")],
            log_dir=str(tmp_path),
        )

        assert log_path is not None
        with open(log_path, encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        assert rows == [
            ["GroupNumber", "FilePath", "Success", "Reason"],
            ["1", "/a/ok.jpg", "1", ""],
            ["2", "/a/bad.jpg", "0", "access denied"],
        ]
