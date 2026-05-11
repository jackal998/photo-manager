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
#
# Sentinel: a value of ``None`` means "preserve the existing qa/settings.json".
# Used when a scenario needs to read back state that a previous scenario
# wrote via the GUI (e.g. s23b reads what s23a persisted through
# ScanDialog._save_to_settings). The configure step becomes a no-op for
# such scenarios; the batch ordering in _batch.py is what guarantees the
# previous scenario ran first.
SCENARIO_SOURCES: dict[str, list[str] | None] = {
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
    # s22 — View → Language switch persists ui.locale and prompts for restart.
    # Driver does no scanning; empty source list is fine. Driver MUST restore
    # ui.locale=en before exiting (see scenario docstring) so subsequent
    # scenarios in the batch don't launch in 繁體中文.
    "s22_language_switch":          [],
    # s23 (#122) — scan dialog settings round-trip across app restart.
    # Split into two scenarios so the cross-launch boundary is an
    # explicit batch step. s23a writes via GUI; s23b launches fresh
    # and reads back. The ``None`` sentinel on s23b tells configure
    # to preserve what s23a persisted.
    "s23a_set_settings":            ["qa/sandbox/unique"],
    "s23b_verify_settings":         None,
    # s24 (#123) — open manifest whose source files were deleted
    # after the scan (stale-paths UX). Driver creates the disposable
    # source dir at startup, then deletes it before the re-load step.
    "s24_stale_manifest_paths":     ["qa/sandbox/_disposable/s24_source"],
    # s25 (#124) — right-click on empty area / menu bar / unselected row
    # must NOT spawn a Qt context menu.
    "s25_empty_area_context_menu":  ["qa/sandbox/near-duplicates"],
    # s26 (#125) — keyboard-only navigation through main flow (tree
    # arrows, Alt+F mnemonic, scan dialog Tab cycle, Esc).
    "s26_keyboard_navigation":      ["qa/sandbox/near-duplicates"],
    # s27 (#142) — re-scan with pending decisions triggers confirmation prompt.
    "s27_rescan_confirm":           ["qa/sandbox/near-duplicates"],
    # s28 — exit-dirty prompt. Runs a small scan + sets a decision to
    # dirty the manifest; the prompt assertions follow.
    "s28_exit_dirty_prompt":        ["qa/sandbox/near-duplicates"],
    # s29 — bulk regex "remove from list" deferred decision. Same fixture
    # as s14 so the regex partition (q[89]\d) keeps producing 3 matches
    # / 2 unchanged.
    "s29_remove_from_list_by_regex": ["qa/sandbox/near-duplicates"],
    # s30 — Phase A regex-dialog UX upgrade: right-click parity in
    # Execute Action dialog. Same fixture and regex partition as s14
    # so the verification flow can mirror it.
    "s30_execute_dialog_regex_right_click": ["qa/sandbox/near-duplicates"],
    # s31 — Phase B Simple mode + Phase C regex-sync round-trip:
    # same fixture as s14/s30; partition is Simple "contains q9" →
    # matches only neardup_00_q95.jpg.
    "s31_simple_mode_regex": ["qa/sandbox/near-duplicates"],
    # s32 (#164) — Lock state protects per-file decisions from bulk
    # regex sweeps. Same fixture as s14 so the regex partition stays
    # stable; q95 locked, q[89]\d destructive regex matches all three
    # but only q88 / q80 actually receive the decision.
    "s32_lock_protects_from_bulk_regex": ["qa/sandbox/near-duplicates"],
    # s33 (#166) — Execute Action dialog all-delete banner renders the
    # group number(s) so they can be clicked to jump to that group.
    # Same fixture as s32; bulk delete .+ covers every row so the
    # banner must fire for the one group_number the scanner produces.
    "s33_execute_dialog_jump_to_all_delete": ["qa/sandbox/near-duplicates"],
}


def build_settings(scenario_name: str) -> dict | None:
    """Return the settings.json dict for a scenario, or ``None`` to mean
    "preserve the existing settings.json on disk" (see SCENARIO_SOURCES
    sentinel docstring above)."""
    if scenario_name not in SCENARIO_SOURCES:
        raise KeyError(
            f"unknown scenario {scenario_name!r}; "
            f"known: {sorted(SCENARIO_SOURCES)}"
        )
    sources = SCENARIO_SOURCES[scenario_name]
    if sources is None:
        return None  # preserve existing settings.json
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
    if cfg is None:
        return SETTINGS_PATH  # preserve — caller's previous scenario already wrote
    SETTINGS_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return SETTINGS_PATH
