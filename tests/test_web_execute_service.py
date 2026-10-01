"""Unit tests for core/app_service/execute_service.py.

These tests exercise the service functions directly (no HTTP layer).
All tests use real sqlite manifests and real temp files — no synthetic
coverage padding.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from core.app_service.execute_service import (
    classify_singletons,
    execute_decisions,
    prune_singletons,
    remove_from_review,
    restore_to_review,
    save_manifest,
)
from core.app_service.review_service import load_review
from infrastructure.manifest_repository import ManifestRepository
from scanner.manifest import _DDL as _MANIFEST_DDL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(tmp_path: Path, rows: list[dict]) -> Path:
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


def _read_col(manifest: Path, file_path: str, col: str) -> object:
    conn = sqlite3.connect(str(manifest))
    try:
        row = conn.execute(
            f"SELECT {col} FROM migration_manifest WHERE source_path = ?",
            (file_path,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _make_real_files(tmp_path: Path, n: int = 2) -> list[Path]:
    files_dir = tmp_path / "files"
    files_dir.mkdir(exist_ok=True)
    paths = []
    for i in range(n):
        f = files_dir / f"f{i}.bin"
        f.write_bytes(b"\x55" * 64)
        paths.append(f)
    return paths


# ---------------------------------------------------------------------------
# execute_decisions
# ---------------------------------------------------------------------------


class TestExecuteDecisions:
    def test_delete_decided_removes_file_and_writes_outcome(self, tmp_path):
        """Core D7: outcome='deleted' written per-file right after trash."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(str(manifest), recycle=False)

        assert set(result["success_paths"]) == {str(f1), str(f2)}
        assert result["failed"] == []
        assert result["missing"] == []
        assert not f1.exists()
        assert not f2.exists()
        assert _read_col(manifest, str(f1), "outcome") == "deleted"
        assert _read_col(manifest, str(f2), "outcome") == "deleted"

    def test_missing_file_in_missing_list_other_succeeds(self, tmp_path):
        """D7 per-file: one missing file in `missing`; the other is still deleted."""
        files = _make_real_files(tmp_path, 2)
        f_ok, f_gone = files
        f_gone.unlink()  # simulate pre-deleted file

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f_ok),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f_gone),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(str(manifest), recycle=False)

        assert str(f_ok) in result["success_paths"]
        assert str(f_gone) in result["missing"]
        assert str(f_gone) not in result["success_paths"]
        assert _read_col(manifest, str(f_ok), "outcome") == "deleted"

    def test_locked_rows_raise_without_force(self, tmp_path):
        """Locked delete-decided rows raise ValueError('locked_paths', [...])."""
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
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        with pytest.raises(ValueError) as exc_info:
            execute_decisions(str(manifest), recycle=False, force_locked=False)

        args = exc_info.value.args
        assert args[0] == "locked_paths"
        assert str(f1) in args[1]
        # Nothing deleted.
        assert f1.exists()
        assert f2.exists()

    def test_force_locked_unlocks_and_deletes(self, tmp_path):
        """force_locked=True clears is_locked and deletes the file."""
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
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(str(manifest), recycle=False, force_locked=True)

        assert str(f1) in result["success_paths"]
        assert str(f2) in result["success_paths"]
        assert not f1.exists()
        assert not f2.exists()
        assert _read_col(manifest, str(f1), "is_locked") == 0

    def test_ignore_decided_not_deleted_from_disk(self, tmp_path):
        """ignore-decided files get outcome='ignored' but files stay on disk."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "ignore",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "ignore",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(str(manifest), recycle=False)

        assert set(result["ignored"]) == {str(f1), str(f2)}
        assert result["success_paths"] == []
        assert f1.exists()
        assert f2.exists()
        assert _read_col(manifest, str(f1), "outcome") == "ignored"
        assert _read_col(manifest, str(f2), "outcome") == "ignored"

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            execute_decisions(str(tmp_path / "nonexistent.sqlite"))

    def test_scope_paths_filters_to_subset(self, tmp_path):
        """scope_paths limits execution to specified files only."""
        files = _make_real_files(tmp_path, 3)
        f1, f2, f3 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f3),
                "action": "",
                "group_id": "g2",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        # Only execute f1, skip f2 and f3.
        result = execute_decisions(
            str(manifest),
            scope_paths=[str(f1)],
            recycle=False,
        )

        assert str(f1) in result["success_paths"]
        assert str(f2) not in result["success_paths"]
        assert str(f3) not in result["success_paths"]
        assert not f1.exists()
        assert f2.exists()   # not in scope
        assert f3.exists()   # not in scope

    def test_unscoped_execute_skips_actioned_singleton_hidden_from_review(self, tmp_path):
        """#941: a partial execute leaves g1's other delete row as a kept
        actioned singleton. The review drops it (orphan-skip), so no tree,
        count or #733 confirm can show it — and an unscoped Execute must not
        delete it ("visible = committed"). It keeps its pending decision."""
        a, b, c, d = _make_real_files(tmp_path, 4)
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(a), "action": "", "group_id": "g1",
             "outcome": "", "user_decision": "delete", "file_size_bytes": 64},
            {"source_path": str(b), "action": "REVIEW_DUPLICATE", "group_id": "g1",
             "hamming_distance": 2, "outcome": "", "user_decision": "delete",
             "file_size_bytes": 64},
            {"source_path": str(c), "action": "", "group_id": "g2",
             "outcome": "", "user_decision": "", "file_size_bytes": 64},
            {"source_path": str(d), "action": "REVIEW_DUPLICATE", "group_id": "g2",
             "hamming_distance": 2, "outcome": "", "user_decision": "delete",
             "file_size_bytes": 64},
        ])

        # "Execute selected" on b alone; the user keeps the actioned singleton.
        execute_decisions(str(manifest), scope_paths=[str(b)], recycle=False)
        assert classify_singletons(str(manifest))["actioned"] == [str(a)]
        visible = {
            item["file_path"]
            for group in load_review(str(manifest))["groups"]
            for item in group["items"]
        }
        assert visible == {str(c), str(d)}

        # Plain "Execute": the dialog lists only d.
        result = execute_decisions(str(manifest), recycle=False)

        assert result["success_paths"] == [str(d)]
        assert not d.exists()
        assert a.exists(), "hidden singleton a was deleted by an unscoped Execute"
        assert _read_col(manifest, str(a), "outcome") == ""
        assert _read_col(manifest, str(a), "user_decision") == "delete"


# ---------------------------------------------------------------------------
# execute_decisions — audit CSV stays out of the user's profile (#948)
# ---------------------------------------------------------------------------


class TestExecuteAuditCsvIsolation:
    """Guard for ``tests/conftest.py::_isolate_delete_log_dir`` (#948).

    ``execute_decisions`` calls ``write_delete_log`` with no ``log_dir``, so the
    CSV goes to ``%LOCALAPPDATA%\\PhotoManager\\delete_logs`` — the audit trail a
    user reads to recover what a real Execute deleted. Before #948 every local
    suite run buried that trail under thousands of pytest CSVs.

    ``LOCALAPPDATA`` is pointed at a temp dir here, so if the conftest redirect
    ever goes inert the stray CSV lands in that temp dir and fails this test —
    never in the real profile.
    """

    def test_audit_csv_lands_in_pytest_temp_not_localappdata(
        self, tmp_path, tmp_path_factory, monkeypatch
    ):
        fake_localappdata = tmp_path / "LOCALAPPDATA"
        fake_localappdata.mkdir()
        monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))

        (victim,) = _make_real_files(tmp_path, 1)
        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(victim),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(str(manifest), recycle=False)

        log_path = result["log_path"]
        assert log_path is not None, "execute wrote no audit CSV at all"
        assert not (fake_localappdata / "PhotoManager").exists(), (
            f"audit CSV escaped into %LOCALAPPDATA%\\PhotoManager: {log_path}"
        )
        assert tmp_path_factory.getbasetemp() in Path(log_path).parents, (
            f"audit CSV is not under pytest's temp root: {log_path}"
        )
        assert str(victim) in Path(log_path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# remove_from_review
# ---------------------------------------------------------------------------


class TestRemoveFromReview:
    def test_removes_sets_ignored_outcome(self, tmp_path):
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
        ])

        result = remove_from_review(str(manifest), [str(f1), str(f2)])

        assert result["removed"] == 2
        assert f1.exists()   # files untouched
        assert f2.exists()
        assert _read_col(manifest, str(f1), "outcome") == "ignored"
        assert _read_col(manifest, str(f2), "outcome") == "ignored"

    def test_locked_row_raises_without_force(self, tmp_path):
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
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
        ])

        with pytest.raises(ValueError) as exc_info:
            remove_from_review(str(manifest), [str(f1), str(f2)], force_locked=False)

        assert exc_info.value.args[0] == "locked_paths"

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            remove_from_review(str(tmp_path / "no.sqlite"), ["x"])


# ---------------------------------------------------------------------------
# restore_to_review — the Undo of an immediate Skip (#909)
# ---------------------------------------------------------------------------


class TestRestoreToReview:
    def _three_row_group(self, tmp_path: Path) -> tuple[Path, list[Path]]:
        files = _make_real_files(tmp_path, 3)
        rows = [
            {
                "source_path": str(f),
                "action": "" if i == 0 else "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": None if i == 0 else 2,
                "outcome": "",
                # A staged decision the skip must not cost the user.
                "user_decision": "delete" if i == 1 else "",
                "file_size_bytes": 64,
            }
            for i, f in enumerate(files)
        ]
        return _make_manifest(tmp_path, rows), files

    def test_undoes_a_skip_and_the_rows_come_back_as_they_were(self, tmp_path):
        manifest, (f0, f1, f2) = self._three_row_group(tmp_path)
        remove_from_review(str(manifest), [str(f0), str(f1)])

        result = restore_to_review(str(manifest), [str(f0), str(f1)])

        assert result["restored"] == 2
        for f in (f0, f1):
            assert _read_col(manifest, str(f), "outcome") == ""
            assert _read_col(manifest, str(f), "executed") == 0
        # The staged decision survives the skip + undo round-trip.
        assert _read_col(manifest, str(f1), "user_decision") == "delete"
        # And the response the frontend renders has the whole group again.
        [group] = result["groups"]
        assert {item["file_path"] for item in group["items"]} == {
            str(f0), str(f1), str(f2)
        }

    def test_never_returns_a_deleted_row_to_review(self, tmp_path):
        # A 'deleted' row's file is in the Recycle Bin; putting it back in
        # review would offer to delete a file that is no longer there.
        manifest, (f0, _f1, _f2) = self._three_row_group(tmp_path)
        ManifestRepository().finalize_outcome(str(manifest), [str(f0)], "deleted")

        result = restore_to_review(str(manifest), [str(f0)])

        assert result["restored"] == 0
        assert _read_col(manifest, str(f0), "outcome") == "deleted"
        assert _read_col(manifest, str(f0), "executed") == 1

    def test_out_of_root_path_is_not_restored(self, tmp_path):
        manifest, (f0, _f1, _f2) = self._three_row_group(tmp_path)
        remove_from_review(str(manifest), [str(f0)])

        result = restore_to_review(
            str(manifest), [str(f0)], allowed_roots=[str(tmp_path / "elsewhere")]
        )

        assert result["restored"] == 0
        assert _read_col(manifest, str(f0), "outcome") == "ignored"

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            restore_to_review(str(tmp_path / "no.sqlite"), ["x"])


# ---------------------------------------------------------------------------
# prune_singletons
# ---------------------------------------------------------------------------


class TestPruneSingletons:
    def _singleton_manifest(self, tmp_path: Path) -> tuple[Path, Path]:
        """One singleton (partner deleted) and returns (manifest, f1)."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "deleted",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])
        return manifest, f1

    def test_plain_singleton_pruned(self, tmp_path):
        manifest, f1 = self._singleton_manifest(tmp_path)
        result = prune_singletons(str(manifest))
        assert str(f1) in result["pruned"]
        assert result["locked_skipped"] == []
        assert _read_col(manifest, str(f1), "outcome") == "ignored"

    def test_locked_singleton_in_locked_skipped(self, tmp_path):
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
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "deleted",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = prune_singletons(str(manifest))
        assert result["pruned"] == []
        assert str(f1) in result["locked_skipped"]
        assert _read_col(manifest, str(f1), "outcome") == ""  # untouched

    def test_actioned_singleton_excluded_without_include_actioned(self, tmp_path):
        """Singleton with decision='delete' is NOT pruned when include_actioned=False."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",  # actioned singleton
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "deleted",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = prune_singletons(str(manifest), include_actioned=False)
        assert result["pruned"] == []

    def test_actioned_singleton_included_with_include_actioned(self, tmp_path):
        """Singleton with decision='delete' IS pruned when include_actioned=True."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",  # actioned singleton
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "deleted",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = prune_singletons(str(manifest), include_actioned=True)
        assert str(f1) in result["pruned"]

    # -- explicit-paths mode (#686) ------------------------------------------

    def _two_singletons_manifest(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        """Two singleton groups: g1 has a PLAIN survivor f1, g2 an ACTIONED
        survivor f3 (decision='delete'). Returns (manifest, f1_plain, f3_actioned).
        """
        f1, f2, f3, f4 = _make_real_files(tmp_path, 4)
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
        ])
        return manifest, f1, f3

    def test_explicit_paths_prunes_only_named_singleton(self, tmp_path):
        """paths=[f3] prunes ONLY f3 — the plain f1 is left even though
        category mode (paths=None) would have pruned it. This is the whole
        point of explicit mode: the caller picks the exact set."""
        manifest, f1, f3 = self._two_singletons_manifest(tmp_path)
        result = prune_singletons(str(manifest), paths=[str(f3)])
        assert result["pruned"] == [str(f3)]
        assert _read_col(manifest, str(f3), "outcome") == "ignored"
        assert _read_col(manifest, str(f1), "outcome") == ""  # untouched

    def test_explicit_paths_ignores_include_actioned_flag(self, tmp_path):
        """Explicit mode prunes a named actioned singleton even with
        include_actioned=False — the caller already resolved the bucket."""
        manifest, f1, f3 = self._two_singletons_manifest(tmp_path)
        result = prune_singletons(
            str(manifest), include_actioned=False, paths=[str(f1), str(f3)]
        )
        assert set(result["pruned"]) == {str(f1), str(f3)}

    def test_explicit_paths_skips_locked(self, tmp_path):
        """A named path that is still locked is NOT pruned — it lands in
        locked_skipped (the FE unlocks before requesting, but a race must
        never silently prune a locked row)."""
        f1, f2 = _make_real_files(tmp_path, 2)
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "action": "", "group_id": "g1",
             "outcome": "", "user_decision": "", "is_locked": 1, "file_size_bytes": 64},
            {"source_path": str(f2), "action": "REVIEW_DUPLICATE", "group_id": "g1",
             "hamming_distance": 2, "outcome": "deleted", "user_decision": "delete",
             "file_size_bytes": 64},
        ])
        result = prune_singletons(str(manifest), paths=[str(f1)])
        assert result["pruned"] == []
        assert str(f1) in result["locked_skipped"]
        assert _read_col(manifest, str(f1), "outcome") == ""  # untouched

    def test_explicit_paths_drops_non_singleton(self, tmp_path):
        """A named path that is no longer a singleton (still has live partners)
        is silently dropped — never pruned."""
        # Two live (outcome='') rows in g1 → NOT a singleton.
        f1, f2 = _make_real_files(tmp_path, 2)
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "action": "", "group_id": "g1",
             "outcome": "", "user_decision": "", "file_size_bytes": 64},
            {"source_path": str(f2), "action": "REVIEW_DUPLICATE", "group_id": "g1",
             "hamming_distance": 2, "outcome": "", "user_decision": "",
             "file_size_bytes": 64},
        ])
        result = prune_singletons(str(manifest), paths=[str(f1)])
        assert result["pruned"] == []
        assert _read_col(manifest, str(f1), "outcome") == ""  # untouched

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            prune_singletons(str(tmp_path / "no.sqlite"))


