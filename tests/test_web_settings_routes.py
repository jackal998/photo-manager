"""Tests for GET /api/settings and PATCH /api/settings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.web.main import create_app
from app.web.routes.settings import _WEB_SETTINGS_KEYS


@pytest.fixture()
def tmp_settings(tmp_path: Path) -> Path:
    """Write a temporary settings.json and point PHOTO_MANAGER_HOME at it."""
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"ui": {"theme": "light"}}), encoding="utf-8")
    return settings_file


@pytest.fixture()
def client(tmp_settings: Path, monkeypatch):
    """TestClient with PHOTO_MANAGER_HOME pointing at tmp settings dir.

    The settings route resolves:
      PHOTO_MANAGER_HOME (relative to repo root) → settings.json

    We set PHOTO_MANAGER_HOME to an absolute path that results in
    tmp_settings.parent being used as config_home.
    """
    # The route builds: repo_root / home_env / "settings.json"
    # We want config_home = tmp_settings.parent so we need home_env
    # to be a relative path such that repo_root / home_env == tmp_settings.parent.
    # Easiest: set PHOTO_MANAGER_HOME to an absolute-like relative path pointing
    # to the parent directory, then patch Path.resolve to return it.
    # Simpler: use the PHOTO_MANAGER_HOME env var as an absolute path —
    # the route does (repo_root / home_env).resolve(), which on an absolute
    # home_env just resolves to home_env itself.
    monkeypatch.setenv("PHOTO_MANAGER_HOME", str(tmp_settings.parent))
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


class TestGetSettings:
    def test_returns_200_and_dict(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, dict)

    def test_returns_only_allowlisted_keys(self, client):
        """GET must not leak any key outside the allowlist."""
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        body = resp.json()
        for key in body:
            assert key in _WEB_SETTINGS_KEYS, (
                f"GET /api/settings returned non-allowlisted key: {key!r}"
            )

    def test_allowlisted_keys_present(self, client):
        """All allowlisted keys must appear in the response (value may be None)."""
        resp = client.get("/api/settings")
        body = resp.json()
        for key in _WEB_SETTINGS_KEYS:
            assert key in body, f"Allowlisted key {key!r} missing from GET response"

    def test_does_not_leak_raw_settings(self, client):
        """Keys NOT in the allowlist (e.g. raw nested keys) must not appear."""
        resp = client.get("/api/settings")
        body = resp.json()
        # Our tmp settings.json has {"ui": {"theme": "light"}} — the raw "ui"
        # top-level key must NOT be present (only dotted allowlisted paths are).
        assert "ui" not in body, (
            "Raw top-level 'ui' key leaked — GET must return only allowlisted dotted paths"
        )


class TestPatchSettings:
    def test_round_trip_allowlisted_key(self, client, tmp_settings: Path):
        """PATCH an allowlisted key and verify it round-trips through GET."""
        resp = client.patch("/api/settings", json={
            "updates": {"sorting.defaults": [["score", False]]}
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 1

        resp2 = client.get("/api/settings")
        body = resp2.json()
        assert body["sorting.defaults"] == [["score", False]]

    def test_non_allowlisted_key_returns_400(self, client):
        """PATCH with a key not in the allowlist must return 400 and not write."""
        resp = client.patch("/api/settings", json={
            "updates": {"secret.token": "hunter2"}
        })
        assert resp.status_code == 400
        assert "secret.token" in resp.json()["detail"]

    def test_non_allowlisted_key_is_not_written(self, client, tmp_settings: Path):
        """A rejected PATCH must not persist the bad key to disk."""
        client.patch("/api/settings", json={
            "updates": {"secret.token": "hunter2"}
        })
        raw = json.loads(tmp_settings.read_text(encoding="utf-8"))
        # Confirm the key was never written — walk nested raw dict
        assert "secret" not in raw, (
            "Non-allowlisted key 'secret.token' was written despite 400 response"
        )

    def test_mixed_batch_rejected_entirely(self, client):
        """If any key in a batch is not allowlisted, the whole PATCH is rejected."""
        resp = client.patch("/api/settings", json={
            "updates": {
                "sorting.defaults": [["score", False]],
                "bad.key": "evil",
            }
        })
        assert resp.status_code == 400

    def test_multiple_allowlisted_updates(self, client):
        resp = client.patch("/api/settings", json={
            "updates": {
                "sorting.defaults": [["score", False]],
                "ui.prune_singletons": True,
            }
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2

    def test_scan_sources_round_trip(self, client, tmp_settings: Path):
        """sources.list (list-of-dicts) + sources.output round-trip (s23a/s23b).

        This is the ScanDialog source-persistence contract: the web dialog
        writes the same shape the Qt ScanDialog._save_to_settings does — a
        list of {"path", "recursive"} entries plus an output string. A scalar
        round-trip (test_round_trip_allowlisted_key) wouldn't catch a list
        value being mishandled by JsonSettings.set at a dotted key.
        """
        sources = [
            {"path": "/photos/unique", "recursive": True},
            {"path": "/photos/near-dups", "recursive": False},
        ]
        resp = client.patch("/api/settings", json={
            "updates": {
                "sources.list": sources,
                "sources.output": "out/scan.db",
            }
        })
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2

        body = client.get("/api/settings").json()
        assert body["sources.list"] == sources
        assert body["sources.output"] == "out/scan.db"

        # And the recursive flags survived per-entry (the row-level contract).
        on_disk = json.loads(tmp_settings.read_text(encoding="utf-8"))
        assert on_disk["sources"]["list"] == sources
        assert on_disk["sources"]["output"] == "out/scan.db"
