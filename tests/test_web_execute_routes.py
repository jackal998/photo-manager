"""Tests for file-mutating execute API routes.

POST /api/execute, /api/remove, /api/prune, /api/save, /api/reveal.

Every test exercises a REAL failure mode (not synthetic coverage padding).
All filesystem interaction uses tmp files / tmp SQLite manifests.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path

import pytest
import httpx
from fastapi.testclient import TestClient

from app.web.main import create_app
from infrastructure.manifest_repository import ManifestRepository
from scanner.manifest import _DDL as _MANIFEST_DDL


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_manifest(tmp_path: Path, rows: list[dict]) -> Path:
    """Build a full-schema SQLite manifest and return its path."""
    manifest = tmp_path / "manifest.sqlite"
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


def _read_outcome(manifest_path: Path, file_path: str) -> str:
    """Read the outcome column for a specific file path from SQLite."""
    conn = sqlite3.connect(str(manifest_path))
    try:
        row = conn.execute(
            "SELECT outcome FROM migration_manifest WHERE source_path = ?",
            (file_path,),
        ).fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def _read_is_locked(manifest_path: Path, file_path: str) -> bool:
    """Read the is_locked value for a file path from SQLite."""
    conn = sqlite3.connect(str(manifest_path))
    try:
        row = conn.execute(
            "SELECT is_locked FROM migration_manifest WHERE source_path = ?",
            (file_path,),
        ).fetchone()
        return bool(row[0]) if row else False
    finally:
        conn.close()


def _make_real_files(tmp_path: Path, n: int = 2) -> list[Path]:
    """Create N real files in tmp_path/files/ and return their paths."""
    files_dir = tmp_path / "files"
    files_dir.mkdir(exist_ok=True)
    paths = []
    for i in range(n):
        f = files_dir / f"file{i}.bin"
        f.write_bytes(b"\x00" * 128)
        paths.append(f)
    return paths


@pytest.fixture()
def client():
    """Fresh TestClient per test with an empty allowed_roots list."""
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def client_with_roots(tmp_path):
    """TestClient with tmp_path registered as an allowed root."""
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        # Register tmp_path as an allowed root.
        app.state.allowed_roots = [tmp_path]
        yield c, tmp_path


# ---------------------------------------------------------------------------
# Allowed-roots guard: ALL 5 routes must 403 outside-root paths
# ---------------------------------------------------------------------------


class TestAllowedRootsGuard:
    """Parametrized across all 5 routes: path outside allowed_roots → 403."""

    @pytest.mark.parametrize("route,method,payload_fn", [
        (
            "/api/execute",
            "post",
            lambda outside: {"manifest_path": str(outside / "x.sqlite"), "scope_paths": None},
        ),
        (
            "/api/remove",
            "post",
            lambda outside: {"manifest_path": str(outside / "x.sqlite"), "file_paths": [str(outside / "a.bin")]},
        ),
        (
            "/api/prune",
            "post",
            lambda outside: {"manifest_path": str(outside / "x.sqlite")},
        ),
        (
            "/api/save",
            "post",
            lambda outside: {"manifest_path": str(outside / "x.sqlite")},
        ),
        (
            "/api/reveal",
            "post",
            lambda outside: {"file_path": str(outside / "a.bin")},
        ),
    ])
    def test_outside_root_returns_403(
        self, client_with_roots, tmp_path, route, method, payload_fn
    ):
        client, root = client_with_roots

        # A path that is NOT under `root` — go up one level.
        outside = tmp_path.parent
        payload = payload_fn(outside)

        fn = getattr(client, method)
        resp = fn(route, json=payload)
        assert resp.status_code == 403, (
            f"{route}: expected 403 for outside-root path, got {resp.status_code}; "
            f"body={resp.text!r}"
        )


# ---------------------------------------------------------------------------
# POST /api/execute — happy path
# ---------------------------------------------------------------------------


class TestPostExecuteHappyPath:
    def test_delete_decided_files_are_deleted_and_outcome_written(
        self, client_with_roots, tmp_path
    ):
        """Files decided 'delete' are removed from disk and outcome='deleted' in DB."""
        client, root = client_with_roots
        files = _make_real_files(tmp_path)
        f1, f2 = files[0], files[1]

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
        ])

        resp = client.post("/api/execute", json={
            "manifest_path": str(manifest),
            "recycle": False,   # direct unlink in tests
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body["success_paths"]) == {str(f1), str(f2)}
        assert body["failed"] == []
        assert body["missing"] == []

        # Files must be gone from disk.
        assert not f1.exists()
        assert not f2.exists()

        # outcome='deleted' must be written to DB.
        assert _read_outcome(manifest, str(f1)) == "deleted"
        assert _read_outcome(manifest, str(f2)) == "deleted"

    def test_ignore_decided_files_get_outcome_ignored(
        self, client_with_roots, tmp_path
    ):
        """Files decided 'ignore' are NOT deleted on disk but get outcome='ignored'."""
        client, root = client_with_roots
        files = _make_real_files(tmp_path)
        f1, f2 = files[0], files[1]

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "ignore",
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "",
                "user_decision": "ignore",
                "file_size_bytes": 128,
            },
        ])

        resp = client.post("/api/execute", json={
            "manifest_path": str(manifest),
            "recycle": False,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body["ignored"]) == {str(f1), str(f2)}
        assert body["success_paths"] == []

        # Files must still exist.
        assert f1.exists()
        assert f2.exists()

        assert _read_outcome(manifest, str(f1)) == "ignored"
        assert _read_outcome(manifest, str(f2)) == "ignored"

    def test_groups_returned_reflect_post_execute_state(
        self, client_with_roots, tmp_path
    ):
        """Response groups are a post-mutation re-read: deleted paths absent, keepers present.

        FIX 3: strengthen beyond isinstance check — assert that:
        - Every deleted file's path does NOT appear in any group member.
        - At least one surviving keeper path IS present in a group member.
        """
        client, root = client_with_roots
        files = _make_real_files(tmp_path, 4)
        f1, f2, f3, f4 = files

        # Group g1: f1 (keeper, undecided) + f2 (decided delete).
        # Group g2: f3 (keeper, undecided) + f4 (keeper, undecided) — both survive.
        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f3),
                "action": "",
                "group_id": "g2",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f4),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g2",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 128,
            },
        ])

        resp = client.post("/api/execute", json={
            "manifest_path": str(manifest),
            "recycle": False,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert isinstance(body["groups"], list)

        # Collect all source_path values that appear in any returned group.
        all_group_paths: set[str] = set()
        for group in body["groups"]:
            # Groups are dicts with a 'members' list or similar; collect any
            # str values that look like file paths.
            _collect_paths(group, all_group_paths)

        # f2 was deleted — it must NOT appear in any returned group.
        assert str(f2) not in all_group_paths, (
            "deleted file f2 still present in returned groups — stale re-read"
        )

        # f1 becomes a singleton after f2 is deleted (group g1 only has f1 now),
        # so it will be absent from groups too (singletons are pruned by load()).
        # f3 and f4 both survive in group g2 — at least one of them must appear.
        assert str(f3) in all_group_paths or str(f4) in all_group_paths, (
            "surviving keeper files (f3/f4) not found in returned groups — groups not re-read"
        )


def _collect_paths(obj: object, acc: set[str]) -> None:
    """Recursively collect string values from a nested dict/list structure."""
    if isinstance(obj, str):
        acc.add(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_paths(v, acc)
    elif isinstance(obj, list):
        for item in obj:
            _collect_paths(item, acc)


# ---------------------------------------------------------------------------
# POST /api/execute — per-file consistency (D7 guarantee)
# ---------------------------------------------------------------------------


class TestExecutePerFileConsistency:
    def test_missing_file_does_not_prevent_other_successes(
        self, client_with_roots, tmp_path
    ):
        """One missing file lands in `missing`; the other is successfully deleted."""
        client, root = client_with_roots
        files = _make_real_files(tmp_path, 2)
        f_exists, f_missing_on_disk = files[0], files[1]

        # Remove f_missing_on_disk from disk BEFORE the call.
        f_missing_on_disk.unlink()

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f_exists),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f_missing_on_disk),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
        ])

        resp = client.post("/api/execute", json={
            "manifest_path": str(manifest),
            "recycle": False,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # The existing file was successfully deleted.
        assert str(f_exists) in body["success_paths"]
        assert _read_outcome(manifest, str(f_exists)) == "deleted"

        # The missing file is in `missing`, NOT in `failed` or `success_paths`.
        assert str(f_missing_on_disk) in body["missing"]
        assert str(f_missing_on_disk) not in body["success_paths"]


# ---------------------------------------------------------------------------
# POST /api/execute — locked gate
# ---------------------------------------------------------------------------


class TestExecuteLockedGate:
    def test_locked_delete_row_without_force_returns_409(
        self, client_with_roots, tmp_path
    ):
        """A locked delete-decided row without force_locked=True → 409 locked_paths."""
        client, root = client_with_roots
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "is_locked": 1,
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
        ])

        resp = client.post("/api/execute", json={
            "manifest_path": str(manifest),
            "recycle": False,
            "force_locked": False,
        })
        assert resp.status_code == 409, resp.text
        body = resp.json()
        assert body["detail"]["code"] == "locked_paths"
        assert str(f1) in body["detail"]["locked_paths"]

        # File must still exist — nothing was deleted.
        assert f1.exists()
        assert f2.exists()

    def test_force_locked_unlocks_and_deletes(self, client_with_roots, tmp_path):
        """force_locked=True unlocks the locked row and proceeds with deletion."""
        client, root = client_with_roots
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "is_locked": 1,
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
        ])

        resp = client.post("/api/execute", json={
            "manifest_path": str(manifest),
            "recycle": False,
            "force_locked": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert str(f1) in body["success_paths"]
        assert str(f2) in body["success_paths"]

        # Row was unlocked and deleted.
        assert not f1.exists()
        assert not f2.exists()
        assert _read_is_locked(manifest, str(f1)) is False


# ---------------------------------------------------------------------------
# POST /api/execute — mutex
# ---------------------------------------------------------------------------


class TestExecuteMutex:
    def test_concurrent_execute_returns_409_real_lock(self, tmp_path):
        """Real concurrency test: two simultaneous POSTs, one gets 200 the other 409.

        FIX 2: replaces the monkeypatch with a genuine two-request race.
        The first request holds the asyncio.Lock via a slow service shim
        (asyncio.sleep(0.25)); the second request, dispatched concurrently,
        sees the lock already held and returns 409 execute_already_running.
        httpx.AsyncClient + ASGITransport runs both requests on the same
        event loop so the asyncio.Lock behaves exactly as in production.
        """
        import app.web.routes.execute as _exec_route

        app_inst = create_app()

        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
        ])

        # Patch _run_execute so the first call sleeps (holds the lock) long enough
        # for the second request to arrive and see the lock already acquired.
        original_run = _exec_route._run_execute

        def _slow_run(manifest_path, scope_paths, recycle, force_locked, allowed_roots):
            import time
            time.sleep(0.25)  # hold lock for 250 ms
            return original_run(manifest_path, scope_paths, recycle, force_locked, allowed_roots)

        _exec_route._run_execute = _slow_run

        payload = {
            "manifest_path": str(manifest),
            "recycle": False,
        }

        try:
            async def _run():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app_inst),
                    # "testserver" (not the arbitrary "test"): the #662
                    # Origin/Host guard allowlists loopback + the standard
                    # test Host only, so a bare made-up hostname is 403.
                    base_url="http://testserver",
                ) as client:
                    # Set allowed_roots on app.state after lifespan startup.
                    app_inst.state.allowed_roots = [tmp_path]
                    # Fire both requests concurrently.
                    r1, r2 = await asyncio.gather(
                        client.post("/api/execute", json=payload),
                        client.post("/api/execute", json=payload),
                        return_exceptions=False,
                    )
                    return r1, r2

            r1, r2 = asyncio.run(_run())
        finally:
            _exec_route._run_execute = original_run

        statuses = {r1.status_code, r2.status_code}

        # Exactly one must succeed (200) and the other must be rejected (409).
        assert 200 in statuses, f"Neither request succeeded: {r1.status_code}, {r2.status_code}"
        assert 409 in statuses, f"Neither request was rejected: {r1.status_code}, {r2.status_code}"

        # The 409 response must carry the correct code.
        rejected = r1 if r1.status_code == 409 else r2
        assert rejected.json()["detail"]["code"] == "execute_already_running"


# ---------------------------------------------------------------------------
# FIX 1 REGRESSION TEST (route-level)
# ---------------------------------------------------------------------------


class TestExecuteOutOfRootRouteLevel:
    """Route-level regression for FIX 1: out-of-root source_path must not be deleted.

    The manifest lives under an allowed root, but one of its source_path rows
    points outside.  After execute, the out-of-root file must still exist.
    """

    def test_out_of_root_source_path_not_deleted(self, tmp_path):
        """End-to-end: manifest under allowed root, source_path row outside — file survives."""
        # manifest_dir is the allowed root; outside_dir is not.
        manifest_dir = tmp_path / "allowed"
        manifest_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        app_inst = create_app()

        outside_file = outside_dir / "must_survive.bin"
        outside_file.write_bytes(b"\xCC" * 128)
        inside_file = manifest_dir / "normal.bin"
        inside_file.write_bytes(b"\xDD" * 128)

        manifest = _make_manifest(manifest_dir, [
            {
                "source_path": str(inside_file),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
            {
                "source_path": str(outside_file),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
        ])

        with TestClient(app_inst, raise_server_exceptions=True) as client:
            # Only manifest_dir is an allowed root — outside_dir is not.
            app_inst.state.allowed_roots = [manifest_dir]

            resp = client.post("/api/execute", json={
                "manifest_path": str(manifest),
                "recycle": False,
            })

        assert resp.status_code == 200, resp.text
        body = resp.json()

        # inside_file is deleted normally.
        assert not inside_file.exists()
        assert str(inside_file) in body["success_paths"]

        # outside_file MUST still exist on disk.
        assert outside_file.exists(), (
            "out-of-root file was deleted — FIX 1 not applied at route level"
        )

        # DB outcome for the outside_file must still be ''.
        assert _read_outcome(manifest, str(outside_file)) == "", (
            "out-of-root file's outcome was written — FIX 1 not applied"
        )

        # The out-of-root path must appear in the response's failed list.
        failed_paths = {entry[0]: entry[1] for entry in body["failed"]}
        assert str(outside_file) in failed_paths, (
            "out-of-root path not in response failed list"
        )
        assert failed_paths[str(outside_file)] == "outside_allowed_roots"


# ---------------------------------------------------------------------------
# POST /api/remove
# ---------------------------------------------------------------------------


class TestPostRemove:
    def test_removes_files_with_outcome_ignored(self, client_with_roots, tmp_path):
        """remove sets outcome='ignored' and does NOT delete files from disk."""
        client, root = client_with_roots
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 128,
            },
        ])

        resp = client.post("/api/remove", json={
            "manifest_path": str(manifest),
            "file_paths": [str(f1), str(f2)],
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["removed"] == 2

        # Files must still exist on disk.
        assert f1.exists()
        assert f2.exists()

        # outcome='ignored' in DB.
        assert _read_outcome(manifest, str(f1)) == "ignored"
        assert _read_outcome(manifest, str(f2)) == "ignored"

    def test_locked_row_without_force_returns_409(self, client_with_roots, tmp_path):
        """A locked row in file_paths without force_locked → 409."""
        client, root = client_with_roots
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "is_locked": 1,
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 128,
            },
        ])

        resp = client.post("/api/remove", json={
            "manifest_path": str(manifest),
            "file_paths": [str(f1), str(f2)],
            "force_locked": False,
        })
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "locked_paths"


# ---------------------------------------------------------------------------
# POST /api/prune
# ---------------------------------------------------------------------------


class TestPostPrune:
    def _make_singleton_manifest(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        """One group with 2 files; one partner is already outcome='deleted' → singleton."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",           # in-review singleton (partner is deleted)
                "user_decision": "",
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "deleted",    # already done
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
        ])
        return manifest, f1, f2

    def test_plain_singleton_is_pruned(self, client_with_roots, tmp_path):
        """Plain singleton (user_decision='') is removed from review."""
        client, root = client_with_roots
        manifest, f1, _ = self._make_singleton_manifest(tmp_path)

        resp = client.post("/api/prune", json={
            "manifest_path": str(manifest),
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert str(f1) in body["pruned"]
        assert body["locked_skipped"] == []
        assert _read_outcome(manifest, str(f1)) == "ignored"

    def test_locked_singleton_is_skipped(self, client_with_roots, tmp_path):
        """A locked singleton stays in locked_skipped and is NOT pruned."""
        client, root = client_with_roots
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "is_locked": 1,      # locked singleton
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "deleted",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
        ])

        resp = client.post("/api/prune", json={
            "manifest_path": str(manifest),
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pruned"] == []
        assert str(f1) in body["locked_skipped"]

        # outcome must be unchanged.
        assert _read_outcome(manifest, str(f1)) == ""

    def test_explicit_paths_prunes_only_named(self, client_with_roots, tmp_path):
        """`paths` in the request body threads through to explicit-mode prune
        (#686): only the named singleton is pruned, not every plain one."""
        client, root = client_with_roots
        f1, f2, f3, f4 = _make_real_files(tmp_path, 4)
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "action": "", "group_id": "g1",
             "outcome": "", "user_decision": "", "file_size_bytes": 128},
            {"source_path": str(f2), "action": "REVIEW_DUPLICATE", "group_id": "g1",
             "hamming_distance": 4, "outcome": "deleted", "user_decision": "delete",
             "file_size_bytes": 128},
            {"source_path": str(f3), "action": "", "group_id": "g2",
             "outcome": "", "user_decision": "", "file_size_bytes": 128},
            {"source_path": str(f4), "action": "REVIEW_DUPLICATE", "group_id": "g2",
             "hamming_distance": 4, "outcome": "deleted", "user_decision": "delete",
             "file_size_bytes": 128},
        ])

        resp = client.post("/api/prune", json={
            "manifest_path": str(manifest),
            "paths": [str(f3)],
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pruned"] == [str(f3)]
        assert _read_outcome(manifest, str(f3)) == "ignored"
        assert _read_outcome(manifest, str(f1)) == ""  # the other plain singleton survives


class TestPostPruneCandidates:
    def test_classifies_singletons_into_buckets(self, client_with_roots, tmp_path):
        """POST /api/prune/candidates (#686) returns plain/actioned/locked."""
        client, root = client_with_roots
        f1, f2, f3, f4, f5, f6 = _make_real_files(tmp_path, 6)
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "action": "", "group_id": "g1",
             "outcome": "", "user_decision": "", "file_size_bytes": 64},
            {"source_path": str(f2), "action": "REVIEW_DUPLICATE", "group_id": "g1",
             "hamming_distance": 2, "outcome": "deleted", "user_decision": "delete",
             "file_size_bytes": 64},
            {"source_path": str(f3), "action": "", "group_id": "g2",
             "outcome": "", "user_decision": "delete", "file_size_bytes": 64},
            {"source_path": str(f4), "action": "REVIEW_DUPLICATE", "group_id": "g2",
             "hamming_distance": 2, "outcome": "deleted", "user_decision": "delete",
             "file_size_bytes": 64},
            {"source_path": str(f5), "action": "", "group_id": "g3",
             "outcome": "", "user_decision": "", "is_locked": 1, "file_size_bytes": 64},
            {"source_path": str(f6), "action": "REVIEW_DUPLICATE", "group_id": "g3",
             "hamming_distance": 2, "outcome": "deleted", "user_decision": "delete",
             "file_size_bytes": 64},
        ])

        resp = client.post("/api/prune/candidates", json={"manifest_path": str(manifest)})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["plain"] == [str(f1)]
        assert body["actioned"] == [str(f3)]
        assert body["locked"] == [str(f5)]


