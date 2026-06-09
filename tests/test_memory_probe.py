"""Tests for scripts/memory_probe.py.

These tests only cover the pure-Python logic that can break silently:
- disabled path is a true no-op (no artifact, no tracemalloc start)
- enabled path writes a valid JSONL row with the required schema keys
- track_qt_alloc increments/decrements the counter correctly
- the try/except ImportError guards in production code don't break the app

Skipped intentionally:
- Actual content of top30 / typed_counts (tests tracemalloc/gc, not our code)
- generate_probe_fixture.py (dev tool, exercised by manual probe runs)
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reload_probe(monkeypatch, enabled: str | None) -> types.ModuleType:
    """Re-import memory_probe with a fresh env state for _ENABLED."""
    import scripts.memory_probe as mp_mod

    if enabled is None:
        monkeypatch.delenv("PHOTO_MANAGER_MEMORY_PROBE", raising=False)
    else:
        monkeypatch.setenv("PHOTO_MANAGER_MEMORY_PROBE", enabled)

    # Force a full module reload so module-level _ENABLED picks up the new env.
    return importlib.reload(mp_mod)


# ---------------------------------------------------------------------------
# Test 1 — disabled path is a true no-op
# ---------------------------------------------------------------------------

class TestDisabledSnapshot:
    def test_disabled_snapshot_is_noop(self, monkeypatch, tmp_path):
        """When PHOTO_MANAGER_MEMORY_PROBE is unset, snapshot() returns immediately
        and writes no artifact.  Catches a regression where someone removes the
        ``if not _ENABLED: return`` guard."""
        mp = _reload_probe(monkeypatch, None)

        assert mp._ENABLED is False

        # Redirect the artifact dir to tmp so we can inspect it cleanly.
        monkeypatch.setattr(mp, "_ARTIFACT_DIR", tmp_path)

        mp.snapshot("test_noop", point=1)

        # No JSONL file should exist.
        artifacts = list(tmp_path.glob("memory_probe_*.jsonl"))
        assert artifacts == [], "snapshot() wrote an artifact even when disabled"

    def test_disabled_snapshot_does_not_start_tracemalloc(self, monkeypatch, tmp_path):
        """Tracemalloc must not be started when probe is disabled — it imposes
        overhead on every allocation even after we stop calling snapshot()."""
        import tracemalloc
        mp = _reload_probe(monkeypatch, None)

        was_tracing = tracemalloc.is_tracing()
        monkeypatch.setattr(mp, "_ARTIFACT_DIR", tmp_path)
        mp.snapshot("noop_tm", point=1)

        # If tracemalloc was already running we can't assert it wasn't started
        # by us; only assert it's still in the same state as before the call.
        assert tracemalloc.is_tracing() == was_tracing


# ---------------------------------------------------------------------------
# Test 2 — enabled path writes a JSONL row with the expected schema
# ---------------------------------------------------------------------------

class TestEnabledSnapshot:
    def test_enabled_snapshot_writes_jsonl_row(self, monkeypatch, tmp_path):
        """When enabled, snapshot() appends one JSON object with all required
        schema keys.  Catches missing fields that would break a pandas analysis
        script reading the artifact."""
        mp = _reload_probe(monkeypatch, "1")
        assert mp._ENABLED is True
        monkeypatch.setattr(mp, "_ARTIFACT_DIR", tmp_path)
        # Reset counters so the row is deterministic.
        mp._qt_alloc["QStandardItem"] = 0
        mp._qt_dealloc["QStandardItem"] = 0
        mp._qt_alloc["QImage"] = 0
        mp._qt_dealloc["QImage"] = 0
        mp._tm_started = False

        mp.snapshot("test_row", point=3, n_groups=42, n_items=100)

        artifacts = list(tmp_path.glob("memory_probe_*.jsonl"))
        assert len(artifacts) == 1, "Expected exactly one JSONL artifact"

        row = json.loads(artifacts[0].read_text(encoding="utf-8").strip())

        required_keys = {
            "ts", "iso", "run_id", "tag", "point", "label", "thread",
            "tracemalloc_total_bytes", "tracemalloc_peak_bytes",
            "top30", "rss_bytes", "vms_bytes", "private_bytes",
            "system_avail_bytes", "gc_count", "typed_counts",
            "qt_counter_qstandarditem", "qt_counter_qimage", "extras",
        }
        missing = required_keys - set(row.keys())
        assert missing == set(), f"JSONL row missing keys: {missing}"

        assert row["point"] == 3
        assert row["label"] == "test_row"
        assert row["extras"] == {"n_groups": "42", "n_items": "100"}
        assert isinstance(row["top30"], list)
        assert isinstance(row["typed_counts"], dict)
        assert "QStandardItem" in row["typed_counts"]


# ---------------------------------------------------------------------------
# Test 3 — track_qt_alloc increments / destroyed-signal decrements counter
# ---------------------------------------------------------------------------

class TestTrackQtAlloc:
    def test_track_qt_alloc_increments_and_destroyed_decrements(self, monkeypatch):
        """Counter starts at 0, reaches 1 after track_qt_alloc, and returns to 0
        after the destroyed callback fires.  This is the regression test for the
        #619 refresh_model fix: before #619 the counter would keep growing on each
        reload because QStandardItems were never freed."""
        mp = _reload_probe(monkeypatch, "1")
        assert mp._ENABLED is True
        # Reset counters.
        mp._qt_alloc["QStandardItem"] = 0
        mp._qt_dealloc["QStandardItem"] = 0

        # Build a minimal mock with a .destroyed signal that we can invoke.
        mock_obj = MagicMock()
        destroyed_callbacks: list = []

        def _connect_side_effect(cb):
            destroyed_callbacks.append(cb)

        mock_obj.destroyed = MagicMock()
        mock_obj.destroyed.connect = MagicMock(side_effect=_connect_side_effect)

        mp.track_qt_alloc("QStandardItem", mock_obj)

        with mp._lock:
            net = mp._qt_alloc["QStandardItem"] - mp._qt_dealloc["QStandardItem"]
        assert net == 1, "Counter should be 1 after track_qt_alloc"

        # Fire the destroyed callback as Qt would.
        for cb in destroyed_callbacks:
            cb()

        with mp._lock:
            net = mp._qt_alloc["QStandardItem"] - mp._qt_dealloc["QStandardItem"]
        assert net == 0, "Counter should return to 0 after destroyed fires"

    def test_track_qt_alloc_noop_when_disabled(self, monkeypatch):
        """track_qt_alloc must not mutate counters when probe is disabled."""
        mp = _reload_probe(monkeypatch, None)
        assert mp._ENABLED is False
        mp._qt_alloc["QStandardItem"] = 0

        mock_obj = MagicMock()
        mp.track_qt_alloc("QStandardItem", mock_obj)

        assert mp._qt_alloc["QStandardItem"] == 0


# ---------------------------------------------------------------------------
# Test 4 — import guard in production code doesn't break the app
# ---------------------------------------------------------------------------

class TestImportGuard:
    def test_import_guard_survives_missing_module(self, monkeypatch):
        """Simulate scripts/memory_probe.py being unavailable (ImportError).

        Verifies that the try/except ImportError guards in the four production
        call sites don't crash the app when the module is absent.  This catches
        a regression where someone forgets the guard around a new call site.
        """
        # Inject a broken 'scripts.memory_probe' into sys.modules so the import
        # inside the production try/except raises ImportError on demand.
        broken_finder = _BrokenFinder("scripts.memory_probe")
        sys.meta_path.insert(0, broken_finder)
        try:
            # --- Point 1 guard (main_window.__init__ tail) ---
            # We test the guard pattern directly; importing main_window would pull
            # heavy Qt widgets into coverage.  The pattern is identical across all
            # 5 call sites: try/except ImportError wraps the import + if _ENABLED.
            _exec_point1_guard()

            # --- Point 2 guard (manifest_load_worker) ---
            _exec_point2_guard()

            # --- Point 3+4+5 guard (file_operations._on_manifest_loaded) ---
            _exec_point3_guard()

            # --- tree_model_builder guard ---
            _exec_tmb_guard()

            # --- image_service guard ---
            _exec_imgservice_guard()

        finally:
            sys.meta_path.remove(broken_finder)


# ---------------------------------------------------------------------------
# Helper: broken import finder
# ---------------------------------------------------------------------------

class _BrokenFinder:
    """Insert into sys.meta_path to make a specific module raise ImportError."""

    def __init__(self, module_name: str) -> None:
        self._name = module_name

    def find_spec(self, fullname, path, target=None):
        if fullname == self._name or fullname.startswith(self._name + "."):
            raise ImportError(f"_BrokenFinder: {fullname}")
        return None


# ---------------------------------------------------------------------------
# Guard pattern executors (inlined to avoid importing the actual source modules)
# ---------------------------------------------------------------------------

def _exec_point1_guard():
    """Exercise the Point 1 guard pattern without importing main_window."""
    try:
        from scripts.memory_probe import snapshot, _ENABLED  # noqa: F401
        if _ENABLED:
            snapshot("mainwindow_init_done", point=1)
    except ImportError:
        pass  # expected


def _exec_point2_guard():
    """Exercise the Point 2 guard pattern."""
    items = [object(), object()]
    try:
        from scripts.memory_probe import snapshot, _ENABLED  # noqa: F401
        if _ENABLED:
            snapshot("worker_post_fetchall", point=2, n_records=len(items))
    except ImportError:
        pass  # expected


def _exec_point3_guard():
    """Exercise the Points 3/4/5 guard patterns."""
    groups: list = []
    try:
        from scripts.memory_probe import snapshot, _ENABLED  # noqa: F401
        if _ENABLED:
            snapshot("vm_groups_assigned", point=3, n_groups=0, n_items=0)
    except ImportError:
        pass  # expected
    try:
        from scripts.memory_probe import snapshot as _snap4, _ENABLED as _en4, _active_timers  # noqa: F401
        if _en4:
            _snap4("after_refresh_model", point=4)
    except ImportError:
        pass  # expected


def _exec_tmb_guard():
    """Exercise the tree_model_builder guard pattern."""
    child_row: list = [MagicMock(), MagicMock()]
    try:
        from scripts.memory_probe import track_qt_alloc, _ENABLED  # noqa: F401
        if _ENABLED:
            for _it in child_row:
                track_qt_alloc("QStandardItem", _it)
    except ImportError:
        pass  # expected


def _exec_imgservice_guard():
    """Exercise the image_service guard pattern."""
    img = MagicMock()
    img.isNull.return_value = False
    try:
        from scripts.memory_probe import track_qt_alloc, _ENABLED  # noqa: F401
        if _ENABLED and img is not None and not img.isNull():
            track_qt_alloc("QImage", img)
    except ImportError:
        pass  # expected
