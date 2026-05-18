# photo-manager — Feature Inventory

Canonical catalogue of user-visible behaviour. Each section names one
feature: where it is reachable, what the user does to trigger it, what
happens, and which conditions change the result.

This file is the answer to "is this behaviour documented?" and
"what's the expected UX for X?". It complements — does not replace —
the happy-path walkthrough in [`README.md` § Usage — GUI](../README.md#usage--gui).

The full backfill ships in two stages: [PR #263](https://github.com/jackal998/photo-manager/pull/263)
seeded the Execute Action cluster; this PR completes the inventory
(scan, review, save/load, preview, i18n). A follow-up PR will harden
`docs_guard` to require an update here whenever user-visible
behaviour changes — see [#262](https://github.com/jackal998/photo-manager/issues/262)
for the chore plan.

---

## Index

| Feature | Area |
|---|---|
| [Bulk regex — remove from list (deferred decision)](#bulk-regex--remove-from-list-deferred-decision) | Bulk operations |
| [Context menu — Lock / Unlock](#context-menu--lock--unlock) | Context menu |
| [Context menu — Open Folder](#context-menu--open-folder) | Context menu |
| [Context menu — Set Action (per-file)](#context-menu--set-action-per-file) | Context menu |
| [Empty area context menu](#empty-area-context-menu) | Context menu |
| [Empty-state action buttons](#empty-state-action-buttons) | Main window |
| [Execute Action — base flow](#execute-action--base-flow) | Execute Action |
| [Execute Action — complete-group delete confirm](#execute-action--complete-group-delete-confirm) | Execute Action |
| [Execute Action — complete-group warning banner with jump-to](#execute-action--complete-group-warning-banner-with-jump-to) | Execute Action |
| [Execute Action — dialog geometry persistence](#execute-action--dialog-geometry-persistence) | Execute Action |
| [Execute Action — lock-confirm dialog](#execute-action--lock-confirm-dialog) | Execute Action |
| [Execute Action — preview pane](#execute-action--preview-pane) | Execute Action |
| [Execute Action — scope to highlighted rows](#execute-action--scope-to-highlighted-rows) | Execute Action |
| [Exit dirty-flag prompt](#exit-dirty-flag-prompt) | Main window |
| [Keep-worthiness scoring](#keep-worthiness-scoring) | Review |
| [Language switch](#language-switch) | i18n |
| [List menu — Remove from List](#list-menu--remove-from-list) | Menus |
| [Log menu](#log-menu) | Menus |
| [Main window — column order/width persistence](#main-window--column-orderwidth-persistence) | Main window |
| [Main window — geometry + splitter persistence](#main-window--geometry--splitter-persistence) | Main window |
| [Main window — keyboard navigation](#main-window--keyboard-navigation) | Main window |
| [Main window — results tree double-click](#main-window--results-tree-double-click) | Main window |
| [Main window — sort persistence within session](#main-window--sort-persistence-within-session) | Main window |
| [Main window — status bar baseline](#main-window--status-bar-baseline) | Main window |
| [Open Manifest — base flow](#open-manifest--base-flow) | File operations |
| [Save Manifest Decisions — base flow](#save-manifest-decisions--base-flow) | File operations |
| [Scan dialog — auto-select after scan](#scan-dialog--auto-select-after-scan) | Scan |
| [Scan dialog — collapse Advanced Settings](#scan-dialog--collapse-advanced-settings) | Scan |
| [Scan dialog — folder list (no priority arrows)](#scan-dialog--folder-list-no-priority-arrows) | Scan |
| [Scan dialog — multi-source scan](#scan-dialog--multi-source-scan) | Scan |
| [Scan flow — rescan confirm](#scan-flow--rescan-confirm) | Scan |
| [Scan flow — visual selection of KEEP rows after scan](#scan-flow--visual-selection-of-keep-rows-after-scan) | Scan |
| [Set Action dialog — Beginner / Regex mode toggle](#set-action-dialog--beginner--regex-mode-toggle) | Set Action dialog |
| [Set Action dialog — live preview + validation](#set-action-dialog--live-preview--validation) | Set Action dialog |
| [Set Action dialog — numeric comparison panel](#set-action-dialog--numeric-comparison-panel) | Set Action dialog |
| [Set Action dialog — Score / Lock / Resolution fields](#set-action-dialog--score--lock--resolution-fields) | Set Action dialog |
| [Similarity column](#similarity-column) | Review |

---

### Bulk regex — remove from list (deferred decision)

- **Entry point:** Set Action dialog's action dropdown — `settable_decisions(include_remove=True)` at [app/views/constants.py:97](../app/views/constants.py#L97).
- **Trigger:** In the Set Action dialog (opened from main window menu or right-click), pick "remove from list" as the action and Apply.
- **Behaviour:** Matched rows get `user_decision='remove_from_list'` set (a third decision value alongside `delete` and `keep`), displayed in the Action column via a localised label. Files are not moved or deleted — rows are reviewed in Execute Action like delete/keep decisions and the actual removal (flag as removed in the manifest, drop from the view) happens at execute time. Single-row right-click in the Execute Action dialog stays IMMEDIATE with its own confirm — that path is set + execute on one click, which is intentionally distinct from the bulk deferred path.
- **Conditions / variants:** The `remove from list` entry appears in the dropdown only when `include_remove=True` is passed (currently the regex dialog and the Execute Action dialog's right-click submenu). The main-window right-click submenu omits it because it already has a top-level **List > Remove from List** item.
- **Related:** [PR #158](https://github.com/jackal998/photo-manager/pull/158); QA scenario [`qa/scenarios/s29_remove_from_list_by_regex.py`](../qa/scenarios/s29_remove_from_list_by_regex.py); related multi-select flow [`qa/scenarios/s20_multi_remove_from_list.py`](../qa/scenarios/s20_multi_remove_from_list.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Context menu — Lock / Unlock

- **Entry point:** Right-click submenu on file rows in the main result tree — `app/views/handlers/context_menu.py`. Also surfaces in the Execute Action dialog's right-click menu and as Lock/Unlock sentinels in the regex dialog action dropdown (`include_lock=True`).
- **Trigger:** User right-clicks a file row (single or multi-select) and picks **Lock** or **Unlock**.
- **Behaviour:** Flips the orthogonal `is_locked` flag on each selected row's manifest record. Locked rows display the 🔒 prefix in the Lock column and freeze their `user_decision` against bulk-regex changes and against execute-time deletion (the lock-confirm dialog gates any attempted change — see [Execute Action — lock-confirm dialog](#execute-action--lock-confirm-dialog)).
- **Conditions / variants:** Lock and Unlock are always idempotent — they never surface the lock-confirm dialog themselves. They are the escape valve for the freeze semantic.
- **Related:** [PR #175](https://github.com/jackal998/photo-manager/pull/175) (closes [#164](https://github.com/jackal998/photo-manager/issues/164)); QA scenario [`qa/scenarios/s35_lock_via_context_menu.py`](../qa/scenarios/s35_lock_via_context_menu.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Context menu — Open Folder

- **Entry point:** Right-click submenu on file rows in the main result tree — shared `app/views/handlers/file_opener.py` (extracted from `context_menu.py` in [PR #198](https://github.com/jackal998/photo-manager/pull/198)).
- **Trigger:** User right-clicks a file row and picks **Open Folder**.
- **Behaviour:** Opens the file's containing directory in the OS file manager with the file pre-selected (on Windows, `explorer /select,<path>`; on other platforms, falls back to `QDesktopServices.openUrl` on the parent directory).
- **Conditions / variants:** The same OS-aware impl is reused by the file-row double-click handler (see [Main window — results tree double-click](#main-window--results-tree-double-click)) — one canonical Open Folder cascade rather than two divergent copies.
- **Related:** Originally part of the [PR #19](https://github.com/jackal998/photo-manager/pull/19) context-menu redesign; extracted into a shared helper in [PR #198](https://github.com/jackal998/photo-manager/pull/198). QA scenario [`qa/scenarios/s19_context_menu_open_folder.py`](../qa/scenarios/s19_context_menu_open_folder.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Context menu — Set Action (per-file)

- **Entry point:** Right-click submenu on file rows in the main result tree — [app/views/handlers/context_menu.py](../app/views/handlers/context_menu.py).
- **Trigger:** User right-clicks a file row (single or multi-select) and picks **Set Action > delete / keep / remove from list**.
- **Behaviour:** Sets `user_decision` on each selected row to the chosen value. Multi-select applies the same decision to every selected row in one batch. For multi-select with locked rows in the set, the lock-confirm dialog gates the write (see [Execute Action — lock-confirm dialog](#execute-action--lock-confirm-dialog)).
- **Conditions / variants:** Single-row right-click also offers **Set Action by Field/Regex…** (multi-select got that entry too in [PR #162](https://github.com/jackal998/photo-manager/pull/162) — parity with single-select). The "remove from list" entry behaves differently per context: from the main-window submenu it's the same deferred decision as the bulk path; from the Execute Action dialog's single-row right-click it's IMMEDIATE (set + execute on one click).
- **Related:** Foundation in [PR #19](https://github.com/jackal998/photo-manager/pull/19); QA scenario [`qa/scenarios/s15_context_menu.py`](../qa/scenarios/s15_context_menu.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Empty area context menu

- **Entry point:** Right-click on the empty area of the main result tree (below the loaded rows).
- **Trigger:** User right-clicks a non-row area of the tree.
- **Behaviour:** Surfaces a context menu with global tree-actions (rather than the per-row Set Action menu). Distinct from the per-row menu so the user always gets the relevant actions for what they actually clicked.
- **Conditions / variants:** Available regardless of whether any rows are selected.
- **Related:** QA scenario [`qa/scenarios/s25_empty_area_context_menu.py`](../qa/scenarios/s25_empty_area_context_menu.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Empty-state action buttons

- **Entry point:** Two `QPushButton` primary actions next to the first-run hint label in the main window — [app/views/components/empty_state.py](../app/views/components/empty_state.py).
- **Trigger:** Pre-manifest state — visible on every launch where no manifest has been loaded yet.
- **Behaviour:** **Scan Sources…** opens the Scan dialog (same end-state as **File > Scan Sources…**); **Open Manifest…** opens the native Open Manifest file picker (same end-state as **File > Open Manifest…**). Button labels are pulled from the same translation keys as the matching menu items, so they stay in sync across locales.
- **Conditions / variants:** The whole label-plus-buttons widget hides atomically once `refresh_tree` sees a loaded manifest. Preserves the pre-#137 contract that the empty state vanishes the moment data lands.
- **Related:** [PR #197](https://github.com/jackal998/photo-manager/pull/197) (closes [#137](https://github.com/jackal998/photo-manager/issues/137)); QA scenario [`qa/scenarios/s41_empty_state_action_buttons.py`](../qa/scenarios/s41_empty_state_action_buttons.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Execute Action — base flow

- **Entry point:** Main window menu → "Execute Action…" — [app/views/main_window.py:584](../app/views/main_window.py#L584) → [app/views/handlers/file_operations.py:877](../app/views/handlers/file_operations.py#L877)
- **Trigger:** User clicks "Execute Action…" from the menu (label defined at [translations/en.yml:32](../translations/en.yml#L32)).
- **Behaviour:** Opens the `ExecuteActionDialog` (a modal review window) listing every record with a non-empty `user_decision`, grouped by duplicate-set. The user reviews the planned actions and clicks **Execute** to apply them or **Close** to dismiss without applying. Execute carries out deletes (via `delete_service`) and writes the manifest changes; Close discards no decisions — they remain queued until the next open.
- **Conditions / variants:** Execute button is disabled when no rows have a `user_decision`. Several layered behaviours modify the flow — see the other Execute Action entries below.
- **Related:** Dialog at [app/views/dialogs/execute_action_dialog.py:55](../app/views/dialogs/execute_action_dialog.py#L55); QA scenario [`qa/scenarios/s13_execute_action.py`](../qa/scenarios/s13_execute_action.py)
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Execute Action — complete-group delete confirm

- **Entry point:** Final confirmation modal inside the Execute Action flow — [app/views/dialogs/execute_action_dialog.py:138](../app/views/dialogs/execute_action_dialog.py#L138) (`_complete_delete_groups`) drives the `QMessageBox.question` shown by `_on_execute`.
- **Trigger:** User clicks **Execute** while at least one group has `user_decision='delete'` on every member (i.e. the whole group would be deleted).
- **Behaviour:** A single `QMessageBox.question` ("ALL files in group N will be deleted — proceed?") appears before any delete fires. **Yes** continues to the actual `delete_service` call; **No** aborts and the dialog stays open with decisions intact.
- **Conditions / variants:** Only fires when at least one group's entire delete set is in scope. When the highlighted-row scope (see below) covers only part of a group's delete decisions, the confirm is suppressed for that group because the "ALL files" copy would no longer be accurate. Partial-group deletes never trigger this confirm.
- **Related:** [PR #30](https://github.com/jackal998/photo-manager/pull/30) collapsed an earlier two-step confirm into the single `QMessageBox`; complete-group scoping for highlighted rows shipped in [PR #219](https://github.com/jackal998/photo-manager/pull/219) via `_complete_delete_groups_in_scope` at [execute_action_dialog.py:869](../app/views/dialogs/execute_action_dialog.py#L869).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Execute Action — complete-group warning banner with jump-to

- **Entry point:** Amber warning banner inside the Execute Action dialog — [app/views/dialogs/execute_action_dialog.py:263](../app/views/dialogs/execute_action_dialog.py#L263) (`_refresh_warning_banner`).
- **Trigger:** Banner appears whenever `_complete_delete_groups()` returns one or more group numbers — i.e. at least one group has `user_decision='delete'` on every row. Refreshes on every decision change.
- **Behaviour:** Renders "⚠ Group(s) N, M will have ALL files deleted…" with each group number as a clickable HTML anchor. Clicking a group number scrolls the dialog's tree to that group and selects its row (via `_on_jump_to_group` reusing the `SORT_ROLE`-keyed `group_number` + the `MainWindow._reselect_by_path` `scrollTo` + `selectionModel.select` pattern).
- **Conditions / variants:** Anchor `href` values that aren't integers or that don't resolve to a known group are no-ops (silent — no error dialog).
- **Related:** [PR #181](https://github.com/jackal998/photo-manager/pull/181) (closes [#166](https://github.com/jackal998/photo-manager/issues/166)); QA scenario [`qa/scenarios/s33_execute_dialog_jump_to_all_delete.py`](../qa/scenarios/s33_execute_dialog_jump_to_all_delete.py)
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Execute Action — dialog geometry persistence

- **Entry point:** `done(result)` override on `ExecuteActionDialog`, plus the `restore_geometry` call at the end of `__init__`. Shared helper lives at [app/views/window_state.py](../app/views/window_state.py).
- **Trigger:** Every dismissal of the dialog (Execute, Close, or X-button) funnels through `done()`, which calls `save_geometry`. The next `__init__` call restores the saved rect on top of the hardcoded `setMinimumSize` default.
- **Behaviour:** User-resized dialog reopens at the same size within the session and across app restarts (state stored via `QSettings` under the path centralised in [window_state.py](../app/views/window_state.py)). The splitter divider between tree and preview persists separately via `save_splitter_state` / `restore_splitter_state` (Qt's `saveState` bytes are distinct from `saveGeometry`).
- **Conditions / variants:** If the saved rect would land off-screen (e.g. multi-monitor disconnect — <25% of the rect visible on any connected screen), the helper falls back to widget defaults rather than reopening on a disconnected monitor. Same behaviour applies to `ScanDialog` and `ActionDialog`.
- **Related:** Geometry — [PR #228](https://github.com/jackal998/photo-manager/pull/228) (closes [#215](https://github.com/jackal998/photo-manager/issues/215)), QA scenario [`qa/scenarios/s48_dialog_geometry_persist.py`](../qa/scenarios/s48_dialog_geometry_persist.py). Splitter persistence — [PR #260](https://github.com/jackal998/photo-manager/pull/260).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Execute Action — lock-confirm dialog

- **Entry point:** `LockedRowsConfirmDialog` at [app/views/dialogs/locked_rows_confirm_dialog.py](../app/views/dialogs/locked_rows_confirm_dialog.py). Wired at four trigger points: `file_operations.set_decision_with_lock_check`, `execute_action_dialog._set_decision`, `execute_action_dialog._set_decision_by_regex`, and `execute_action_dialog._on_execute_requested`.
- **Trigger:** Any path that would change a locked row's `user_decision` OR execute a delete on a locked row surfaces this confirm dialog before acting. Includes bulk regex from the main window, bulk regex inside the Execute Action dialog, single-row right-click on a locked row, and the pre-execute scan that catches rows locked AFTER their decision was set.
- **Behaviour:** Three-button modal — **Unlock & Apply to All** unlocks the affected rows and applies the action; **Apply to Unlocked Only** runs the action only on the rows that weren't locked (disabled when every affected row is locked — the degenerate case); **Cancel** aborts with no changes.
- **Conditions / variants:** Lock / Unlock toggles themselves never surface this dialog (they're always-allowed). The `delete_service.plan_delete` lock filter at [infrastructure/delete_service.py](../infrastructure/delete_service.py) was retired in favour of a defensive assertion — callers are now responsible for routing through this confirm first.
- **Related:** [PR #183](https://github.com/jackal998/photo-manager/pull/183) (closes [#182](https://github.com/jackal998/photo-manager/issues/182), supersedes the [PR #175](https://github.com/jackal998/photo-manager/pull/175) hybrid lock semantic); QA scenarios [`qa/scenarios/s32_lock_confirm_bulk_regex.py`](../qa/scenarios/s32_lock_confirm_bulk_regex.py), [`qa/scenarios/s34_lock_confirm_at_execute.py`](../qa/scenarios/s34_lock_confirm_at_execute.py), [`qa/scenarios/s36_lock_confirm_destructive_execute.py`](../qa/scenarios/s36_lock_confirm_destructive_execute.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Execute Action — preview pane

- **Entry point:** Embedded `PreviewPane` (same class as the main window) inside `ExecuteActionDialog`, mounted via a horizontal `QSplitter` — [app/views/dialogs/execute_action_dialog.py:176](../app/views/dialogs/execute_action_dialog.py#L176).
- **Trigger:** Pane is present whenever a `task_runner` is threaded through the dialog constructor (the production path from [`file_operations.py:888`](../app/views/handlers/file_operations.py#L888)). Selecting a single row in the dialog's tree drives `PreviewPane.show_single(path, info)`; multi-select or empty-select calls `clear`.
- **Behaviour:** Lets the user see what each row's file looks like before confirming destructive actions, reusing the same `PreviewPane` + `ImageTaskRunner` instance as the main window (no second runner spun up). Splitter divider position persists per dialog across opens — see geometry feature above.
- **Conditions / variants:** When `task_runner=None` (test/legacy path) the dialog falls back to the pre-#165 single-column layout — no splitter, no preview. The `info` dict passed to `show_single` is minimal (`name` + `folder`); richer metadata (size / shot date) is deferred.
- **Related:** [PR #260](https://github.com/jackal998/photo-manager/pull/260) (closes [#165](https://github.com/jackal998/photo-manager/issues/165)); QA scenario [`qa/scenarios/s51_execute_dialog_preview.py`](../qa/scenarios/s51_execute_dialog_preview.py). Failure-bucket split ([#68](https://github.com/jackal998/photo-manager/issues/68)) was deliberately deferred.
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Execute Action — scope to highlighted rows

- **Entry point:** Tree's `selectionChanged` signal in `ExecuteActionDialog` — [app/views/dialogs/execute_action_dialog.py:278](../app/views/dialogs/execute_action_dialog.py#L278) (`_selected_file_paths`, `_on_selection_changed`, scoped `_on_execute_requested`).
- **Trigger:** User highlights one or more file rows in the dialog's tree (multi-row via `ExtendedSelection` mode, matching the main result tree at [tree_controller.py:45](../app/views/handlers/tree_controller.py#L45)). With an empty selection, falls back to "execute every decided row".
- **Behaviour:** Execute button label tracks the selection — `Execute` ↔ `Execute Action (highlighted)` — and clicking it processes ONLY the highlighted rows' decisions. Empty selection preserves the pre-#211 "execute every decided row" semantics. Lock guard narrows with scope: locked rows OUTSIDE the highlight don't fire `LockedRowsConfirmDialog`; locked rows INSIDE the highlight still do (scope narrows, never skips).
- **Conditions / variants:** Complete-group "ALL files will be deleted" confirm only fires when the highlighted scope fully covers a group's delete-decision rows. Partial selections suppress that confirm so the "EVERY file deleted" copy stays accurate. The selection listener must be re-wired on every `_rebuild_tree_model` because `QTreeView.setModel` installs a fresh `QItemSelectionModel`.
- **Related:** [PR #219](https://github.com/jackal998/photo-manager/pull/219) (closes [#211](https://github.com/jackal998/photo-manager/issues/211)); QA scenario [`qa/scenarios/s44_execute_highlighted_rows.py`](../qa/scenarios/s44_execute_highlighted_rows.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Exit dirty-flag prompt

- **Entry point:** `MainWindow.closeEvent` reads `FileOperationsHandler._is_dirty`.
- **Trigger:** User closes the app (X button, Alt+F4, File > Exit) after making decision changes that haven't been explicitly saved via Save Manifest Decisions.
- **Behaviour:** A 3-button `QMessageBox` appears — **Save & leave** silently saves to the loaded manifest path then exits; **Leave** exits without an additional save; **Back** stays in the app (the default, so accidental Esc/Enter keeps the user in place). Decisions auto-persist to the loaded manifest as soon as they're set, so **Leave** never loses data — the prompt is purely about offering an explicit save (e.g. before a Save-As to another path).
- **Conditions / variants:** Dirty flag flips on `set_decision`, `remove_items_from_list`, and `remove_from_list_toolbar`. It clears on manifest load, save, silent save, and successful execute — so a fresh manifest with no changes never triggers the prompt.
- **Related:** [PR #158](https://github.com/jackal998/photo-manager/pull/158); QA scenario [`qa/scenarios/s28_exit_dirty_prompt.py`](../qa/scenarios/s28_exit_dirty_prompt.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Keep-worthiness scoring

- **Entry point:** Score column (COL_SCORE at index 2) in the main result tree — [app/views/constants.py:22](../app/views/constants.py#L22). Within-group rows sort by score descending so the best copy lands at the top of every group.
- **Trigger:** Every scanned file gets a score automatically — no user action needed. The score lands in the manifest at scan time. Re-scoring without re-scanning is available via `ManifestRepository.rescore(weights)`.
- **Behaviour:** Composite score in `[0.0, 1.0]` measuring how "keep-worthy" each file is, computed as a pure function of file attributes (no user-intent signals). Two-tier algorithm: tier 1 absolute penalties (format, `xmpMM:DerivedFrom`); tier 2 weighted composite of eight continuous signals (resolution, EXIF completeness, date provenance, filename, GPS, path, Live Photo, file size). Live Photo MOV passengers get `score = NULL` and are skipped by ranking — they inherit the paired HEIC's decision.
- **Conditions / variants:** The previous "Apply best-copy decisions to this group" right-click action was removed in [PR #224](https://github.com/jackal998/photo-manager/pull/224) (closes [#210](https://github.com/jackal998/photo-manager/issues/210)) because it was superseded by the regex dialog's "top 1 by score within group" numeric condition (see [Set Action dialog — numeric comparison panel](#set-action-dialog--numeric-comparison-panel)). Auto-select after scan (see [Scan dialog — auto-select after scan](#scan-dialog--auto-select-after-scan)) is the third surface that consumes scoring.
- **Related:** Cluster originated in [#187](https://github.com/jackal998/photo-manager/issues/187): [PR #199](https://github.com/jackal998/photo-manager/pull/199), [#200](https://github.com/jackal998/photo-manager/pull/200), [#202](https://github.com/jackal998/photo-manager/pull/202), [#203](https://github.com/jackal998/photo-manager/pull/203), [#204](https://github.com/jackal998/photo-manager/pull/204), [#205](https://github.com/jackal998/photo-manager/pull/205), [#206](https://github.com/jackal998/photo-manager/pull/206); QA scenario [`qa/scenarios/s42_scoring.py`](../qa/scenarios/s42_scoring.py). Algorithm details in [README.md § Keep-worthiness scoring](../README.md#keep-worthiness-scoring-187).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Language switch

- **Entry point:** Main window menu → **View > Language** submenu — built by `menu_controller.py` via `QActionGroup(exclusive=True)`.
- **Trigger:** User picks a locale from the Language submenu.
- **Behaviour:** A Yes/No confirm prompt appears. On Yes the `MainWindow` rebuilds in place via the same factory used at startup — no app restart needed. State preserved best-effort: window geometry, splitter sizes, selected row's path. The chosen locale persists to `settings.json` under `ui.locale`.
- **Conditions / variants:** Available locales are discovered from `translations/<code>.yml` files. Each new YAML file appearing alongside `en.yml` shows up automatically in the picker on the next launch (no enum to update). Adding a new locale: copy `en.yml` → `<code>.yml`, translate values, restart once. Picking the already-active locale is a no-op (no confirm fires).
- **Related:** [PR #157](https://github.com/jackal998/photo-manager/pull/157); QA scenario [`qa/scenarios/s22_language_switch.py`](../qa/scenarios/s22_language_switch.py); translator workflow in [`docs/i18n.md`](i18n.md).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### List menu — Remove from List

- **Entry point:** Main window menu → **List > Remove from List** — handler at `MainWindow._remove_from_list_toolbar`.
- **Trigger:** User selects one or more rows in the main tree and picks **List > Remove from List**.
- **Behaviour:** Drops the selected rows from the in-memory view and queues them for removal from the manifest on save. Flips the dirty flag (see [Exit dirty-flag prompt](#exit-dirty-flag-prompt)). This is the immediate "drop from view" path — distinct from the bulk regex deferred decision (see [Bulk regex — remove from list (deferred decision)](#bulk-regex--remove-from-list-deferred-decision)).
- **Conditions / variants:** Works with single or multi-select. Lock-aware via the standard `set_decision_with_lock_check` route (locked rows trigger the lock-confirm dialog).
- **Related:** Originally [PR #158](https://github.com/jackal998/photo-manager/pull/158); QA scenarios [`qa/scenarios/s20_multi_remove_from_list.py`](../qa/scenarios/s20_multi_remove_from_list.py), [`qa/scenarios/s21_list_menu_remove.py`](../qa/scenarios/s21_list_menu_remove.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Log menu

- **Entry point:** Main window menu → **Log** — labels in [translations/en.yml:36-41](../translations/en.yml#L36).
- **Trigger:** User picks one of: **Open Latest Log**, **Open Latest Delete Log**, **Open Log Directory**, **Open Delete Log Directory**.
- **Behaviour:** Opens the corresponding log file or directory in the OS default application / file manager. "Latest log" resolves to the most recently rotated `loguru` log file; "delete log" resolves to the audit CSV that `delete_service` writes on every Execute Action run.
- **Conditions / variants:** Log directory path comes from `infrastructure/logging.py` configuration. If no log file has been written yet (first run before any logging fires) the "Open Latest" entries open the directory instead.
- **Related:** QA scenario [`qa/scenarios/s18_log_menu.py`](../qa/scenarios/s18_log_menu.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Main window — column order/width persistence

- **Entry point:** Tree header drag/resize signals in `MainWindow`, persisting to `QSettings` via the helpers in [app/views/window_state.py](../app/views/window_state.py).
- **Trigger:** User drags a column header to reorder or resizes a column boundary.
- **Behaviour:** Saves the new column order and widths on every drag/resize signal — not only at `closeEvent` — so the layout survives force-quits and OS-level kills, not just clean exits. Re-applies on launch.
- **Conditions / variants:** Persisted alongside main-window geometry under `PHOTO_MANAGER_HOME` (when set) so QA scenarios and dev runs stay isolated from any installed-app state.
- **Related:** [PR #227](https://github.com/jackal998/photo-manager/pull/227) (closes [#214](https://github.com/jackal998/photo-manager/issues/214)); QA scenario [`qa/scenarios/s47_column_layout_persist.py`](../qa/scenarios/s47_column_layout_persist.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Main window — geometry + splitter persistence

- **Entry point:** `MainWindow.closeEvent` saves geometry; the matching restore runs in `__init__`. Shared helpers in [app/views/window_state.py](../app/views/window_state.py).
- **Trigger:** Window position, size, maximize state, and splitter ratio round-trip on every clean exit and restore on the next launch.
- **Behaviour:** Geometry stored under QSettings key `geometry/main_window` (`saveGeometry()` bytes); splitter state under `geometry/main_splitter` (`saveState()` bytes). Stored under `PHOTO_MANAGER_HOME` when set; otherwise under repo root in `window_state.ini`. The splitter also enforces a 200 px floor on each pane and disables collapse, so the preview pane can no longer be squeezed to invisibility ([#136](https://github.com/jackal998/photo-manager/issues/136)).
- **Conditions / variants:** Position tolerance on round-trip is ~50–60 px on Win10 due to DWM's invisible-frame extension and high-DPI rcNormalPosition rounding — the contract is "reopens where it was," not pixel-perfect. Off-screen guard (rect <25% visible on any connected screen) falls back to widget defaults.
- **Related:** [PR #191](https://github.com/jackal998/photo-manager/pull/191) (closes [#141](https://github.com/jackal998/photo-manager/issues/141), [#136](https://github.com/jackal998/photo-manager/issues/136)); QA scenario [`qa/scenarios/s39_window_geometry_persist.py`](../qa/scenarios/s39_window_geometry_persist.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Main window — keyboard navigation

- **Entry point:** Main result tree's built-in `QTreeView` keyboard handling, augmented in [tree_controller.py](../app/views/handlers/tree_controller.py).
- **Trigger:** User presses arrow keys / Home / End / Page Up / Page Down with the tree focused.
- **Behaviour:** Navigate rows with arrow keys; expand/collapse groups with Left/Right at group-header rows. Selected row is preserved across model rebuilds (e.g. after a decision change) so keyboard-driven review doesn't lose place.
- **Conditions / variants:** Multi-select works with Shift+arrow and Ctrl+click as in the standard Qt tree behaviour. The selection model is preserved across `setModel` calls so the highlighted row survives a tree refresh.
- **Related:** QA scenario [`qa/scenarios/s26_keyboard_navigation.py`](../qa/scenarios/s26_keyboard_navigation.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Main window — results tree double-click

- **Entry point:** `TreeController` double-click dispatcher — [tree_controller.py](../app/views/handlers/tree_controller.py) (dispatcher added in [PR #198](https://github.com/jackal998/photo-manager/pull/198)).
- **Trigger:** User double-clicks a row in the main result tree.
- **Behaviour:** **File row** → opens the file in the OS default viewer (`QDesktopServices.openUrl`). **Group header row** → toggles expand/collapse for that group. Qt's built-in `setExpandsOnDoubleClick` is disabled so the toggle path doesn't race the default expansion behaviour.
- **Conditions / variants:** The OS-spawn branch for files is layer-1 covered only — spawning a real viewer has no deterministic close-trigger across image apps. The Open Folder cascade is shared with the context menu via [app/views/handlers/file_opener.py](../app/views/handlers/file_opener.py).
- **Related:** [PR #198](https://github.com/jackal998/photo-manager/pull/198) (closes [#143](https://github.com/jackal998/photo-manager/issues/143)); QA scenario [`qa/scenarios/s40_results_tree_double_click.py`](../qa/scenarios/s40_results_tree_double_click.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Main window — sort persistence within session

- **Entry point:** Header-click handler — `MainWindow._on_header_clicked` ([app/views/main_window.py:806](../app/views/main_window.py#L806)) stashes `(logical_index, order)` on the `TreeController`. `TreeController.refresh_model` ([tree_controller.py:225](../app/views/handlers/tree_controller.py#L225)) replays the stashed state on every model rebuild.
- **Trigger:** User clicks a column header to change sort field/direction.
- **Behaviour:** Within the session, the chosen sort survives every model rebuild — a File → Open Manifest, a decision change, an execute run — without reverting to defaults. Within-group rows always sort by score descending first (the keep-worthiness ranking), with the user's column sort layered on top.
- **Conditions / variants:** The across-launch surface (writing the sort state to `window_state.ini` so a fresh process restores it) is **not** implemented today — sort resets on app restart. Tracked separately from the within-session persistence.
- **Related:** [#121](https://github.com/jackal998/photo-manager/issues/121); QA scenario [`qa/scenarios/s45_sort_persistence.py`](../qa/scenarios/s45_sort_persistence.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Main window — status bar baseline

- **Entry point:** Persistent `QLabel` attached to `QStatusBar` via `addWidget` in `MainWindow`.
- **Trigger:** Always present — never expires. Temporary action toasts (`showMessage(text, timeout)`) layer on top.
- **Behaviour:** The baseline label always shows a resting message ("Ready" on startup, "Loaded manifest: <parts>" after a load). Qt's hide-during-temp / show-after-clear semantics fall back to the label so the bar never goes blank after a transient message expires or after a menu hover clears the bar.
- **Conditions / variants:** Pre-#138, startup `status_ready` was shown via `showMessage(text, 3000)` and the bar went blank after 3s. Pre-#140, opening any menu cleared the load-summary text permanently because Qt's `QAction` hover path calls `statusBar().showMessage(action.statusTip())` even when the tip is empty. The baseline label fixes both.
- **Related:** [#138](https://github.com/jackal998/photo-manager/issues/138), [#140](https://github.com/jackal998/photo-manager/issues/140); QA scenario [`qa/scenarios/s37_status_bar_baseline.py`](../qa/scenarios/s37_status_bar_baseline.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Open Manifest — base flow

- **Entry point:** Main window menu → **File > Open Manifest…** ([translations/en.yml:26](../translations/en.yml#L26)). Also reachable from the empty-state primary button (see [Empty-state action buttons](#empty-state-action-buttons)).
- **Trigger:** User picks **File > Open Manifest…** or clicks the empty-state **Open Manifest…** button.
- **Behaviour:** Opens the native file picker filtered to `*.sqlite`. On accept, loads the chosen manifest via `ManifestLoadWorker` (a background `QThread` so the UI stays responsive), then refreshes the tree. Status bar updates to "Loaded manifest: <name>".
- **Conditions / variants:** When the currently loaded manifest has unsaved decisions, a "Discard pending decisions?" confirm fires before the new manifest replaces it. Old manifests without the cached columns (`file_size_bytes`, `shot_date`, `creation_date`, `mtime`) auto-migrate and fall back to per-row filesystem reads transparently — re-scan once for the load-time speed benefit.
- **Related:** Foundation in [PR #12](https://github.com/jackal998/photo-manager/pull/12); QA scenario [`qa/scenarios/s16_open_manifest.py`](../qa/scenarios/s16_open_manifest.py); stale-path handling exercised in [`qa/scenarios/s24_stale_manifest_paths.py`](../qa/scenarios/s24_stale_manifest_paths.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Save Manifest Decisions — base flow

- **Entry point:** Main window menu → **File > Save Manifest Decisions…** ([translations/en.yml:27](../translations/en.yml#L27)).
- **Trigger:** User picks **File > Save Manifest Decisions…**.
- **Behaviour:** Opens a file picker. Choosing the same path saves in-place; choosing a new path exports a copy. Decisions are written to the chosen file, and subsequent saves default to that location. Clears the dirty flag on success (see [Exit dirty-flag prompt](#exit-dirty-flag-prompt)).
- **Conditions / variants:** Decisions also auto-persist to the loaded manifest as soon as they're set — Save Manifest Decisions is the explicit "save to a different path" / "snapshot" affordance, not the only persistence path. A silent variant (`save_manifest_decisions_silent`) writes to the loaded manifest path with no picker — used by the exit prompt's **Save & leave** branch.
- **Related:** Dirty-flag plumbing in [PR #158](https://github.com/jackal998/photo-manager/pull/158); QA scenario [`qa/scenarios/s12_save_manifest.py`](../qa/scenarios/s12_save_manifest.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Scan dialog — auto-select after scan

- **Entry point:** "Auto select after scan" checkbox under Advanced Settings in [app/views/dialogs/scan_dialog.py](../app/views/dialogs/scan_dialog.py).
- **Trigger:** User expands **Advanced settings** in the Scan dialog and ticks **Auto select after scan**. Setting persists across sessions via `ui.scan_dialog.auto_select_enabled` (defaults `False`).
- **Behaviour:** When enabled, the scan worker promotes the top-scored row in each duplicate group to `action="KEEP"` before writing the manifest. The manifest loads with keepers already chosen and the user does not have to open the Selection dialog manually. Other duplicates retain their classifier action (`MOVE` / `EXACT` / `REVIEW_DUPLICATE`) so deletions still require explicit user confirmation through the review workflow. Auto-select picks keepers, never deleters.
- **Conditions / variants:** Default is off — pre-#212 behaviour is preserved for users who don't opt in. Ranking semantics match the regex dialog's "Top 1 by score" rule (see [Set Action dialog — numeric comparison panel](#set-action-dialog--numeric-comparison-panel)): `score=None` rows excluded, ties break by `source_path` ascending — so manual and auto runs converge on the same keeper. Pairs with the post-scan visual-selection feature (see [Scan flow — visual selection of KEEP rows after scan](#scan-flow--visual-selection-of-keep-rows-after-scan)).
- **Related:** [PR #232](https://github.com/jackal998/photo-manager/pull/232) (closes [#212](https://github.com/jackal998/photo-manager/issues/212)); QA scenario [`qa/scenarios/s49_scan_auto_select.py`](../qa/scenarios/s49_scan_auto_select.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Scan dialog — collapse Advanced Settings

- **Entry point:** **Advanced settings** collapsible panel in the Scan dialog — [app/views/dialogs/scan_dialog.py](../app/views/dialogs/scan_dialog.py).
- **Trigger:** User clicks the **Advanced settings** disclosure to expand or collapse the panel.
- **Behaviour:** Tech-detail settings (similarity threshold, mean-color threshold, grouping parameters, auto-select toggle) live under a single collapsible panel rather than cluttering the main scan UI. New users see only the source list and the **Start Scan** button by default; power users expand to tune.
- **Conditions / variants:** Expanded/collapsed state is not persisted today — opens collapsed every time.
- **Related:** [PR #179](https://github.com/jackal998/photo-manager/pull/179) collapsed grouping parameters; [#163](https://github.com/jackal998/photo-manager/issues/163) drove the original consolidation.
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Scan dialog — folder list (no priority arrows)

- **Entry point:** Source list widget in the Scan dialog (`_SourceListWidget`) — [app/views/dialogs/scan_dialog.py](../app/views/dialogs/scan_dialog.py).
- **Trigger:** Always — applies to every interaction with the source list.
- **Behaviour:** Clean 3-column list (path / Recursive checkbox / × remove). Display is sorted alphabetically by path (case-insensitive). The underlying entries list stays insertion-ordered so the duplicate-path check and the scanner's source-priority inference (top of scan = highest priority) still work.
- **Conditions / variants:** Replaces the pre-#213 5-column table that had ↑/↓ priority arrows. Per-row callbacks receive the entries-index (not the display row) so clicking row 0 after the alphabetical sort still targets the alphabetically-first entry. ⚠ The README's Step 1 wording still mentions the removed arrows — tracked in [#264](https://github.com/jackal998/photo-manager/issues/264).
- **Related:** [PR #223](https://github.com/jackal998/photo-manager/pull/223) (closes [#213](https://github.com/jackal998/photo-manager/issues/213)); QA scenario [`qa/scenarios/s17_scan_dialog_widgets.py`](../qa/scenarios/s17_scan_dialog_widgets.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Scan dialog — multi-source scan

- **Entry point:** Source list widget in the Scan dialog plus the **+ Add Selected Folder** button — [app/views/dialogs/scan_dialog.py](../app/views/dialogs/scan_dialog.py).
- **Trigger:** User browses the embedded folder tree, double-clicks a folder or clicks **+ Add Selected Folder** to add it to the source list. Repeats for unlimited folders. Toggles **Recursive** per source as needed.
- **Behaviour:** Walks every source folder, hashes every file, and writes one consolidated `migration_manifest.sqlite` covering the whole set. Recursive sources walk subdirectories; non-recursive scan only the immediate folder. Layout is two-column (folder tree on the left, source list also on the left below it — see [PR #160](https://github.com/jackal998/photo-manager/pull/160)).
- **Conditions / variants:** Source paths persist to `settings.json` (`sources.list`) between sessions. The Scan dialog accepts invalid / missing paths but surfaces a validation toast on Start Scan ([`qa/scenarios/s38_scan_dialog_invalid_path.py`](../qa/scenarios/s38_scan_dialog_invalid_path.py)).
- **Related:** [PR #17](https://github.com/jackal998/photo-manager/pull/17) (dynamic multi-source scan); [PR #160](https://github.com/jackal998/photo-manager/pull/160) (two-column layout); QA scenarios [`qa/scenarios/s10_multi_source.py`](../qa/scenarios/s10_multi_source.py), [`qa/scenarios/s17_scan_dialog_widgets.py`](../qa/scenarios/s17_scan_dialog_widgets.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Scan flow — rescan confirm

- **Entry point:** Confirm dialog fired before a re-scan replaces the currently loaded manifest.
- **Trigger:** User starts a scan while a manifest with pending decisions is already loaded.
- **Behaviour:** A confirm dialog asks the user to acknowledge that re-scanning will replace the loaded manifest. If the scan output path matches the loaded manifest path, those decisions will be permanently lost on disk; otherwise the previous manifest is preserved on disk but no longer visible in this window.
- **Conditions / variants:** Cancel keeps the loaded manifest intact and aborts the scan. Confirm proceeds with the scan.
- **Related:** QA scenario [`qa/scenarios/s27_rescan_confirm.py`](../qa/scenarios/s27_rescan_confirm.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Scan flow — visual selection of KEEP rows after scan

- **Entry point:** Post-scan tree-selection hook in `MainWindow` after the manifest loads via **Close & Load**.
- **Trigger:** Scan with auto-select enabled finishes and the user clicks **Close & Load**.
- **Behaviour:** The rows that auto-select marked `KEEP` are visually highlighted in the result tree so the user can see at a glance which keepers the scorer picked — eliminating the "what just happened?" moment after a silent auto-select.
- **Conditions / variants:** Only fires when auto-select after scan is enabled (see [Scan dialog — auto-select after scan](#scan-dialog--auto-select-after-scan)). Without auto-select, no rows are pre-marked, so nothing to highlight.
- **Related:** [PR #255](https://github.com/jackal998/photo-manager/pull/255) (fix for [#239](https://github.com/jackal998/photo-manager/issues/239)).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Set Action dialog — Beginner / Regex mode toggle

- **Entry point:** Radio toggle at the top of the Set Action dialog — [app/views/dialogs/select_dialog.py](../app/views/dialogs/select_dialog.py).
- **Trigger:** User opens **Action > Set Action by Field/Regex…** (or right-clicks a row → **Set Action by Field/Regex…**). Beginner is the default for new users.
- **Behaviour:** **Beginner** mode replaces the regex line edit with "Find rows where it [contains | starts with | ends with | exactly matches] [text]". The dialog synthesises the regex internally (via `re.escape` so the user's plain text stays literal — no need to know that `()/.` are special). **Regex** mode exposes the raw pattern input plus a cheatsheet chip row (`.*`, `\d`, `\w`, `^`, `$`, `\.`, `[abc]`) for power users. Recent patterns dropdown (capped at 10, deduped, persisted under `ui.action_dialog.recent_patterns`) is reachable from a `Recent ▾` button next to the regex input; picking from Recent always lands the user in Regex mode (the stored values are raw regex, not Beginner tuples).
- **Conditions / variants:** Mode persists in `settings.json` under `ui.action_dialog.mode` so power users who flip to Regex once stay there. The match counter sits in a dedicated row visible in both modes — toggling mode never hides the live count, which is the primary feedback for both inputs. Match-span highlighting in the preview emboldens the matched substring in each row regardless of mode.
- **Related:** [PR #167](https://github.com/jackal998/photo-manager/pull/167) (Phase B — Beginner mode, cheatsheet, recent patterns, match highlight); [PR #168](https://github.com/jackal998/photo-manager/pull/168) (Phase C — Simple rename + 3-col cheatsheet); QA scenario [`qa/scenarios/s31_simple_mode_regex.py`](../qa/scenarios/s31_simple_mode_regex.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Set Action dialog — live preview + validation

- **Entry point:** Right-side `QListWidget` + match counter + validation icon in the Set Action dialog — [app/views/dialogs/select_dialog.py](../app/views/dialogs/select_dialog.py). Match closure built by `build_match_fn` in [app/views/handlers/file_operations.py](../app/views/handlers/file_operations.py).
- **Trigger:** User types in the Beginner-mode text input or Regex-mode pattern input. Debounced 150 ms after the last keystroke.
- **Behaviour:** Preview shows up to 50 matched filenames with a "…and N more" footer; match counter shows "N of M match". Live validation surfaces a green ✓ / red ✗ icon and a friendly error label ("Invalid regex: unmatched ')' at position 7") the moment `re.compile` fails — no more silent failure on Apply. The closure short-circuits on invalid regex so the dialog never iterates the record set with a broken pattern. The same `build_match_fn` closure is shared by both Apply and preview so what you see is byte-for-byte what `set_decision_by_regex` will match.
- **Conditions / variants:** Right-click parity — the Execute Action dialog's tree context menu and the main window's multi-selection right-click both offer **Set Action by Field/Regex…**, opening the same dialog with the same live preview.
- **Related:** [PR #162](https://github.com/jackal998/photo-manager/pull/162) (Phase A — live preview, validation, right-click parity); QA scenarios [`qa/scenarios/s14_action_by_regex.py`](../qa/scenarios/s14_action_by_regex.py), [`qa/scenarios/s30_execute_dialog_regex_right_click.py`](../qa/scenarios/s30_execute_dialog_regex_right_click.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Set Action dialog — numeric comparison panel

- **Entry point:** Numeric panel that replaces the regex input when a numeric-capable field is chosen — [app/views/dialogs/select_dialog.py](../app/views/dialogs/select_dialog.py).
- **Trigger:** User opens the Set Action dialog **from the Execute Action dialog** and picks one of the numeric-capable fields: Size (Bytes), Group Count, Similarity, Score, Creation Date, Shot Date. The main-window standalone route doesn't pass `groups`, so the numeric panel never surfaces there — existing regex/simple behaviour is unchanged for the main-window entry.
- **Behaviour:** Two modes — **Threshold comparison** (`>`, `>=`, `<`, `<=`, `==`, `!=`) against a typed value (date fields accept ISO `YYYY-MM-DD`); **Top N / Bottom N within group** ranked by the selected field with stable `file_path` tiebreak so the same configuration always selects the same rows. Both modes ride through the existing `setActionRequested(field, pattern, decision)` signal as encoded pseudo-patterns (`__cmp__:OP:VALUE`, `__top_n__:N:asc|desc`).
- **Conditions / variants:** The "top 1 by score within group" configuration is the supported way to apply best-copy to a group — the standalone right-click "Apply best-copy decisions to this group" action was removed in [PR #224](https://github.com/jackal998/photo-manager/pull/224).
- **Related:** [PR #221](https://github.com/jackal998/photo-manager/pull/221) (closes [#209](https://github.com/jackal998/photo-manager/issues/209)); QA scenarios [`qa/scenarios/s43_numeric_condition.py`](../qa/scenarios/s43_numeric_condition.py), [`qa/scenarios/s50_select_numeric_panel_from_main_window.py`](../qa/scenarios/s50_select_numeric_panel_from_main_window.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Set Action dialog — Score / Lock / Resolution fields

- **Entry point:** Field dropdown in the Set Action dialog — [app/views/dialogs/select_dialog.py](../app/views/dialogs/select_dialog.py); field metadata in [app/views/constants.py](../app/views/constants.py).
- **Trigger:** User opens the Set Action dialog and expands the field combo.
- **Behaviour:** Score, Lock, and Resolution each appear as fields the user can match against. Score auto-opens the numeric comparison panel (it's in `_NUMERIC_FIELDS`). Lock is matched as a stringified flag ("Locked" / ""). Resolution is matched as `WIDTH×HEIGHT` (e.g. `^1920×1080$`) to mirror the tree's Resolution column rendering exactly.
- **Conditions / variants:** All three labels go through `t()` so they translate correctly. Without this addition, picking Score from the combo would have rendered untranslated; Lock would have been picker-visible but raw-English; Resolution wasn't there at all.
- **Related:** [PR #250](https://github.com/jackal998/photo-manager/pull/250) (closes [#238](https://github.com/jackal998/photo-manager/issues/238)); QA scenario [`qa/scenarios/s50_select_numeric_panel_from_main_window.py`](../qa/scenarios/s50_select_numeric_panel_from_main_window.py).
- **Last verified:** 2026-05-17 (PR for [#262](https://github.com/jackal998/photo-manager/issues/262))

---

### Similarity column

- **Entry point:** First column of the main result tree (COL_GROUP at index 0) — [app/views/tree_model_builder.py](../app/views/tree_model_builder.py). On the group header row it shows the localised "Group N" label; on each file row it shows one of: `Ref`, `100%`, an `N%` similarity, `—`, or `~dup`.
- **Trigger:** Populated automatically when a manifest is loaded; no user action.
- **Behaviour:** Per file row, the cell renders one of five values: (a) `Ref` — exactly one row per group, picked via the score-aware tie-break (highest score among Ref-tier action rows, lex name as final tiebreaker); (b) `100%` — `action='EXACT'` (SHA / format duplicate); (c) `N%` — `action='REVIEW_DUPLICATE'`, computed at render time as `round((64 - hamming) / 64 * 100)` where `hamming` is the pHash Hamming distance between the row's pHash and **the displayed Ref's pHash**, not the scanner's anchor's pHash; (d) `—` — Ref-tier sibling row (e.g. Live Photo MOV passenger sitting alongside the HEIC primary) that did not win the Ref pick; (e) `~dup` — fallback placeholder when neither pHash can be read.
- **Conditions / variants:** The render-time recomputation requires both the displayed Ref's pHash and the row's pHash to be populated. When either is missing (old manifests pre-phash column, video rows, or imagehash not installed), the cell falls back to the scanner's stored `hamming_distance` so old manifests degrade gracefully. The manifest's `hamming_distance` column is still written by the scanner but is no longer the source of truth for the rendered % when phashes are available — the rendered value is always relative to the row the user sees as `Ref`.
- **Related:** [#253](https://github.com/jackal998/photo-manager/issues/253) (render against displayed Ref); [#241](https://github.com/jackal998/photo-manager/issues/241) (score-aware Ref tie-break); QA scenarios [`qa/scenarios/s52_similarity_against_displayed_ref.py`](../qa/scenarios/s52_similarity_against_displayed_ref.py); helper module [`scanner/phash_distance.py`](../scanner/phash_distance.py).
- **Last verified:** 2026-05-19 (PR for [#253](https://github.com/jackal998/photo-manager/issues/253))

---

## How to update this file

When user-visible behaviour changes (button label, conditional dialog,
action scope, new keyboard shortcut, new menu item, post-action state
change, new condition gating a flow), add or update the corresponding
section here in the same PR.

The [`update-docs` skill](../.claude/skills/update-docs/SKILL.md)'s
"User-visible behaviour changed?" row is the trigger; the
[`docs_guard` hook](../scripts/hooks/docs_guard.py) enforces the touch
at `gh pr create` time for changes in `app/views/dialogs/` or
`app/views/handlers/`.

If a change genuinely doesn't shift user-visible behaviour, the bypass
token `[docs-not-needed: <reason>]` in the `gh pr create` body still
works — but the reason should be specific and reviewer-visible.

### Section schema

Each feature is one section using this template:

```markdown
### <Feature name>

- **Entry point:** <UI location> — `<file:line>`
- **Trigger:** <user action that activates it>
- **Behaviour:** <1-3 sentences on expected UX>
- **Conditions / variants:** <state that changes the behaviour>
- **Related:** PR #<N>, issue #<N>, qa scenario `sNN_<name>.py`
- **Last verified:** <PR # or YYYY-MM-DD>
```

Keep the index table at the top alphabetised by feature name.
