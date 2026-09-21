"""Tests for POST /api/action/bulk-decide.

Every test exercises a REAL failure mode (not synthetic coverage padding).
All filesystem interaction uses tmp files / tmp SQLite manifests.

Scaffold mirrors test_web_execute_routes.py:
- _make_manifest: real SQLite via ManifestRepository
- client_with_roots: sets app.state.allowed_roots INSIDE the with-block
- DB-requery verifiers: re-read user_decision / is_locked from SQLite
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.web.main import create_app
from infrastructure.manifest_repository import ManifestRepository
from scanner.manifest import _DDL as _MANIFEST_DDL


# ---------------------------------------------------------------------------
# Shared helpers (same scaffold as test_web_execute_routes.py)
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


def _read_user_decision(manifest_path: Path, file_path: str) -> str:
    """Read the user_decision column for a specific source_path from SQLite."""
    conn = sqlite3.connect(str(manifest_path))
    try:
        row = conn.execute(
            "SELECT user_decision FROM migration_manifest WHERE source_path = ?",
            (file_path,),
        ).fetchone()
        return row[0] if row and row[0] is not None else ""
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


@pytest.fixture()
def client_with_roots(tmp_path):
    """TestClient with tmp_path registered as an allowed root."""
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        app.state.allowed_roots = [tmp_path]
        yield c, tmp_path


# ---------------------------------------------------------------------------
# POST /api/action/bulk-decide — allowed-roots guard
# ---------------------------------------------------------------------------


class TestBulkDecideManifestLocation:
    """The manifest .db itself is NOT roots-gated — it is an output file the
    user may store anywhere (exactly as GET /api/manifest loads it). The real
    security boundary is the affected PHOTO paths (see the out-of-root source
    filter test below), NOT the manifest's own location.

    Regression guard: a live ActionDialog run 403'd a perfectly valid db that
    lived in the system temp dir rather than under the scanned photo roots —
    the old test co-located the db with the photos under one root, which
    masked the bug.
    """

    def test_manifest_outside_photo_roots_still_applies(self, tmp_path):
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        fa = files_dir / "alpha.jpg"
        fb = files_dir / "beta.jpg"
        fa.write_bytes(b"\x00" * 64)
        fb.write_bytes(b"\x00" * 64)

        # Manifest db lives at tmp_path (the PARENT of the photo root) — i.e.
        # OUTSIDE the allowed photo root below.
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(fa), "action": "REVIEW_DUPLICATE", "group_id": "g1",
             "outcome": "", "user_decision": "", "file_size_bytes": 64},
            {"source_path": str(fb), "action": "REVIEW_DUPLICATE", "group_id": "g1",
             "outcome": "", "user_decision": "", "file_size_bytes": 64},
        ])

        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            # allowed_roots = the PHOTO dir only; the manifest db (at tmp_path)
            # is OUTSIDE it. The decision must still apply (db isn't gated),
            # and the in-root photo is mutated.
            app.state.allowed_roots = [files_dir]
            resp = client.post("/api/action/bulk-decide", json={
                "manifest_path": str(manifest),
                "field": "File Name",
                "pattern": "^alpha",
                "action": "delete",
            })
        assert resp.status_code == 200, resp.text
        assert _read_user_decision(manifest, str(fa)) == "delete"
        assert _read_user_decision(manifest, str(fb)) == ""


# ---------------------------------------------------------------------------
# POST /api/action/bulk-decide — plain regex decision
# ---------------------------------------------------------------------------


class TestBulkDecidePlainRegexDecision:
    """Plain-regex pattern → 200, DB confirms decision on matched rows only."""

    def test_regex_sets_decision_on_matched_rows_only(self, client_with_roots, tmp_path):
        """Rows whose File Name matches the regex get 'delete'; non-matching rows unchanged."""
        client, root = client_with_roots
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        fa = files_dir / "alpha.jpg"
        fb = files_dir / "beta.jpg"
        fa.write_bytes(b"\x00" * 64)
        fb.write_bytes(b"\x00" * 64)

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(fa),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(fb),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
        ])

        resp = client.post("/api/action/bulk-decide", json={
            "manifest_path": str(manifest),
            "field": "File Name",
            "pattern": "^alpha",
            "action": "delete",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] == 1
        assert str(fa) in body["affected_paths"]
        assert str(fb) not in body["affected_paths"]
        assert body["action_applied"] == "decision"
        assert len(body["groups"]) > 0

        # Re-query DB: alpha has decision 'delete', beta unchanged.
        assert _read_user_decision(manifest, str(fa)) == "delete"
        assert _read_user_decision(manifest, str(fb)) == ""


# ---------------------------------------------------------------------------
# POST /api/action/bulk-decide — __cmp__ pattern on numeric field
# ---------------------------------------------------------------------------


class TestBulkDecideCmpPattern:
    """__cmp__:>=:VALUE on a numeric field → correct rows mutated."""

    def test_cmp_ge_size_sets_decision_on_large_files(self, client_with_roots, tmp_path):
        """Score >= 0.8 should match high-score rows only."""
        client, root = client_with_roots
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        f_hi = files_dir / "hi.jpg"
        f_lo = files_dir / "lo.jpg"
        f_hi.write_bytes(b"\x00" * 64)
        f_lo.write_bytes(b"\x00" * 64)

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f_hi),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
                "score": 0.9,
            },
            {
                "source_path": str(f_lo),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
                "score": 0.3,
            },
        ])

        # Pattern: Score >= 0.8 → delete the high-score row
        resp = client.post("/api/action/bulk-decide", json={
            "manifest_path": str(manifest),
            "field": "Score",
            "pattern": "__cmp__:>=:0.8",
            "action": "delete",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] == 1
        assert str(f_hi) in body["affected_paths"]

        assert _read_user_decision(manifest, str(f_hi)) == "delete"
        assert _read_user_decision(manifest, str(f_lo)) == ""


# ---------------------------------------------------------------------------
# POST /api/action/bulk-decide — __top_n__ pattern
# ---------------------------------------------------------------------------


class TestBulkDecideTopNPattern:
    """__top_n__:1:desc selects the top-scored row per group."""

    def test_top1_desc_sets_decision_on_highest_score_per_group(
        self, client_with_roots, tmp_path
    ):
        """Top-1 desc (highest score) in one group → exactly 1 row mutated."""
        client, root = client_with_roots
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        f_top = files_dir / "top.jpg"
        f_bot = files_dir / "bot.jpg"
        f_top.write_bytes(b"\x00" * 64)
        f_bot.write_bytes(b"\x00" * 64)

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f_top),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
                "score": 0.95,
            },
            {
                "source_path": str(f_bot),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
                "score": 0.55,
            },
        ])

        resp = client.post("/api/action/bulk-decide", json={
            "manifest_path": str(manifest),
            "field": "Score",
            "pattern": "__top_n__:1:desc",
            "action": "__ignore__",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] == 1
        assert str(f_top) in body["affected_paths"]

        assert _read_user_decision(manifest, str(f_top)) == "ignore"
        assert _read_user_decision(manifest, str(f_bot)) == ""


# ---------------------------------------------------------------------------
# POST /api/action/bulk-decide — preview mode
# ---------------------------------------------------------------------------


class TestBulkDecidePreview:
    """preview=true → 200, affected_paths populated, DB UNCHANGED."""

    def test_preview_does_not_mutate_db(self, client_with_roots, tmp_path):
        client, root = client_with_roots
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        fa = files_dir / "a.jpg"
        fb = files_dir / "b.jpg"
        fa.write_bytes(b"\x00" * 64)
        fb.write_bytes(b"\x00" * 64)

        # Need at least 2 rows in the same group so ManifestRepository.load()
        # doesn't skip the group as a singleton orphan.
        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(fa),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(fb),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
        ])

        resp = client.post("/api/action/bulk-decide", json={
            "manifest_path": str(manifest),
            "field": "File Name",
            "pattern": "^a",   # matches only fa
            "action": "delete",
            "preview": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] == 1
        assert str(fa) in body["affected_paths"]
        assert "groups" in body

        # DB must be UNCHANGED after preview.
        assert _read_user_decision(manifest, str(fa)) == ""
        assert _read_user_decision(manifest, str(fb)) == ""


# ---------------------------------------------------------------------------
# POST /api/action/bulk-decide — empty pattern
# ---------------------------------------------------------------------------


class TestBulkDecideEmptyPattern:
    """Empty pattern → matched==0, no mutation."""

    def test_empty_pattern_returns_zero_matched(self, client_with_roots, tmp_path):
        client, root = client_with_roots
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        fa = files_dir / "a.jpg"
        fb = files_dir / "b.jpg"
        fa.write_bytes(b"\x00" * 64)
        fb.write_bytes(b"\x00" * 64)

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(fa),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(fb),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
        ])

        resp = client.post("/api/action/bulk-decide", json={
            "manifest_path": str(manifest),
            "field": "File Name",
            "pattern": "",
            "action": "delete",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] == 0
        assert body["affected_paths"] == []
        # DB unchanged
        assert _read_user_decision(manifest, str(fa)) == ""
        assert _read_user_decision(manifest, str(fb)) == ""


# ---------------------------------------------------------------------------
# POST /api/action/bulk-decide — __lock__ action
# ---------------------------------------------------------------------------


class TestBulkDecideLockAction:
    """__lock__ action → is_locked True on matched rows."""

    def test_lock_action_sets_is_locked(self, client_with_roots, tmp_path):
        client, root = client_with_roots
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        fa = files_dir / "a.jpg"
        fb = files_dir / "b.jpg"
        fa.write_bytes(b"\x00" * 64)
        fb.write_bytes(b"\x00" * 64)

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(fa),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
                "is_locked": 0,
            },
            {
                "source_path": str(fb),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
                "is_locked": 0,
            },
        ])

        resp = client.post("/api/action/bulk-decide", json={
            "manifest_path": str(manifest),
            "field": "File Name",
            "pattern": "^a",
            "action": "__lock__",
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] == 1
        assert body["action_applied"] == "lock"

        assert _read_is_locked(manifest, str(fa)) is True
        assert _read_is_locked(manifest, str(fb)) is False


# ---------------------------------------------------------------------------
# POST /api/action/bulk-decide — out-of-root source_path filter
# ---------------------------------------------------------------------------


class TestBulkDecideRootFilter:
    """An in-root manifest referencing an out-of-root source_path is excluded."""

    def test_out_of_root_source_path_is_filtered_not_mutated(
        self, tmp_path
    ):
        """In-root manifest has a source_path outside allowed_roots → filtered out."""
        app = create_app()
        with TestClient(app, raise_server_exceptions=True) as client:
            # Allowed root is a sub-dir of tmp_path.
            root_dir = tmp_path / "root"
            root_dir.mkdir()
            app.state.allowed_roots = [root_dir]

            # Manifest lives inside root_dir (so the manifest path check passes).
            in_root_file = root_dir / "inside.jpg"
            in_root_file.write_bytes(b"\x00" * 64)

            # A second file lives OUTSIDE root_dir.
            outside_dir = tmp_path / "outside"
            outside_dir.mkdir()
            outside_file = outside_dir / "outside.jpg"
            outside_file.write_bytes(b"\x00" * 64)

            manifest = _make_manifest(root_dir, [
                {
                    "source_path": str(in_root_file),
                    "action": "REVIEW_DUPLICATE",
                    "group_id": "g1",
                    "outcome": "",
                    "user_decision": "",
                    "file_size_bytes": 64,
                },
                {
                    "source_path": str(outside_file),
                    "action": "REVIEW_DUPLICATE",
                    "group_id": "g1",
                    "outcome": "",
                    "user_decision": "",
                    "file_size_bytes": 64,
                },
            ])

            resp = client.post("/api/action/bulk-decide", json={
                "manifest_path": str(manifest),
                "field": "File Name",
                "pattern": ".*",
                "action": "delete",
            })
            assert resp.status_code == 200, resp.text
            body = resp.json()
            # Only the in-root path was mutated; the out-of-root was filtered.
            assert body["matched"] == 1
            assert str(in_root_file) in body["affected_paths"]
            assert str(outside_file) not in body["affected_paths"]

            # In-root path has decision 'delete'; out-of-root survives un-mutated.
            assert _read_user_decision(manifest, str(in_root_file)) == "delete"
            assert _read_user_decision(manifest, str(outside_file)) == ""


# ---------------------------------------------------------------------------
# POST /api/action/bulk-decide — locked-row gate
# ---------------------------------------------------------------------------


class TestBulkDecideLockedRowGate:
    """Decision on locked rows with force_locked=False → 409; True → 200 + applied."""

    def test_locked_row_without_force_returns_409(self, client_with_roots, tmp_path):
        client, root = client_with_roots
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        fa = files_dir / "locked.jpg"
        fb = files_dir / "peer.jpg"
        fa.write_bytes(b"\x00" * 64)
        fb.write_bytes(b"\x00" * 64)

        # Need 2 rows in the same group so ManifestRepository.load() doesn't
        # skip the group as an orphaned singleton.
        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(fa),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "is_locked": 1,
                "file_size_bytes": 64,
            },
            {
                "source_path": str(fb),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "is_locked": 0,
                "file_size_bytes": 64,
            },
        ])

        resp = client.post("/api/action/bulk-decide", json={
            "manifest_path": str(manifest),
            "field": "File Name",
            "pattern": "locked",  # matches only fa
            "action": "delete",
            "force_locked": False,
        })
        assert resp.status_code == 409, resp.text
        body = resp.json()
        detail = body.get("detail", {})
        assert detail.get("code") == "locked_paths"
        assert str(fa) in detail.get("locked_paths", [])
        # #674 — the 409 carries the FULL matched count so the FE can size the
        # unlocked subset without a preview. Only fa matches "locked", so the
        # full matched set is 1 (all of it locked → 0 unlocked).
        assert detail.get("matched_total") == 1

        # DB must be UNCHANGED.
        assert _read_user_decision(manifest, str(fa)) == ""

    def test_locked_row_with_force_true_applies_decision(self, client_with_roots, tmp_path):
        client, root = client_with_roots
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        fa = files_dir / "locked.jpg"
        fb = files_dir / "peer.jpg"
        fa.write_bytes(b"\x00" * 64)
        fb.write_bytes(b"\x00" * 64)

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(fa),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "is_locked": 1,
                "file_size_bytes": 64,
            },
            {
                "source_path": str(fb),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "is_locked": 0,
                "file_size_bytes": 64,
            },
        ])

        resp = client.post("/api/action/bulk-decide", json={
            "manifest_path": str(manifest),
            "field": "File Name",
            "pattern": "locked",  # matches only fa
            "action": "delete",
            "force_locked": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] == 1
        assert str(fa) in body["affected_paths"]

        # Decision must be applied despite the lock.
        assert _read_user_decision(manifest, str(fa)) == "delete"
        # #733 — "Unlock & Apply All" UNLOCKS the row in the same write (Qt
        # verdict parity; execute_service's force path does the same). Before
        # the fix the decision landed but is_locked stayed True, so the very
        # next execute re-tripped the lock gate on a row the user had already
        # force-confirmed.
        assert _read_is_locked(manifest, str(fa)) is False
        # fb is NOT in the pattern match, so it stays undecided.
        assert _read_user_decision(manifest, str(fb)) == ""
        assert _read_is_locked(manifest, str(fb)) is False

    def test_preview_on_locked_row_returns_200_not_409(self, client_with_roots, tmp_path):
        # Ship-blocker (adversarial review, both peers): preview is a DRY-RUN
        # and must NEVER trip the locked-rows gate. A preview of a decision
        # that matches a locked row must return 200 with the locked path in
        # affected_paths (so the FE can show what a real apply would touch),
        # NOT 409, and must write nothing.
        client, root = client_with_roots
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        fa = files_dir / "locked.jpg"
        fb = files_dir / "peer.jpg"
        fa.write_bytes(b"\x00" * 64)
        fb.write_bytes(b"\x00" * 64)

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(fa), "action": "REVIEW_DUPLICATE",
                "group_id": "g1", "outcome": "", "user_decision": "",
                "is_locked": 1, "file_size_bytes": 64,
            },
            {
                "source_path": str(fb), "action": "REVIEW_DUPLICATE",
                "group_id": "g1", "outcome": "", "user_decision": "",
                "is_locked": 0, "file_size_bytes": 64,
            },
        ])

        resp = client.post("/api/action/bulk-decide", json={
            "manifest_path": str(manifest),
            "field": "File Name",
            "pattern": "locked",  # matches the locked row
            "action": "delete",
            "force_locked": False,
            "preview": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert str(fa) in body["affected_paths"]
        # No write — the locked row stays undecided after a preview.
        assert _read_user_decision(manifest, str(fa)) == ""


# ---------------------------------------------------------------------------
# POST /api/action/bulk-decide — malformed sentinel → 422
# ---------------------------------------------------------------------------


class TestBulkDecideMalformedSentinel:
    """A malformed __cmp__/__top_n__ sentinel is rejected (422), never treated
    as a literal regex (which could match + mutate a file). Mirrors the Qt
    handler raising ValueError on an undecodable prefix."""

    def test_malformed_cmp_sentinel_returns_422(self, client_with_roots, tmp_path):
        client, root = client_with_roots
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        fa = files_dir / "a.jpg"
        fb = files_dir / "b.jpg"
        fa.write_bytes(b"\x00" * 64)
        fb.write_bytes(b"\x00" * 64)

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(fa), "action": "REVIEW_DUPLICATE",
                "group_id": "g1", "outcome": "", "user_decision": "",
                "is_locked": 0, "file_size_bytes": 64,
            },
            {
                "source_path": str(fb), "action": "REVIEW_DUPLICATE",
                "group_id": "g1", "outcome": "", "user_decision": "",
                "is_locked": 0, "file_size_bytes": 64,
            },
        ])

        resp = client.post("/api/action/bulk-decide", json={
            "manifest_path": str(manifest),
            "field": "File Name",
            "pattern": "__cmp__:bogus",  # prefix commits, payload undecodable
            "action": "delete",
        })
        assert resp.status_code == 422, resp.text
        # Nothing mutated.
        assert _read_user_decision(manifest, str(fa)) == ""


# ---------------------------------------------------------------------------
# POST /api/action/bulk-decide — skip_locked (Apply to Unlocked Only, #674)
# ---------------------------------------------------------------------------


class TestBulkDecideSkipLocked:
    """skip_locked applies the decision to the UNLOCKED subset of matched rows,
    leaving locked rows fully untouched — the Qt LockedRowsConfirmDialog
    APPLY_UNLOCKED_ONLY verdict ported to the web (#674). Every test below
    exercises a real failure mode, not synthetic branch coverage.
    """

    def _two_row_manifest(self, tmp_path, *, locked_basename: str):
        """Build a 2-row same-group manifest; lock exactly one row."""
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        fa = files_dir / "shot_01.jpg"
        fb = files_dir / "shot_02.jpg"
        fa.write_bytes(b"\x00" * 64)
        fb.write_bytes(b"\x00" * 64)
        rows = []
        for f in (fa, fb):
            rows.append({
                "source_path": str(f), "action": "REVIEW_DUPLICATE",
                "group_id": "g1", "outcome": "", "user_decision": "",
                "is_locked": 1 if f.name == locked_basename else 0,
                "file_size_bytes": 64,
            })
        return _make_manifest(tmp_path, rows), fa, fb

    def test_skip_locked_applies_to_unlocked_subset_only(
        self, client_with_roots, tmp_path
    ):
        """Regex matches BOTH a locked and an unlocked row → skip_locked writes
        the unlocked row only; the locked row keeps is_locked + decision=''.

        Doubles as the route-threading guard: a positional miswire of
        skip_locked through _run_bulk_decide would 409 here (or mutate the
        locked row), so a green result proves skip_locked reached the service.
        """
        client, root = client_with_roots
        manifest, fa, fb = self._two_row_manifest(tmp_path, locked_basename="shot_01.jpg")

        resp = client.post("/api/action/bulk-decide", json={
            "manifest_path": str(manifest),
            "field": "File Name",
            "pattern": "shot",          # matches BOTH rows
            "action": "delete",
            "skip_locked": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # matched/affected_paths report the ACTUALLY-applied unlocked subset.
        assert body["matched"] == 1
        assert str(fb) in body["affected_paths"]
        assert str(fa) not in body["affected_paths"]

        # The unlocked row got 'delete'; the locked row is untouched.
        assert _read_user_decision(manifest, str(fb)) == "delete"
        assert _read_user_decision(manifest, str(fa)) == ""
        assert _read_is_locked(manifest, str(fa)) is True

    def test_skip_locked_all_matched_locked_is_noop(
        self, client_with_roots, tmp_path
    ):
        """Regex matches only the locked row → skip_locked applies to NOTHING
        (no error, no mutation) — the all-locked degenerate case."""
        client, root = client_with_roots
        manifest, fa, fb = self._two_row_manifest(tmp_path, locked_basename="shot_01.jpg")

        resp = client.post("/api/action/bulk-decide", json={
            "manifest_path": str(manifest),
            "field": "File Name",
            "pattern": "shot_01",       # matches ONLY the locked row
            "action": "delete",
            "skip_locked": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] == 0
        assert body["affected_paths"] == []
        # Nothing mutated; the locked row stays locked + undecided.
        assert _read_user_decision(manifest, str(fa)) == ""
        assert _read_is_locked(manifest, str(fa)) is True

    def test_force_and_skip_both_true_returns_422(
        self, client_with_roots, tmp_path
    ):
        """force_locked AND skip_locked are contradictory write intents — the
        public HTTP boundary rejects the incoherent body with 422 and mutates
        nothing. (The 3-button FE can never send both; this guards raw clients.)
        """
        client, root = client_with_roots
        manifest, fa, fb = self._two_row_manifest(tmp_path, locked_basename="shot_01.jpg")

        resp = client.post("/api/action/bulk-decide", json={
            "manifest_path": str(manifest),
            "field": "File Name",
            "pattern": "shot",
            "action": "delete",
            "force_locked": True,
            "skip_locked": True,
        })
        assert resp.status_code == 422, resp.text
        # No write on a rejected request.
        assert _read_user_decision(manifest, str(fa)) == ""


# ---------------------------------------------------------------------------
# POST /api/action/apply-best-copy (#744)
# ---------------------------------------------------------------------------


class TestApplyBestCopy:
    """Review-time twin of scan-time auto-select, scoped to one group: the
    top-score row becomes the keeper (decision '' + locked); every row the
    classifier positively identified as a duplicate gets 'delete'; a Ref-tier
    passenger (not a positively-classified duplicate) is left untouched.
    """

    def _three_row_group(self, tmp_path):
        """keeper (fa, score=0.9, REVIEW_DUPLICATE) is the highest score;
        fb (score=0.5, REVIEW_DUPLICATE) is a lower-score duplicate peer;
        fc (score=0.7, action='') is a Ref-tier passenger pulled into the
        group by the ungated pair edge — never auto-deleted."""
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        fa = files_dir / "keeper.jpg"
        fb = files_dir / "peer.jpg"
        fc = files_dir / "passenger.jpg"
        for f in (fa, fb, fc):
            f.write_bytes(b"\x00" * 64)
        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(fa), "action": "REVIEW_DUPLICATE",
                "group_id": "g1", "outcome": "", "user_decision": "",
                "is_locked": 0, "file_size_bytes": 64, "score": 0.9,
            },
            {
                "source_path": str(fb), "action": "REVIEW_DUPLICATE",
                "group_id": "g1", "outcome": "", "user_decision": "",
                "is_locked": 0, "file_size_bytes": 64, "score": 0.5,
            },
            {
                "source_path": str(fc), "action": "",
                "group_id": "g1", "outcome": "", "user_decision": "",
                "is_locked": 0, "file_size_bytes": 64, "score": 0.7,
            },
        ])
        return manifest, fa, fb, fc

    def test_success_keeper_locked_duplicate_deleted_passenger_untouched(
        self, client_with_roots, tmp_path
    ):
        client, root = client_with_roots
        manifest, fa, fb, fc = self._three_row_group(tmp_path)

        resp = client.post("/api/action/apply-best-copy", json={
            "manifest_path": str(manifest),
            "group_number": 1,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action_applied"] == "apply_best_copy"
        assert body["matched"] == 2
        assert set(body["affected_paths"]) == {str(fa), str(fb)}

        # Keeper: highest score, canonical-empty keep decision, locked.
        assert _read_user_decision(manifest, str(fa)) == ""
        assert _read_is_locked(manifest, str(fa)) is True
        # Lower-score DUPLICATE peer: deleted, never locked.
        assert _read_user_decision(manifest, str(fb)) == "delete"
        assert _read_is_locked(manifest, str(fb)) is False
        # Ref-tier passenger: untouched (not a positively-classified duplicate).
        assert _read_user_decision(manifest, str(fc)) == ""
        assert _read_is_locked(manifest, str(fc)) is False

    def test_no_scored_rows_is_noop(self, client_with_roots, tmp_path):
        client, root = client_with_roots
        files_dir = tmp_path / "files"
        files_dir.mkdir()
        fa = files_dir / "a.jpg"
        fb = files_dir / "b.jpg"
        fa.write_bytes(b"\x00" * 64)
        fb.write_bytes(b"\x00" * 64)
        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(fa), "action": "",
                "group_id": "g1", "outcome": "", "user_decision": "",
                "is_locked": 0, "file_size_bytes": 64, "score": None,
            },
            {
                "source_path": str(fb), "action": "",
                "group_id": "g1", "outcome": "", "user_decision": "",
                "is_locked": 0, "file_size_bytes": 64, "score": None,
            },
        ])

        resp = client.post("/api/action/apply-best-copy", json={
            "manifest_path": str(manifest),
            "group_number": 1,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] == 0
        assert body["affected_paths"] == []
        assert _read_user_decision(manifest, str(fa)) == ""

    def test_unknown_group_number_returns_404(self, client_with_roots, tmp_path):
        client, root = client_with_roots
        manifest, fa, fb, fc = self._three_row_group(tmp_path)

        resp = client.post("/api/action/apply-best-copy", json={
            "manifest_path": str(manifest),
            "group_number": 999,
        })
        assert resp.status_code == 404, resp.text
        # Nothing mutated.
        assert _read_user_decision(manifest, str(fa)) == ""

    def test_manifest_not_found_returns_404(self, client_with_roots, tmp_path):
        client, root = client_with_roots
        resp = client.post("/api/action/apply-best-copy", json={
            "manifest_path": str(tmp_path / "does_not_exist.sqlite"),
            "group_number": 1,
        })
        assert resp.status_code == 404, resp.text

    def test_force_and_skip_both_true_returns_422(self, client_with_roots, tmp_path):
        client, root = client_with_roots
        manifest, fa, fb, fc = self._three_row_group(tmp_path)

        resp = client.post("/api/action/apply-best-copy", json={
            "manifest_path": str(manifest),
            "group_number": 1,
            "force_locked": True,
            "skip_locked": True,
        })
        assert resp.status_code == 422, resp.text
        assert _read_user_decision(manifest, str(fa)) == ""

    def test_locked_peer_without_override_returns_409(self, client_with_roots, tmp_path):
        """fb (a delete-target) is locked; no override → 409 locked_paths, no write."""
        client, root = client_with_roots
        manifest, fa, fb, fc = self._three_row_group(tmp_path)
        conn = sqlite3.connect(str(manifest))
        try:
            conn.execute(
                "UPDATE migration_manifest SET is_locked = 1 WHERE source_path = ?",
                (str(fb),),
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.post("/api/action/apply-best-copy", json={
            "manifest_path": str(manifest),
            "group_number": 1,
        })
        assert resp.status_code == 409, resp.text
        detail = resp.json().get("detail", {})
        assert detail.get("code") == "locked_paths"
        assert str(fb) in detail.get("locked_paths", [])
        # The full write-set size (keeper + duplicate peer) is 2.
        assert detail.get("matched_total") == 2
        # Nothing mutated.
        assert _read_user_decision(manifest, str(fa)) == ""
        assert _read_user_decision(manifest, str(fb)) == ""
        assert _read_is_locked(manifest, str(fb)) is True

    def test_force_locked_unlocks_locked_peer_and_applies(
        self, client_with_roots, tmp_path
    ):
        """force_locked=True writes anyway and force-unlocks the previously
        locked delete-target (Qt 'Unlock & Apply All' parity)."""
        client, root = client_with_roots
        manifest, fa, fb, fc = self._three_row_group(tmp_path)
        conn = sqlite3.connect(str(manifest))
        try:
            conn.execute(
                "UPDATE migration_manifest SET is_locked = 1 WHERE source_path = ?",
                (str(fb),),
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.post("/api/action/apply-best-copy", json={
            "manifest_path": str(manifest),
            "group_number": 1,
            "force_locked": True,
        })
        assert resp.status_code == 200, resp.text
        assert _read_user_decision(manifest, str(fb)) == "delete"
        assert _read_is_locked(manifest, str(fb)) is False
        assert _read_user_decision(manifest, str(fa)) == ""
        assert _read_is_locked(manifest, str(fa)) is True

    def test_force_locked_keeper_already_locked_stays_locked(
        self, client_with_roots, tmp_path
    ):
        """The keeper (fa) is ALREADY locked when apply-best-copy re-selects
        it as the keeper; force_locked must not accidentally unlock it — the
        keeper's own lock write (True) from build_auto_select_writes wins."""
        client, root = client_with_roots
        manifest, fa, fb, fc = self._three_row_group(tmp_path)
        conn = sqlite3.connect(str(manifest))
        try:
            conn.execute(
                "UPDATE migration_manifest SET is_locked = 1 WHERE source_path IN (?, ?)",
                (str(fa), str(fb)),
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.post("/api/action/apply-best-copy", json={
            "manifest_path": str(manifest),
            "group_number": 1,
            "force_locked": True,
        })
        assert resp.status_code == 200, resp.text
        # Keeper stays locked (it's supposed to be); peer gets force-unlocked.
        assert _read_is_locked(manifest, str(fa)) is True
        assert _read_user_decision(manifest, str(fa)) == ""
        assert _read_is_locked(manifest, str(fb)) is False
        assert _read_user_decision(manifest, str(fb)) == "delete"

    def test_skip_locked_leaves_locked_peer_untouched(
        self, client_with_roots, tmp_path
    ):
        """skip_locked=True narrows the write to the unlocked subset only —
        the locked peer keeps both its decision and lock state untouched."""
        client, root = client_with_roots
        manifest, fa, fb, fc = self._three_row_group(tmp_path)
        conn = sqlite3.connect(str(manifest))
        try:
            conn.execute(
                "UPDATE migration_manifest SET is_locked = 1 WHERE source_path = ?",
                (str(fb),),
            )
            conn.commit()
        finally:
            conn.close()

        resp = client.post("/api/action/apply-best-copy", json={
            "manifest_path": str(manifest),
            "group_number": 1,
            "skip_locked": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["matched"] == 1
        assert body["affected_paths"] == [str(fa)]

        assert _read_user_decision(manifest, str(fa)) == ""
        assert _read_is_locked(manifest, str(fa)) is True
        # fb untouched — still locked, still no decision.
        assert _read_user_decision(manifest, str(fb)) == ""
        assert _read_is_locked(manifest, str(fb)) is True
        assert _read_user_decision(manifest, str(fb)) == ""
