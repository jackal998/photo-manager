"""Canonical, Qt-free registry of qa scenario ids.

``ALL_SCENARIOS`` is the single source of truth for the ordered set of
scenario ids. It lives here (not in ``qa.scenarios._batch``) because two
web-side consumers need it WITHOUT pulling in the Qt batch runner's
``ctypes`` / subprocess machinery:

  - ``qa.web._batch`` — the web QA batch runner
  - ``scripts/check_qa_parity.py`` — the parity CI check

``qa.scenarios._batch`` re-imports the list from here so the Qt batch
runner keeps working unchanged. This module imports nothing beyond the
standard library, so importing it never transitively loads PySide6.
"""
from __future__ import annotations

ALL_SCENARIOS = [
    "s01_happy_path",
    "s02_empty_folder",
    "s03_cancel_scan",
    "s04_corrupted",
    "s05_huge_preview",
    "s06_formats",
    "s07_format_dup",
    "s08_exif_edge",
    "s09_walker_exclusions",
    "s10_multi_source",
    "s11_video_live",
    "s12_save_manifest",
    "s13_execute_action",
    "s14_action_by_regex",
    "s15_context_menu",
    "s16_open_manifest",
    "s17_scan_dialog_widgets",
    "s18_log_menu",
    "s19_context_menu_open_folder",
    "s20_multi_remove_from_list",
    "s21_list_menu_remove",
    "s22_language_switch",
    # s23 is split A/B so the cross-launch boundary is an explicit batch step.
    # Order matters: s23b reads what s23a's GUI mutations persisted to disk.
    "s23a_set_settings",
    "s23b_verify_settings",
    "s24_stale_manifest_paths",
    "s25_empty_area_context_menu",
    "s26_keyboard_navigation",
    "s27_rescan_confirm",
    # s28 — dirty-flag exit prompt. Run AFTER s27 so any test order
    # change still puts s28 next to its closest neighbour (manifest
    # state-mutation scenarios). Self-cleans by exiting the app with
    # "Leave"; the next scenario relaunches.
    "s28_exit_dirty_prompt",
    # s29 — bulk regex remove-from-list as a deferred decision. Sister
    # to s14 (bulk regex delete) but with the deferred-remove action.
    "s29_remove_from_list_by_regex",
    # s30 — Phase A regex-dialog UX upgrade: right-click parity in
    # Execute Action dialog opens the same enhanced ActionDialog.
    # Sister to s14 (menu route) and s13 (toolbar-button route).
    "s30_execute_dialog_regex_right_click",
    # s31 — Phase B Simple mode (renamed from "Beginner" in Phase C)
    # plus the Phase C regex-sync invariants. Verifies Simple is the
    # default, drives the Simple widgets, then round-trips through
    # Regex mode to confirm the regex line edit holds the synthesised
    # pattern and reverse-parsing back populates Simple cleanly.
    "s31_simple_mode_regex",
    # s32 (#182) — Bulk regex on locked rows now surfaces the unified
    # LockedRowsConfirmDialog. Scenario drives "Apply to Unlocked Only"
    # end-to-end (today's old silent-skip behavior made explicit); the
    # other two verdicts (Unlock & Apply All, Cancel) are unit-tested.
    "s32_lock_confirm_bulk_regex",
    # s33 (#166) — Execute Action dialog's all-delete banner renders
    # the flagged group number as a clickable anchor (the click → jump
    # itself is covered by unit tests since QLabel HTML anchors aren't
    # first-class UIA elements).
    "s33_execute_dialog_jump_to_all_delete",
    # s34 (#182) — Execute-time lock confirm drives the
    # LockedRowsConfirmDialog when locked rows have decision='delete'
    # at the moment the user clicks Execute. Sister to s32 (bulk regex
    # trigger); same fixture as s14.
    "s34_lock_confirm_at_execute",
    # s35 (#182 follow-up, closes the gap that hid #175's missing
    # ActionHandlersImpl.set_locked_state proxy) — main-window
    # right-click Lock / Unlock for single + multi-select.
    "s35_lock_via_context_menu",
    # s36 (#182) — DESTRUCTIVE Execute through the lock-confirm
    # dialog. Sister to s13 (destructive happy path) and s34 (lock-
    # confirm Cancel, non-destructive). Proves the full chain when
    # the user picks Unlock & Apply All at Execute time: locked row
    # unlocks, send2trash fires for every row, manifest writes
    # executed=1. Disposable fixture; sends 5 files to recycle bin.
    "s36_lock_confirm_destructive_execute",
    # s37 (#138, #140) — persistent status-bar baseline. Probes that the
    # startup "Ready" message survives past the original 3s timeout and
    # that a post-load summary survives opening + dismissing the File
    # menu (the QAction-hover path that previously wiped temp messages).
    "s37_status_bar_baseline",
    # s38 (#144) — scan dialog inline error when "+ Add" is clicked with
    # a typed path that doesn't exist. Sister to s17 (in-dialog widget
    # ops); only s38 exercises the failure path.
    "s38_scan_dialog_invalid_path",
    # s39 (#136 + #141) — window geometry + splitter state persist
    # across launches, AND the splitter min-width constraints lift
    # the window's own minimum width above the #136 broken threshold.
    # Owns its own re-launch mid-scenario (the geometry round-trip
    # is what's under test); writes ``qa/window_state.ini`` and
    # cleans it up at startup.
    "s39_window_geometry_persist",
    # s40 (#143) — double-click dispatcher in TreeController. Verifies
    # group-header rows toggle expand/collapse on double-click (file-row
    # branch → OS viewer is unit-tested at layer 1; not driven here
    # because an OS-spawned image viewer has no deterministic
    # observable / cleanup path).
    "s40_results_tree_double_click",
    # s41 (#137) — empty-state primary-action buttons. Drives the
    # pre-manifest state: clicks Scan Sources… (asserts the scan
    # dialog opens), then clicks Open Manifest… (asserts the native
    # file picker opens, then cancels via Esc). Verifies the buttons
    # converge on the same handlers as the File-menu route.
    "s41_empty_state_action_buttons",
    # s42 (#187) — end-to-end keep-worthiness scoring: scan populates
    # the score column, within-group sort orders by score-DESC, and
    # the new "Apply best-copy decisions to this group" right-click
    # action picks the top scorer for KEEP + marks the rest DELETE.
    # Reuses near-duplicates fixture (5 q-quality variants); file-size
    # is the only signal that differs across the 5 files, so q95 wins.
    "s42_scoring",
    # s43 (#209) — Set Action dialog's new numeric-condition panel.
    # Opens Execute Action → Set Action by Field → switches the
    # field combo to Size (Bytes) → verifies the numeric panel
    # surfaces → sets a threshold > (q72's size) → verifies the 3
    # larger files are marked delete and the 2 smaller ones stay
    # unchanged. Non-destructive: cancels Execute before deletion.
    "s43_numeric_condition",
    # s44 — selection-scoped Execute (#211). Highlights 2 of 5
    # delete-decision rows in the Execute dialog tree, clicks Execute,
    # asserts only the highlighted files leave disk and the rest keep
    # their decisions intact (executed=0). Destructive like s13 —
    # 2 files per run go to the recycle bin.
    "s44_execute_highlighted_rows",
    # s45 (#121) — column-header sort flow + in-memory sort
    # preservation across manifest reload. Clicks File Name + Size
    # (Bytes) column headers, asserts the displayed row order toggles
    # ASC ↔ DESC via a new y-filter-free read helper (avoids the
    # read_result_rows y_min=600 trap on the smaller windows-latest
    # render), then triggers File → Open Manifest on the same path
    # and asserts the sort survives. Non-destructive.
    "s45_sort_persistence",
    # s47 (#214) — column layout (visual order + widths) persists
    # across launches. Owns its own re-launch mid-scenario (mirrors
    # s39's lifecycle for window geometry, which has the same
    # save-on-close / restore-on-next-launch property). The drag-to-
    # reorder path is layer-1 — synthetic SendInput is reliable for a
    # resize (drag the right-edge handle) but flaky for a move (Qt's
    # section-drag threshold is sensitive to event pacing on busy CI).
    "s47_column_layout_persist",
    # s48 (#215) — geometry persists across close-and-reopen WITHIN
    # one app session for ScanDialog / ExecuteActionDialog /
    # ActionDialog. Companion to s39 which covers the main-window
    # round-trip across an app restart. Non-destructive: scans
    # near-duplicates to load a manifest, then resizes / closes /
    # reopens each dialog and asserts the size came back through.
    "s48_dialog_geometry_persist",
    # s49 (#212) — "Auto select after scan" checkbox end-to-end.
    # Two phases inside one app session against the near-duplicates
    # fixture: phase 1 toggles the new Advanced-Settings checkbox ON
    # via UIA and asserts the top-scored row carries action="KEEP" in
    # the manifest; phase 2 toggles it OFF and asserts zero KEEP rows.
    "s49_scan_auto_select",
    # s50 (#237) — Select dialog's numeric-condition panel must surface
    # when the dialog is opened from the main-window menu route.
    # Sister to s43 which covers the same numeric panel reached via the
    # Execute Action dialog's "Select by Field/Regex…" button. Non-
    # destructive — just probes that the widgets surface after picking
    # a numeric field, then closes the dialog without applying.
    "s50_select_numeric_panel_from_main_window",
    # s51 (#165) — Execute Action dialog now embeds a PreviewPane via a
    # horizontal splitter. Non-destructive: opens the dialog with one
    # row marked 'delete', clicks the row, asserts that the dialog
    # contains both a tree and a preview pane visible to UIA, then
    # cancels without executing.
    "s51_execute_dialog_preview",
    # s52 (#253) — REVIEW_DUPLICATE rows' Similarity % is recomputed at
    # render time against the *displayed* Ref winner (which can diverge
    # from the scanner's anchor after #241's score-aware tie-break).
    # Scans the near-duplicates fixture, reads back the manifest, and
    # verifies that for every REVIEW_DUPLICATE row the (Ref-winner pHash,
    # row pHash) Hamming distance reaches the renderer — phash column
    # has to be wired through PhotoRecord for the new render path to
    # work. Read-only on the manifest.
    "s52_similarity_against_displayed_ref",
    # s53 (#324) — Execute Action dialog right-click → Lock / Unlock /
    # Set Action → delete. Layer-3 anchor for the non-regex decision
    # paths #322 plumbed status_reporter through under [qa-not-needed].
    # L1 tests (TestExecuteDialogStatusEmission) pin the methods when
    # called directly; this driver pins the right-click → context menu
    # → method chain.
    "s53_execute_dialog_lock_decision",
    # s54 (#324) — Execute Action dialog right-click → Set Action →
    # Remove from List → Yes-confirm. Companion to s53 covering the
    # fourth non-regex decision path, which adds a QMessageBox confirm
    # the L1 unit tests can't drive.
    "s54_execute_dialog_remove_from_list",
    # s55 (#347 C1, closes #366 C1) — ActionDialog C1 contract: opening
    # via the menu after scanning a no-dedup fixture (unique/) produces
    # match_fn=None, which must disable the Simple radio and force-check
    # Regex. Layer-1 pins the constructor branch; this driver pins the
    # UIA-observable disabled state. Non-destructive.
    "s55_action_dialog_no_match_fn",
    # s56 (#392) — ActionDialog Apply with field=Score writes decisions
    # via __cmp__: dispatch in file_operations.set_decision_by_regex.
    # Pins the fix for the original audit-triggering bug — before the
    # fix the dispatch was missing entirely and Apply silently no-op'd
    # for every non-Size numeric field via the main-window route.
    "s56_action_dialog_apply_by_score",
    # s57 (#393) — auto-select aggressive mode. Same near-duplicates
    # fixture as s49; aggressive flag tags the 4 non-keepers in the
    # scored group user_decision='delete'. Non-destructive (manifest
    # writes only).
    "s57_scan_auto_select_aggressive",
    # s58 (#428) — language switch preserves the loaded manifest.
    # Scan + load the near-duplicates fixture, then trigger View →
    # Language → 繁體中文; the post-switch result tree MUST still
    # show the 5 file rows the user had pre-switch. Driver restores
    # ui.locale=en on exit (mirrors s22).
    "s58_language_switch_preserves_manifest",
    # s59 (#444) — Execute Action dialog Select-by → main tree sync.
    # Sister to s30: same scan + seed + open Execute + Select-by +
    # regex apply, but verifies the MAIN WINDOW tree's rendered Action
    # cells after Close (not Execute). Pre-#444 fix the handler only
    # called refresh_tree on accept or remove-from-list, so the reject-
    # after-decision-change path left the main tree's rendered cells
    # stale relative to vm.groups + SQLite.
    "s59_execute_dialog_select_by_main_tree_sync",
    # s60 (#502) — Execute Action dialog type-filter combo end-to-end:
    # regen 8-file disposable fixture (2 clustering seeds × 4 qualities),
    # mark group A delete + group B remove via standalone regex,
    # exercise the combo (All/Delete only/Remove only), verify
    # hidden-destructive banner under Remove-only, execute under
    # Delete-only and verify group A files removed + group B intact.
    "s60_execute_filter_by_action_type",
    # s61 (#484) — SingletonPruneConfirmDialog actioned-singleton
    # classification (PruneVerdict, 3 layout/verdict variants). DB-only
    # mutation (Remove from List), no file deletes. Configure overrides
    # ui.prune_singletons="ask" so the dialog actually fires.
    "s61_actioned_singleton_prune",
    # s62 (#486-PR3c) removed in #560 — the re-calibrate checkbox it drove no
    # longer exists (calibration is now always-on, non-user-facing). The
    # always-auto resolution is unit-covered by TestResolveHashPool.
    # s63 (#475) — late-stage (post-HASH) cancel + main-window-X-during-
    # scan #468 guard. Uses a large disposable stub source so the cancel
    # lands past WALK/HASH and the worker is still alive when the main-
    # window close is sent.
    "s63_late_cancel_and_main_window_guard",
    # s64 (#483) — DESTRUCTIVE: Execute Action "Execute selected"
    # partial-execute button. Disposable 2-cluster fixture (6 JPEGs);
    # a highlighted subset is sent to the recycle bin, the remainder
    # via a follow-up full Execute. Same disposable contract as s13.
    # (s60/s62 are taken on master by #502/#486; this scenario landed
    # on the next free slot s64.)
    "s64_execute_selected_partial",
    "s65_passenger_bridge",
    "s66_autotune_read_knee",
    # s67 (#589) — D6 regression guard: under ui.prune_singletons="always"
    # the LockedRowsConfirmDialog still gates locked singletons (pre-D6
    # the always-path swept them silently). Disposable 2-JPEG cluster;
    # CANCEL → outcome='' (lock holds); Unlock & Apply → outcome='ignored'.
    "s67_locked_singleton_prune_always",
    # s68 (#622 Phase 1) — double-click on the single-view preview tile
    # opens FullResViewerDialog as a top-level window with the filename in
    # title. Uses qa/sandbox/huge (1 file). Pins the live double-click →
    # requestFullRes → on_open_full_res_viewer(service=…) wiring.
    "s68_full_res_viewer_double_click",
    # s69 — V1 web video playback: GET /api/media + <video> element.
    # Scans a WebM/VP9 test clip (qa/sandbox/video-playback/clip.webm),
    # selects the row, asserts <video> renders + real decode (readyState >= 2,
    # duration > 0) + frames advance after play(). VP8/VP9 = no codec gate.
    "s69_video_playback",
]
