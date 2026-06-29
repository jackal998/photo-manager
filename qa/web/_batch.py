"""Web QA batch runner — mirrors qa.scenarios._batch for the web harness.

In Phase 1 no web drivers exist yet, so every scenario is marked SKIP.
Phase 2+ drivers live under qa/web/scenarios/ and are wired in via
``scenario_map.yml``.

Playwright is imported lazily; if it is not installed, all scenarios
degrade to SKIP (not FAIL) so CI unit-test jobs don't break.

Usage
-----
python -m qa.web._batch
python -m qa.web._batch --phase 1
python -m qa.web._batch s01_happy_path s02_empty_folder
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]

# --- import ALL_SCENARIOS from the Qt-free registry (no Qt / ctypes pulled in)
sys.path.insert(0, str(_REPO))
from qa.scenario_ids import ALL_SCENARIOS  # noqa: E402  # type: ignore[import]

# --- determine whether playwright is available ----------------------------
try:
    import playwright  # type: ignore[import]  # noqa: F401
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

# --- load scenario_map.yml ------------------------------------------------
_MAP_PATH = _REPO / "qa" / "web" / "scenario_map.yml"


def _load_map() -> dict[str, dict]:
    """Return a {scenario_name: entry_dict} mapping from scenario_map.yml."""
    try:
        import yaml  # type: ignore[import]
        with _MAP_PATH.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return {e["scenario"]: e for e in data.get("scenarios", [])}
    except ImportError:
        # yaml not available: return empty map so every scenario is "todo"
        return {}


# ---------------------------------------------------------------------------
# Result constants (mirrors qa.scenarios._batch convention)
# ---------------------------------------------------------------------------
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
ERROR = "ERROR"


def _run_one(scenario_name: str, entry: dict, base_url: str) -> tuple[str, str]:
    """Run a single web driver scenario and return (status, detail).

    Parameters
    ----------
    scenario_name:
        The scenario key, e.g. ``"s01_happy_path"``.
    entry:
        The corresponding entry from scenario_map.yml.
    base_url:
        Base URL of the running FastAPI server.

    Returns
    -------
    tuple[str, str]
        ``(status, detail)`` where status is one of PASS / FAIL / SKIP / ERROR.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        return SKIP, "playwright not installed"

    status = entry.get("status", "todo")
    if status == "todo":
        return SKIP, "not yet ported (status: todo)"
    if status == "skip":
        return SKIP, entry.get("notes", "marked skip in scenario_map.yml")

    module_path = entry.get("playwright_module")
    if not module_path:
        return SKIP, "playwright_module is null in scenario_map.yml"

    # Attempt to import and run the driver.
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        return ERROR, f"cannot import {module_path}: {exc}"

    try:
        run_fn = getattr(mod, "run", None)
        if run_fn is None:
            return ERROR, f"{module_path} has no run() function"
        # Per-scenario settings reset (web analog of the Qt batch's per-scenario
        # `configure`). The web ScanDialog persists sources/output across opens
        # (#678-E / s23a/s23b); clearing them before each scenario keeps the
        # "always one blank row" assumption that ~all scan scenarios rely on.
        # Fail-open — never blocks a scenario.
        from qa.web._invariants import reset_scan_persistence
        reset_scan_persistence(base_url)
        run_fn(base_url=base_url)
        return PASS, ""
    except AssertionError as exc:
        return FAIL, str(exc)
    except Exception as exc:  # noqa: BLE001
        return ERROR, f"{type(exc).__name__}: {exc}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenarios",
        nargs="*",
        metavar="SCENARIO",
        help="Specific scenarios to run (default: all).",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8765",
        help="Base URL of the running FastAPI server.",
    )
    parser.add_argument(
        "--phase",
        type=int,
        default=None,
        help="Filter to scenarios whose status is appropriate for this phase.",
    )
    args = parser.parse_args(argv)

    scenario_map = _load_map()

    # Determine the run list.
    if args.scenarios:
        run_list = [s for s in ALL_SCENARIOS if s in args.scenarios]
        unknown = set(args.scenarios) - set(ALL_SCENARIOS)
        if unknown:
            print(f"WARNING: unknown scenarios: {sorted(unknown)}", file=sys.stderr)
    else:
        run_list = list(ALL_SCENARIOS)

    results: dict[str, tuple[str, str]] = {}
    for name in run_list:
        entry = scenario_map.get(name, {"scenario": name, "status": "todo", "notes": ""})
        status, detail = _run_one(name, entry, base_url=args.base_url)
        results[name] = (status, detail)
        symbol = {"PASS": ".", "FAIL": "F", "SKIP": "s", "ERROR": "E"}.get(status, "?")
        if detail:
            print(f"  {symbol} {name}: {detail}")
        else:
            print(f"  {symbol} {name}")

    # Summary
    counts = {k: sum(1 for s, _ in results.values() if s == k) for k in (PASS, FAIL, SKIP, ERROR)}
    total = len(results)
    print(
        f"\n{total} scenarios — "
        f"{counts[PASS]} passed, {counts[FAIL]} failed, "
        f"{counts[SKIP]} skipped, {counts[ERROR]} errors"
    )

    # Non-zero exit if any FAIL or ERROR.
    return 0 if (counts[FAIL] == 0 and counts[ERROR] == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
