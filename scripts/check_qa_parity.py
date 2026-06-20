"""Check parity between qa/web/scenario_map.yml and ALL_SCENARIOS.

Invariant: the number of ``scenarios`` entries in scenario_map.yml must
equal ``len(ALL_SCENARIOS)`` from qa.scenarios._batch.  This script
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
    """Import ALL_SCENARIOS from qa.scenarios._batch and return it."""
    sys.path.insert(0, str(_REPO))
    try:
        from qa.scenarios._batch import ALL_SCENARIOS  # type: ignore[import]
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
# only — all 67 entries are "todo", so the target is 0.
PHASE_TARGETS: dict[int, int] = {
    0: 0,   # pre-scaffold
    1: 0,   # scaffold only — all todo
    2: 10,  # Phase 2 milestone: 10 scenarios ported
    3: 35,  # Phase 3 milestone: 35 scenarios ported
    4: 67,  # Phase 4 complete: all scenarios ported
}


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
        if args.phase not in PHASE_TARGETS:
            print(
                f"FAIL: unknown phase {args.phase}; valid values: {sorted(PHASE_TARGETS)}",
                file=sys.stderr,
            )
            return 1

        target = PHASE_TARGETS[args.phase]
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
