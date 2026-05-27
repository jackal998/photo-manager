"""#424 — pure-logic tests for ScanDialog's progress-formatting
helpers: ``_format_throughput`` and ``_format_eta``.

The Qt widget assembly (frame visibility, label updates, progress
bar mode flips) is layer-3 territory exercised by s01_happy_path's
soft probes. These tests pin the receiver-side math + display rules
the user sees on every scan.
"""
from __future__ import annotations

import pytest

from app.views.dialogs.scan_dialog import _format_eta, _format_throughput


class TestFormatThroughput:
    """The third progress row's left half. Wrong here means the
    user sees "0 files/sec" right after the bar started ticking, or
    a noisy 200.7 files/sec wobble on a steady fast scan."""

    def test_zero_renders_as_dash(self):
        """No throughput → em-dash, not "0 files/sec" or "0.0".
        The em-dash is the canonical "unknown" marker for this row;
        a literal 0 would imply a real stall."""
        assert _format_throughput(0.0) == "—"

    def test_negative_renders_as_dash(self):
        """Defensive — if the rate came out negative (counter went
        backwards somehow), don't surface a nonsense number to the
        user."""
        assert _format_throughput(-1.5) == "—"

    def test_sub_ten_files_per_sec_uses_decimal(self):
        """Slow scans (SMB, video-heavy) — the tenths digit is
        meaningful at this rate."""
        assert _format_throughput(3.4) == "3.4 files/sec"

    def test_ten_or_more_drops_decimal(self):
        """Fast scans — tenths digit wobbles every emit and reads as
        UI jitter. 200/s is a steadier display than 200.7/s →
        200.3/s → 201.1/s every second."""
        assert _format_throughput(200.7) == "201 files/sec"
        assert _format_throughput(10.0) == "10 files/sec"


class TestFormatEta:
    """The third progress row's right half. Wrong here means the
    user either sees "ETA 0s" forever (stall hidden) or a misleading
    countdown that doesn't match reality."""

    def test_zero_throughput_returns_dash(self):
        """Stall → no ETA. Issue's acceptance criterion: "ETA … —
        clamped to '—' if throughput is zero (stalled)."""
        assert _format_eta(1000, 0.0) == "—"

    def test_zero_remaining_returns_dash(self):
        """Stage already complete (boundary emit) — no ETA needed,
        the bar already reads 100%."""
        assert _format_eta(0, 10.0) == "—"

    def test_sub_second_renders_lt_1s(self):
        """5 files at 100/s = 0.05s — render as "<1s" rather than
        "~0s" which reads as "instant" (it isn't, the bar will
        actually tick once more before this completes)."""
        assert _format_eta(5, 100.0) == "<1s"

    def test_under_a_minute_renders_seconds(self):
        """30 files at 1/s = 30s."""
        assert _format_eta(30, 1.0) == "~30s"

    def test_one_minute_to_one_hour_renders_minutes_seconds(self):
        """120 files at 1/s = 120s = 2m 00s. Always two-digit seconds
        so the column doesn't reflow as the value crosses 10s."""
        assert _format_eta(120, 1.0) == "~2m 00s"
        assert _format_eta(125, 1.0) == "~2m 05s"

    def test_over_an_hour_renders_hours_minutes(self):
        """7200 files at 1/s = 7200s = 2h. Past the one-hour mark
        the seconds digit isn't useful (user will look at the bar)."""
        assert _format_eta(7200, 1.0) == "~2h 00m"

    def test_remaining_negative_treated_as_done(self):
        """Defensive — if the receiver miscalculates and passes a
        negative remaining (e.g. completed > total because of a
        rounding bug), surface dash, not a negative ETA."""
        assert _format_eta(-5, 10.0) == "—"
