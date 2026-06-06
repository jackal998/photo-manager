"""Tests for scanner/byte_budget.py — the #587 HASH-stage byte-budget gate.

These are pure layer-1 tests: ``ByteBudget`` has no Qt / no I/O, so the
accounting, the admit-one-over-budget rule, the cancel-wake (the real deadlock
failure mode), and the concurrent peak-bound invariant are all unit-testable
without a scan. ``default_budget_bytes`` is tested with the RAM probe
monkeypatched (the real ``ctypes.windll`` / ``os.sysconf`` probe is excluded
from coverage — it can't run portably on CI).
"""

from __future__ import annotations

import threading
import time

import scanner.byte_budget as bb_mod
from scanner.byte_budget import ByteBudget, default_budget_bytes


class TestByteBudgetAccounting:
    def test_acquire_release_tracks_inflight(self):
        bb = ByteBudget(1000, lambda: False)
        assert bb.acquire(400) is True
        assert bb._inflight == 400
        assert bb.acquire(600) is True
        assert bb._inflight == 1000
        bb.release(400)
        assert bb._inflight == 600
        bb.release(600)
        assert bb._inflight == 0

    def test_zero_and_negative_bytes_are_noops(self):
        # video / None / ReadFailure payloads have no byte cost — must never
        # block and never touch the counter.
        bb = ByteBudget(100, lambda: False)
        assert bb.acquire(0) is True
        assert bb.acquire(-5) is True
        assert bb._inflight == 0
        bb.release(0)
        bb.release(-5)
        assert bb._inflight == 0

    def test_release_clamps_at_zero_and_never_raises(self):
        # A done-callback that over-releases (or double-releases) must not drive
        # _inflight negative or raise — a raise there would skip out_q.put and
        # hang the parent drain loop (#587 invariant).
        bb = ByteBudget(100, lambda: False)
        bb.acquire(30)
        bb.release(100)  # more than held
        assert bb._inflight == 0


class TestByteBudgetBlocking:
    def test_acquire_blocks_until_room_then_admits(self):
        bb = ByteBudget(100, lambda: False)
        assert bb.acquire(100) is True  # budget now full
        out = {}

        def waiter():
            out["r"] = bb.acquire(50)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.2)
        assert t.is_alive(), "acquire must block while the budget is full"
        bb.release(100)  # make room
        t.join(timeout=2)
        assert out["r"] is True
        assert bb._inflight == 50

    def test_acquire_unblocks_on_cancel(self):
        # THE deadlock failure mode: a dispatch/reader thread blocked in
        # acquire() must wake when the scan is cancelled, or cancel wedges
        # (the #492/#495/#507/#561 scar class).
        cancelled = {"v": False}
        bb = ByteBudget(100, lambda: cancelled["v"])
        bb.acquire(100)  # fill
        out = {}

        def waiter():
            out["r"] = bb.acquire(50)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.2)
        assert t.is_alive(), "acquire should be blocked (budget full, not cancelled)"
        cancelled["v"] = True
        t.join(timeout=2)
        assert not t.is_alive(), "acquire must return promptly once cancel flips"
        assert out["r"] is False, "acquire must report False (not admitted) on cancel"


class TestByteBudgetAdmitOneOverBudget:
    def test_over_budget_file_admitted_alone_when_idle(self):
        # A single file larger than the whole budget must not deadlock: it is
        # admitted once nothing else is in flight.
        bb = ByteBudget(100, lambda: False)
        assert bb.acquire(500) is True
        assert bb._inflight == 500
        bb.release(500)
        assert bb._inflight == 0

    def test_over_budget_file_waits_until_inflight_drains(self):
        bb = ByteBudget(100, lambda: False)
        bb.acquire(50)  # something else in flight
        out = {}

        def waiter():
            out["r"] = bb.acquire(500)  # over budget → must wait for _inflight == 0

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.2)
        assert t.is_alive(), "over-budget acquire must wait while others are in flight"
        bb.release(50)  # drain to idle
        t.join(timeout=2)
        assert out["r"] is True
        assert bb._inflight == 500


class TestByteBudgetConcurrencyInvariant:
    def test_peak_inflight_never_exceeds_budget(self):
        # The core memory-safety invariant: under many concurrent acquire/release
        # cycles the in-flight total never crosses the ceiling. A buggy acquire
        # that admitted without checking room would breach it.
        budget = 1000
        chunk = 200  # < budget, never the over-budget path
        bb = ByteBudget(budget, lambda: False)
        peak = {"v": 0}
        stop = {"v": False}

        def sampler():
            while not stop["v"]:
                with bb._lock:
                    if bb._inflight > peak["v"]:
                        peak["v"] = bb._inflight
                time.sleep(0.0005)

        s = threading.Thread(target=sampler)
        s.start()

        def worker():
            for _ in range(40):
                bb.acquire(chunk)
                time.sleep(0.001)
                bb.release(chunk)

        ts = [threading.Thread(target=worker) for _ in range(12)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        stop["v"] = True
        s.join()

        assert peak["v"] <= budget, f"in-flight {peak['v']} breached budget {budget}"
        assert bb._inflight == 0


class TestDefaultBudgetSizing:
    def test_clamped_between_floor_and_cap(self, monkeypatch):
        _MIB = 1024 * 1024
        _GIB = 1024 ** 3
        # 64 GB workstation → half is 32 GB → capped at 2 GiB.
        monkeypatch.setattr(bb_mod, "_probe_total_ram", lambda: 64 * _GIB)
        assert default_budget_bytes() == 2 * _GIB
        # 400 MB box → half is 200 MB (< floor) → floored at 256 MiB.
        monkeypatch.setattr(bb_mod, "_probe_total_ram", lambda: 400 * _MIB)
        assert default_budget_bytes() == 256 * _MIB
        # 3 GB box → half is 1.5 GB → between floor and cap, returned as-is.
        monkeypatch.setattr(bb_mod, "_probe_total_ram", lambda: 3 * _GIB)
        assert default_budget_bytes() == (3 * _GIB) // 2

    def test_probe_failure_falls_back_below_cap(self, monkeypatch):
        # On probe failure the fallback must not exceed the cap (so a failed
        # probe on a small-RAM box can't hand out a budget larger than the box).
        monkeypatch.setattr(bb_mod, "_probe_total_ram", lambda: None)
        budget = default_budget_bytes()
        assert budget == 1 * 1024 ** 3
        assert budget <= 2 * 1024 ** 3
