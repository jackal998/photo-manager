"""Tests for the shared OS-opener helpers (#143).

Covers ``app/views/handlers/file_opener.py``:
  - ``open_file_in_default_viewer`` — happy path delegates to
    ``QDesktopServices.openUrl``; empty / missing paths are silent
    no-ops; exceptions are swallowed (no crash to caller).
  - ``open_folder_containing`` — Windows ``explorer /select,`` branch,
    fallback to folder-only branch, non-Windows ``QDesktopServices``
    branch, and the subprocess-failure fallback. Mirrors the coverage
    pre-#143 test_context_menu.TestOpenFolderAction had on the
    pre-extraction inline impl.

All file-system / shell side effects are mocked — these tests must
never actually open an Explorer window or external viewer.
"""

from __future__ import annotations

from unittest.mock import patch


# ── open_file_in_default_viewer ────────────────────────────────────────────


class TestOpenFileInDefaultViewer:
    def test_empty_path_is_noop(self):
        from app.views.handlers.file_opener import open_file_in_default_viewer
        with patch("app.views.handlers.file_opener.QDesktopServices.openUrl") as open_url:
            open_file_in_default_viewer("")
        open_url.assert_not_called()

    def test_missing_file_is_noop(self, tmp_path):
        """Row whose backing file was deleted out-of-band — must not pop a
        system error dialog; silent no-op is correct."""
        from app.views.handlers.file_opener import open_file_in_default_viewer
        missing = tmp_path / "gone.jpg"  # never written
        with patch("app.views.handlers.file_opener.QDesktopServices.openUrl") as open_url:
            open_file_in_default_viewer(str(missing))
        open_url.assert_not_called()

    def test_existing_file_calls_qdesktopservices(self, tmp_path):
        from app.views.handlers.file_opener import open_file_in_default_viewer
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"data")
        with patch("app.views.handlers.file_opener.QDesktopServices.openUrl") as open_url:
            open_file_in_default_viewer(str(f))
        open_url.assert_called_once()
        # Argument is a QUrl pointing at the local file.
        args, _ = open_url.call_args
        assert args[0].isLocalFile()

    def test_qdesktopservices_exception_is_swallowed(self, tmp_path):
        """If Qt's openUrl itself raises, the helper must not propagate —
        callers (signal slots) treat the action as best-effort."""
        from app.views.handlers.file_opener import open_file_in_default_viewer
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"data")
        with patch(
            "app.views.handlers.file_opener.QDesktopServices.openUrl",
            side_effect=RuntimeError("Qt blew up"),
        ):
            # Must not raise
            open_file_in_default_viewer(str(f))


# ── open_folder_containing ─────────────────────────────────────────────────


class TestOpenFolderContaining:
    def test_empty_path_is_noop(self):
        from app.views.handlers.file_opener import open_folder_containing
        with (
            patch("subprocess.Popen") as popen,
            patch("app.views.handlers.file_opener.QDesktopServices.openUrl") as open_url,
        ):
            open_folder_containing("")
        popen.assert_not_called()
        open_url.assert_not_called()

    def test_existing_file_on_windows_uses_explorer_select(
        self, tmp_path, monkeypatch
    ):
        from app.views.handlers.file_opener import open_folder_containing
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"data")
        monkeypatch.setattr("os.name", "nt")
        with patch("subprocess.Popen") as popen:
            open_folder_containing(str(f))
        popen.assert_called_once()
        args = popen.call_args[0][0]
        assert args[0] == "explorer"
        assert args[1] == "/select,"

    def test_missing_file_on_windows_falls_back_to_folder(
        self, tmp_path, monkeypatch
    ):
        """File doesn't exist but folder does → Popen(['explorer', folder])."""
        from app.views.handlers.file_opener import open_folder_containing
        f = tmp_path / "missing.jpg"
        monkeypatch.setattr("os.name", "nt")
        with patch("subprocess.Popen") as popen:
            open_folder_containing(str(f))
        popen.assert_called_once()
        args = popen.call_args[0][0]
        assert args[0] == "explorer"
        assert "/select," not in args

    def test_non_windows_uses_qdesktopservices(self, tmp_path, monkeypatch):
        from app.views.handlers.file_opener import open_folder_containing
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"data")
        monkeypatch.setattr("os.name", "posix")
        with (
            patch("subprocess.Popen") as popen,
            patch("app.views.handlers.file_opener.QDesktopServices.openUrl") as open_url,
        ):
            open_folder_containing(str(f))
        popen.assert_not_called()
        open_url.assert_called_once()

    def test_subprocess_failure_falls_back_to_qdesktopservices(
        self, tmp_path, monkeypatch
    ):
        from app.views.handlers.file_opener import open_folder_containing
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"data")
        monkeypatch.setattr("os.name", "nt")
        with (
            patch("subprocess.Popen", side_effect=OSError("explorer broke")),
            patch("app.views.handlers.file_opener.QDesktopServices.openUrl") as open_url,
        ):
            open_folder_containing(str(f))
        open_url.assert_called_once()
