"""Tests for review API routes: GET /api/manifest, PATCH /api/decision, PATCH /api/lock."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.web.main import create_app
from infrastructure.manifest_repository import ManifestRepository
from scanner.manifest import _DDL as _MANIFEST_DDL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manifest(tmp_path: Path, rows: list[dict]) -> Path:
    """Build a full-schema SQLite manifest using the canonical scanner DDL."""
    manifest = tmp_path / "manifest.sqlite"
    # Create table + run migrations BEFORE inserting rows so row dicts
    # can reference migration columns like outcome, is_locked.
    conn = sqlite3.connect(str(manifest))
    try:
        conn.executescript(_MANIFEST_DDL)
        conn.commit()
    finally:
        conn.close()
    ManifestRepository().ensure_schema(str(manifest))

    conn = sqlite3.connect(str(manifest))
    try:
        for r in rows:
            row = {"source_label": "test", **r}
            cols = list(row.keys())
            placeholders = ", ".join(f":{c}" for c in cols)
            col_list = ", ".join(cols)
            conn.execute(
                f"INSERT INTO migration_manifest ({col_list}) VALUES ({placeholders})",
                row,
            )
        conn.commit()
    finally:
        conn.close()
    return manifest


def _make_pair(tmp_path: Path) -> Path:
    """Two-row manifest: Ref + REVIEW_DUPLICATE."""
    return _make_manifest(tmp_path, [
        {
            "source_path": "/photos/ref.jpg",
            "action": "",
            "group_id": "/g/1",
            "outcome": "",
            "file_size_bytes": 2048,
        },
        {
            "source_path": "/photos/dup.jpg",
            "action": "REVIEW_DUPLICATE",
            "group_id": "/g/1",
            "hamming_distance": 4,
            "outcome": "",
            "file_size_bytes": 1024,
        },
    ])


def _make_real_file_manifest(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create two real JPEG files in a sub-directory and a manifest pointing to them.

    Files are placed in tmp_path/photos/ so the allowed root registered is
    tmp_path/photos/, NOT tmp_path itself. This lets the negative test place a
    "secret" file directly under tmp_path (outside the manifest root) to verify
    the traversal guard denies it.

    Returns (manifest_path, jpg1_path, jpg2_path).
    Two files in one group so the group survives the singleton-skip.
    """
    from PIL import Image as PILImage
    photos_dir = tmp_path / "photos"
    photos_dir.mkdir()
    jpg1 = photos_dir / "ref.jpg"
    jpg2 = photos_dir / "dup.jpg"
    PILImage.new("RGB", (8, 8)).save(str(jpg1), "JPEG")
    PILImage.new("RGB", (8, 8), color=(128, 0, 0)).save(str(jpg2), "JPEG")

    manifest = _make_manifest(tmp_path, [
        {
            "source_path": str(jpg1),
            "action": "",
            "group_id": "/g/real",
            "outcome": "",
            "file_size_bytes": jpg1.stat().st_size,
        },
        {
            "source_path": str(jpg2),
            "action": "REVIEW_DUPLICATE",
            "group_id": "/g/real",
            "hamming_distance": 4,
            "outcome": "",
            "file_size_bytes": jpg2.stat().st_size,
        },
    ])
    return manifest, jpg1, jpg2


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Fresh TestClient per test."""
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# GET /api/manifest
# ---------------------------------------------------------------------------

class TestGetManifest:
    def test_happy_path_returns_200(self, client, tmp_path):
        manifest = _make_pair(tmp_path)
        resp = client.get("/api/manifest", params={"path": str(manifest)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_groups"] == 1
        assert body["total_files"] == 2
        assert len(body["groups"]) == 1

    def test_empty_path_returns_400(self, client):
        resp = client.get("/api/manifest", params={"path": ""})
        assert resp.status_code == 400

    def test_missing_path_param_returns_400(self, client):
        # FastAPI default for path="" gives 400 from our check
        resp = client.get("/api/manifest")
        assert resp.status_code == 400

    def test_missing_file_returns_404(self, client, tmp_path):
        resp = client.get("/api/manifest", params={"path": str(tmp_path / "no.sqlite")})
        assert resp.status_code == 404

    def test_response_has_expected_keys(self, client, tmp_path):
        manifest = _make_pair(tmp_path)
        resp = client.get("/api/manifest", params={"path": str(manifest)})
        body = resp.json()
        assert "manifest_path" in body
        assert "groups" in body
        assert "total_groups" in body
        assert "total_files" in body

    def test_allowed_roots_populated_after_load(self, client, tmp_path):
        """After GET /api/manifest, app.state.allowed_roots must be non-empty."""
        manifest = _make_pair(tmp_path)
        resp = client.get("/api/manifest", params={"path": str(manifest)})
        assert resp.status_code == 200
        allowed: list = client.app.state.allowed_roots
        assert len(allowed) > 0, "allowed_roots must be populated after manifest load"

    def test_non_manifest_sqlite_returns_422(self, client, tmp_path):
        """A .sqlite file that is not a valid manifest must return 422 (not 500).

        Catches the case where the file exists on disk but sqlite can't open
        a valid manifest schema — e.g. a plaintext file with a .sqlite extension.
        """
        junk = tmp_path / "junk.sqlite"
        junk.write_text("not a sqlite file at all", encoding="utf-8")
        resp = client.get("/api/manifest", params={"path": str(junk)})
        assert resp.status_code == 422

    def test_all_singleton_manifest_returns_empty_groups(self, client, tmp_path):
        """A manifest whose every group has only one member yields 0 groups after load.

        singleton-skip: groups with < 2 survivors are dropped by MainVM.
        """
        # Three rows, each in its own distinct group_id — all singletons.
        manifest = _make_manifest(tmp_path, [
            {
                "source_path": "/a/file1.jpg",
                "action": "",
                "group_id": "/g/solo1",
                "outcome": "",
                "file_size_bytes": 100,
            },
            {
                "source_path": "/b/file2.jpg",
                "action": "",
                "group_id": "/g/solo2",
                "outcome": "",
                "file_size_bytes": 100,
            },
        ])
        resp = client.get("/api/manifest", params={"path": str(manifest)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_groups"] == 0
        assert body["groups"] == []


# ---------------------------------------------------------------------------
# PATCH /api/decision
# ---------------------------------------------------------------------------

class TestPatchDecision:
    def test_happy_path_returns_updated_count(self, client, tmp_path):
        manifest = _make_pair(tmp_path)
        resp = client.patch("/api/decision", json={
            "manifest_path": str(manifest),
            "decisions": [{"file_path": "/photos/dup.jpg", "decision": "delete"}],
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 1

    def test_multiple_decisions(self, client, tmp_path):
        manifest = _make_pair(tmp_path)
        resp = client.patch("/api/decision", json={
            "manifest_path": str(manifest),
            "decisions": [
                {"file_path": "/photos/ref.jpg", "decision": ""},
                {"file_path": "/photos/dup.jpg", "decision": "delete"},
            ],
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2

    def test_invalid_decision_returns_400(self, client, tmp_path):
        manifest = _make_pair(tmp_path)
        resp = client.patch("/api/decision", json={
            "manifest_path": str(manifest),
            "decisions": [{"file_path": "/photos/dup.jpg", "decision": "BOGUS"}],
        })
        assert resp.status_code == 400
        assert "BOGUS" in resp.json()["detail"] or "Invalid decision" in resp.json()["detail"]

    def test_bogus_manifest_path_returns_404(self, client, tmp_path):
        """PATCH /api/decision with a non-existent manifest_path must return 404.

        Without _require_manifest, sqlite3.connect() would silently CREATE the
        file and then fail on the missing schema — this test catches that regression.
        """
        bogus = str(tmp_path / "does_not_exist.sqlite")
        resp = client.patch("/api/decision", json={
            "manifest_path": bogus,
            "decisions": [{"file_path": "/photos/dup.jpg", "decision": "delete"}],
        })
        assert resp.status_code == 404
        # Verify the bogus path was NOT silently created on disk.
        assert not Path(bogus).exists(), (
            "sqlite3.connect() must not silently create a manifest file on PATCH"
        )


# ---------------------------------------------------------------------------
# PATCH /api/lock
# ---------------------------------------------------------------------------

class TestPatchLock:
    def test_lock_returns_updated_count(self, client, tmp_path):
        manifest = _make_pair(tmp_path)
        resp = client.patch("/api/lock", json={
            "manifest_path": str(manifest),
            "locks": [{"file_path": "/photos/ref.jpg", "locked": True}],
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 1

    def test_unlock_also_works(self, client, tmp_path):
        manifest = _make_pair(tmp_path)
        resp = client.patch("/api/lock", json={
            "manifest_path": str(manifest),
            "locks": [{"file_path": "/photos/ref.jpg", "locked": False}],
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 1

    def test_bogus_manifest_path_returns_404(self, client, tmp_path):
        """PATCH /api/lock with a non-existent manifest_path must return 404.

        Mirrors the decision test: _require_manifest prevents silent file creation.
        """
        bogus = str(tmp_path / "does_not_exist.sqlite")
        resp = client.patch("/api/lock", json={
            "manifest_path": bogus,
            "locks": [{"file_path": "/photos/ref.jpg", "locked": True}],
        })
        assert resp.status_code == 404
        assert not Path(bogus).exists(), (
            "sqlite3.connect() must not silently create a manifest file on PATCH"
        )


# ---------------------------------------------------------------------------
# End-to-end: allowed_roots authorization chain (Fix 7)
# ---------------------------------------------------------------------------

class TestAllowedRootsEndToEnd:
    """Prove the full load→roots→serve authorization chain works correctly.

    The old test only asserted len(allowed_roots) > 0 — it missed the case
    where the image route still denied requests because the path wasn't truly
    under any registered root.
    """

    def test_image_served_after_manifest_load(self, tmp_path):
        """A file from a loaded manifest must be serveable via GET /api/image."""
        manifest, jpg1, jpg2 = _make_real_file_manifest(tmp_path)
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            # Load the manifest — this populates allowed_roots.
            resp = client.get("/api/manifest", params={"path": str(manifest)})
            assert resp.status_code == 200, f"manifest load failed: {resp.text}"

            # Now the image route must serve a file that IS in the manifest.
            resp2 = client.get("/api/image", params={"path": str(jpg1), "size": 64})
            assert resp2.status_code == 200, (
                f"Image route returned {resp2.status_code} for a file that should be "
                f"allowed by allowed_roots. Body: {resp2.text}"
            )
            assert resp2.headers["content-type"].startswith("image/"), (
                "Expected image/jpeg content-type"
            )

    def test_image_denied_outside_manifest_root(self, tmp_path):
        """A file NOT in any loaded manifest must return 403 from GET /api/image.

        This is the load-bearing security test — it verifies the traversal guard
        actually denies non-manifest paths, not just that allowed_roots is non-empty.

        _make_real_file_manifest places real images under tmp_path/photos/, so the
        registered allowed root is tmp_path/photos/.  A file directly under tmp_path
        (not under photos/) is therefore OUTSIDE any allowed root and must be denied.
        """
        manifest, jpg1, jpg2 = _make_real_file_manifest(tmp_path)

        # Place a "secret" JPEG directly in tmp_path — NOT under tmp_path/photos/
        # (the registered root), so it is outside the allowed root.
        from PIL import Image as PILImage
        outside_jpg = tmp_path / "secret.jpg"
        PILImage.new("RGB", (8, 8), color=(0, 255, 0)).save(str(outside_jpg), "JPEG")

        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            # Load the manifest — registers tmp_path/photos/ as an allowed root.
            resp = client.get("/api/manifest", params={"path": str(manifest)})
            assert resp.status_code == 200

            # secret.jpg is in tmp_path, not in tmp_path/photos/ → must be denied.
            resp2 = client.get("/api/image", params={"path": str(outside_jpg), "size": 64})
            assert resp2.status_code == 403, (
                f"Expected 403 for path outside allowed roots but got {resp2.status_code}"
            )
