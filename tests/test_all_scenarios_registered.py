"""Tests that every qa scenario driver on disk is registered in
``ALL_SCENARIOS`` and configured in ``SCENARIO_SOURCES``.

The gap the harness was missing until this test landed: a contributor
(or AI agent) can add a new ``qa/scenarios/sNN_*.py`` driver, forget
to also register it in ``_batch.py::ALL_SCENARIOS``, and CI will
silently skip the new scenario — the only thing that ever runs is
the layer-1 unit tests. ``tests/test_batch_shard.py`` already pins
the partitioning *assuming* ``ALL_SCENARIOS`` is correct; this file
pins the input itself.

The four assertions form a bidirectional check across two registries
(``ALL_SCENARIOS`` + ``SCENARIO_SOURCES``) so any one of the
following accidents fails loudly at unit-test time:

  - new sNN_*.py file but not added to ALL_SCENARIOS
  - new sNN_*.py file but not added to SCENARIO_SOURCES
  - entry in ALL_SCENARIOS but file was renamed / deleted
  - entry in SCENARIO_SOURCES but file was renamed / deleted
"""
from __future__ import annotations

import re
from pathlib import Path

from qa.scenarios._batch import ALL_SCENARIOS
from qa.scenarios._config import SCENARIO_SOURCES

REPO = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = REPO / "qa" / "scenarios"

# Filename convention: ``sNN_*.py`` or ``sNNl_*.py`` where NN is two
# digits and l is an optional lowercase letter suffix (s23a / s23b are
# the only letter-suffixed names today — they form a paired scenario
# that splits a cross-launch boundary across two drivers). The regex
# matches the file stem, so e.g. ``s44_execute_highlighted_rows``.
_SCENARIO_RE = re.compile(r"^s\d{2}[a-z]?_[a-z0-9_]+$")


def _on_disk_scenario_names() -> set[str]:
    """Return the set of scenario module stems present under
    ``qa/scenarios/``. Excludes helper modules (``_batch.py``,
    ``_uia.py``, etc.) and any future filename that doesn't match
    the documented naming convention.
    """
    return {
        p.stem
        for p in SCENARIOS_DIR.glob("s*.py")
        if _SCENARIO_RE.match(p.stem)
    }


# ── ALL_SCENARIOS <-> on-disk parity ───────────────────────────────────────

def test_every_scenario_file_is_registered_in_all_scenarios() -> None:
    """sNN_*.py on disk → ALL_SCENARIOS. Catches the headline gap:
    'I added the driver but forgot to register it in _batch.py and CI
    silently skipped my new layer-3 coverage'.
    """
    on_disk = _on_disk_scenario_names()
    registered = set(ALL_SCENARIOS)
    missing = on_disk - registered
    assert not missing, (
        f"qa/scenarios/sNN_*.py files NOT registered in ALL_SCENARIOS "
        f"(CI skips these silently — add them to qa/scenarios/_batch.py): "
        f"{sorted(missing)}"
    )


def test_no_stale_entries_in_all_scenarios() -> None:
    """ALL_SCENARIOS → sNN_*.py on disk. Catches the reverse: a
    rename or delete that didn't update the registry. The batch
    runner would crash at import time, but this surfaces the
    mismatch in pytest output instead of behind a CI red.
    """
    on_disk = _on_disk_scenario_names()
    registered = set(ALL_SCENARIOS)
    stale = registered - on_disk
    assert not stale, (
        f"ALL_SCENARIOS entries with no matching qa/scenarios/<name>.py "
        f"(rename or delete didn't update _batch.py): {sorted(stale)}"
    )


# ── ALL_SCENARIOS <-> SCENARIO_SOURCES parity ─────────────────────────────

def test_every_scenario_has_source_config() -> None:
    """ALL_SCENARIOS → SCENARIO_SOURCES. ``qa.scenarios.configure``
    looks up the scenario name in SCENARIO_SOURCES to write a
    per-scenario ``qa/settings.json`` before the launch — a missing
    key fails at configure time, but pinning here surfaces it during
    unit tests instead of on the first CI run of the new scenario.
    """
    missing_config = [
        s for s in ALL_SCENARIOS if s not in SCENARIO_SOURCES
    ]
    assert not missing_config, (
        f"scenarios registered in ALL_SCENARIOS but missing from "
        f"SCENARIO_SOURCES (qa/scenarios/_config.py): {missing_config}"
    )


def test_no_stale_entries_in_scenario_sources() -> None:
    """SCENARIO_SOURCES → ALL_SCENARIOS. A SCENARIO_SOURCES key with
    no matching ALL_SCENARIOS entry is dead config — the source list
    is never read because the runner never invokes the scenario.
    """
    registered = set(ALL_SCENARIOS)
    stale_keys = set(SCENARIO_SOURCES) - registered
    assert not stale_keys, (
        f"SCENARIO_SOURCES keys not in ALL_SCENARIOS (dead config in "
        f"qa/scenarios/_config.py): {sorted(stale_keys)}"
    )
