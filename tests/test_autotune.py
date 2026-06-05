"""Tests for scanner/autotune.py — the #551 read-knee probe pure logic.

Two units under test, both pure (no I/O, no Qt, no threads):

* ``knee_from_throughput`` — picks the read-concurrency knee from a
  ``{c: files/s}`` map.
* ``ReadKneeRamp`` — the per-device ramp state machine the scan_worker consumer
  drives.

Every test here exercises a real decision a user's hardware would force the
probe to make (a 2-channel NAS the static 8 over-subscribes, an SSD that scales,
a spinning HDD, a noisy curve, a device that vanishes mid-probe, completion-order
skew, the widen fill-transient). None monkeypatch internals to reach a defensive
branch, force a flag, or assert merely that a branch was reached.
"""

from __future__ import annotations

import pytest

from scanner.autotune import (
    AUTOTUNE_RECIPE_VERSION,
    READ_KNEE_LADDER,
    ReadKneeRamp,
    knee_from_throughput,
)


class TestKneeFromThroughput:
    """The pure knee picker on representative throughput-vs-concurrency curves."""

    def test_monotonic_plateau_picks_knee(self):
        # 2-channel NAS: doubling past 2 buys < 15% — static 8 over-subscribes it.
        # The comparator's baseline must be the previous c (190→200 = 5%), not c=1.
        assert knee_from_throughput({1: 100, 2: 190, 4: 200, 8: 205}) == 2

    def test_strictly_rising_returns_cap(self):
        # SSD/NVMe that genuinely scales: every doubling ~doubles → take the cap.
        # The inverse of the plateau case; together they pin the comparator's sign.
        assert knee_from_throughput({1: 100, 2: 200, 4: 400, 8: 800}) == 8

    def test_hdd_knee_one(self):
        # Spinning HDD: concurrency makes it WORSE (seek thrash). The probe must
        # reproduce the static _HDD_WORKERS=1, never regress to a higher count.
        assert knee_from_throughput({1: 97, 2: 80, 4: 62, 8: 55}) == 1

    def test_noisy_curve_stable_pick(self):
        # Knee at 2; a later noisy uptick (193→191 is actually a dip, but even an
        # uptick) must NOT un-knee it, or the cached value oscillates run-to-run.
        assert knee_from_throughput({1: 100, 2: 188, 4: 193, 8: 191}) == 2

    def test_flat_plateau_ties_to_smaller_c(self):
        # Wholly flat: the first doubling already < floor → smallest c wins.
        assert knee_from_throughput({1: 100, 2: 100, 4: 100, 8: 100}) == 1

    def test_empty_samples_fall_open(self):
        assert knee_from_throughput({}) is None

    def test_single_concurrency_fall_open(self):
        # One rung → no doubling to compare → fall open to the static value.
        assert knee_from_throughput({1: 100}) is None

    def test_none_value_mid_probe_falls_open(self):
        # A device that went away mid-probe yields a None rate → fall open, never
        # crash and never invent a knee from a half-measured curve.
        assert knee_from_throughput({1: 100, 2: None}) is None

    def test_zero_rate_falls_open(self):
        # A measured zero rate (stalled device) is not a real plateau → fall open.
        assert knee_from_throughput({1: 100, 2: 0}) is None

    def test_knee_never_exceeds_measured_concurrency(self):
        # Only {1,2} measured and still rising → knee is the largest MEASURED rung
        # (2), never an unmeasured 4/8.
        knee = knee_from_throughput({1: 100, 2: 200})
        assert knee == 2
        assert knee in (1, 2)

    def test_non_doubling_gap_falls_open(self):
        # {1,4} with no 2 measured is not a clean doubling pair → fall open.
        assert knee_from_throughput({1: 100, 4: 400}) is None

    def test_recipe_version_is_a_nonempty_token(self):
        # Guards the cache-keying contract: an empty/None token would make every
        # device's cache entry collide or never invalidate.
        assert isinstance(AUTOTUNE_RECIPE_VERSION, str) and AUTOTUNE_RECIPE_VERSION


def _ts_for_rate(t0: float, n: int, rate: float) -> list[float]:
    """``n`` evenly-spaced completion timestamps from ``t0`` whose
    ``(n-1)/span`` files/s equals ``rate``."""
    step = 1.0 / rate
    return [t0 + i * step for i in range(n)]