# ---------------------------------------------------------------------------
# classify_singletons (#686 — the prune-offer classifier the web flow reads,
# because the review view drops single-member groups)
# ---------------------------------------------------------------------------


class TestClassifySingletons:
    def test_buckets_plain_actioned_locked(self, tmp_path):
        """Three singleton groups → plain / actioned / locked; locked wins over
        decision (a locked row with a decision lands in `locked`)."""
        from core.app_service.execute_service import classify_singletons

        f1, f2, f3, f4, f5, f6 = _make_real_files(tmp_path, 6)
        manifest = _make_manifest(tmp_path, [
            # g1: plain survivor f1 (partner f2 deleted)
            {"source_path": str(f1), "action": "", "group_id": "g1",
             "outcome": "", "user_decision": "", "file_size_bytes": 64},
            {"source_path": str(f2), "action": "REVIEW_DUPLICATE", "group_id": "g1",
             "hamming_distance": 2, "outcome": "deleted", "user_decision": "delete",
             "file_size_bytes": 64},
            # g2: actioned survivor f3 (un-executed delete)
            {"source_path": str(f3), "action": "", "group_id": "g2",
             "outcome": "", "user_decision": "delete", "file_size_bytes": 64},
            {"source_path": str(f4), "action": "REVIEW_DUPLICATE", "group_id": "g2",
             "hamming_distance": 2, "outcome": "deleted", "user_decision": "delete",
             "file_size_bytes": 64},
            # g3: locked survivor f5 — locked wins even though it has a decision
            {"source_path": str(f5), "action": "", "group_id": "g3",
             "outcome": "", "user_decision": "delete", "is_locked": 1, "file_size_bytes": 64},
            {"source_path": str(f6), "action": "REVIEW_DUPLICATE", "group_id": "g3",
             "hamming_distance": 2, "outcome": "deleted", "user_decision": "delete",
             "file_size_bytes": 64},
        ])

        result = classify_singletons(str(manifest))
        assert result["plain"] == [str(f1)]
        assert result["actioned"] == [str(f3)]
        assert result["locked"] == [str(f5)]

    def test_ignore_decision_is_actioned(self, tmp_path):
        from core.app_service.execute_service import classify_singletons

        f1, f2 = _make_real_files(tmp_path, 2)
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "action": "", "group_id": "g1",
             "outcome": "", "user_decision": "ignore", "file_size_bytes": 64},
            {"source_path": str(f2), "action": "REVIEW_DUPLICATE", "group_id": "g1",
             "hamming_distance": 2, "outcome": "deleted", "user_decision": "delete",
             "file_size_bytes": 64},
        ])
        result = classify_singletons(str(manifest))
        assert result["actioned"] == [str(f1)]
        assert result["plain"] == []

    def test_non_singleton_excluded(self, tmp_path):
        """A group with two live (outcome='') rows is NOT a singleton."""
        from core.app_service.execute_service import classify_singletons

        f1, f2 = _make_real_files(tmp_path, 2)
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "action": "", "group_id": "g1",
             "outcome": "", "user_decision": "", "file_size_bytes": 64},
            {"source_path": str(f2), "action": "REVIEW_DUPLICATE", "group_id": "g1",
             "hamming_distance": 2, "outcome": "", "user_decision": "",
             "file_size_bytes": 64},
        ])
        result = classify_singletons(str(manifest))
        assert result == {"plain": [], "actioned": [], "locked": []}

    def test_out_of_root_excluded(self, tmp_path):
        """An out-of-root singleton is not offered (can't be pruned)."""
        from core.app_service.execute_service import classify_singletons

        f1, f2 = _make_real_files(tmp_path, 2)
        manifest = _make_manifest(tmp_path, [
            {"source_path": str(f1), "action": "", "group_id": "g1",
             "outcome": "", "user_decision": "", "file_size_bytes": 64},
            {"source_path": str(f2), "action": "REVIEW_DUPLICATE", "group_id": "g1",
             "hamming_distance": 2, "outcome": "deleted", "user_decision": "delete",
             "file_size_bytes": 64},
        ])
        # Roots that do NOT contain f1 → f1 excluded.
        result = classify_singletons(str(manifest), allowed_roots=[str(tmp_path / "elsewhere")])
        assert result == {"plain": [], "actioned": [], "locked": []}


