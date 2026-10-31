"""Tests for core/app_service/review_service.py.

Uses real temp SQLite manifests (no mocking) so tests catch actual
data-layer bugs rather than just exercising test infrastructure.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.app_service.lock_gate import LockFlagsConflictError
from core.app_service.review_service import (
    VALID_DECISIONS,
    load_review,
    set_decisions,
    set_locks,
)
from infrastructure.manifest_repository import ManifestRepository
from scanner.manifest import _DDL as _MANIFEST_DDL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_full_schema_manifest(tmp_path: Path, rows: list[dict]) -> Path:
    """Build a SQLite manifest using the canonical scanner DDL.

    scanner.manifest._DDL has the full base schema including hamming_distance,
    phash, group_id, is_locked, outcome (via ManifestRepository.ensure_schema).
    We run ensure_schema after creation so all additive migration columns
    (is_locked, outcome, etc.) are also present.
    """
    manifest = tmp_path / "manifest.sqlite"
    # Create the base table, then run migrations (adds is_locked, outcome, etc.)
    # BEFORE inserting rows so row dicts can reference those migration columns.
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
            # Ensure required NOT NULL columns have a fallback value.
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


def _make_pair(tmp_path: Path, group_id: str = "/grp/a") -> Path:
    """Build a two-row manifest: one Ref row + one REVIEW_DUPLICATE."""
    rows = [
        {
            "source_path": "/photos/ref.jpg",
            "source_label": "test",
            "action": "",
            "group_id": group_id,
            "score": 0.9,
            "file_size_bytes": 2048,
            "outcome": "",
        },
        {
            "source_path": "/photos/dup.jpg",
            "source_label": "test",
            "action": "REVIEW_DUPLICATE",
            "group_id": group_id,
            "hamming_distance": 4,
            "file_size_bytes": 1024,
            "outcome": "",
        },
    ]
    return _make_full_schema_manifest(tmp_path, rows)


def _make_pair_with_lock(tmp_path: Path, group_id: str = "/grp/a") -> Path:
    """Two-row manifest: locked Ref row + unlocked REVIEW_DUPLICATE row."""
    rows = [
        {
            "source_path": "/photos/ref.jpg",
            "source_label": "test",
            "action": "",
            "group_id": group_id,
            "score": 0.9,
            "file_size_bytes": 2048,
            "outcome": "",
            "is_locked": 1,
        },
        {
            "source_path": "/photos/dup.jpg",
            "source_label": "test",
            "action": "REVIEW_DUPLICATE",
            "group_id": group_id,
            "hamming_distance": 4,
            "file_size_bytes": 1024,
            "outcome": "",
            "is_locked": 0,
        },
    ]
    return _make_full_schema_manifest(tmp_path, rows)


def _read_field(manifest: Path, field: str) -> dict[str, object]:
    """Return {source_path: field_value} for all rows."""
    conn = sqlite3.connect(str(manifest))
    try:
        rows = conn.execute(
            f"SELECT source_path, {field} FROM migration_manifest"
        ).fetchall()
    finally:
        conn.close()
    return dict(rows)


def _set_locked_row(manifest: Path, source_path: str) -> None:
    """Lock one row directly, to model a lock set after the dialog opened."""
    conn = sqlite3.connect(str(manifest))
    try:
        conn.execute(
            "UPDATE migration_manifest SET is_locked = 1 WHERE source_path = ?",
            (source_path,),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# load_review
# ---------------------------------------------------------------------------

class TestLoadReview:
    def test_returns_groups_and_totals(self, tmp_path):
        manifest = _make_pair(tmp_path)
        result = load_review(str(manifest))
        assert result["total_groups"] == 1
        assert result["total_files"] == 2
        assert len(result["groups"]) == 1

    def test_group_has_two_items(self, tmp_path):
        manifest = _make_pair(tmp_path)
        result = load_review(str(manifest))
        assert result["groups"][0]["member_count"] == 2
        assert len(result["groups"][0]["items"]) == 2

    def test_roots_contains_folder_paths(self, tmp_path):
        manifest = _make_pair(tmp_path)
        result = load_review(str(manifest))
        # Both rows live in /photos/ — check that roots is non-empty and
        # contains the parent folder (path separator is OS-dependent on Windows).
        assert len(result["roots"]) > 0, "roots must be non-empty"
        assert any("photos" in r for r in result["roots"])

    def test_manifest_path_echoed(self, tmp_path):
        manifest = _make_pair(tmp_path)
        result = load_review(str(manifest))
        assert result["manifest_path"] == str(manifest)

    def test_legacy_keep_normalized_at_load(self, tmp_path):
        """user_decision='keep' in DB must come out as '' in the API."""
        rows = [
            {
                "source_path": "/p/ref.jpg",
                "action": "",
                "group_id": "/grp/k",
                "user_decision": "keep",
                "outcome": "",
                "file_size_bytes": 100,
            },
            {
                "source_path": "/p/dup.jpg",
                "action": "REVIEW_DUPLICATE",
                "group_id": "/grp/k",
                "user_decision": "",
                "outcome": "",
                "file_size_bytes": 100,
            },
        ]
        manifest = _make_full_schema_manifest(tmp_path, rows)
        result = load_review(str(manifest))
        items = result["groups"][0]["items"]
        ref_item = next(i for i in items if i["file_path"] == "/p/ref.jpg")
        assert ref_item["user_decision"] == "", (
            "Legacy 'keep' must be normalized to '' by load_review"
        )

    def test_multiple_groups(self, tmp_path):
        rows = [
            {"source_path": "/a/ref.jpg", "action": "", "group_id": "/g/1",
             "outcome": "", "file_size_bytes": 100},
            {"source_path": "/a/dup.jpg", "action": "REVIEW_DUPLICATE",
             "group_id": "/g/1", "outcome": "", "file_size_bytes": 100},
            {"source_path": "/b/ref.jpg", "action": "", "group_id": "/g/2",
             "outcome": "", "file_size_bytes": 200},
            {"source_path": "/b/dup.jpg", "action": "REVIEW_DUPLICATE",
             "group_id": "/g/2", "outcome": "", "file_size_bytes": 200},
        ]
        manifest = _make_full_schema_manifest(tmp_path, rows)
        result = load_review(str(manifest))
        assert result["total_groups"] == 2
        assert result["total_files"] == 4


# ---------------------------------------------------------------------------
# set_decisions
# ---------------------------------------------------------------------------

class TestSetDecisionsLockAfter:
    """``lock_after`` (A7): the per-row lock written in the decision statement.
    Red before A7: set_decisions had no such parameter."""

    def test_lock_after_true_locks_the_row_with_its_decision(self, tmp_path):
        manifest = _make_pair(tmp_path)
        set_decisions(
            str(manifest),
            {"/photos/dup.jpg": "delete"},
            lock_after={"/photos/dup.jpg": True},
        )
        assert _read_field(manifest, "user_decision")["/photos/dup.jpg"] == "delete"
        assert _read_field(manifest, "is_locked")["/photos/dup.jpg"] == 1

    def test_lock_after_false_on_a_confirmed_locked_row_unlocks_it(self, tmp_path):
        manifest = _make_pair_with_lock(tmp_path)
        set_decisions(
            str(manifest),
            {"/photos/ref.jpg": ""},
            confirmed_locked=["/photos/ref.jpg"],
            lock_after={"/photos/ref.jpg": False},
        )
        assert _read_field(manifest, "is_locked")["/photos/ref.jpg"] == 0

    def test_lock_after_false_on_an_unnamed_locked_row_raises_and_writes_nothing(
        self, tmp_path
    ):
        manifest = _make_pair_with_lock(tmp_path)
        with pytest.raises(ValueError) as exc:
            set_decisions(
                str(manifest),
                {"/photos/ref.jpg": ""},
                lock_after={"/photos/ref.jpg": False},
            )
        assert exc.value.args[0] == "locked_paths"
        assert exc.value.args[1] == ["/photos/ref.jpg"]
        assert _read_field(manifest, "user_decision")["/photos/ref.jpg"] == ""
        assert _read_field(manifest, "is_locked")["/photos/ref.jpg"] == 1

    def test_absent_lock_after_entry_keeps_todays_behaviour(self, tmp_path):
        manifest = _make_pair_with_lock(tmp_path)
        set_decisions(
            str(manifest),
            {"/photos/ref.jpg": "delete", "/photos/dup.jpg": "delete"},
            confirmed_locked=["/photos/ref.jpg"],
            lock_after={"/photos/dup.jpg": True},
        )
        # ref is confirmed and has no lock_after entry: unlocked, as before A7.
        assert _read_field(manifest, "is_locked")["/photos/ref.jpg"] == 0
        assert _read_field(manifest, "is_locked")["/photos/dup.jpg"] == 1


class TestSetDecisions:
    def test_persists_delete_decision(self, tmp_path):
        manifest = _make_pair(tmp_path)
        n = set_decisions(str(manifest), {"/photos/dup.jpg": "delete"})
        assert n == 1
        # Reload and confirm the decision was written.
        decisions = _read_field(manifest, "user_decision")
        assert decisions["/photos/dup.jpg"] == "delete"

    def test_persists_ignore_decision(self, tmp_path):
        manifest = _make_pair(tmp_path)
        set_decisions(str(manifest), {"/photos/dup.jpg": "ignore"})
        decisions = _read_field(manifest, "user_decision")
        assert decisions["/photos/dup.jpg"] == "ignore"

    def test_persists_empty_decision_clears(self, tmp_path):
        manifest = _make_pair(tmp_path)
        # First mark as delete, then clear.
        set_decisions(str(manifest), {"/photos/dup.jpg": "delete"})
        set_decisions(str(manifest), {"/photos/dup.jpg": ""})
        decisions = _read_field(manifest, "user_decision")
        assert decisions["/photos/dup.jpg"] == ""

    def test_invalid_decision_raises_value_error(self, tmp_path):
        manifest = _make_pair(tmp_path)
        with pytest.raises(ValueError, match="Invalid decision"):
            set_decisions(str(manifest), {"/photos/dup.jpg": "KEEP"})

    def test_invalid_decision_names_the_bad_value(self, tmp_path):
        manifest = _make_pair(tmp_path)
        with pytest.raises(ValueError, match="bogus"):
            set_decisions(str(manifest), {"/photos/dup.jpg": "bogus"})

    def test_returns_count(self, tmp_path):
        manifest = _make_pair(tmp_path)
        n = set_decisions(str(manifest), {
            "/photos/ref.jpg": "",
            "/photos/dup.jpg": "delete",
        })
        assert n == 2

    def test_reload_reflects_decision(self, tmp_path):
        """set_decisions + load_review round-trip catches persistence bugs."""
        manifest = _make_pair(tmp_path)
        set_decisions(str(manifest), {"/photos/dup.jpg": "delete"})
        result = load_review(str(manifest))
        items = result["groups"][0]["items"]
        dup_item = next(i for i in items if i["file_path"] == "/photos/dup.jpg")
        assert dup_item["user_decision"] == "delete"


# ---------------------------------------------------------------------------
# set_decisions — locked-rows gate (#733, Qt parity)
# ---------------------------------------------------------------------------

class TestSetDecisionsLockedRowGate:
    def test_locked_row_without_flags_raises_and_writes_nothing(self, tmp_path):
        manifest = _make_pair_with_lock(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            set_decisions(str(manifest), {"/photos/ref.jpg": "delete"})
        assert exc_info.value.args[0] == "locked_paths"
        assert exc_info.value.args[1] == ["/photos/ref.jpg"]
        assert exc_info.value.args[2] == 1
        # DB must be UNCHANGED — the gate fires before any write.
        decisions = _read_field(manifest, "user_decision")
        assert decisions["/photos/ref.jpg"] == ""

    def test_locked_row_clearing_decision_is_also_gated(self, tmp_path):
        """Qt does not special-case '' — clearing a locked row's decision
        is gated exactly like setting one (#733 parity)."""
        manifest = _make_pair_with_lock(tmp_path)
        with pytest.raises(ValueError) as exc_info:
            set_decisions(str(manifest), {"/photos/ref.jpg": ""})
        assert exc_info.value.args[0] == "locked_paths"

    def test_confirmed_locked_applies_decision_and_unlocks(self, tmp_path):
        manifest = _make_pair_with_lock(tmp_path)
        n = set_decisions(
            str(manifest),
            {"/photos/ref.jpg": "delete"},
            confirmed_locked=["/photos/ref.jpg"],
        )
        assert n == 1
        decisions = _read_field(manifest, "user_decision")
        locks = _read_field(manifest, "is_locked")
        assert decisions["/photos/ref.jpg"] == "delete"
        assert locks["/photos/ref.jpg"] == 0

    def test_row_locked_after_dialog_is_refused_and_named_alone(self, tmp_path):
        """D7 / B-2: a row locked after the dialog opened is not named, so the
        write refuses it. Named rows are not written either (one txn)."""
        manifest = _make_pair_with_lock(tmp_path)
        _set_locked_row(manifest, "/photos/dup.jpg")
        with pytest.raises(ValueError) as exc_info:
            set_decisions(
                str(manifest),
                {"/photos/ref.jpg": "delete", "/photos/dup.jpg": "delete"},
                confirmed_locked=["/photos/ref.jpg"],
            )
        assert exc_info.value.args[0] == "locked_paths"
        assert exc_info.value.args[1] == ["/photos/dup.jpg"]
        assert _read_field(manifest, "user_decision")["/photos/ref.jpg"] == ""
        assert _read_field(manifest, "is_locked")["/photos/ref.jpg"] == 1

    def test_confirmed_locked_with_skip_locked_raises_flags_conflict(self, tmp_path):
        manifest = _make_pair_with_lock(tmp_path)
        with pytest.raises(LockFlagsConflictError):
            set_decisions(
                str(manifest),
                {"/photos/ref.jpg": "delete"},
                confirmed_locked=["/photos/ref.jpg"],
                skip_locked=True,
            )
        assert _read_field(manifest, "user_decision")["/photos/ref.jpg"] == ""

    def test_skip_locked_writes_unlocked_only_leaves_locked_untouched(self, tmp_path):
        manifest = _make_pair_with_lock(tmp_path)
        n = set_decisions(
            str(manifest),
            {"/photos/ref.jpg": "delete", "/photos/dup.jpg": "delete"},
            skip_locked=True,
        )
        assert n == 1
        decisions = _read_field(manifest, "user_decision")
        locks = _read_field(manifest, "is_locked")
        # Locked ref.jpg is untouched — neither decision nor lock state changed.
        assert decisions["/photos/ref.jpg"] == ""
        assert locks["/photos/ref.jpg"] == 1
        # Unlocked dup.jpg got the decision.
        assert decisions["/photos/dup.jpg"] == "delete"

    def test_no_locked_rows_targeted_fast_path_still_writes(self, tmp_path):
        """A locked row exists in the manifest but isn't in the target set —
        the gate must not fire (mirrors bulk_decide's targeted-only check)."""
        manifest = _make_pair_with_lock(tmp_path)
        n = set_decisions(str(manifest), {"/photos/dup.jpg": "delete"})
        assert n == 1
        decisions = _read_field(manifest, "user_decision")
        assert decisions["/photos/dup.jpg"] == "delete"


# ---------------------------------------------------------------------------
# set_locks
# ---------------------------------------------------------------------------

class TestSetLocks:
    def test_persists_lock(self, tmp_path):
        manifest = _make_pair(tmp_path)
        n = set_locks(str(manifest), {"/photos/ref.jpg": True})
        assert n == 1
        locks = _read_field(manifest, "is_locked")
        assert locks["/photos/ref.jpg"] == 1

    def test_persists_unlock(self, tmp_path):
        manifest = _make_pair(tmp_path)
        set_locks(str(manifest), {"/photos/ref.jpg": True})
        set_locks(str(manifest), {"/photos/ref.jpg": False})
        locks = _read_field(manifest, "is_locked")
        assert locks["/photos/ref.jpg"] == 0

    def test_returns_count(self, tmp_path):
        manifest = _make_pair(tmp_path)
        n = set_locks(str(manifest), {
            "/photos/ref.jpg": True,
            "/photos/dup.jpg": False,
        })
        assert n == 2

    def test_reload_reflects_lock(self, tmp_path):
        """set_locks + load_review round-trip catches persistence bugs."""
        manifest = _make_pair(tmp_path)
        set_locks(str(manifest), {"/photos/ref.jpg": True})
        result = load_review(str(manifest))
        items = result["groups"][0]["items"]
        ref_item = next(i for i in items if i["file_path"] == "/photos/ref.jpg")
        assert ref_item["is_locked"] is True


# ---------------------------------------------------------------------------
# VALID_DECISIONS constant
# ---------------------------------------------------------------------------

class TestValidDecisions:
    def test_contains_expected_values(self):
        assert "" in VALID_DECISIONS
        assert "delete" in VALID_DECISIONS
        assert "ignore" in VALID_DECISIONS

    def test_does_not_contain_legacy_keep(self):
        """Legacy 'keep' must not be a valid decision; normalize to '' instead."""
        assert "keep" not in VALID_DECISIONS

    def test_ignore_in_sync_with_constants(self):
        """IGNORE_DECISION from core.constants must be in VALID_DECISIONS."""
        from core.constants import IGNORE_DECISION
        assert IGNORE_DECISION in VALID_DECISIONS
