"""Tests for GET /api/fs/browse."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.web.main import create_app


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def dir_tree(tmp_path: Path) -> Path:
    """Create a small directory tree for browse tests.

    Structure:
      tmp_path/
        alpha/          (dir)
        zeta/           (dir)
        file.txt        (file)
        manifest.sqlite (file, is_manifest=True)
    """
    (tmp_path / "alpha").mkdir()
    (tmp_path / "zeta").mkdir()
    (tmp_path / "file.txt").write_text("hello")
    (tmp_path / "manifest.sqlite").write_bytes(b"")
    return tmp_path


class TestFsBrowse:
    def test_happy_path_returns_200(self, client, dir_tree):
        resp = client.get("/api/fs/browse", params={"path": str(dir_tree)})
        assert resp.status_code == 200

    def test_dirs_come_before_files(self, client, dir_tree):
        resp = client.get("/api/fs/browse", params={"path": str(dir_tree)})
        entries = resp.json()["entries"]
        dir_indices = [i for i, e in enumerate(entries) if e["is_dir"]]
        file_indices = [i for i, e in enumerate(entries) if not e["is_dir"]]
        if dir_indices and file_indices:
            assert max(dir_indices) < min(file_indices)

    def test_dirs_sorted_case_insensitive(self, client, dir_tree):
        resp = client.get("/api/fs/browse", params={"path": str(dir_tree)})
        entries = resp.json()["entries"]
        dirs = [e["name"].lower() for e in entries if e["is_dir"]]
        assert dirs == sorted(dirs)

    def test_files_sorted_case_insensitive(self, client, dir_tree):
        resp = client.get("/api/fs/browse", params={"path": str(dir_tree)})
        entries = resp.json()["entries"]
        files = [e["name"].lower() for e in entries if not e["is_dir"]]
        assert files == sorted(files)

    def test_is_manifest_flag_set_for_sqlite(self, client, dir_tree):
        resp = client.get("/api/fs/browse", params={"path": str(dir_tree)})
        entries = resp.json()["entries"]
        sqlite_entries = [e for e in entries if e["name"] == "manifest.sqlite"]
        assert len(sqlite_entries) == 1
        assert sqlite_entries[0]["is_manifest"] is True

    def test_regular_file_is_not_manifest(self, client, dir_tree):
        resp = client.get("/api/fs/browse", params={"path": str(dir_tree)})
        entries = resp.json()["entries"]
        txt_entries = [e for e in entries if e["name"] == "file.txt"]
        assert len(txt_entries) == 1
        assert txt_entries[0]["is_manifest"] is False

    def test_db_and_case_variants_are_manifests(self, client, tmp_path):
        """The app accepts ``.db`` as well as ``.sqlite`` (Qt scan_dialog.py:774;
        every qa scenario writes ``.db``). Detection is case-insensitive so the
        picker highlights real manifests regardless of how they were named."""
        (tmp_path / "scan.db").write_bytes(b"")
        (tmp_path / "LOUD.DB").write_bytes(b"")
        (tmp_path / "Mixed.SqLiTe").write_bytes(b"")
        (tmp_path / "notes.txt").write_text("x")
        resp = client.get("/api/fs/browse", params={"path": str(tmp_path)})
        flags = {e["name"]: e["is_manifest"] for e in resp.json()["entries"]}
        assert flags["scan.db"] is True
        assert flags["LOUD.DB"] is True
        assert flags["Mixed.SqLiTe"] is True
        assert flags["notes.txt"] is False

    def test_response_has_path_and_parent(self, client, dir_tree):
        resp = client.get("/api/fs/browse", params={"path": str(dir_tree)})
        body = resp.json()
        assert "path" in body
        assert "parent" in body
        assert "entries" in body
        # parent must not be equal to path (dir_tree is not a root)
        assert body["parent"] is not None

    def test_404_on_missing_path(self, client, tmp_path):
        resp = client.get("/api/fs/browse", params={"path": str(tmp_path / "missing")})
        assert resp.status_code == 404

    def test_400_on_file_as_path(self, client, tmp_path):
        f = tmp_path / "afile.txt"
        f.write_text("x")
        resp = client.get("/api/fs/browse", params={"path": str(f)})
        assert resp.status_code == 400

    @pytest.mark.skipif(
        sys.platform != "win32",
        reason="bare-drive-letter normalisation is Windows-specific",
    )
    def test_bare_drive_letter_normalises_to_drive_root(self, client):
        """A bare 'C:' is CWD-relative on Windows; the browser must treat it as
        the drive root, not the process working directory.

        Without normalisation Path('C:') resolves against the CWD, so the
        picker would silently open at the server's working dir when fed a bare
        drive (which ``splitPath('C:\\\\out.db')`` produces). The drive root has
        no parent — the CWD would."""
        resp = client.get("/api/fs/browse", params={"path": "C:"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["parent"] is None, "drive root must have no parent"
        assert body["path"].rstrip("\\/").endswith(":"), (
            f"expected a drive-root path, got {body['path']!r}"
        )

    def test_empty_path_lists_roots(self, client):
        resp = client.get("/api/fs/browse", params={"path": ""})
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == ""
        assert body["parent"] is None
        assert len(body["entries"]) > 0
        # On Windows all entries should be drives (is_dir=True); on POSIX "/" entry.
        assert all(e["is_dir"] for e in body["entries"])

    def test_no_path_param_lists_roots(self, client):
        resp = client.get("/api/fs/browse")
        assert resp.status_code == 200
        body = resp.json()
        assert body["parent"] is None