def _feed_level(
    ramp: ReadKneeRamp,
    c: int,
    t0: float,
    rate: float,
    *,
    n: int = 4,
    fill_n: int = 0,
) -> int:
    """Feed one level worth of reads at concurrency ``c`` then close it.

    ``fill_n`` fill-transient reads are clustered just *before* ``t0`` (so the
    ramp's earliest-completion fill discard drops exactly them); the ``n``
    measured reads land at ``t0`` onward at the requested files/s. Returns the
    ramp's ``current_permits`` after the close attempt.
    """
    for j in range(fill_n):
        ramp.record(1000, t0 - (fill_n - j) * 1e-4, level_tag=c)
    for t in _ts_for_rate(t0, n, rate):
        ramp.record(1000, t, level_tag=c)
    return ramp.advance_if_level_done()


def _fresh_ramp(max_c: int = 8) -> ReadKneeRamp:
    # Small per-level budget + no min-seconds gate keeps the state-machine tests
    # fast and deterministic; the constants themselves are covered by the
    # min-seconds and fill-transient tests below.
    return ReadKneeRamp(max_c, target_files_per_level=4, min_seconds=0.0)


class TestReadKneeRampLadder:
    """Ladder clamping + the inert HDD / single-rung path."""

    def test_hdd_single_rung_is_inert(self):
        ramp = ReadKneeRamp(1, target_files_per_level=4, min_seconds=0.0)
        assert ramp.is_ramping() is False
        assert ramp.knee() == 1
        assert ramp.current_permits() == 1
        assert ramp.summary()["ladder"] == [1]

    def test_ssd_ladder_clamps_to_four(self):
        ramp = _fresh_ramp(4)
        assert ramp.summary()["ladder"] == [1, 2, 4]
        assert ramp.is_ramping() is True

    def test_nas_ladder_is_full(self):
        assert _fresh_ramp(8).summary()["ladder"] == list(READ_KNEE_LADDER)


class TestReadKneeRampMeasurement:
    """Driving the ramp through real curves end-to-end."""

    def test_plateau_freezes_with_knee_below_live_budget(self):
        # The key current_permits-vs-knee semantic: detecting knee=2 REQUIRES
        # measuring level 4, so the live Semaphore budget overshoots to 4 while
        # the value cached (knee) is 2. The Semaphore is never narrowed live.
        ramp = _fresh_ramp(8)
        _feed_level(ramp, 1, t0=0.0, rate=100.0)
        _feed_level(ramp, 2, t0=100.0, rate=190.0, fill_n=2)
        _feed_level(ramp, 4, t0=200.0, rate=200.0, fill_n=4)
        assert ramp.is_ramping() is False
        assert ramp.knee() == 2
        assert ramp.current_permits() == 4

    def test_rising_curve_reaches_cap(self):
        ramp = _fresh_ramp(8)
        _feed_level(ramp, 1, t0=0.0, rate=100.0)
        _feed_level(ramp, 2, t0=100.0, rate=200.0, fill_n=2)
        _feed_level(ramp, 4, t0=200.0, rate=400.0, fill_n=4)
        _feed_level(ramp, 8, t0=400.0, rate=800.0, fill_n=8)
        assert ramp.is_ramping() is False
        assert ramp.knee() == 8
        assert ramp.current_permits() == 8

    def test_ramp_knee_agrees_with_pure_picker(self):
        # The incremental freeze-vs-step logic must agree with the pure
        # knee_from_throughput on the SAME measured samples — guards drift
        # between the two independent implementations of the gain rule.
        ramp = _fresh_ramp(8)
        _feed_level(ramp, 1, t0=0.0, rate=100.0)
        _feed_level(ramp, 2, t0=100.0, rate=190.0, fill_n=2)
        _feed_level(ramp, 4, t0=200.0, rate=200.0, fill_n=4)
        assert ramp.knee() == knee_from_throughput(ramp.summary()["levels"])

    def test_zero_byte_reads_do_not_advance_a_level(self):
        # Video/gif/skip/ReadFailure arrive with nbytes==0: they flow through the
        # pipeline but must NOT count toward the files/s signal, or an all-video
        # level would fabricate a rate.
        ramp = _fresh_ramp(8)
        for t in _ts_for_rate(0.0, 8, 100.0):
            ramp.record(0, t, level_tag=1)
        assert ramp.advance_if_level_done() == 1  # level 0 not closed
        assert ramp.summary()["levels"] == {}
        assert ramp.is_ramping() is True

    def test_advance_respects_min_seconds(self):
        # Enough reads but too little wall-time → don't close yet (a sub-half-
        # second burst is too noisy to trust). Extending the span closes it.
        ramp = ReadKneeRamp(8, target_files_per_level=4, min_seconds=0.5)
        for t in (0.0, 0.01, 0.02, 0.03):  # 4 reads spanning 0.03s < 0.5s
            ramp.record(1000, t, level_tag=1)
        assert ramp.advance_if_level_done() == 1  # not closed — span too short
        assert ramp.summary()["levels"] == {}
        ramp.record(1000, 0.6, level_tag=1)  # now spans 0.6s ≥ 0.5s
        ramp.advance_if_level_done()
        assert 1 in ramp.summary()["levels"]