# ---------------------------------------------------------------------------
# POST /api/save
# ---------------------------------------------------------------------------


class TestPostSave:
    def test_in_place_save(self, client_with_roots, tmp_path):
        """In-place save (target_path=None) checkpoints WAL and returns in-review count.

        FIX 4: updated is now the count of outcome='' rows, not a MainVM-based write.
        Decisions were already persisted per-PATCH; in-place save just flushes WAL.
        """
        client, root = client_with_roots
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 128,
            },
        ])

        resp = client.post("/api/save", json={
            "manifest_path": str(manifest),
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["saved_to"] == str(manifest)
        assert isinstance(body["updated"], int)
        # Both rows are in-review (outcome=''), so count should be 2.
        assert body["updated"] == 2

        # FIX 4: decisions are persisted per-PATCH; verify user_decision is still correct.
        conn = sqlite3.connect(str(manifest))
        try:
            row = conn.execute(
                "SELECT user_decision FROM migration_manifest WHERE source_path = ?",
                (str(f1),),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == "delete"

    def test_save_as_creates_copy_with_decisions(self, client_with_roots, tmp_path):
        """save-as creates a copy that carries the same decisions.

        FIX 4: copy2 after WAL checkpoint carries decisions without a redundant
        MainVM load + repo.save into the copy.
        """
        client, root = client_with_roots
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 128,
            },
        ])
        target = tmp_path / "copy.sqlite"

        resp = client.post("/api/save", json={
            "manifest_path": str(manifest),
            "target_path": str(target),
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["saved_to"] == str(target)
        assert target.exists(), "save-as must create the target file"

        # FIX 4: verify the copy carries the decision (via WAL checkpoint + copy2).
        conn = sqlite3.connect(str(target))
        try:
            row = conn.execute(
                "SELECT user_decision FROM migration_manifest WHERE source_path = ?",
                (str(f1),),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == "delete", (
            "save-as copy does not carry decisions — FIX 4 not applied"
        )

    def test_save_as_outside_roots_returns_403(self, client_with_roots, tmp_path):
        """target_path outside allowed roots → 403."""
        client, root = client_with_roots
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 128,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 4,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 128,
            },
        ])
        outside_target = tmp_path.parent / "evil_copy.sqlite"

        resp = client.post("/api/save", json={
            "manifest_path": str(manifest),
            "target_path": str(outside_target),
        })
        assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# POST /api/reveal