# ---------------------------------------------------------------------------
# save_manifest
# ---------------------------------------------------------------------------


class TestSaveManifest:
    def test_in_place_save_writes_decisions(self, tmp_path):
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
        ])

        result = save_manifest(str(manifest))
        assert result["saved_to"] == str(manifest)
        # FIX 4: updated is now the count of in-review (outcome='') rows.
        assert isinstance(result["updated"], int)
        assert result["updated"] >= 1

        # Verify the decision is still persisted (it was written per-PATCH).
        assert _read_col(manifest, str(f1), "user_decision") == "delete"

    def test_save_as_creates_copy_with_schema(self, tmp_path):
        """save-as copies the manifest; the copy carries the same decisions."""
        files = _make_real_files(tmp_path, 2)
        f1, f2 = files

        # Write a decision directly so we can verify the copy carries it.
        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f1),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(f2),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
        ])
        target = tmp_path / "copy.sqlite"

        result = save_manifest(str(manifest), str(target))
        assert result["saved_to"] == str(target)
        assert target.exists()

        # Verify the copy carries the decision row by opening it fresh.
        copy_repo = ManifestRepository()
        conn = sqlite3.connect(str(target))
        try:
            count_row = conn.execute(
                "SELECT COUNT(*) FROM migration_manifest"
            ).fetchone()
            decision_row = conn.execute(
                "SELECT user_decision FROM migration_manifest WHERE source_path = ?",
                (str(f1),),
            ).fetchone()
        finally:
            conn.close()
        assert count_row[0] >= 2
        # FIX 4: copy2 carries the decisions because WAL was checkpointed first.
        assert decision_row is not None
        assert decision_row[0] == "delete"

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            save_manifest(str(tmp_path / "no.sqlite"))