class TestReadKneeRampRobustness:
    """The two correctness guards the #551 review (F1, F7) added."""

    def test_knee_invariant_to_completion_order(self):
        # F1 guard: a read belongs to the level it was READ at (its level_tag),
        # so the SAME per-level reads delivered to record() in a different order
        # must yield the identical knee. (The ramp sorts each level's timestamps
        # at close, so call order cannot skew which files land in a level.)
        def drive(reverse_within_level: bool) -> ReadKneeRamp:
            ramp = _fresh_ramp(8)
            for c, t0, rate, fill_n in (
                (1, 0.0, 100.0, 0),
                (2, 100.0, 190.0, 2),
                (4, 200.0, 200.0, 4),
            ):
                calls = [
                    (1000, t0 - (fill_n - j) * 1e-4, c) for j in range(fill_n)
                ] + [(1000, t, c) for t in _ts_for_rate(t0, 4, rate)]
                if reverse_within_level:
                    calls = list(reversed(calls))
                for nbytes, ts, tag in calls:
                    ramp.record(nbytes, ts, level_tag=tag)
                ramp.advance_if_level_done()
            return ramp

        in_order = drive(reverse_within_level=False)
        shuffled = drive(reverse_within_level=True)
        assert in_order.knee() == shuffled.knee() == 2
        assert in_order.summary()["levels"] == pytest.approx(
            shuffled.summary()["levels"]
        )

    def test_discards_fill_transient(self):
        # F7 guard: when the budget widens, the first new_c reads run at an
        # intermediate concurrency while the new permits fill. If they were
        # counted, the level's measured rate would be dragged down (knee biased
        # low). They are the earliest completions, so the fill discard drops them
        # and the recorded rate reflects only the post-fill reads.
        ramp = _fresh_ramp(8)
        _feed_level(ramp, 1, t0=0.0, rate=100.0)
        # Level 2 (fill_skip=2): 2 slow fill reads spanning a big early gap, then
        # 4 measured reads at a clean 190 files/s. Including the fill reads would
        # stretch the span and crater the rate; discarding them must not.
        ramp.record(1000, 100.0, level_tag=2)   # fill: far-early, slow
        ramp.record(1000, 130.0, level_tag=2)   # fill: 30s gap (would tank rate)
        measured_t0 = 160.0
        for t in _ts_for_rate(measured_t0, 4, 190.0):
            ramp.record(1000, t, level_tag=2)
        ramp.advance_if_level_done()
        assert ramp.summary()["levels"][2] == pytest.approx(190.0)

    def test_drained_old_level_read_is_ignored(self):
        # A read acquired under the previous concurrency can still be in flight
        # when the budget has already widened; it arrives tagged with the OLD
        # level. It must not count toward the current level's measurement, or a
        # slow straggler would corrupt the new level's rate.
        ramp = _fresh_ramp(8)
        _feed_level(ramp, 1, t0=0.0, rate=100.0)  # closes level 1 → now at c=2
        assert ramp.current_permits() == 2
        ramp.record(1000, 50.0, level_tag=1)  # stray drained level-1 read
        # Now measure level 2 cleanly; the stray must not be in its set.
        _feed_level(ramp, 2, t0=100.0, rate=190.0, fill_n=2)
        assert ramp.summary()["levels"][2] == pytest.approx(190.0)

    def test_equal_timestamps_do_not_divide_by_zero(self):
        # A pathologically fast device can complete a level's reads on the same
        # monotonic tick → zero span. The ramp must not crash; it keeps the level
        # open (no rate yet) rather than dividing by zero.
        ramp = _fresh_ramp(8)
        for _ in range(6):
            ramp.record(1000, 5.0, level_tag=1)  # all identical timestamps
        assert ramp.advance_if_level_done() == 1  # not closed, no exception
        assert ramp.summary()["levels"] == {}

    def test_frozen_ramp_ignores_further_records(self):
        # Once frozen (HDD here), record/advance are inert — late draining reads
        # can't corrupt a settled measurement.
        ramp = ReadKneeRamp(1, target_files_per_level=4, min_seconds=0.0)
        for t in _ts_for_rate(0.0, 8, 100.0):
            ramp.record(1000, t, level_tag=1)
        assert ramp.advance_if_level_done() == 1
        assert ramp.knee() == 1
        assert ramp.summary()["levels"] == {}


