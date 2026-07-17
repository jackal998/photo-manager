"""Check parity between qa/web/scenario_map.yml and ALL_SCENARIOS.

Invariant: the number of ``scenarios`` entries in scenario_map.yml must
equal ``len(ALL_SCENARIOS)`` from qa.scenario_ids.  This script
enforces that invariant in CI (layer 1 probe) and locally.

Exit codes
----------
0 — parity OK (or the --phase target is met)
1 — parity failure or count below --phase target

Usage
-----
python scripts/check_qa_parity.py
python scripts/check_qa_parity.py --phase 1
python scripts/check_qa_parity.py --phase 2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Locate the repo root relative to this script.
_REPO = Path(__file__).resolve().parents[1]


def _load_all_scenarios() -> list[str]:
    """Import ALL_SCENARIOS from the Qt-free qa.scenario_ids registry."""
    sys.path.insert(0, str(_REPO))
    try:
        from qa.scenario_ids import ALL_SCENARIOS  # type: ignore[import]
        return list(ALL_SCENARIOS)
    except ImportError as exc:
        print(f"ERROR: cannot import ALL_SCENARIOS: {exc}", file=sys.stderr)
        sys.exit(1)


def _load_scenario_map() -> list[str]:
    """Parse qa/web/scenario_map.yml and return the list of scenario keys."""
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        # PyYAML is not available; fall back to a minimal line-scanner that
        # looks for ``  - scenario: <name>`` lines.  Good enough for CI that
        # doesn't want to install PyYAML just for this check.
        return _parse_yaml_fallback()

    map_path = _REPO / "qa" / "web" / "scenario_map.yml"
    with map_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    entries = data.get("scenarios", [])
    return [e["scenario"] for e in entries]


def _parse_yaml_fallback() -> list[str]:
    """Minimal line scanner used when PyYAML is not installed."""
    map_path = _REPO / "qa" / "web" / "scenario_map.yml"
    names: list[str] = []
    with map_path.open(encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("- scenario:"):
                name = stripped.removeprefix("- scenario:").strip()
                names.append(name)
    return names


# Phase targets: number of scenarios expected to have status != "todo"
# (i.e. actively ported or marked skip).  Phase 1 ships the scaffold
# only — all entries are "todo", so the target is 0.
#
# Phases 0-3 are fixed milestones (small subsets of the total). Phase 4
# ("complete") is deliberately NOT a fixed number here — see
# _phase4_target(). A hard-coded PHASE_TARGETS[4] would silently stop
# tracking the real total the moment a new scenario is added to
# ALL_SCENARIOS (Correction #14): the total would grow but the frozen
# target would not, so a newly added `todo` scenario could pass the
# phase-4 gate unported.
PHASE_TARGETS: dict[int, int] = {
    0: 0,   # pre-scaffold
    1: 0,   # scaffold only — all todo
    2: 10,  # Phase 2 milestone: 10 scenarios ported
    3: 35,  # Phase 3 milestone: 35 scenarios ported
}


def _phase4_target(source_count: int) -> int:
    """Phase-4 target: every scenario must be non-"todo" (ported or
    permanently marked skip) — i.e. zero todo entries remain.

    Runtime-derived from the current total instead of a frozen constant, so
    adding a new scenario to ALL_SCENARIOS without porting or skipping it
    fails this gate instead of silently passing (Correction #14).

    NOTE: no separate "documented skip count" is subtracted here.
    ``_count_ported`` below already treats status "skip" as non-todo (it
    counts anything != "todo"), so permanently-skipped scenarios are already
    included in ``ported`` — subtracting a skip count from the target would
    double-count them and create N slots of slack (one new unported
    scenario per skip entry could slip through before the gate ever fires).
    """
    return source_count


def _count_ported(scenario_map_names: list[str]) -> int:
    """Return the number of entries whose status is not 'todo'.

    This requires parsing the full YAML, so it tries yaml first and
    falls back to a status-line scanner.
    """
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        return _count_ported_fallback()

    map_path = _REPO / "qa" / "web" / "scenario_map.yml"
    with map_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    entries = data.get("scenarios", [])
    return sum(1 for e in entries if e.get("status", "todo") != "todo")


def _count_ported_fallback() -> int:
    """Minimal status counter used when PyYAML is not installed."""
    map_path = _REPO / "qa" / "web" / "scenario_map.yml"
    count = 0
    with map_path.open(encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("status:") and "todo" not in stripped:
                count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        type=int,
        default=None,
        help="Check that the number of ported scenarios meets the phase target.",
    )
    args = parser.parse_args(argv)

    all_scenarios = _load_all_scenarios()
    map_names = _load_scenario_map()

    source_count = len(all_scenarios)
    map_count = len(map_names)

    ok = True

    # --- parity check -------------------------------------------------------
    if source_count != map_count:
        print(
            f"FAIL parity: ALL_SCENARIOS has {source_count} entries "
            f"but scenario_map.yml has {map_count}.",
            file=sys.stderr,
        )
        ok = False

    # Report missing / extra entries
    source_set = set(all_scenarios)
    map_set = set(map_names)
    missing_from_map = source_set - map_set
    extra_in_map = map_set - source_set

    if missing_from_map:
        for name in sorted(missing_from_map):
            print(f"  missing from scenario_map.yml: {name}", file=sys.stderr)
    if extra_in_map:
        for name in sorted(extra_in_map):
            print(f"  extra in scenario_map.yml (not in ALL_SCENARIOS): {name}", file=sys.stderr)

    # --- phase target check -------------------------------------------------
    if args.phase is not None:
        valid_phases = sorted(set(PHASE_TARGETS) | {4})
        if args.phase not in valid_phases:
            print(
                f"FAIL: unknown phase {args.phase}; valid values: {valid_phases}",
                file=sys.stderr,
            )
            return 1

        target = (
            _phase4_target(source_count)
            if args.phase == 4
            else PHASE_TARGETS[args.phase]
        )
        ported = _count_ported(map_names)
        if ported < target:
            print(
                f"FAIL phase {args.phase}: {ported}/{source_count} scenarios ported "
                f"(target: {target}).",
                file=sys.stderr,
            )
            ok = False
        else:
            print(
                f"OK   phase {args.phase}: {ported}/{source_count} scenarios ported "
                f"(target: {target})."
            )

    if ok and args.phase is None:
        print(
            f"OK   parity: {source_count} scenarios in ALL_SCENARIOS "
            f"== {map_count} entries in scenario_map.yml."
        )

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
