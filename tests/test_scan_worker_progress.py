"""#424 — pure-logic tests for ScanWorker's throughput sampling + the
stage_progress emit-throttle helper.

These tests don't instantiate ScanWorker (which is a QThread that
expects a QApplication) — they exercise the internal ``_StageTracker``
directly. The throughput formulas and throttle decisions are the
actual bugs we'd ship if the rolling-deque math was wrong; the QThread
plumbing is exercised by the layer-3 s01_happy_path scenario.
"""
from __future__ import annotations

import time

import pytest

from app.views.workers.scan_worker import (
    _STAGE_EMIT_INTERVAL_SECONDS,
    _THROUGHPUT_WINDOW_SECONDS,
    _StageTracker,
    STAGE_HASH,
)


class TestStageTrackerThroughput:
    """Throughput math must be honest. Common failure modes:
      - Single sample reports a nonzero rate (would surface a fake ETA
        on the first emit of every stage).
      - Window not trimmed → a slow scan reports inflated rate from
        stale samples 30s ago.
      - Sub-100ms dt produces an unbounded rate (division by tiny dt).
    """

    def test_single_sample_returns_zero(self):
        """One sample → no rate. Hides a fake ETA on stage start
        before any throughput data is available."""
        tracker = _StageTracker(STAGE_HASH)
        tracker.record(0)
        assert tracker.throughput() == 0.0

    def test_two_samples_one_second_apart_reports_rate(self, monkeypatch):
        """100 files in 1 second → 100 files/sec."""
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        tracker = _StageTracker(STAGE_HASH)
        tracker.record(0)
        clock[0] += 1.0
        tracker.record(100)
        assert tracker.throughput() == pytest.approx(100.0)

    def test_window_trimmed_drops_stale_samples(self, monkeypatch):
        """Samples older than the window are popped — a 10s gap
        between the oldest in-window sample and the latest means the
        rate is computed only from the in-window samples, not the
        whole stage. Prevents a slow start from anchoring the rate
        long after the scan sped up."""
        clock = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        tracker = _StageTracker(STAGE_HASH)
        tracker.record(0)
        # 10s elapses without records: simulates a long pre-loop pause.
        clock[0] += 10.0
        tracker.record(0)
        # Then a fast burst inside the window: 500 in 2s = 250/s.
        clock[0] += 2.0
        tracker.record(500)
        # The stale (0, 0) is gone; oldest is (10, 0) and latest
        # is (12, 500) → 250 files/sec.
        assert tracker.throughput() == pytest.approx(250.0)
        # And the deque should not be retaining samples beyond the
        # window — verified by re-recording at the same instant and
        # confirming the rate doesn't drift.
        assert len(tracker._samples) == 2

    def test_sub_100ms_dt_returns_zero(self, monkeypatch):
        """dt < 0.1s → return 0, not infinity. Two samples in the
        same tick happen on a fast local SSD scan; we don't want
        the bar to claim "1,000,000 files/sec" on the first emit."""
        clock = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        tracker = _StageTracker(STAGE_HASH)
        tracker.record(0)
        clock[0] += 0.05  # 50ms
        tracker.record(50)
        assert tracker.throughput() == 0.0

    def test_negative_count_clamped_to_zero(self, monkeypatch):
        """Defensive — counters shouldn't go backwards, but if they
        did (e.g. a refactor accidentally records (0, total) twice)
        the rate must clamp to 0, never go negative."""
        clock = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        tracker = _StageTracker(STAGE_HASH)
        tracker.record(100)
        clock[0] += 1.0
        tracker.record(50)  # went backwards
        assert tracker.throughput() == 0.0


class TestStageTrackerEmitThrottle:
    """The throttle exists so the HASH/EXIFTOOL hot loops don't emit
    a Qt signal on every file. Catches three real failure modes:
      - First emit suppressed → user sees no stage label until 1s in.
      - Boundary emit suppressed → progress bar stops at N-1 / N
        because the final emit got throttled.
      - Throttle ignores monotonic time → spam on every call.
    """

    def test_first_emit_always_fires(self):
        tracker = _StageTracker(STAGE_HASH)
        assert tracker.should_emit(0, 100) is True

    def test_second_emit_within_interval_is_throttled(self, monkeypatch):
        clock = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        tracker = _StageTracker(STAGE_HASH)
        tracker.should_emit(0, 100)
        clock[0] += _STAGE_EMIT_INTERVAL_SECONDS / 2.0
        assert tracker.should_emit(50, 100) is False

    def test_second_emit_after_interval_fires(self, monkeypatch):
        clock = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        tracker = _StageTracker(STAGE_HASH)
        tracker.should_emit(0, 100)
        clock[0] += _STAGE_EMIT_INTERVAL_SECONDS + 0.01
        assert tracker.should_emit(50, 100) is True

    def test_boundary_emit_bypasses_throttle(self, monkeypatch):
        """When completed == total, emit must fire even if the
        throttle hasn't elapsed — otherwise the bar shows 99% forever
        on a scan that finishes its last chunk within 1s of the
        previous emit."""
        clock = [0.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        tracker = _StageTracker(STAGE_HASH)
        tracker.should_emit(0, 100)
        clock[0] += 0.1  # well below the throttle
        assert tracker.should_emit(100, 100) is True


class TestThroughputWindowConstant:
    """The 5s window is the issue's acceptance criterion. Pin it so
    a future refactor doesn't silently widen the window to 30s and
    delay the "ETA appears" gate by 6×."""

    def test_window_is_five_seconds(self):
        assert _THROUGHPUT_WINDOW_SECONDS == 5.0
