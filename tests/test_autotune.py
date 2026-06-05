"""Tests for scanner/autotune.py — OccupancyProbe pure logic.

These tests verify the regime-classification logic the #551 auto-tuner
depends on.  They feed crafted sample streams and assert on the EWMA
convergence and regime verdict.

No pipeline wiring is tested here (that is env-gated and exercised via
the #551 Phase-0 validation run — see docs/testing.md).
"""

from __future__ import annotations

import pytest

from scanner.autotune import OccupancyProbe, _EWMA_ALPHA


# ---------------------------------------------------------------------------
# EWMA convergence helpers
# ---------------------------------------------------------------------------

def _feed(probe: OccupancyProbe, qsize: int, maxsize: int, n: int) -> None:
    """Feed ``n`` identical samples into ``probe``."""
    for _ in range(n):
        probe.sample(qsize, maxsize)


# ---------------------------------------------------------------------------
# (a) High-occupancy stream → compute-bound
# ---------------------------------------------------------------------------

class TestComputeBound:
    def test_high_occupancy_regime(self):
        """120/128 occupancy for long enough must converge to >= 0.90 EWMA."""
        probe = OccupancyProbe()
        # At alpha=0.1, after N steps from 0 toward target T:
        #   ewma_N = T * (1 - (1-alpha)^N)
        # For T=120/128≈0.9375 to reach 0.90:
        #   0.9375 * (1 - 0.9^N) >= 0.90  → N >= log(1 - 0.90/0.9375) / log(0.9) ≈ 30
        # Feed 100 samples to be comfortably above.
        _feed(probe, 120, 128, 100)
        s = probe.summary()
        assert s["occ_ewma"] >= 0.90, f"Expected >= 0.90, got {s['occ_ewma']:.4f}"
        assert s["regime"] == "compute-bound"

    def test_n_samples_count(self):
        probe = OccupancyProbe()
        _feed(probe, 120, 128, 50)
        assert probe.summary()["n_samples"] == 50


# ---------------------------------------------------------------------------
# (b) Low-occupancy stream → io-bound
# ---------------------------------------------------------------------------

class TestIoBound:
    def test_low_occupancy_regime(self):
        """5/128 occupancy for long enough must converge to <= 0.15 EWMA."""
        probe = OccupancyProbe()
        # T = 5/128 ≈ 0.039; EWMA converges to 0.039, well below 0.15.
        _feed(probe, 5, 128, 100)
        s = probe.summary()
        assert s["occ_ewma"] <= 0.15, f"Expected <= 0.15, got {s['occ_ewma']:.4f}"
        assert s["regime"] == "io-bound"


# ---------------------------------------------------------------------------
# (c) Mixed stream → mixed/unclear
# ---------------------------------------------------------------------------

class TestMixed:
    def test_mixed_regime(self):
        """A transient between high and low occupancy sits in the mixed band.

        At alpha=0.1, 50 high samples drive EWMA to ~0.93.  Adding 15 low
        samples (10/128 ≈ 0.078) pulls it to ~0.38 — well inside (0.15, 0.90).
        Using 50 low samples overshoots: the EWMA converges to ~0.08 (<= 0.15)
        and tips into io-bound.  The 15-sample count is deliberate.
        """
        probe = OccupancyProbe()
        _feed(probe, 120, 128, 50)   # drive EWMA up to ~0.93
        _feed(probe, 10, 128, 15)    # pull down, stay above 0.15
        s = probe.summary()
        assert 0.15 < s["occ_ewma"] < 0.90, (
            f"Expected mixed range (0.15, 0.90), got {s['occ_ewma']:.4f}"
        )
        assert s["regime"] == "mixed/unclear"

    def test_n_samples_counts_all_samples(self):
        probe = OccupancyProbe()
        _feed(probe, 120, 128, 50)
        _feed(probe, 10, 128, 15)
        assert probe.summary()["n_samples"] == 65


# ---------------------------------------------------------------------------
# (d) starved_count accumulates correctly
# ---------------------------------------------------------------------------

class TestStarvedCount:
    def test_starved_zero_initially(self):
        probe = OccupancyProbe()
        assert probe.summary()["starved"] == 0

    def test_starved_accumulates(self):
        probe = OccupancyProbe()
        for _ in range(7):
            probe.note_starved()
        assert probe.summary()["starved"] == 7

    def test_starved_independent_of_samples(self):
        """note_starved() must not affect occ_ewma or n_samples."""
        probe = OccupancyProbe()
        _feed(probe, 64, 128, 10)
        for _ in range(5):
            probe.note_starved()
        s = probe.summary()
        assert s["n_samples"] == 10
        assert s["starved"] == 5
        # occ_ewma should reflect 64/128 = 0.5 only, not the starved count
        assert 0.15 < s["occ_ewma"] < 0.90


# ---------------------------------------------------------------------------
# (e) fail-safe on maxsize=0
# ---------------------------------------------------------------------------

class TestFailSafe:
    def test_maxsize_zero_skips_sample(self):
        """sample() with maxsize=0 must not crash and must not increment n_samples."""
        probe = OccupancyProbe()
        probe.sample(0, 0)
        probe.sample(10, 0)
        s = probe.summary()
        assert s["n_samples"] == 0
        assert s["regime"] == "no-data"

    def test_maxsize_zero_leaves_ewma_zero(self):
        """occ_ewma stays 0.0 when only zero-maxsize samples are fed."""
        probe = OccupancyProbe()
        probe.sample(5, 0)
        assert probe.summary()["occ_ewma"] == 0.0

    def test_maxsize_zero_mixed_with_valid(self):
        """Zero-maxsize samples are skipped; valid ones still accumulate."""
        probe = OccupancyProbe()
        _feed(probe, 120, 128, 10)
        probe.sample(50, 0)    # should be silently ignored
        s = probe.summary()
        assert s["n_samples"] == 10

    def test_no_data_regime_when_no_samples(self):
        probe = OccupancyProbe()
        assert probe.summary()["regime"] == "no-data"


# ---------------------------------------------------------------------------
# EWMA arithmetic — spot-check the formula
# ---------------------------------------------------------------------------

class TestEwmaArithmetic:
    def test_first_sample_seeds_ewma(self):
        """The very first sample sets occ_ewma to the exact occupancy value."""
        probe = OccupancyProbe()
        probe.sample(64, 128)   # 0.5
        assert probe.summary()["occ_ewma"] == pytest.approx(0.5)

    def test_ewma_alpha_applied(self):
        """Second sample should blend at _EWMA_ALPHA."""
        probe = OccupancyProbe()
        probe.sample(64, 128)   # seed = 0.5
        probe.sample(128, 128)  # new obs = 1.0; blend = 0.1*1.0 + 0.9*0.5 = 0.55
        expected = _EWMA_ALPHA * 1.0 + (1.0 - _EWMA_ALPHA) * 0.5
        assert probe.summary()["occ_ewma"] == pytest.approx(expected)

    def test_custom_alpha(self):
        """OccupancyProbe accepts a custom alpha for test-faster convergence."""
        probe = OccupancyProbe(alpha=0.5)
        probe.sample(0, 128)    # seed = 0.0
        probe.sample(128, 128)  # blend = 0.5*1.0 + 0.5*0.0 = 0.5
        assert probe.summary()["occ_ewma"] == pytest.approx(0.5)
