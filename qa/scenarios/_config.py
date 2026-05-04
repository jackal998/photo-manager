"""Per-scenario source-folder configurations.

Each scenario needs a different source list in `qa/settings.json` before
the app launches. /qa-explore calls `python -m qa.scenarios.configure
<scenario_name>` between launches; that writes the right settings file
based on the table below.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO_ROOT / "qa" / "settings.json"

# Source folder lists per scenario. Keys are module names under qa.scenarios.
SCENARIO_SOURCES: dict[str, list[str]] = {
    "s01_happy_path":      ["qa/sandbox/huge", "qa/sandbox/near-duplicates", "qa/sandbox/unique"],
    "s02_empty_folder":    ["qa/sandbox/empty"],
    "s03_cancel_scan":     ["qa/sandbox/near-duplicates", "qa/sandbox/huge", "qa/sandbox/unique"],
    "s04_corrupted":       ["qa/sandbox/corrupted"],
    "s05_huge_preview":    ["qa/sandbox/huge"],
    "s06_formats":         ["qa/sandbox/formats"],
    "s07_format_dup":      ["qa/sandbox/format-dup"],
    "s08_exif_edge":       ["qa/sandbox/exif-edge"],
    "s09_walker_exclusions": ["qa/sandbox/walker-exclusions"],
    "s10_multi_source":    ["qa/sandbox/multi-source-a", "qa/sandbox/multi-source-b"],
    "s11_video_live":      ["qa/sandbox/videos", "qa/sandbox/live-photo"],
    "s12_save_manifest":   ["qa/sandbox/near-duplicates"],
    "s13_execute_action":  ["qa/sandbox/_disposable/s13_source"],
    "s14_action_by_regex": ["qa/sandbox/near-duplicates"],
    "s15_context_menu":    ["qa/sandbox/near-duplicates"],
    "s16_open_manifest":   ["qa/sandbox/near-duplicates"],
    # s17 starts from an empty source list — the driver populates it via the
    # in-dialog widgets; that's the whole point of the scenario.
    "s17_scan_dialog_widgets": [],
    # s18 doesn't run a scan; the source list is irrelevant.
    "s18_log_menu":            [],
    "s19_context_menu_open_folder": ["qa/sandbox/near-duplicates"],
    "s20_multi_remove_from_list":   ["qa/sandbox/near-duplicates", "qa/sandbox/format-dup"],
    "s21_list_menu_remove":         ["qa/sandbox/near-duplicates"],
}


def build_settings(scenario_name: str) -> dict:
    """Return the settings.json dict for a scenario."""
    sources = SCENARIO_SOURCES.get(scenario_name)
    if sources is None:
        raise KeyError(
            f"unknown scenario {scenario_name!r}; "
            f"known: {sorted(SCENARIO_SOURCES)}"
        )
    return {
        "_comment": f"Auto-written by qa.scenarios.configure for {scenario_name}.",
        "thumbnail_size": 256,
        "thumbnail_mem_cache": 128,
        "thumbnail_disk_cache_dir": "qa/.thumb-cache",
        "sorting": {"defaults": [{"field": "file_size_bytes", "asc": False}]},
        "sources": {
            "list": [{"path": p, "recursive": True} for p in sources],
            "output": "qa/run-manifest.sqlite",
        },
    }


def write_settings(scenario_name: str) -> Path:
    cfg = build_settings(scenario_name)
    SETTINGS_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return SETTINGS_PATH
