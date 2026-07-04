"""Tests for core/app_service/scan_runner.py — Qt-free pipeline.

Tests:

1. Happy path — run_pipeline() completes and writes a manifest.
2. Pre-cancel — run_pipeline() calls bus.failed("Scan cancelled.") and
   does NOT write the manifest when cancel_token is set before calling.
3. No-Qt-import guard — importing scan_runner must not pull PySide6 into
   sys.modules (the T6 probe in test_ui_probes.py also checks the AST;
   this test checks the live runtime).
4. Auto-select integration — run_pipeline() with auto_select_enabled=True
   writes KEEP locks for the top keeper in each dup group.
5. Auto-select aggressive delete — with auto_select_aggressive_delete=True
   non-keepers get user_decision='delete'.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from PIL import Image


def _write_jpeg(path: Path, color=(128, 64, 32)) -> None:
    Image.new("RGB", (32, 32), color).save(path, "JPEG")


class _SpyBus:
    """Test double that records all ScanProgressBus calls."""

    def __init__(self):
        self.logs: list[str] = []
        self.stages: list[tuple] = []
        self.failed_msgs: list[str] = []
        self.finished_paths: list[str] = []
        self.empty_calls: int = 0
        self.pool_measured: list[dict] = []
        self.knee_measured: list[dict] = []

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    def stage(self, stage_name, completed, total, files_per_sec) -> None:
        self.stages.append((stage_name, completed, total, files_per_sec))

    def failed(self, msg: str) -> None:
        self.failed_msgs.append(msg)

    def finished(self, output_path: str) -> None:
        self.finished_paths.append(output_path)

    def completed_empty(self) -> None:
        self.empty_calls += 1

    def hash_pool_measured(self, rates: dict) -> None:
        self.pool_measured.append(rates)

    def read_knee_measured(self, summary: dict) -> None:
        self.knee_measured.append(summary)


class TestRunPipelineHappyPath:
    def test_run_pipeline_writes_manifest(self, tmp_path, monkeypatch):
        """run_pipeline() with one real JPEG completes: bus.finished() is called
        with the manifest path and the SQLite file exists on disk."""
        from core.app_service.cancel_token import _CancelToken
        from core.app_service.dtos import ScanConfig
        from core.app_service.scan_runner import run_pipeline

        a = tmp_path / "a.jpg"
        _write_jpeg(a)
        out = tmp_path / "manifest.sqlite"

        config = ScanConfig(
            sources={"src": tmp_path},
            output_path=out,
            recursive_map={"src": False},
            workers=1,
            exif_workers=1,
            hash_pool="thread",
        )
        cancel_token = _CancelToken()
        bus = _SpyBus()

        run_pipeline(config, cancel_token, bus)

        assert bus.finished_paths == [str(out)], (
            f"run_pipeline() must call bus.finished(manifest_path) on success;"
            f" got {bus.finished_paths!r}"
        )
        assert out.exists(), "manifest SQLite must be written to disk on success"
        assert bus.failed_msgs == [], (
            f"no bus.failed() on success; got {bus.failed_msgs!r}"
        )
        assert any("Done." in m for m in bus.logs), (
            "final 'Done.' log line must reach the bus"
        )
        assert any(s[0] == "WALK" for s in bus.stages), (
            "run_pipeline must emit at least one WALK stage event; "
            f"got stages: {bus.stages!r}"
        )


class TestRunPipelinePreCancel:
    def test_pre_cancel_skips_manifest_write(self, tmp_path, monkeypatch):
        """When cancel_token is set before run_pipeline() is called, the
        pipeline must call bus.failed('Scan cancelled.') and must NOT write
        the manifest (preserving any prior manifest at output_path)."""
        from core.app_service.cancel_token import _CancelToken
        from core.app_service.dtos import ScanConfig
        from core.app_service.scan_runner import run_pipeline

        a = tmp_path / "a.jpg"
        _write_jpeg(a)
        out = tmp_path / "manifest.sqlite"
        out.write_bytes(b"PRIOR-MANIFEST-SENTINEL")

        config = ScanConfig(
            sources={"src": tmp_path},
            output_path=out,
            recursive_map={"src": False},
            workers=1,
            exif_workers=1,
            hash_pool="thread",
        )
        cancel_token = _CancelToken()
        cancel_token.request()  # cancel BEFORE calling run_pipeline
        bus = _SpyBus()

        run_pipeline(config, cancel_token, bus)

        assert bus.failed_msgs == ["Scan cancelled."], (
            f"pre-cancel must call bus.failed('Scan cancelled.');"
            f" got {bus.failed_msgs!r}"
        )
        assert bus.finished_paths == [], (
            "bus.finished() must not fire when cancelled"
        )
        assert out.read_bytes() == b"PRIOR-MANIFEST-SENTINEL", (
            "prior manifest must survive a pre-cancel"
        )


class TestScanRunnerNoQtImport:
    def test_importing_scan_runner_does_not_import_pyside6(self):
        """scan_runner.py must be importable without pulling PySide6 into
        sys.modules — the Qt-free contract that enables the Phase 1 web API.

        This test spawns a fresh subprocess so it checks the actual sys.modules
        state on a clean Python import; the T6 AST probe in test_ui_probes.py
        is the complementary static guard.
        """
        import subprocess
        import sys as _sys

        result = subprocess.run(
            [
                _sys.executable,
                "-c",
                (
                    "import sys;"
                    "import core.app_service.scan_runner;"
                    "qt = [m for m in sys.modules if 'PySide6' in m];"
                    "print(qt);"
                    "assert qt == [], qt"
                ),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Importing scan_runner pulled PySide6 into sys.modules.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )


class TestAutoSelectPipeline:
    """Integration tests for the auto-select path in run_pipeline().

    These are REAL runs on real tmp JPEGs — no mock-driven coverage —
    because auto-select (lines ~1179-1227 of scan_runner.py) is the only
    extracted code that calls an external service (core.services.auto_select)
    and is the exact place a mechanical-extraction wiring bug hides.

    Two identical JPEGs in one directory → hash-based EXACT duplicate group →
    the higher-scored keeper gets is_locked=1, non-keeper optionally gets
    user_decision='delete'.  The tests read the SQLite manifest directly to
    assert the persisted state.
    """

    def _make_dup_dir(self, tmp_path: Path) -> tuple[Path, Path]:
        """Write two identical JPEGs that will form an EXACT dup group."""
        a = tmp_path / "a.jpg"
        b = tmp_path / "b.jpg"
        # Identical bytes → same hash → EXACT duplicate group.
        img_bytes = None
        Image.new("RGB", (64, 64), (100, 150, 200)).save(a, "JPEG")
        img_bytes = a.read_bytes()
        b.write_bytes(img_bytes)
        return a, b

    def _read_manifest_rows(self, db_path: Path) -> list[dict]:
        """Read all rows from the manifest SQLite as plain dicts."""
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT source_path, user_decision, is_locked FROM migration_manifest"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def test_auto_select_enabled_locks_keeper(self, tmp_path):
        """run_pipeline with auto_select_enabled=True locks the top keeper.

        After a successful scan of a dup group:
        - bus.finished() fires (no bus.failed())
        - exactly one row has is_locked=1 (the keeper)
        - that keeper's user_decision is '' (canonical keep, not 'keep')
        """
        from core.app_service.cancel_token import _CancelToken
        from core.app_service.dtos import ScanConfig
        from core.app_service.scan_runner import run_pipeline

        src = tmp_path / "src"
        src.mkdir()
        self._make_dup_dir(src)
        out = tmp_path / "manifest.sqlite"

        config = ScanConfig(
            sources={"src": src},
            output_path=out,
            recursive_map={"src": False},
            workers=1,
            exif_workers=1,
            hash_pool="thread",
            auto_select_enabled=True,
        )
        bus = _SpyBus()
        run_pipeline(config, _CancelToken(), bus)

        assert bus.failed_msgs == [], (
            f"run_pipeline must not fail; got bus.failed_msgs={bus.failed_msgs!r}"
        )
        assert bus.finished_paths == [str(out)], (
            f"bus.finished must fire with manifest path; got {bus.finished_paths!r}"
        )

        rows = self._read_manifest_rows(out)
        locked = [r for r in rows if r["is_locked"]]
        assert len(locked) >= 1, (
            f"auto-select must lock at least one keeper; rows={rows!r}"
        )
        for r in locked:
            assert r["user_decision"] == "", (
                f"keeper user_decision must be '' (not 'keep'); got {r['user_decision']!r}"
            )

    def test_auto_select_aggressive_delete_marks_non_keepers(self, tmp_path):
        """run_pipeline with aggressive delete marks non-keepers for deletion.

        After a successful scan of a dup group with auto_select_aggressive_delete:
        - bus.finished() fires
        - non-keeper rows get user_decision='delete'
        - the keeper row stays user_decision='' with is_locked=1
        """
        from core.app_service.cancel_token import _CancelToken
        from core.app_service.dtos import ScanConfig
        from core.app_service.scan_runner import run_pipeline

        src = tmp_path / "src"
        src.mkdir()
        self._make_dup_dir(src)
        out = tmp_path / "manifest.sqlite"

        config = ScanConfig(
            sources={"src": src},
            output_path=out,
            recursive_map={"src": False},
            workers=1,
            exif_workers=1,
            hash_pool="thread",
            auto_select_enabled=True,
            auto_select_aggressive_delete=True,
        )
        bus = _SpyBus()
        run_pipeline(config, _CancelToken(), bus)

        assert bus.failed_msgs == [], (
            f"run_pipeline must not fail; got bus.failed_msgs={bus.failed_msgs!r}"
        )
        assert bus.finished_paths == [str(out)], (
            f"bus.finished must fire with manifest path; got {bus.finished_paths!r}"
        )

        rows = self._read_manifest_rows(out)
        locked = [r for r in rows if r["is_locked"]]
        deleted = [r for r in rows if r["user_decision"] == "delete"]
        assert len(locked) >= 1, (
            f"aggressive auto-select must still lock a keeper; rows={rows!r}"
        )
        assert len(deleted) >= 1, (
            f"aggressive auto-select must mark at least one non-keeper for delete; rows={rows!r}"
        )

    def test_manifest_write_is_atomic_with_auto_select(self, tmp_path):
        """#651 — positive atomicity check.

        write_manifest() now folds the auto-select keeper lock into the
        SAME connection/transaction as the row inserts. Read the manifest
        ONCE and assert it holds both facts at once: every scanned row
        AND the keeper's lock/decision. Before #651 these were two
        separate writes (write_manifest, then a standalone
        apply_auto_select_decisions call) — a crash between them could
        leave a manifest with all rows but no keeper lock. One coherent
        read proves that window is gone.
        """
        from core.app_service.cancel_token import _CancelToken
        from core.app_service.dtos import ScanConfig
        from core.app_service.scan_runner import run_pipeline

        src = tmp_path / "src"
        src.mkdir()
        self._make_dup_dir(src)
        out = tmp_path / "manifest.sqlite"

        config = ScanConfig(
            sources={"src": src},
            output_path=out,
            recursive_map={"src": False},
            workers=1,
            exif_workers=1,
            hash_pool="thread",
            auto_select_enabled=True,
        )
        bus = _SpyBus()
        run_pipeline(config, _CancelToken(), bus)

        assert bus.failed_msgs == [], (
            f"run_pipeline must not fail; got bus.failed_msgs={bus.failed_msgs!r}"
        )
        assert bus.finished_paths == [str(out)], (
            f"bus.finished must fire with manifest path; got {bus.finished_paths!r}"
        )

        rows = self._read_manifest_rows(out)
        assert len(rows) == 2, (
            "manifest write must not be skipped/truncated by folding in "
            f"the auto-select pass; expected 2 rows, got {rows!r}"
        )
        names = {Path(r["source_path"]).name for r in rows}
        assert names == {"a.jpg", "b.jpg"}, f"unexpected rows: {rows!r}"

        locked = [r for r in rows if r["is_locked"]]
        assert len(locked) == 1, (
            "exactly one keeper must be locked in the SAME manifest that "
            f"holds all scanned rows; rows={rows!r}"
        )
        assert locked[0]["user_decision"] == "", (
            f"keeper's user_decision must be canonical ''; got {locked[0]!r}"
        )

    def test_failed_auto_select_write_leaves_no_partial_manifest(
        self, tmp_path, monkeypatch
    ):
        """#651 — failure-injection rollback proof.

        Inject a failure INSIDE write_manifest's auto-select block (after
        the row INSERTs, before the commit + os.replace). The whole
        write_manifest call must raise and output_path must be left
        completely unchanged — no partial manifest (rows without a
        keeper lock) is ever swapped into place. This is the exact
        incoherence window #651 closes: pre-fix, the row INSERTs were
        already committed and swapped in via a first os.replace() before
        the separate auto-select write ran, so a crash here used to
        leave a real, unlocked manifest on disk.
        """
        import core.services.auto_select as auto_select_mod
        from core.app_service.cancel_token import _CancelToken
        from core.app_service.dtos import ScanConfig
        from core.app_service.scan_runner import run_pipeline

        src = tmp_path / "src"
        src.mkdir()
        self._make_dup_dir(src)
        out = tmp_path / "manifest.sqlite"
        assert not out.exists(), "precondition: no prior manifest on first scan"

        def _boom(*args, **kwargs):
            raise RuntimeError("injected failure mid-write (#651 rollback test)")

        # build_auto_select_writes is called from inside write_manifest's
        # tmp-connection block, AFTER rows are inserted but BEFORE
        # conn.commit() / os.replace() — the exact seam #651 added.
        monkeypatch.setattr(auto_select_mod, "build_auto_select_writes", _boom)

        config = ScanConfig(
            sources={"src": src},
            output_path=out,
            recursive_map={"src": False},
            workers=1,
            exif_workers=1,
            hash_pool="thread",
            auto_select_enabled=True,
        )
        bus = _SpyBus()

        with pytest.raises(RuntimeError, match="injected failure mid-write"):
            run_pipeline(config, _CancelToken(), bus)

        assert not out.exists(), (
            "a failed auto-select write must NOT leave a partial manifest "
            "at output_path — os.replace() must never run when the "
            "in-progress tmp write raised"
        )
        assert bus.finished_paths == [], (
            "bus.finished() must not fire when the manifest write raised"
        )

    def test_failed_rescan_preserves_prior_manifest(self, tmp_path, monkeypatch):
        """#651 — the load-bearing durability guarantee: a failed RE-SCAN
        must never destroy an existing good manifest.

        First write a real manifest (auto-select on → rows + a locked
        keeper). Then inject a failure inside write_manifest's auto-select
        block on a second scan to the SAME output_path. The second
        run_pipeline must raise and the ORIGINAL manifest — every row and
        the keeper lock — must survive byte-for-byte, because os.replace()
        is skipped and the destination is never touched. This is the exact
        real-user scenario #651 protects (re-scanning over prior decisions);
        the fresh-scan sibling test only proves no partial file is created.
        """
        import core.services.auto_select as auto_select_mod
        from core.app_service.cancel_token import _CancelToken
        from core.app_service.dtos import ScanConfig
        from core.app_service.scan_runner import run_pipeline

        src = tmp_path / "src"
        src.mkdir()
        self._make_dup_dir(src)
        out = tmp_path / "manifest.sqlite"

        config = ScanConfig(
            sources={"src": src},
            output_path=out,
            recursive_map={"src": False},
            workers=1,
            exif_workers=1,
            hash_pool="thread",
            auto_select_enabled=True,
        )

        # 1. First scan succeeds — capture the good manifest's state.
        run_pipeline(config, _CancelToken(), _SpyBus())
        good_rows = self._read_manifest_rows(out)
        good_bytes = out.read_bytes()
        assert len(good_rows) == 2 and sum(r["is_locked"] for r in good_rows) == 1, (
            f"precondition: first scan must write 2 rows + 1 lock; got {good_rows!r}"
        )

        # 2. Second scan fails inside the folded auto-select write.
        def _boom(*args, **kwargs):
            raise RuntimeError("injected failure mid-rescan (#651 clobber test)")

        monkeypatch.setattr(auto_select_mod, "build_auto_select_writes", _boom)
        with pytest.raises(RuntimeError, match="injected failure mid-rescan"):
            run_pipeline(config, _CancelToken(), _SpyBus())

        # 3. The prior good manifest must be completely untouched.
        assert out.read_bytes() == good_bytes, (
            "a failed re-scan must leave the prior manifest byte-identical — "
            "os.replace() must never swap in the aborted tmp write"
        )
        assert self._read_manifest_rows(out) == good_rows, (
            "prior rows/locks must survive a failed re-scan intact"
        )
