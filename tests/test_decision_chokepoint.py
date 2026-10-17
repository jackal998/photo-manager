"""A3 — decision writes refuse locked rows inside one BEGIN IMMEDIATE txn.

Pins docs/audits/2026-10-17-write-chokepoint/design.md section 7 (A3):
  - a stale plan cannot write a locked row (refusal lives in the primitive);
  - a locked row outside the visible set (hidden) is still gated (B-5);
  - both lock flags are refused for every decision verb (B-6);
  - load() inside an open write_txn raises at once (ordering rule, 2.4);
  - BE-18 (auto-migrate before write) and BE-20 (PATCH on a pre-outcome
    manifest succeeds) pins.

Real temp SQLite manifests, no mocks of the data layer.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import infrastructure.manifest_repository as mr
from app.web.main import create_app
from core.app_service.action_service import bulk_decide
from core.app_service.review_service import set_decisions
from infrastructure.manifest_repository import ManifestRepository
from scanner.manifest import _DDL as _MANIFEST_DDL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manifest(tmp_path: Path, rows: list[dict]) -> Path:
    """Full-schema manifest: canonical DDL, migrations, then the rows."""
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
            cols = ", ".join(row)
            params = ", ".join(f":{c}" for c in row)
            conn.execute(
                f"INSERT INTO migration_manifest ({cols}) VALUES ({params})", row
            )
        conn.commit()
    finally:
        conn.close()
    return manifest


def _visible_pair(group_id: str = "/grp/a", *, ref_locked: int = 0) -> list[dict]:
    return [
        {"source_path": "/photos/ref.jpg", "action": "", "group_id": group_id,
         "score": 0.9, "file_size_bytes": 2048, "outcome": "",
         "is_locked": ref_locked},
        {"source_path": "/photos/dup.jpg", "action": "REVIEW_DUPLICATE",
         "group_id": group_id, "hamming_distance": 4,
         "file_size_bytes": 1024, "outcome": "", "is_locked": 0},
    ]


def _make_hidden_locked(tmp_path: Path) -> Path:
    """Visible pair plus a LOCKED row with outcome='ignored' in the same group.

    load() yields only outcome='' rows, so the hidden row never reaches the
    VM the old gate read; the primitive must still see it.
    """
    rows = _visible_pair()
    rows.append({"source_path": "/photos/hidden.jpg", "action": "REVIEW_DUPLICATE",
                 "group_id": "/grp/a", "file_size_bytes": 512,
                 "outcome": "ignored", "is_locked": 1})
    return _make_manifest(tmp_path, rows)


def _make_pre_outcome(tmp_path: Path) -> Path:
    """A manifest written with the base DDL only (no outcome/is_locked yet)."""
    manifest = tmp_path / "old.sqlite"
    conn = sqlite3.connect(str(manifest))
    try:
        conn.executescript(_MANIFEST_DDL)
        conn.execute(
            "INSERT INTO migration_manifest (source_path, source_label, action) "
            "VALUES ('/photos/a.jpg', 'test', '')"
        )
        conn.commit()
    finally:
        conn.close()
    return manifest


def _read(manifest: Path, field: str, path: str):
    conn = sqlite3.connect(str(manifest))
    try:
        row = conn.execute(
            f"SELECT {field} FROM migration_manifest WHERE source_path = ?", (path,)
        ).fetchone()
    finally:
        conn.close()
    return row[0]


@pytest.fixture
def client():
    with TestClient(create_app(), raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# A stale plan cannot write a locked row
# ---------------------------------------------------------------------------

class TestStalePlanCannotWriteLockedRow:
    def test_plan_that_sees_no_locks_still_gets_409(self, tmp_path, monkeypatch):
        manifest = _make_manifest(tmp_path, _visible_pair(ref_locked=1))
        # The service's plan is made from tx.locked_paths(); force it stale.
        monkeypatch.setattr(
            mr.ManifestTxn, "locked_paths", lambda self, *, outcome=None: set()
        )
        with pytest.raises(ValueError) as exc_info:
            set_decisions(str(manifest), {"/photos/ref.jpg": "delete"})
        assert exc_info.value.args[0] == "locked_paths"
        assert exc_info.value.args[1] == ["/photos/ref.jpg"]
        assert _read(manifest, "user_decision", "/photos/ref.jpg") == ""
        assert _read(manifest, "is_locked", "/photos/ref.jpg") == 1


# ---------------------------------------------------------------------------
# Hidden locked rows are gated (B-5)
# ---------------------------------------------------------------------------

class TestHiddenLockedRow:
    def test_targeted_hidden_locked_row_gets_409(self, tmp_path):
        manifest = _make_hidden_locked(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            set_decisions(str(manifest), {"/photos/hidden.jpg": "delete"})
        assert exc_info.value.args[0] == "locked_paths"
        assert exc_info.value.args[1] == ["/photos/hidden.jpg"]
        assert _read(manifest, "user_decision", "/photos/hidden.jpg") == ""

    def test_forced_hidden_locked_row_is_unlocked(self, tmp_path):
        manifest = _make_hidden_locked(tmp_path)
        n = set_decisions(
            str(manifest), {"/photos/hidden.jpg": "delete"}, force_locked=True
        )
        assert n == 1
        assert _read(manifest, "user_decision", "/photos/hidden.jpg") == "delete"
        assert _read(manifest, "is_locked", "/photos/hidden.jpg") == 0


# ---------------------------------------------------------------------------
# Both lock flags are refused (B-6)
# ---------------------------------------------------------------------------

class TestBothFlagsRefused:
    def test_service_both_flags_raise_plain_value_error_and_write_nothing(
        self, tmp_path
    ):
        manifest = _make_manifest(tmp_path, _visible_pair(ref_locked=1))
        with pytest.raises(ValueError) as exc_info:
            set_decisions(
                str(manifest), {"/photos/ref.jpg": "delete"},
                force_locked=True, skip_locked=True,
            )
        assert exc_info.value.args[0] != "locked_paths"
        assert _read(manifest, "user_decision", "/photos/ref.jpg") == ""
        assert _read(manifest, "is_locked", "/photos/ref.jpg") == 1

    def test_route_both_flags_answer_422(self, client, tmp_path):
        manifest = _make_manifest(tmp_path, _visible_pair(ref_locked=1))
        resp = client.patch("/api/decision", json={
            "manifest_path": str(manifest),
            "decisions": [{"file_path": "/photos/ref.jpg", "decision": "delete"}],
            "force_locked": True,
            "skip_locked": True,
        })
        assert resp.status_code == 422, resp.text
        assert _read(manifest, "user_decision", "/photos/ref.jpg") == ""


# ---------------------------------------------------------------------------
# write_txn: ordering rule (2.4) and auto-migrate (BE-18)
# ---------------------------------------------------------------------------

class TestWriteTxn:
    def test_load_inside_write_txn_raises_at_once(self, tmp_path):
        manifest = _make_manifest(tmp_path, _visible_pair())
        repo = ManifestRepository()
        with repo.write_txn(str(manifest)):
            started = time.monotonic()
            with pytest.raises(RuntimeError):
                repo.load(str(manifest))
            assert time.monotonic() - started < 1.0

    def test_write_txn_auto_migrates_pre_outcome_manifest(self, tmp_path):
        manifest = _make_pre_outcome(tmp_path)
        repo = ManifestRepository()
        with repo.write_txn(str(manifest)) as tx:
            tx.write_decisions(
                [mr.DecisionWrite(path="/photos/a.jpg", decision="delete")],
                confirmed=frozenset(),
            )
        assert _read(manifest, "user_decision", "/photos/a.jpg") == "delete"
        assert _read(manifest, "outcome", "/photos/a.jpg") == ""

    def test_patch_on_pre_outcome_manifest_succeeds(self, tmp_path):
        manifest = _make_pre_outcome(tmp_path)
        assert set_decisions(str(manifest), {"/photos/a.jpg": "ignore"}) == 1
        assert _read(manifest, "user_decision", "/photos/a.jpg") == "ignore"


# ---------------------------------------------------------------------------
# Ordering rule holds for bulk_decide on a manifest with a reconcile candidate
# ---------------------------------------------------------------------------

class TestBulkDecideWithReconcileCandidate:
    def test_bulk_decide_completes_and_dismisses_skip_directory_row(self, tmp_path):
        rows = _visible_pair()
        rows.append({"source_path": "/x/$RECYCLE.BIN/junk.jpg",
                     "action": "REVIEW_DUPLICATE", "group_id": "/grp/a",
                     "file_size_bytes": 64, "outcome": ""})
        manifest = _make_manifest(tmp_path, rows)
        result = bulk_decide(str(manifest), "File Name", "dup", "delete")
        assert result["matched"] == 1
        assert _read(manifest, "user_decision", "/photos/dup.jpg") == "delete"
        assert _read(manifest, "outcome", "/x/$RECYCLE.BIN/junk.jpg") == "ignored"