# ---------------------------------------------------------------------------


class TestPostReveal:
    """Tests for POST /api/reveal.

    The Starlette TestClient sends client.host='testclient' (not 127.0.0.1).
    We test the localhost guard by monkeypatching the route module's client
    host extraction to return the desired IP.
    """

    def test_non_localhost_returns_403(self, tmp_path, monkeypatch):
        """Requests from non-localhost IP → 403."""
        files = _make_real_files(tmp_path, 1)
        f = files[0]

        _app = create_app()

        # Inject middleware BEFORE creating TestClient (before __enter__).
        from starlette.middleware.base import BaseHTTPMiddleware

        class _SpoofClient(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.scope["client"] = ("10.0.0.99", 9999)
                return await call_next(request)

        _app.add_middleware(_SpoofClient)

        with TestClient(_app, raise_server_exceptions=True) as client:
            # Set roots INSIDE context so lifespan doesn't reset them.
            _app.state.allowed_roots = [tmp_path]
            resp = client.post("/api/reveal", json={"file_path": str(f)})

        # Non-localhost client: 403 fires before path guard.
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["code"] == "permission_denied"

    def test_non_windows_returns_501(self, tmp_path, monkeypatch):
        """Non-Windows platform → 501."""
        import app.web.routes.execute as _exec_route

        files = _make_real_files(tmp_path, 1)
        f = files[0]

        # Patch _is_windows() not os.name (patching os.name globally breaks
        # pathlib.PosixPath/WindowsPath on the current platform).
        monkeypatch.setattr(_exec_route, "_is_windows", lambda: False)

        _app = create_app()

        from starlette.middleware.base import BaseHTTPMiddleware

        class _SpoofLocalhost(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.scope["client"] = ("127.0.0.1", 9999)
                return await call_next(request)

        _app.add_middleware(_SpoofLocalhost)

        with TestClient(_app, raise_server_exceptions=True) as client:
            # Set allowed_roots INSIDE context so lifespan doesn't reset it.
            _app.state.allowed_roots = [tmp_path]
            resp = client.post("/api/reveal", json={"file_path": str(f)})

        assert resp.status_code == 501, resp.text
        assert resp.json()["detail"]["code"] == "platform_unsupported"

    def test_localhost_nt_calls_explorer(self, tmp_path, monkeypatch):
        """Localhost on Windows calls subprocess.Popen with explorer /select."""
        import app.web.routes.execute as _exec_route

        files = _make_real_files(tmp_path, 1)
        f = files[0]

        # _is_windows() returns True — stay on the nt path.
        monkeypatch.setattr(_exec_route, "_is_windows", lambda: True)
        popen_calls: list[list[str]] = []

        class _FakePopen:
            def __init__(self, args, **kwargs):
                popen_calls.append(list(args))

        monkeypatch.setattr(_exec_route.subprocess, "Popen", _FakePopen)

        _app = create_app()

        from starlette.middleware.base import BaseHTTPMiddleware

        class _SpoofLocalhost(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.scope["client"] = ("127.0.0.1", 9999)
                return await call_next(request)

        _app.add_middleware(_SpoofLocalhost)

        with TestClient(_app, raise_server_exceptions=True) as client:
            # Set allowed_roots INSIDE context so lifespan doesn't reset it.
            _app.state.allowed_roots = [tmp_path]
            resp = client.post("/api/reveal", json={"file_path": str(f)})

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "ok"

        assert len(popen_calls) == 1
        assert popen_calls[0][0] == "explorer"
        assert popen_calls[0][1] == "/select,"
        # Third argument is the normalized path.
        assert os.path.normpath(str(f)) == popen_calls[0][2]