# ---------------------------------------------------------------------------
# FIX 1 — out-of-root source_path rows are refused
# ---------------------------------------------------------------------------


class TestExecuteDecisionsOutOfRoot:
    """FIX 1 REGRESSION TEST: out-of-root source_path rows must be refused."""

    def test_out_of_root_delete_row_is_refused_file_survives(self, tmp_path):
        """SHIP-BLOCKER test: a source_path outside allowed_roots must NOT be deleted.

        Build a manifest under tmp_path/manifest_dir (which is an allowed root).
        That manifest contains a source_path pointing to tmp_path/outside_dir
        (which is NOT in allowed_roots).  Create the out-of-root file on disk.
        Run execute_decisions with allowed_roots=[manifest_dir].
        Assert:
        - The out-of-root file still exists on disk.
        - Its DB outcome is still '' (not 'deleted').
        - It appears in the returned 'failed' list with reason 'outside_allowed_roots'.
        """
        # Two separate directories: one allowed, one not.
        manifest_dir = tmp_path / "allowed"
        manifest_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        # A real file inside the allowed root (will be deleted normally).
        inside_file = manifest_dir / "inside.bin"
        inside_file.write_bytes(b"\xAA" * 64)

        # A real file OUTSIDE the allowed root (must survive).
        outside_file = outside_dir / "sensitive.bin"
        outside_file.write_bytes(b"\xBB" * 64)

        manifest = _make_manifest(manifest_dir, [
            {
                "source_path": str(inside_file),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(outside_file),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(
            str(manifest),
            recycle=False,
            allowed_roots=[str(manifest_dir)],
        )

        # The inside file is gone (normal delete).
        assert not inside_file.exists()
        assert str(inside_file) in result["success_paths"]

        # The outside file MUST still exist.
        assert outside_file.exists(), (
            "out-of-root file was deleted — FIX 1 not applied"
        )

        # DB outcome for the out-of-root path must still be '' (untouched).
        assert _read_col(manifest, str(outside_file), "outcome") == "", (
            "out-of-root file's DB outcome was written — FIX 1 not applied"
        )

        # The out-of-root path must appear in failed with the correct reason.
        failed_paths = {entry[0]: entry[1] for entry in result["failed"]}
        assert str(outside_file) in failed_paths, (
            "out-of-root path not in failed list"
        )
        assert failed_paths[str(outside_file)] == "outside_allowed_roots", (
            f"unexpected reason: {failed_paths[str(outside_file)]!r}"
        )

    def test_out_of_root_ignore_row_is_skipped(self, tmp_path):
        """Out-of-root ignore-decided rows are silently skipped (not finalized)."""
        manifest_dir = tmp_path / "allowed"
        manifest_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        inside_file = manifest_dir / "inside.bin"
        inside_file.write_bytes(b"\xAA" * 64)
        outside_file = outside_dir / "outside.bin"
        outside_file.write_bytes(b"\xBB" * 64)

        manifest = _make_manifest(manifest_dir, [
            {
                "source_path": str(inside_file),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "ignore",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(outside_file),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "ignore",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(
            str(manifest),
            recycle=False,
            allowed_roots=[str(manifest_dir)],
        )

        # Inside file is correctly ignored.
        assert str(inside_file) in result["ignored"]
        assert _read_col(manifest, str(inside_file), "outcome") == "ignored"

        # Outside file is NOT in ignored and its DB outcome is still ''.
        assert str(outside_file) not in result["ignored"]
        assert _read_col(manifest, str(outside_file), "outcome") == ""

    def test_no_allowed_roots_skips_safety_check(self, tmp_path):
        """When allowed_roots is None, the safety check is skipped (legacy behavior)."""
        files = _make_real_files(tmp_path, 2)
        f, partner = files

        # The undecided partner keeps g1 a group the review shows — a lone row
        # would be a hidden singleton, which execute never acts on (#941).
        manifest = _make_manifest(tmp_path, [
            {
                "source_path": str(f),
                "action": "",
                "group_id": "g1",
                "outcome": "",
                "user_decision": "delete",
                "file_size_bytes": 64,
            },
            {
                "source_path": str(partner),
                "action": "REVIEW_DUPLICATE",
                "group_id": "g1",
                "hamming_distance": 2,
                "outcome": "",
                "user_decision": "",
                "file_size_bytes": 64,
            },
        ])

        result = execute_decisions(str(manifest), recycle=False, allowed_roots=None)
        assert str(f) in result["success_paths"]
        assert not f.exists()
