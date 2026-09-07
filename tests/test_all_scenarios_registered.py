"""Tests that the layer-3 scenario registry, the web scenario map and the
drivers on disk all agree.

The gap the harness was missing until this test landed: a contributor (or AI
agent) can add a new driver, forget to register it, and CI silently skips the
new scenario — the only thing that ever runs is the layer-1 unit tests.

#646 retargeted this file. Before the cutover the registry
(``qa.scenario_ids.ALL_SCENARIOS``) was checked against the desktop client's
drivers and its source-folder config; both went with that client. The web
harness resolves a scenario id through ``qa/web/scenario_map.yml`` to a
``qa.web.scenarios`` module, so that is the pair of registries pinned here.

The four assertions form a bidirectional check so any one of these accidents
fails loudly at unit-test time:

  - new qa/web/scenarios/sNN_*.py but no scenario_map.yml row
  - scenario_map.yml row naming a module that does not exist
  - ALL_SCENARIOS id with no scenario_map.yml row (the web batch iterates
    the map, so the id would never run)
  - scenario_map.yml row that is not in ALL_SCENARIOS (dead config)
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from qa.scenario_ids import ALL_SCENARIOS

REPO = Path(__file__).resolve().parent.parent
WEB_SCENARIOS_DIR = REPO / "qa" / "web" / "scenarios"
SCENARIO_MAP = REPO / "qa" / "web" / "scenario_map.yml"

# Filename convention: ``sNN_*.py`` or ``sNNl_*.py`` where NN is two digits
# and l is an optional lowercase letter suffix (s23a / s23b are the only
# letter-suffixed names today — they form a paired scenario that splits a
# cross-launch boundary across two drivers).
_SCENARIO_RE = re.compile(r"^s\d{2}[a-z]?_[a-z0-9_]+$")


def _map_entries() -> list[dict]:
    with SCENARIO_MAP.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return list(data.get("scenarios", []))


def _on_disk_driver_stems() -> set[str]:
    """Module stems present under ``qa/web/scenarios/``.

    Excludes helper modules (``__init__.py`` and any ``_*.py``) and any
    filename that doesn't match the documented naming convention.
    """
    return {
        p.stem
        for p in WEB_SCENARIOS_DIR.glob("s*.py")
        if _SCENARIO_RE.match(p.stem)
    }


def _mapped_module_stems() -> dict[str, str]:
    """``{scenario id: driver module stem}`` for every ported row.

    ``playwright_module`` is null for a scenario with no web analogue
    (status ``skip``); those rows are excluded here and covered by
    ``test_unported_rows_are_marked_skip`` instead.
    """
    out: dict[str, str] = {}
    for entry in _map_entries():
        module = entry.get("playwright_module")
        if module:
            out[entry["scenario"]] = module.rsplit(".", 1)[-1]
    return out


# ── scenario_map.yml <-> on-disk parity ────────────────────────────────────

def test_every_mapped_module_exists_on_disk() -> None:
    """scenario_map.yml → qa/web/scenarios/<module>.py.

    A row pointing at a renamed or deleted driver makes the web batch ERROR
    on that scenario at import time; this surfaces the mismatch in pytest
    output instead of behind a CI red.
    """
    missing = {
        scenario: stem
        for scenario, stem in _mapped_module_stems().items()
        if not (WEB_SCENARIOS_DIR / f"{stem}.py").is_file()
    }
    assert not missing, (
        "scenario_map.yml rows whose playwright_module has no file under "
        f"qa/web/scenarios/: {missing}"
    )


def test_every_driver_on_disk_is_mapped() -> None:
    """qa/web/scenarios/sNN_*.py → scenario_map.yml. Catches the headline
    gap: 'I added the driver but forgot to map it, and the batch silently
    skipped my new layer-3 coverage'.
    """
    unmapped = _on_disk_driver_stems() - set(_mapped_module_stems().values())
    assert not unmapped, (
        "qa/web/scenarios/ drivers with no scenario_map.yml row (the web "
        f"batch never runs these): {sorted(unmapped)}"
    )


# ── ALL_SCENARIOS <-> scenario_map.yml parity ──────────────────────────────

def test_every_registered_scenario_has_a_map_row() -> None:
    """ALL_SCENARIOS → scenario_map.yml. The web batch resolves ids through
    the map, so an id with no row can never run.
    """
    mapped = {e["scenario"] for e in _map_entries()}
    missing = set(ALL_SCENARIOS) - mapped
    assert not missing, (
        "ids in qa/scenario_ids.ALL_SCENARIOS with no qa/web/scenario_map.yml "
        f"row: {sorted(missing)}"
    )


def test_no_stale_rows_in_scenario_map() -> None:
    """scenario_map.yml → ALL_SCENARIOS. A row with no matching id is dead
    config: nothing iterates it, so its driver never runs.
    """
    mapped = {e["scenario"] for e in _map_entries()}
    stale = mapped - set(ALL_SCENARIOS)
    assert not stale, (
        f"qa/web/scenario_map.yml rows not in ALL_SCENARIOS: {sorted(stale)}"
    )


# ── the "no web analogue" exemption stays honest ───────────────────────────

def test_unported_rows_are_marked_skip() -> None:
    """A row may omit ``playwright_module`` only while it is explicitly
    ``todo`` / ``in_progress`` / ``skip``. A row marked ``done`` with no
    module is the dangerous shape: ``check_qa_parity.py`` counts it toward
    the ported total while nothing runs.
    """
    lying = [
        e["scenario"]
        for e in _map_entries()
        if not e.get("playwright_module") and e.get("status") == "done"
    ]
    assert not lying, (
        "scenario_map.yml rows marked status: done with no playwright_module "
        f"(counted as ported, never executed): {sorted(lying)}"
    )
