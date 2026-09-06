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
6. exiftool-missing in-memory date backfill (#793/#811) — a JPEG dated only
   in XMP keeps its shot_date and is not filed as UNDATED when exiftool is
   absent; a JPEG with no EXIF at all still lands, UNDATED, shot_date NULL.
7. Malformed-EXIF robustness (#793/#811) — a decodable JPEG whose EXIF block
   is corrupt still reaches the manifest instead of being silently dropped.
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


# ── #793 acceptance — exiftool-missing mode + malformed EXIF ───────────────
#
# PR #811 moved the ``exif_date`` backfill out of ``elif et_records:`` so it
# runs regardless of exiftool availability (core/app_service/scan_runner.py
# :1152-1166), and guarded ``getexif()`` in ``extract_pil_scoring_signals``
# (scanner/exif.py:645-655). Its own Scope note deferred the scan_runner-level
# test as "a full-pipeline scenario"; #793's acceptance asks for it. These
# tests drive the real ``run_pipeline`` over real files on disk.


def _write_xmp_only_dated_jpeg(path: Path, iso: str = "2024-07-15T10:30:00") -> None:
    """Write a real JPEG whose capture date exists ONLY in XMP, not EXIF.

    This is the shape that makes the backfill load-bearing. ``hasher.
    _raw_exif_date`` reads EXIF only, so ``HashResult.exif_date`` comes back
    ``None``; the date is recovered solely by the in-memory PIL pass (#786),
    which lands it in ``extracts[path].exif_date`` via the XMP fallback chain
    in ``extract_pil_scoring_signals``. Only the backfill copies it back onto
    the record that ``classify`` reads — so a JPEG dated in EXIF would make
    this test vacuous.

    Real editing pipelines produce exactly this file (Lightroom / Photoshop
    writing XMP sidecar metadata into the JPEG without touching EXIF).
    """
    packet = (
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description rdf:about=""'
        b' xmlns:exif="http://ns.adobe.com/exif/1.0/"'
        b' xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
        b'exif:DateTimeOriginal="' + iso.encode() + b'"'
        b'/></rdf:RDF></x:xmpmeta>'
    )
    Image.new("RGB", (32, 32), (12, 200, 90)).save(path, "JPEG", xmp=packet)


def _write_malformed_exif_jpeg(path: Path) -> None:
    """Write a real, decodable JPEG whose EXIF block cannot be parsed.

    Mirror of ``tests/test_scanner_exif.py::_malformed_exif_jpeg`` — see that
    docstring for why the TIFF byte-order mark is the byte to destroy and why
    ``dpi=(72, 72)`` is required to keep Pillow's own open-time pre-warm from
    swallowing the parse error.
    """
    img = Image.new("RGB", (32, 32), (200, 30, 30))
    exif = img.getexif()
    exif.get_ifd(0x8769)[36867] = "2024:08:01 12:00:00"
    payload = bytearray(exif.tobytes())
    assert payload[:6] == b"Exif\x00\x00", payload[:6]
    payload[6:8] = b"\x00\x00"
    img.save(str(path), "JPEG", quality=90, exif=bytes(payload), dpi=(72, 72))


class TestExiftoolMissingDateBackfill:
    """#793 — in exiftool-missing mode a JPEG whose date the in-memory pass
    DID recover must not be filed as UNDATED.

    User-visible failure being pinned: on a machine without exiftool, a photo
    the app itself dated (it is scored with that date, and the date shows in
    the scoring) is nonetheless listed under UNDATED with an empty Date
    column — an internally contradictory row the user cannot act on.

    ``scanner/dedup.py:293-294`` is where a ``HashResult`` with
    ``exif_date is None`` becomes ``action='UNDATED'``, and
    ``scanner/dedup.py:893`` is where ``shot_date`` is written from the same
    field — so both manifest columns are decided by whether the backfill ran.
    """

    def _rows(self, db_path: Path) -> list[dict]:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT source_path, action, shot_date FROM migration_manifest"
                ).fetchall()
            ]
        finally:
            conn.close()

    def _run(self, tmp_path: Path, monkeypatch) -> tuple[list[dict], _SpyBus]:
        """Run the real pipeline over ``tmp_path/src`` with exiftool absent.

        ``ExiftoolProcess()`` raising ``FileNotFoundError`` is the exact seam
        the pipeline latches ``exiftool_missing`` on (scan_runner.py:743) —
        patched rather than relying on the host lacking exiftool, so the test
        means the same thing on a developer machine that has it installed.
        """
        import scanner.exif as _exif
        from core.app_service.cancel_token import _CancelToken
        from core.app_service.dtos import ScanConfig
        from core.app_service.scan_runner import run_pipeline

        def _raise_missing(*_a, **_kw):
            raise FileNotFoundError("exiftool not found")

        monkeypatch.setattr(_exif, "ExiftoolProcess", _raise_missing)

        out = tmp_path / "manifest.sqlite"
        config = ScanConfig(
            sources={"src": tmp_path / "src"},
            output_path=out,
            recursive_map={"src": False},
            workers=1,
            exif_workers=1,
            hash_pool="thread",
        )
        bus = _SpyBus()
        run_pipeline(config, _CancelToken(), bus)

        assert bus.failed_msgs == [], (
            f"run_pipeline must not fail; got {bus.failed_msgs!r}"
        )
        assert any("exiftool not found on PATH" in m for m in bus.logs), (
            "precondition: the run must actually be in exiftool-missing mode; "
            f"logs={bus.logs!r}"
        )
        return self._rows(out), bus

    def test_xmp_only_date_is_backfilled_and_row_is_not_undated(
        self, tmp_path, monkeypatch
    ):
        """The #793 regression itself: exiftool missing + a JPEG dated only in
        XMP. The manifest row must carry the date and must NOT be UNDATED."""
        src = tmp_path / "src"
        src.mkdir()
        _write_xmp_only_dated_jpeg(src / "edited.jpg")

        rows, _bus = self._run(tmp_path, monkeypatch)

        assert len(rows) == 1, f"expected the one scanned JPEG; got {rows!r}"
        row = rows[0]
        assert row["shot_date"] == "2024-07-15T10:30:00", (
            "the XMP date recovered by the in-memory pass must reach the "
            f"manifest's shot_date column; got {row!r}"
        )
        assert row["action"] != "UNDATED", (
            "a row the pipeline DID date must not also be filed as UNDATED — "
            f"that is the internally inconsistent row #793 reports; got {row!r}"
        )

    def test_jpeg_with_no_exif_at_all_stays_undated(self, tmp_path, monkeypatch):
        """The other half of the value domain: with no date anywhere, the row
        must still land in the manifest and must legitimately be UNDATED with
        an empty shot_date. The backfill rescues dates that EXIST; it must not
        invent one."""
        src = tmp_path / "src"
        src.mkdir()
        _write_jpeg(src / "plain.jpg")

        rows, _bus = self._run(tmp_path, monkeypatch)

        assert len(rows) == 1, f"expected the one scanned JPEG; got {rows!r}"
        row = rows[0]
        assert row["shot_date"] is None, f"no date anywhere -> NULL; got {row!r}"
        assert row["action"] == "UNDATED", (
            f"a genuinely undated photo must still be flagged; got {row!r}"
        )

    def test_malformed_exif_jpeg_still_reaches_the_manifest(
        self, tmp_path, monkeypatch
    ):
        """#793's other fix, end to end: a decodable JPEG whose EXIF block is
        corrupt must appear in the manifest. Unguarded, ``getexif()`` raises
        inside ``extract_pil_scoring_signals`` → ``compute_from_bytes``' broad
        except → ``HashFailure`` → the photo is dropped from the scan with
        only a line in the skipped log.

        Same scope caveat as its unit sibling in ``tests/test_scanner_exif.py``
        (``test_malformed_exif_jpeg_still_hashes_via_compute_from_bytes``):
        this pins the end-to-end OUTCOME and passes even with the #811 guard
        removed, because ``hasher._raw_exif_date`` pre-warms PIL's cached
        parse first. The guard's non-vacuous test lives in that file.
        """
        src = tmp_path / "src"
        src.mkdir()
        _write_malformed_exif_jpeg(src / "damaged.jpg")

        rows, _bus = self._run(tmp_path, monkeypatch)

        assert [Path(r["source_path"]).name for r in rows] == ["damaged.jpg"], (
            "a decodable JPEG with a malformed EXIF block must not be dropped "
            f"from the manifest; got {rows!r}"
        )
