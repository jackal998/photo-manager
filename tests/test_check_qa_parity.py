"""Tests for scripts/check_qa_parity.py — Correction #14 (runtime-derived
phase-4 target).

Before the fix, ``PHASE_TARGETS[4] = 67`` was a frozen constant. The total
scenario count is 71 today (67 done + 4 permanently-skip), so the gate
happened to pass — but a newly added ``todo`` scenario would raise the total
without raising the frozen target, so the gate would keep passing with the
new scenario silently unported. These tests pin the runtime-derived
replacement: the target must track ``len(ALL_SCENARIOS)`` so an unported
addition fails the gate instead of slipping through.

NOTE: the target is simply ``source_count`` (zero todo entries remain), not
``source_count - skip_count``. ``_count_ported`` already counts status
"skip" as non-todo, so subtracting a skip count from the target would
double-count permanently-skipped entries and create slack — verified by
``test_new_todo_scenario_fails_phase4_gate`` below, which failed under the
skip-subtracting formula (one new todo scenario was absorbed by the slack
instead of failing the gate).
"""

from __future__ import annotations

import scripts.check_qa_parity as parity


def test_phase4_target_derives_from_source_count() -> None:
    """The formula is source_count itself, not a frozen constant."""
    assert parity._phase4_target(71) == 71
    assert parity._phase4_target(72) == 72


def test_phase4_target_matches_todays_real_registry() -> None:
    """Sanity pin: today's real ALL_SCENARIOS count is 71, and the target
    tracks it exactly (no slack) since all 71 are already non-todo
    (67 done + 4 skip).

    Not a tautology — this would fail if the formula stopped deriving from
    the live registry count.
    """
    all_scenarios = parity._load_all_scenarios()
    assert len(all_scenarios) == 71
    assert parity._phase4_target(len(all_scenarios)) == 71


def test_new_todo_scenario_fails_phase4_gate(monkeypatch) -> None:
    """Injecting one extra 'todo' scenario must fail the phase-4 gate.

    Regression pin for Correction #14: the pre-fix PHASE_TARGETS[4] = 67 was
    hard-coded, so a new todo scenario (raising the total past 71) still
    passed because ported (71) stayed >= the frozen target (67). The
    runtime-derived target must track the total instead, so the gate now
    correctly fails when a scenario is added but not ported.
    """
    real_scenarios = list(parity._load_all_scenarios())
    real_map_names = list(parity._load_scenario_map())
    real_ported = parity._count_ported(real_map_names)

    fake_name = "s99_hypothetical_todo"
    monkeypatch.setattr(
        parity, "_load_all_scenarios", lambda: real_scenarios + [fake_name]
    )
    monkeypatch.setattr(
        parity, "_load_scenario_map", lambda: real_map_names + [fake_name]
    )
    # The injected scenario stays "todo" — _count_ported must not count it.
    monkeypatch.setattr(parity, "_count_ported", lambda names: real_ported)

    exit_code = parity.main(["--phase", "4"])
    assert exit_code == 1, (
        "a newly injected todo scenario must fail the phase-4 gate instead "
        "of silently passing under a stale hard-coded target"
    )


def test_phase_0_to_3_targets_unchanged() -> None:
    """Correction #14 only touches phase 4 — phases 0-3 keep their fixed milestones."""
    assert parity.PHASE_TARGETS == {0: 0, 1: 0, 2: 10, 3: 35}
