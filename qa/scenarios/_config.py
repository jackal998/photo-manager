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
    # s32 (#182, supersedes #175) — bulk regex on locked rows surfaces
    # the LockedRowsConfirmDialog. Scenario drives "Apply to Unlocked
    # Only" (today's silent skip-locked, now an explicit choice); the
    # other two verdicts are pinned at layer 1. Same fixture as s14.
    "s32_lock_confirm_bulk_regex": ["qa/sandbox/near-duplicates"],
    # s33 (#166) — Execute Action dialog all-delete banner renders the
    # group number(s) so they can be clicked to jump to that group.
    # Same fixture as s32; bulk delete .+ covers every row so the
    # banner must fire for the one group_number the scanner produces.
    "s33_execute_dialog_jump_to_all_delete": ["qa/sandbox/near-duplicates"],
    # s34 (#182) — Execute-time lock confirm: when the user has set
    # decision='delete' on a row and THEN locked it, clicking Execute
    # must surface the same LockedRowsConfirmDialog before any
    # destructive action runs.
    "s34_lock_confirm_at_execute": ["qa/sandbox/near-duplicates"],
    # s35 (#182 follow-up) — main-window right-click Lock / Unlock
    # for single + multi-select. End-to-end coverage of the
    # ActionHandlersImpl.set_locked_state proxy that was missing
    # silently from #175 to #182.
    "s35_lock_via_context_menu": ["qa/sandbox/near-duplicates"],
    # s36 (#182) — DESTRUCTIVE end-to-end through the Execute-time
    # lock confirm dialog. Disposable fixture (regenerated each run);
    # sends 5 files to the Windows recycle bin per run.
    "s36_lock_confirm_destructive_execute": ["qa/sandbox/_disposable/s36_source"],
    # s37 (#138, #140) — exercises status-bar baseline. Needs a source
    # that produces at least one group so the post-load summary is
    # non-trivial; near-duplicates is the standard small fixture.
    "s37_status_bar_baseline": ["qa/sandbox/near-duplicates"],
    # s38 (#144) — empty source list; driver populates via the path-field
    # validation flow (bad path → inline error, then good path → row).
    "s38_scan_dialog_invalid_path": [],
    # s39 (#136 + #141) — window geometry + splitter state round-trip
    # across launches and #136 min-width floor. No scan performed; an
    # empty source list is fine.
    "s39_window_geometry_persist": [],
    # s40 (#143) — double-click dispatcher (group-row toggle expand).
    # Same small fixture as s19 (Open Folder counterpart) so the scan
    # is fast and the group label is deterministic ("Group 1").
    "s40_results_tree_double_click": ["qa/sandbox/near-duplicates"],
    # s41 (#137) — empty-state primary-action buttons. Drives the
    # first-run state, so the source list is empty by design (any
    # populated source list would let a prior scan leak in via cached
    # state). Scenario clicks each button and asserts the right
    # dialog opens, then cancels the open-manifest picker.
    "s41_empty_state_action_buttons": [],
    # s42 (#187) — end-to-end keep-worthiness scoring with two
    # fixtures:
    #
    # * near-duplicates: 5 q-quality variants of one image —
    #   file_size is the only differentiating signal. Pins the
    #   pipeline plumbing (score column populates, within-group
    #   order is score-DESC, "Apply best-copy" picks the largest
    #   and marks the rest delete).
    #
    # * scoring-mixed: 4 near-duplicates that vary per-dimension —
    #   one with GPS+clean-name+clean-path (winner), one with
    #   filename penalty ("Copy of …"), one with GPS stripped, one
    #   in a Downloads/ subdir (path penalty). Pins the EXTRACTION
    #   wiring — that the real exiftool produces the keys we parse
    #   for gps_present, that filename/path regex flows reach the
    #   stored signals, and that the composite picks the clean file
    #   even though the classifier's lexicographic source-priority
    #   would have picked "Copy of …" as the action=MOVE primary.
    "s42_scoring": [
        "qa/sandbox/near-duplicates",
        "qa/sandbox/scoring-mixed",
    ],
    # s43 (#209) — Set Action dialog's new numeric-condition panel.
    # Reuses near-duplicates (5 q-quality variants); the JPEG sizes
    # are well-separated, so a Size (Bytes) threshold cleanly splits
    # the group into matched vs. unchanged subsets.
    "s43_numeric_condition": ["qa/sandbox/near-duplicates"],
    # s44 (#211) — selection-scoped Execute. Disposable fixture
    # (regenerated each run by the driver, 5 near-duplicate JPEGs);
    # 2 of the 5 are sent to the Windows recycle bin per run. Same
    # destructive-fixture pattern as s13 and s36.
    "s44_execute_highlighted_rows": ["qa/sandbox/_disposable/s44_source"],
    # s45 (#121) — column-header sort + in-memory sort preservation
    # across manifest reload. Drives File Name + Size (Bytes) header
    # clicks, asserts row order through a new y-filter-free helper,
    # then re-opens the same manifest in-process and asserts the
    # sort survives. Near-duplicates: 5 files with distinct sizes
    # (q-quality variants), so the size sort is deterministic.
    "s45_sort_persistence": ["qa/sandbox/near-duplicates"],
    # s46 (#165 prototype) — Execute Mode toggle. Non-destructive;
    # needs a loaded manifest so the View → Execute Mode action is
    # enabled. Near-duplicates is the standard small fixture.
    "s46_execute_mode_toggle": ["qa/sandbox/near-duplicates"],
    # s47 (#214) — column layout persists across launches. Needs a
    # fixture that produces at least one group so the result tree
    # renders and the header columns are interactable. Same fixture as
    # s40 / s14 / s32; deterministic file count and group label.
    "s47_column_layout_persist": ["qa/sandbox/near-duplicates"],
    # s48 (#215) — dialog geometry persists across close-and-reopen.
    # Near-duplicates fixture so the in-session scan produces a
    # manifest, which enables Execute Action and the ActionDialog
    # preview-pane path.
    "s48_dialog_geometry_persist": ["qa/sandbox/near-duplicates"],
    # s49 (#212) — "Auto select after scan" end-to-end. Same fixture as
    # s42 (5 q-quality near-duplicates); file_size_bytes is the sole
    # differentiating signal so q95 is the deterministic per-group score
    # winner the scenario asserts on.
    "s49_scan_auto_select": ["qa/sandbox/near-duplicates"],
    # s50 (#237) — Select dialog's numeric panel reachable from the
    # main-window menu route. Same fixture as s43 — needs a loaded
    # manifest so the menu item is enabled and ``records_provider``
    # has groups to pass to ActionDialog. Non-destructive.
    "s50_select_numeric_panel_from_main_window": ["qa/sandbox/near-duplicates"],
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