class TestReadKneeCache:
    """store_read_knee / _valid_read_knee — the device_key-keyed lifetime cache."""

    def test_store_read_knee_round_trips_through_settings(self, tmp_path):
        # Real JsonSettings round-trip (no Qt) — a knee written under a device_key
        # survives write+reload so the next scan of that device reads it back.
        import json

        from infrastructure.settings import JsonSettings
        from scanner.autotune import AUTOTUNE_RECIPE_VERSION, store_read_knee

        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"sources": {}}), encoding="utf-8")
        settings = JsonSettings(settings_path)

        store_read_knee(settings, r"\\LINXIAOYUN", 2)

        entry = settings.get("scan.read_knee_cache")[r"\\LINXIAOYUN"]
        assert entry == {"knee": 2, "recipe": AUTOTUNE_RECIPE_VERSION}
        reloaded = JsonSettings(settings_path)  # next session reads from disk
        assert reloaded.get("scan.read_knee_cache")[r"\\LINXIAOYUN"]["knee"] == 2

    def test_store_read_knee_is_per_device_not_per_source_set(self, tmp_path):
        # The whole point of dropping the source-path fingerprint: two devices'
        # knees coexist under their own keys, and re-measuring one device updates
        # it in place without disturbing the other — independent of which folders
        # were scanned to produce them (no fingerprint in the key).
        import json

        from infrastructure.settings import JsonSettings
        from scanner.autotune import store_read_knee

        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({}), encoding="utf-8")
        settings = JsonSettings(settings_path)

        store_read_knee(settings, r"\\NAS", 2)
        store_read_knee(settings, "D:", 1)
        store_read_knee(settings, r"\\NAS", 4)  # re-measured on a later, different scan

        cache = settings.get("scan.read_knee_cache")
        assert cache[r"\\NAS"]["knee"] == 4
        assert cache["D:"]["knee"] == 1

    def test_valid_read_knee_accepts_current_recipe(self):
        from scanner.autotune import AUTOTUNE_RECIPE_VERSION, _valid_read_knee

        assert _valid_read_knee({"knee": 2, "recipe": AUTOTUNE_RECIPE_VERSION})

    def test_valid_read_knee_rejects_stale_recipe(self):
        # A knee measured under an older probe algorithm must be re-probed — this
        # is the ONLY invalidation lever now the source-path fingerprint is gone.
        from scanner.autotune import _valid_read_knee

        assert not _valid_read_knee({"knee": 2, "recipe": "999-old"})

    def test_valid_read_knee_rejects_malformed_entries(self):
        # Hand-editable settings.json → a corrupt/partial entry is a cache miss,
        # never a crash. bool is rejected though it's an int subclass; a
        # non-positive knee is rejected (a stalled-device 0 is not a real knee).
        from scanner.autotune import AUTOTUNE_RECIPE_VERSION as V
        from scanner.autotune import _valid_read_knee

        assert not _valid_read_knee(None)
        assert not _valid_read_knee({"recipe": V})              # no knee
        assert not _valid_read_knee({"knee": 2})                # no recipe
        assert not _valid_read_knee({"knee": "x", "recipe": V})  # non-int knee
        assert not _valid_read_knee({"knee": True, "recipe": V})  # bool, not a knee
        assert not _valid_read_knee({"knee": 0, "recipe": V})    # non-positive
