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
| [Execute Action — filter by action type](#execute-action--filter-by-action-type-502) | Execute Action |
| [Execute Action — partial execution via "Execute selected"](#execute-action--partial-execution-via-execute-selected) | Execute Action |
| [Execute Action — preview pane](#execute-action--preview-pane) | Execute Action |
| [Execute Action — scope to highlighted rows](#execute-action--scope-to-highlighted-rows) | Execute Action |
| [Exit dirty-flag prompt](#exit-dirty-flag-prompt) | Main window |
| [Keep-worthiness scoring](#keep-worthiness-scoring) | Review |
| [Language switch](#language-switch) | i18n |
| [List menu — Remove from List](#list-menu--remove-from-list) | Menus |
| [Log menu](#log-menu) | Menus |
| [Main window — close-during-scan confirm](#main-window--close-during-scan-confirm) | Main window |
| [Main window — column order/width persistence](#main-window--column-orderwidth-persistence) | Main window |
| [Main window — geometry + splitter persistence](#main-window--geometry--splitter-persistence) | Main window |
| [Main window — keyboard navigation](#main-window--keyboard-navigation) | Main window |
| [Main window — results tree double-click](#main-window--results-tree-double-click) | Main window |
| [Main window — row selection (no horizontal auto-scroll)](#main-window--row-selection-no-horizontal-auto-scroll) | Main window |
| [Main window — sort persistence within session](#main-window--sort-persistence-within-session) | Main window |
| [Main window — status bar baseline](#main-window--status-bar-baseline) | Main window |
| [Open Manifest — base flow](#open-manifest--base-flow) | File operations |
| [Save Manifest Decisions — base flow](#save-manifest-decisions--base-flow) | File operations |
| [Scan dialog — auto-select after scan](#scan-dialog--auto-select-after-scan) | Scan |
| [Scan dialog — auto-select aggressive ("delete all others")](#scan-dialog--auto-select-aggressive-delete-all-others) | Scan |
| [Scan dialog — collapse Advanced Settings](#scan-dialog--collapse-advanced-settings) | Scan |
| [Scan dialog — exiftool workers (setting-only)](#scan-dialog--exiftool-workers-setting-only) | Scan |
| [Scan dialog — folder list (no priority arrows)](#scan-dialog--folder-list-no-priority-arrows) | Scan |
| [Scan dialog — hash pool mode](#scan-dialog--hash-pool-mode) | Scan |
| [Scan dialog — hash workers (NAS-aware auto, no UI control)](#scan-dialog--hash-workers-nas-aware-auto-no-ui-control) | Scan |
| [Scan dialog — multi-source scan](#scan-dialog--multi-source-scan) | Scan |
| [Scan dialog — read-knee autotune opt-out](#scan-dialog--read-knee-autotune-opt-out) | Scan |
| [Scan flow — manifest summary in progress log](#scan-flow--manifest-summary-in-progress-log) | Scan |
| [Scan flow — rescan confirm](#scan-flow--rescan-confirm) | Scan |
| [Scan flow — visual selection of KEEP rows after scan](#scan-flow--visual-selection-of-keep-rows-after-scan) | Scan |
| [Scan walk — skip-on-error traversal](#scan-walk--skip-on-error-traversal) | Scan |
| [Set Action dialog — dual-section Simple + Regex view](#set-action-dialog--dual-section-simple--regex-view) | Set Action dialog |
| [Set Action dialog — live preview + validation](#set-action-dialog--live-preview--validation) | Set Action dialog |
| [Set Action dialog — numeric comparison panel](#set-action-dialog--numeric-comparison-panel) | Set Action dialog |
| [Set Action dialog — Score / Lock / Resolution fields](#set-action-dialog--score--lock--resolution-fields) | Set Action dialog |
| [Set Action dialog — geometry persistence](#set-action-dialog--geometry-persistence) | Set Action dialog |
| [Similarity column](#similarity-column) | Review |
| [Preview pane — byte-budget LRU cache](#preview-pane--byte-budget-lru-cache) | Preview pane |
| [Preview pane — full-resolution viewer](#preview-pane--full-resolution-viewer) | Preview pane |
| [Preview pane — no-autoplay video default](#preview-pane--no-autoplay-video-default) | Preview pane |

---

### Bulk regex — remove from list (deferred decision)

- **Entry point:** Set Action dialog's action dropdown — `settable_decisions(include_remove=True)` at [app/views/constants.py:97](../app/views/constants.py#L97).
- **Trigger:** In the Set Action dialog (opened from main window menu or right-click), pick "remove from list" as the action and Apply.
- **Behaviour:** Matched rows get `user_decision='ignore'` set (a third decision value alongside `delete` and `keep`; wire value is `IGNORE_DECISION='ignore'`), displayed in the Action column via a localised label. Files are not moved or deleted — rows are reviewed in Execute Action like delete/keep decisions and the actual removal (write `outcome='ignored'` to the manifest, drop from the view) happens at execute time. Single-row right-click in the Execute Action dialog stays IMMEDIATE with its own confirm — that path is set + execute on one click, which is intentionally distinct from the bulk deferred path. (#584 unification: visibility predicate changed from `executed=0` + Python filter to `WHERE outcome=''`.)
- **Conditions / variants:** The `remove from list` entry appears in the dropdown only when `include_remove=True` is passed (currently the regex dialog and the Execute Action dialog's right-click submenu). The main-window right-click submenu omits it because it already has a top-level **List > Remove from List** item.
- **Related:** [PR #158](https://github.com/jackal998/photo-manager/pull/158); QA scenario [`qa/scenarios/s29_remove_from_list_by_regex.py`](../qa/scenarios/s29_remove_from_list_by_regex.py); related multi-select flow [`qa/scenarios/s20_multi_remove_from_list.py`](../qa/scenarios/s20_multi_remove_from_list.py); single-row IMMEDIATE path inside the Execute Action dialog covered by [`qa/scenarios/s54_execute_dialog_remove_from_list.py`](../qa/scenarios/s54_execute_dialog_remove_from_list.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Context menu — Lock / Unlock

- **Entry point:** Right-click submenu on file rows in the main result tree — `app/views/handlers/context_menu.py`. Also surfaces in the Execute Action dialog's right-click menu and as Lock/Unlock sentinels in the regex dialog action dropdown (`include_lock=True`).
- **Trigger:** User right-clicks a file row (single or multi-select) and picks **Lock** or **Unlock**.
- **Behaviour:** Flips the orthogonal `is_locked` flag on each selected row's manifest record. Locked rows display the 🔒 prefix in the Lock column and freeze their `user_decision` against bulk-regex changes and against execute-time deletion (the lock-confirm dialog gates any attempted change — see [Execute Action — lock-confirm dialog](#execute-action--lock-confirm-dialog)).
- **Conditions / variants:** Lock and Unlock are always idempotent — they never surface the lock-confirm dialog themselves. They are the escape valve for the freeze semantic.
- **Related:** [PR #175](https://github.com/jackal998/photo-manager/pull/175) (closes [#164](https://github.com/jackal998/photo-manager/issues/164)); QA scenarios [`qa/scenarios/s35_lock_via_context_menu.py`](../qa/scenarios/s35_lock_via_context_menu.py) (main-window route) and [`qa/scenarios/s53_execute_dialog_lock_decision.py`](../qa/scenarios/s53_execute_dialog_lock_decision.py) (Execute Action dialog route).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Context menu — Open Folder

- **Entry point:** Right-click submenu on file rows in the main result tree — shared `app/views/handlers/file_opener.py` (extracted from `context_menu.py` in [PR #198](https://github.com/jackal998/photo-manager/pull/198)).
- **Trigger:** User right-clicks a file row and picks **Open Folder**.
- **Behaviour:** Opens the file's containing directory in the OS file manager with the file pre-selected (on Windows, `explorer /select,<path>`; on other platforms, falls back to `QDesktopServices.openUrl` on the parent directory).
- **Conditions / variants:** The same OS-aware impl is reused by the file-row double-click handler (see [Main window — results tree double-click](#main-window--results-tree-double-click)) — one canonical Open Folder cascade rather than two divergent copies.
- **Related:** Originally part of the [PR #19](https://github.com/jackal998/photo-manager/pull/19) context-menu redesign; extracted into a shared helper in [PR #198](https://github.com/jackal998/photo-manager/pull/198). QA scenario [`qa/scenarios/s19_context_menu_open_folder.py`](../qa/scenarios/s19_context_menu_open_folder.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Context menu — Set Action (per-file)

- **Entry point:** Right-click submenu on file rows in the main result tree — [app/views/handlers/context_menu.py](../app/views/handlers/context_menu.py).
- **Trigger:** User right-clicks a file row (single or multi-select) and picks **Set Action > delete / keep / remove from list**.
- **Behaviour:** Sets `user_decision` on each selected row to the chosen value. Multi-select applies the same decision to every selected row in one batch. For multi-select with locked rows in the set, the lock-confirm dialog gates the write (see [Execute Action — lock-confirm dialog](#execute-action--lock-confirm-dialog)). **Perf (#613):** single-row and multi-select right-click use incremental cell update (path index O(1) lookup + `TreeController.update_decision_cells`) instead of a full `QStandardItemModel` rebuild; the `set_decision_by_regex` path still does a full rebuild because group-level `SORT_ROLE` aggregates require re-reading all sibling rows. The `APPLY_ALL_UNLOCKED` verdict in the lock-confirm flow issues a single `batch_update_decisions_and_lock` DB transaction instead of two separate commits.
- **Conditions / variants:** Single-row right-click also offers **Set Action by Field…** (multi-select got that entry too in [PR #162](https://github.com/jackal998/photo-manager/pull/162) — parity with single-select). The "remove from list" entry behaves differently per context: from the main-window submenu it's the same deferred decision as the bulk path; from the Execute Action dialog's single-row right-click it's IMMEDIATE (set + execute on one click).
- **Related:** Foundation in [PR #19](https://github.com/jackal998/photo-manager/pull/19); QA scenarios [`qa/scenarios/s15_context_menu.py`](../qa/scenarios/s15_context_menu.py) (main-window route) and [`qa/scenarios/s53_execute_dialog_lock_decision.py`](../qa/scenarios/s53_execute_dialog_lock_decision.py) (Set Action → delete via Execute Action dialog's right-click, verified through the status-bar "Decision set" emit). Path-index invalidation and incremental-vs-full-rebuild boundary covered by `tests/test_file_operations.py::TestSetDecision`.
- **Last verified:** 2026-06-08 (#613 incremental refresh + path index)

---

### Empty area context menu

- **Entry point:** Right-click on the empty area of the main result tree (below the loaded rows).
- **Trigger:** User right-clicks a non-row area of the tree.
- **Behaviour:** Surfaces a context menu with global tree-actions (rather than the per-row Set Action menu). Distinct from the per-row menu so the user always gets the relevant actions for what they actually clicked.
- **Conditions / variants:** Available regardless of whether any rows are selected.
- **Related:** QA scenario [`qa/scenarios/s25_empty_area_context_menu.py`](../qa/scenarios/s25_empty_area_context_menu.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Empty-state action buttons

- **Entry point:** Two `QPushButton` primary actions next to the first-run hint label in the main window — [app/views/components/empty_state.py](../app/views/components/empty_state.py).
- **Trigger:** Pre-manifest state — visible on every launch where no manifest has been loaded yet.
- **Behaviour:** **Scan Sources…** opens the Scan dialog (same end-state as **File > Scan Sources…**); **Open Manifest…** opens the native Open Manifest file picker (same end-state as **File > Open Manifest…**). Button labels are pulled from the same translation keys as the matching menu items, so they stay in sync across locales.
- **Conditions / variants:** The whole label-plus-buttons widget hides atomically once `refresh_tree` sees a loaded manifest. Preserves the pre-#137 contract that the empty state vanishes the moment data lands.
- **Related:** [PR #197](https://github.com/jackal998/photo-manager/pull/197) (closes [#137](https://github.com/jackal998/photo-manager/issues/137)); QA scenario [`qa/scenarios/s41_empty_state_action_buttons.py`](../qa/scenarios/s41_empty_state_action_buttons.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Execute Action — base flow

- **Entry point:** Main window menu → "Execute Action…" — [app/views/main_window.py:579](../app/views/main_window.py#L579) → [app/views/handlers/file_operations.py:877](../app/views/handlers/file_operations.py#L877)
- **Trigger:** User clicks "Execute Action…" from the menu (label defined at [translations/en.yml:32](../translations/en.yml#L32)).
- **Behaviour:** Opens the `ExecuteActionDialog` (a modal review window) listing every record with a non-empty `user_decision`, grouped by duplicate-set. The user reviews the planned actions and clicks **Execute** to apply them or **Close** to dismiss without applying. Execute carries out deletes (via `delete_service`) and writes the manifest changes; Close discards no decisions — they remain queued until the next open. **Before** iterating files, `_on_execute` calls `self._preview.release_file_handles()` to drop any open video-player handles — otherwise `send2trash` on the previewed file hits `COPYENGINE_E_SHARING_VIOLATION_SRC` (0x80270027). Post-execute, missing files are reported in a 'Files Not Found' warning; files that failed to delete are reported in a separate 'Files Failed to Delete' warning, with each path annotated by a **decoded reason** (e.g. "file is in use by another process", "access denied (source)", "path too long for Recycle Bin") via the `_decode_winerror(exc)` helper — the dialog no longer prefixes the body with a misleading static three-cause list.
- **Conditions / variants:** Execute button is disabled when no rows have a `user_decision`. Several layered behaviours modify the flow — see the other Execute Action entries below. **Layout posture (#408):** the summary label and "Select by Field/Regex…" button at the top of the dialog use `QSizePolicy(Preferred, Maximum)` so they stay compact at their `sizeHint`; the tree/splitter gets explicit `stretch=1` and absorbs all vertical growth when the dialog is resized. **Select-by scope (#443):** the "Select by Field/Regex…" button — and the right-click "Set Action by Field…" menu item — pass `_groups_with_decisions()` (the rendered subset) to the inner `ActionDialog`, not `self._groups` (the full manifest); match/preview/dispatch is scoped to the rows visible in the Execute dialog's tree. **Main-tree sync after Close (#444):** `_decisions_changed` is set whenever in-dialog batch decision / lock writes mutate `vm.groups` in place; the handler reads the flag on reject and calls `refresh_tree(self.vm.groups)` so the main window's tree re-renders the new Action / Lock cells even when the user closes the dialog without executing. Single source of truth — a record's `user_decision` is identical wherever it's rendered.
- **Related:** Dialog at [app/views/dialogs/execute_action_dialog.py:56](../app/views/dialogs/execute_action_dialog.py#L56); QA scenarios [`qa/scenarios/s13_execute_action.py`](../qa/scenarios/s13_execute_action.py), [`qa/scenarios/s59_execute_dialog_select_by_main_tree_sync.py`](../qa/scenarios/s59_execute_dialog_select_by_main_tree_sync.py) (Select-by → Close → main-tree sync, #444)
- **Last verified:** 2026-06-10 (pre-delete handle release + decoded failure reason)

---

### Execute Action — complete-group delete confirm

- **Entry point:** Final confirmation modal inside the Execute Action flow — [app/views/dialogs/execute_action_dialog.py:149](../app/views/dialogs/execute_action_dialog.py#L149) (`_complete_delete_groups`) drives the `QMessageBox.question` shown by `_on_execute`.
- **Trigger:** User clicks **Execute** while at least one group has `user_decision='delete'` on every member (i.e. the whole group would be deleted).
- **Behaviour:** A single `QMessageBox.question` ("ALL files in group N will be deleted — proceed?") appears before any delete fires. **Yes** continues to the actual `delete_service` call; **No** aborts and the dialog stays open with decisions intact.
- **Conditions / variants:** Only fires when at least one group's entire delete set is in scope. When the highlighted-row scope (see below) covers only part of a group's delete decisions, the confirm is suppressed for that group because the "ALL files" copy would no longer be accurate. Partial-group deletes never trigger this confirm.
- **Related:** [PR #30](https://github.com/jackal998/photo-manager/pull/30) collapsed an earlier two-step confirm into the single `QMessageBox`; complete-group scoping for highlighted rows shipped in [PR #219](https://github.com/jackal998/photo-manager/pull/219) via `_complete_delete_groups_in_scope` at [execute_action_dialog.py:928](../app/views/dialogs/execute_action_dialog.py#L928).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Execute Action — complete-group warning banner with jump-to

- **Entry point:** Amber warning banner inside the Execute Action dialog — [app/views/dialogs/execute_action_dialog.py:274](../app/views/dialogs/execute_action_dialog.py#L274) (`_refresh_warning_banner`).
- **Trigger:** Banner appears whenever `_complete_delete_groups()` returns one or more group numbers — i.e. at least one group has `user_decision='delete'` on every row. Refreshes on every decision change.
- **Behaviour:** Renders "⚠ Group(s) N, M will have ALL files deleted…" with each group number as a clickable HTML anchor. Clicking a group number scrolls the dialog's tree to that group and selects its row (via `_on_jump_to_group` reusing the `SORT_ROLE`-keyed `group_number` + the `MainWindow._reselect_by_path` `scrollTo` + `selectionModel.select` pattern).
- **Conditions / variants:** Anchor `href` values that aren't integers or that don't resolve to a known group are no-ops (silent — no error dialog).
- **Related:** [PR #181](https://github.com/jackal998/photo-manager/pull/181) (closes [#166](https://github.com/jackal998/photo-manager/issues/166)); QA scenario [`qa/scenarios/s33_execute_dialog_jump_to_all_delete.py`](../qa/scenarios/s33_execute_dialog_jump_to_all_delete.py)
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Execute Action — dialog geometry persistence

- **Entry point:** `done(result)` override on `ExecuteActionDialog`, plus the `restore_geometry` call at the end of `__init__`. Shared helper lives at [app/views/window_state.py](../app/views/window_state.py).
- **Trigger:** Every dismissal of the dialog (Execute, Close, or X-button) funnels through `done()`, which calls `save_geometry`. The next `__init__` call restores the saved rect on top of the hardcoded `setMinimumSize` default.
- **Behaviour:** User-resized dialog reopens at the same size within the session and across app restarts (state stored via `QSettings` under the path centralised in [window_state.py](../app/views/window_state.py)). The splitter divider between tree and preview persists separately via `save_splitter_state` / `restore_splitter_state` (Qt's `saveState` bytes are distinct from `saveGeometry`).
- **Conditions / variants:** If the saved rect would land off-screen (e.g. multi-monitor disconnect — <25% of the rect visible on any connected screen), the helper falls back to widget defaults rather than reopening on a disconnected monitor. Same behaviour applies to `ScanDialog` and `ActionDialog`.
- **Related:** Geometry — [PR #228](https://github.com/jackal998/photo-manager/pull/228) (closes [#215](https://github.com/jackal998/photo-manager/issues/215)), QA scenario [`qa/scenarios/s48_dialog_geometry_persist.py`](../qa/scenarios/s48_dialog_geometry_persist.py). Splitter persistence — [PR #260](https://github.com/jackal998/photo-manager/pull/260).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Execute Action — lock-confirm dialog

- **Entry point:** `LockedRowsConfirmDialog` at [app/views/dialogs/locked_rows_confirm_dialog.py](../app/views/dialogs/locked_rows_confirm_dialog.py). Wired at four trigger points: `file_operations.set_decision_with_lock_check`, `execute_action_dialog._set_decision`, `execute_action_dialog._set_decision_by_regex`, and `execute_action_dialog._on_execute_requested`.
- **Trigger:** Any path that would change a locked row's `user_decision` OR execute a delete on a locked row surfaces this confirm dialog before acting. Includes bulk regex from the main window, bulk regex inside the Execute Action dialog, single-row right-click on a locked row, and the pre-execute scan that catches rows locked AFTER their decision was set.
- **Behaviour:** Three-button modal — the primary "Apply" button unlocks the affected rows and applies the action; **Apply to Unlocked Only** runs the action only on the rows that weren't locked (disabled when every affected row is locked — the degenerate case); **Cancel** aborts with no changes.
- **Context-specific wording (#417):** the dialog body + primary-button label are caller-driven so the gate's own text tells the user the consequence. Two contexts inside the Execute Action dialog:
  - **IMMEDIATE** — the pre-execute scan (`_on_execute_requested`): clicking "Apply" **deletes files now**. Body leads with "You're about to DELETE …" and the primary button reads **Unlock & Delete All**.
  - **DEFERRED** — decision-setting (`_set_decision`, `_set_decision_by_regex`): clicking "Apply" only **queues a decision** to the manifest; nothing is deleted until Execute Action runs. Body states "Nothing is deleted yet — this only queues the decision" and the primary button reads **Unlock & Set Action**.

  The main-window `file_operations.set_decision_with_lock_check` route still uses the generic shared wording (the single-key default kept as the fallback). Both wordings are localised in `translations/{en,zh_TW}.yml` under `locked_confirm.{body_immediate,body_deferred,btn_unlock_apply_immediate,btn_unlock_apply_deferred}` (+ the `*_all_locked_*` degenerate variants).
- **Conditions / variants:** Lock / Unlock toggles themselves never surface this dialog (they're always-allowed). The `delete_service.plan_delete` lock filter at [infrastructure/delete_service.py](../infrastructure/delete_service.py) was retired in favour of a defensive assertion — callers are now responsible for routing through this confirm first.
- **Related:** [PR #183](https://github.com/jackal998/photo-manager/pull/183) (closes [#182](https://github.com/jackal998/photo-manager/issues/182), supersedes the [PR #175](https://github.com/jackal998/photo-manager/pull/175) hybrid lock semantic); context-specific wording from [#417](https://github.com/jackal998/photo-manager/issues/417); QA scenarios [`qa/scenarios/s32_lock_confirm_bulk_regex.py`](../qa/scenarios/s32_lock_confirm_bulk_regex.py), [`qa/scenarios/s34_lock_confirm_at_execute.py`](../qa/scenarios/s34_lock_confirm_at_execute.py) (asserts the IMMEDIATE delete-now wording), [`qa/scenarios/s36_lock_confirm_destructive_execute.py`](../qa/scenarios/s36_lock_confirm_destructive_execute.py).
- **Last verified:** 2026-06-01 (#417 context wording split)

---

### Execute Action — preview pane

- **Entry point:** Embedded `PreviewPane` (same class as the main window) inside `ExecuteActionDialog`, mounted via a horizontal `QSplitter` — [app/views/dialogs/execute_action_dialog.py:188](../app/views/dialogs/execute_action_dialog.py#L188).
- **Trigger:** Pane is present whenever a `task_runner` is threaded through the dialog constructor (the production path from [`file_operations.py:888`](../app/views/handlers/file_operations.py#L888)). Selecting a single row in the dialog's tree drives `PreviewPane.show_single(path, info)`; multi-select or empty-select calls `clear`.
- **Behaviour:** Lets the user see what each row's file looks like before confirming destructive actions, reusing the same `ImageTaskRunner` instance as the main window (no second runner spun up). The dialog owns its own `PreviewPane` instance — the runner's `imageLoaded` signal is forwarded both to the main window's pane and (since #409) to the dialog's pane via an explicit connect at construction. Splitter divider position persists per dialog across opens — see geometry feature above.
- **Conditions / variants:** When `task_runner=None` (test/legacy path) the dialog falls back to the pre-#165 single-column layout — no splitter, no preview. The `info` dict passed to `show_single` is minimal (`name` + `folder`); richer metadata (size / shot date) is deferred.
- **Related:** [PR #260](https://github.com/jackal998/photo-manager/pull/260) (closes [#165](https://github.com/jackal998/photo-manager/issues/165)); QA scenario [`qa/scenarios/s51_execute_dialog_preview.py`](../qa/scenarios/s51_execute_dialog_preview.py). Failure-bucket split ([#68](https://github.com/jackal998/photo-manager/issues/68)) was deliberately deferred.
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Execute Action — partial execution via "Execute selected"

- **Entry point:** "Execute selected" `QPushButton` next to the Execute/Cancel buttons in [app/views/dialogs/execute_action_dialog.py](../app/views/dialogs/execute_action_dialog.py). Wired in `_build_ui` with `ActionRole`; disabled by default and toggled on by `_refresh_execute_selected_state` whenever the tree selection contains at least one decided file row.
- **Trigger:** User highlights one or more file rows inside the Execute Action dialog (multi-row via `ExtendedSelection` — same selection mechanic the preview pane already uses) and clicks **Execute selected**.
- **Behaviour:** Runs the standard execute pipeline (lock pre-scan, complete-group confirm, decision iteration, manifest persist, finalize-outcome) but narrowed to records whose `file_path` is in the selected set — `_on_execute_requested(paths_filter=selected)` → `_on_execute(paths_filter=selected)`. Records OUTSIDE the filter keep their `user_decision` intact; records INSIDE the filter have their `user_decision` cleared after the action so a subsequent Execute click doesn't re-process them. The dialog stays open afterwards — the tree refreshes, the user keeps reviewing. Full "Execute" still closes the dialog via `accept()` as before. The "(only selected)" Action-menu entry from #410 is unaffected; that path narrows scope at construction time (groups arrive pre-filtered), whereas this button narrows at execute time inside the dialog. Both can coexist on the same session: pre-filter to a subset of groups, then partial-execute within that subset. (#584: `mark_executed` removed; outcomes written via `finalize_outcome(path, 'deleted'|'ignored')`; manifest visibility gate is `WHERE outcome=''`.)
- **Conditions / variants:** Empty selection makes the button a silent no-op (a race between selection-cleared and clicked-signal lands here harmlessly). The complete-group confirm is also narrowed by the same filter — a group qualifies as "complete delete" only when every in-scope record is a delete decision, so partial selections never falsely trigger the confirm (or fail to trigger when only the selected subset of a group is decided=delete).
- **Related:** [Improvement 1 in the partial-execute bundle PR]; supersedes the in-dialog scope-narrowing approach rejected in [#410](https://github.com/jackal998/photo-manager/issues/410) by making it a SECOND button (not a relabel of the existing Execute button). Composes with [Singleton-prune offer after destructive ops](#singleton-prune-offer-after-destructive-ops-426)'s new "actioned-singleton" classification — partial execute is the primary producer of actioned singletons (one item left in a group with a not-yet-executed decision).
- **Last verified:** 2026-05-30

---

### Execute Action — filter by action type (#502)

- **Entry point:** Type-filter `QComboBox` (`objectName="executeDialogTypeFilterCombo"`) above the tree in the Execute Action dialog ([app/views/dialogs/execute_action_dialog.py](../app/views/dialogs/execute_action_dialog.py)). Wired in `_build_ui` between the "Select by Field/Regex…" button and the tree. Three options: **All decisions** (default), **Delete only**, **Remove from list only**.
- **Trigger:** User opens the Execute Action dialog and changes the combo selection.
- **Behaviour:** Filter is **purely a display + scope narrower**, never a structural mutation. `_apply_type_filter` uses `dataclasses.replace` to return shallow `PhotoGroup` copies with filtered items; `self._groups` (which aliases `vm.groups`) is untouched (#430 group-context contract preserved). Switching the filter rebuilds the tree (`_rebuild_tree_model`), updates the summary count (`_update_summary`), refreshes the warning banner (`_refresh_warning_banner`), and narrows the Execute commit scope (`_on_execute_requested` computes `effective_filter = type_paths ∩ paths_filter` BEFORE the `in_scope` closure so the lock-confirm pre-execute scan and complete-group confirm both see the same narrowed view). Mirrors the "visible = committed" contract established by #410 and #485 — clicking Execute commits only what's visible after the filter; rows of other decision types keep their `user_decision` and survive into the next session. Combo resets to "All decisions" on every dialog reopen (no persistence).
- **Conditions / variants:** **Lock/Unlock and Keep are intentionally excluded** from the combo. Lock/Unlock live on `is_locked`, not `user_decision`, so filtering by them would silently match nothing; Keep collides with the undecided state (both empty-string), making the filter ambiguous. **Warning banner under non-default filter:** the banner composes two parts — (a) the existing complete-delete-groups warning, narrowed to the visible-after-filter scope, and (b) a new hidden-destructive line ("⚠ {count} pending delete row(s) hidden by the current filter") that surfaces whenever the filter is hiding delete-decision rows. Without (b) the user would assume nothing destructive was staged after switching to "Remove from list only" while delete decisions still exist. **Composition with #485 partial-execute:** filter and "Execute selected" highlight intersect — filtering to "Delete only" then highlighting 3 rows then clicking Execute selected commits exactly those 3 rows, leaving the other delete-decision rows marked. **Composition with #175 / #182 lock-confirm:** a "Remove only" filter on a manifest containing locked-delete rows does NOT trigger the destructive-row lock-confirm gate — the locked deletes are outside the type-filter scope and never reach `_ask_lock_confirm`'s in-scope predicate.
- **Related:** [#502](https://github.com/jackal998/photo-manager/issues/502); composes with [Execute Action — partial execution via "Execute selected"](#execute-action--partial-execution-via-execute-selected) (#485, in-dialog scope) and [Execute Action — scope to selected groups](#execute-action--scope-to-selected-groups-only-selected-entry-410-430-429) (#410 / #430, pre-dialog scope); composes with [Execute Action — lock-confirm dialog](#execute-action--lock-confirm-dialog) (#175 / #182); composes with [Execute Action — complete-group warning banner with jump-to](#execute-action--complete-group-warning-banner-with-jump-to) (#166) via the new two-part banner composition. Layer-1 coverage: `tests/test_execute_action_dialog.py::TestExecuteDialogTypeFilter`. Layer-3 driver: [`qa/scenarios/s60_execute_filter_by_action_type.py`](../qa/scenarios/s60_execute_filter_by_action_type.py).
- **Last verified:** 2026-05-31 (#502)

---

### Execute Action — scope to selected groups (only-selected entry, #410, #430, #429)

- **Entry point:** Two affordances, same semantic:
  - **Action menu → Execute Action (only selected)…** — second sibling of the plain "Execute Action…" entry in [app/views/components/menu_controller.py](../app/views/components/menu_controller.py) (`execute_action_selected_only`). Wired to `MainWindow.on_execute_action_selected_only` which calls `FileOperationsHandler.execute_action(selected_only=True)`.
  - **Right-click → Execute Action (only selected)…** (#429) — added to both the single-file-row context menu and the multi-selection context menu in [app/views/handlers/context_menu.py](../app/views/handlers/context_menu.py); routed through `ActionHandlersImpl.execute_action_selected_only` which forwards to the same handler entry point. Group-only selections (single group row or all-group multi-selection) hide the entry — the menu-bar entry remains the path for that case.
- **Trigger:** User highlights one or more file rows (or group headers) in the **main window tree** (multi-row via `ExtendedSelection`), then either picks the Action-menu entry or right-clicks any selected file row and picks the same label from the context menu. Both menu-bar and context-menu entries are gated on (manifest_loaded AND ≥1 file row selected) — empty selection or no manifest greys/hides them (handled by `MainWindow._refresh_execute_selected_only_enabled` for the menu-bar entry; `_create_*_selection_menu` enforces the file-row gate for the context entry).
- **Behaviour:** Scope is a kwarg through the handler call chain — NOT in-dialog state. `execute_action(selected_only=True)` narrows `vm.groups` by **group membership** (#430): any selected file row pulls in its whole parent group; a selected group header pulls in that group too. The original `PhotoGroup` instances pass through unchanged (no cloning) so the dialog renders the same ref-row, near-dup tags, and score comparisons the user sees in the main tree. Selection is re-read from `tree_controller.get_selected_items()` at execute time (the context-menu `items` argument is discarded inside the bridge), so a stale list can't desync the dialog from the visible selection. The plain "Execute Action…" entry remains unchanged and passes `vm.groups` whole. Execute button label is **static** ("Execute") — the older `execute_button_highlighted` swap and in-dialog `_selected_file_paths` scope branch were removed in #410, because conflating scope and intent at the same affordance hid the "only selected" capability and loaded the dialog with rows the user had no intent to act on.
- **Conditions / variants:** Groups not represented in the selection are dropped entirely. The "ALL files will be deleted" complete-group confirm fires per the dialog's normal logic over the groups it was given — because groups arrive whole (#430), the confirm only fires when every row of the original group is actually decided=delete, matching the user's mental model. Lock guard scans every locked delete row in the passed groups (no in-dialog scope narrowing); upstream pre-filter already excluded out-of-scope groups.
- **Related:** [#429](https://github.com/jackal998/photo-manager/issues/429) (context-menu sibling for the menu-bar entry); [#430](https://github.com/jackal998/photo-manager/issues/430) (group-level scope replaces the per-row filter); [#410](https://github.com/jackal998/photo-manager/issues/410) (original menu entry, supersedes in-dialog scope-narrowing from [PR #219](https://github.com/jackal998/photo-manager/pull/219) for [#211](https://github.com/jackal998/photo-manager/issues/211)); QA scenario [`qa/scenarios/s44_execute_highlighted_rows.py`](../qa/scenarios/s44_execute_highlighted_rows.py) re-recorded under #430's group-level semantic (highlight any row → full group in dialog → all 5 executed).
- **Last verified:** 2026-05-27 (#429)

---

### Exit dirty-flag prompt

- **Entry point:** `MainWindow.closeEvent` reads `FileOperationsHandler._is_dirty`.
- **Trigger:** User closes the app (X button, Alt+F4, File > Exit) after making decision changes that haven't been explicitly saved via Save Manifest Decisions.
- **Behaviour:** A 3-button `QMessageBox` appears — **Save & leave** silently saves to the loaded manifest path then exits; **Leave** exits without an additional save; **Back** stays in the app (the default, so accidental Esc/Enter keeps the user in place). Decisions auto-persist to the loaded manifest as soon as they're set, so **Leave** never loses data — the prompt is purely about offering an explicit save (e.g. before a Save-As to another path).
- **Conditions / variants:** Dirty flag flips on `set_decision`, `remove_items_from_list`, and `remove_from_list_toolbar`. It clears on manifest load, save, silent save, and successful execute — so a fresh manifest with no changes never triggers the prompt.
- **Related:** [PR #158](https://github.com/jackal998/photo-manager/pull/158); QA scenario [`qa/scenarios/s28_exit_dirty_prompt.py`](../qa/scenarios/s28_exit_dirty_prompt.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Singleton-prune offer after destructive ops (#426)

- **Entry point:** Fired automatically at the tail of every destructive op that may collapse a group to one item — `FileOperationsHandler._maybe_offer_singleton_prune` is called from the Execute Action accept branch, `remove_from_list_toolbar`, `remove_items_from_list`, and the lock-confirm "apply unlocked only" sub-branches that also remove rows. Single helper, single call site per flow.
- **Trigger:** Helper scans `vm.groups` after the destructive op completes and short-circuits when no group has exactly one item left. If at least one singleton is present, behaviour branches on `JsonSettings.get("ui.prune_singletons", "ask")`.
- **Behaviour:** Three preference states: `"ask"` (default) opens `SingletonPruneConfirmDialog` ([app/views/dialogs/singleton_prune_confirm_dialog.py](../app/views/dialogs/singleton_prune_confirm_dialog.py)) with [Remove N] / [Keep all] buttons and a "Remember my choice — don't ask again" checkbox; `"always"` silently prunes; `"never"` silently keeps. When the user checks the box, Remove flips the setting to `"always"` and Keep to `"never"`, persisted via `JsonSettings.set` + `.save()`. The prune itself is **batched** — ONE `vm.remove_from_list(paths)` call per non-empty bucket, ONE `_sync_removed_to_db(paths)` write per bucket, ONE `ui_updater.refresh_tree` per bucket (perf-aware for the s44-scale ≤5000-singletons acceptance criterion). Esc / window close on the dialog yields Keep (the safe default).
- **Actioned-singleton classification (Improvement 2 in the partial-execute bundle):** When a singleton's remaining item carries a NON-KEEP-ABLE pending decision (`delete` or `ignore`) that was NOT executed, the handler classifies it into a separate `count_actioned` bucket instead of the default plain bucket. The dialog adapts its body text and, when both buckets are populated, shows an additional opt-in checkbox **default UNCHECKED**: "Also remove N singleton(s) with pending non-executed actions". Clicking Remove sweeps the plain bucket automatically; the actioned bucket is included only when its checkbox is also checked. Returns a `PruneVerdict(prune_plain, prune_actioned, remember)` dataclass — the caller fires `_apply_singleton_prune` once per opted-in bucket. The `"always"` preference still sweeps both buckets in the single batched call — the user's standing "don't ask, just prune" instruction is not narrowed by action state. Common producer of actioned singletons: the partial-execute flow ([Execute Action — partial execution via "Execute selected"](#execute-action--partial-execution-via-execute-selected)) where the user runs a subset of decisions and the un-executed remainder ends up alone in its group.
- **#584 — outcome model + locked-singleton gate (D6 / D10):** The prune now writes `outcome='ignored'` via `finalize_outcome` (replacing the old `user_decision='removed'` tombstone). Locked singletons are no longer swept silently — they route through `LockedRowsConfirmDialog` before any prune on BOTH the `"ask"` and `"always"` paths (CANCEL keeps them; Unlock & Apply prunes them). The DB write now precedes the in-memory `vm.remove_from_list` (DB-first), so a failed write leaves the UI and DB consistent instead of diverging.
- **Conditions / variants:** The helper is a tail-call hook — every destructive method that lands rows in the prune-candidate state calls it as the LAST step (after refresh + status report + dirty flag). Skipping a destructive op (no rows actually removed) → no singletons appear → helper short-circuits without UI. Singleton state is read fresh from `vm.groups` each call, so deferred removals (Execute Action's `removed_from_list_paths`) are detected after the dialog accepts and dropping them happens before the offer fires.
- **Related:** [#426](https://github.com/jackal998/photo-manager/issues/426); precedent for batched-confirm pattern is [#417](https://github.com/jackal998/photo-manager/issues/417) (LockedRowsConfirmDialog); the existing `vm.remove_deleted_and_prune(prune_singles=True)` was the alternative considered but rejected as too implicit — see issue body. The setting key `ui.prune_singletons` is gitignored via `settings.json`; an example default goes in `settings.json.example`. QA scenarios: [`qa/scenarios/s61_actioned_singleton_prune.py`](../qa/scenarios/s61_actioned_singleton_prune.py) covers the `"ask"` path (mixed bucket layouts A/B/C + D6 lock-gate variants D-cancel/D-apply, #589); [`qa/scenarios/s67_locked_singleton_prune_always.py`](../qa/scenarios/s67_locked_singleton_prune_always.py) covers the `"always"` path D6 regression guard (#589 — proves the lock dialog STILL fires under the standing "always" instruction).
- **Last verified:** 2026-06-06 (#589 — D6 layer-3 coverage)

---

### Keep-worthiness scoring

- **Entry point:** Score column (COL_SCORE at index 2) in the main result tree — [app/views/constants.py:22](../app/views/constants.py#L22). Within-group rows sort by score descending so the best copy lands at the top of every group.
- **Trigger:** Every scanned file gets a score automatically — no user action needed. The score lands in the manifest at scan time. Re-scoring without re-scanning is available via `ManifestRepository.rescore(weights)`.
- **Behaviour:** Composite score in `[0.0, 1.0]` measuring how "keep-worthy" each file is, computed as a pure function of file attributes (no user-intent signals). Two-tier algorithm: tier 1 absolute penalties (format, `xmpMM:DerivedFrom`); tier 2 weighted composite of eight continuous signals (resolution, EXIF completeness, date provenance, filename, GPS, path, Live Photo, file size). Live Photo MOV passengers get `score = NULL` and are skipped by ranking — they inherit the paired HEIC's decision.
- **Conditions / variants:** The previous "Apply best-copy decisions to this group" right-click action was removed in [PR #224](https://github.com/jackal998/photo-manager/pull/224) (closes [#210](https://github.com/jackal998/photo-manager/issues/210)) because it was superseded by the regex dialog's "top 1 by score within group" numeric condition (see [Set Action dialog — numeric comparison panel](#set-action-dialog--numeric-comparison-panel)). Auto-select after scan (see [Scan dialog — auto-select after scan](#scan-dialog--auto-select-after-scan)) is the third surface that consumes scoring.
- **Related:** Cluster originated in [#187](https://github.com/jackal998/photo-manager/issues/187): [PR #199](https://github.com/jackal998/photo-manager/pull/199), [#200](https://github.com/jackal998/photo-manager/pull/200), [#202](https://github.com/jackal998/photo-manager/pull/202), [#203](https://github.com/jackal998/photo-manager/pull/203), [#204](https://github.com/jackal998/photo-manager/pull/204), [#205](https://github.com/jackal998/photo-manager/pull/205), [#206](https://github.com/jackal998/photo-manager/pull/206); QA scenario [`qa/scenarios/s42_scoring.py`](../qa/scenarios/s42_scoring.py). Algorithm details in [README.md § Keep-worthiness scoring](../README.md#keep-worthiness-scoring-187).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Language switch

- **Entry point:** Main window menu → **View > Language** submenu — built by `menu_controller.py` via `QActionGroup(exclusive=True)`.
- **Trigger:** User picks a locale from the Language submenu.
- **Behaviour:** A Yes/No confirm prompt appears. On Yes the `MainWindow` rebuilds in place via the same factory used at startup — no app restart needed. State preserved best-effort: window geometry, splitter sizes, selected row's path. If a manifest was loaded pre-switch, it is re-loaded into the new window so the result tree stays populated — any pending decisions are silent-saved to the manifest's SQLite before the swap so the reload sees the user's latest state ([#428](https://github.com/jackal998/photo-manager/issues/428)). The chosen locale persists to `settings.json` under `ui.locale`.
- **Conditions / variants:** Available locales are discovered from `translations/<code>.yml` files. Each new YAML file appearing alongside `en.yml` shows up automatically in the picker on the next launch (no enum to update). Adding a new locale: copy `en.yml` → `<code>.yml`, translate values, restart once. Picking the already-active locale is a no-op (no confirm fires).
- **Related:** [PR #157](https://github.com/jackal998/photo-manager/pull/157), [#428](https://github.com/jackal998/photo-manager/issues/428); QA scenarios [`qa/scenarios/s22_language_switch.py`](../qa/scenarios/s22_language_switch.py) and [`qa/scenarios/s58_language_switch_preserves_manifest.py`](../qa/scenarios/s58_language_switch_preserves_manifest.py); translator workflow in [`docs/i18n.md`](i18n.md).
- **Last verified:** 2026-05-27 (manifest-preservation behaviour landed via [#428](https://github.com/jackal998/photo-manager/issues/428))

---

### List menu — Remove from List

- **Entry point:** Main window menu → **List > Remove from List** — handler at `MainWindow._remove_from_list_toolbar`.
- **Trigger:** User selects one or more rows in the main tree and picks **List > Remove from List**.
- **Behaviour:** Drops the selected rows from the in-memory view and queues them for removal from the manifest on save. Flips the dirty flag (see [Exit dirty-flag prompt](#exit-dirty-flag-prompt)). This is the immediate "drop from view" path — distinct from the bulk regex deferred decision (see [Bulk regex — remove from list (deferred decision)](#bulk-regex--remove-from-list-deferred-decision)).
- **Conditions / variants:** Works with single or multi-select. Lock-aware via the standard `set_decision_with_lock_check` route (locked rows trigger the lock-confirm dialog).
- **Related:** Originally [PR #158](https://github.com/jackal998/photo-manager/pull/158); QA scenarios [`qa/scenarios/s20_multi_remove_from_list.py`](../qa/scenarios/s20_multi_remove_from_list.py), [`qa/scenarios/s21_list_menu_remove.py`](../qa/scenarios/s21_list_menu_remove.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Log menu

- **Entry point:** Main window menu → **Log** — labels in [translations/en.yml:36-41](../translations/en.yml#L36).
- **Trigger:** User picks one of: **Open Latest Log**, **Open Latest Delete Log**, **Open Log Directory**, **Open Delete Log Directory**.
- **Behaviour:** Opens the corresponding log file or directory in the OS default application / file manager. "Latest log" resolves to the most recently rotated `loguru` log file; "delete log" resolves to the audit CSV (`delete_<timestamp>.csv`) written on every Execute Action run — both the regex/service delete path and the in-dialog Execute Action delete path go through the shared `infrastructure.logging.write_delete_log`, so a row (group, path, success, reason) is recorded for every file the run removed.
- **Conditions / variants:** Log directory path comes from `infrastructure/logging.py` configuration. If no log file has been written yet (first run before any logging fires) the "Open Latest" entries open the directory instead.
- **Related:** QA scenario [`qa/scenarios/s18_log_menu.py`](../qa/scenarios/s18_log_menu.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Main window — close-during-scan confirm

- **Entry point:** Main window title-bar X / Alt+F4 / File > Exit while a scan is running (the `ScanDialog` is open and its `_worker` is alive).
- **Trigger:** User attempts to close the main window while a `ScanWorker` is running. `MainWindow.scan_running` is kept in sync via `ScanDialog.scan_started` / `scan_finished` signals connected in `on_scan_sources`.
- **Behaviour:** A `QMessageBox.question` confirms before the close proceeds — translated title `exit.scan_running_title` ("Scan in progress" / "掃描進行中") and body `exit.scan_running_body` explaining the scan will be cancelled. Default button is **No** (keeps the user in the app), matching the existing exit-dirty-prompt's accidental-Esc protection. **Yes** accepts the close, the modal cascade runs `ScanDialog.closeEvent` → `worker.requestInterruption()` + `wait(3000)` → `super().closeEvent()`, and Qt's `aboutToQuit` then fires `_cleanup_on_quit` in `main.py` for loguru flush + any belt-and-braces worker drain.
- **Conditions / variants:** Today the Scan dialog is modal so Qt's natural cascade also handles the close; the guard is defense-in-depth for a future non-modal scan dialog or a refactor that moves worker ownership up to `MainWindow`. Independent of the unsaved-decisions prompt — both can fire in the same close attempt, but the scan-running guard runs first (a scan is more disruptive to interrupt than an unsaved decision).
- **Related:** issue [#468](https://github.com/jackal998/photo-manager/issues/468); paired with graceful shutdown hook [#473](https://github.com/jackal998/photo-manager/issues/473) and the Windows Job Object work in [#460](https://github.com/jackal998/photo-manager/issues/460). The kill-on-close reaping was **silently inert from #460 through #553** — [#555](https://github.com/jackal998/photo-manager/issues/555) fixed a handle-lifetime bug (`int(PyHANDLE)` dropped the OS handle before `AssignProcessToJobObject` ran → ERROR_INVALID_HANDLE) that meant nothing was ever actually placed in the job. With that fixed, the **process-mode hash workers** (the `ProcessPoolExecutor` children — the real disk-readers behind the #549 orphan pain) now genuinely terminate on a hard parent-kill via the shared `assign_pid_to_kill_job` helper. **exiftool reaping is job-nesting-gated** ([#556](https://github.com/jackal998/photo-manager/issues/556) → [#558](https://github.com/jackal998/photo-manager/issues/558)): jailing a live `-stay_open` exiftool corrupts its extended EXIF pass *only when the parent is itself inside another job* (CI runner / console host). exiftool is a PAR self-extracting Perl exe whose re-exec'd child interpreter (a grandchild of the app) gets force-joined to the nested job chain and starved by the outer job's intersected limits — this is what surfaced `s42_scoring` NULL `exif_tag_count` for a non-deterministic subset of files in #556. #556 un-jailed exiftool wholesale as the emergency fix; [#558](https://github.com/jackal998/photo-manager/issues/558) re-enables the reaping **only when the process is not already in a job** (`_process_in_any_job`, which fails safe → `True` on any uncertainty so it never jails when in doubt). So a bare-desktop hard-exit (crash / Task Manager force-kill) reaps exiftool as #460 intended, while a job-nested parent (CI runner) skips jailing entirely and never corrupts the pass. The process-pool hash workers are single leaf processes with no grandchild interpreter, so they were always safe to jail unconditionally. **Graceful close during HASH** ([#561](https://github.com/jackal998/photo-manager/issues/561)): the cancel path now **hard-kills the live exiftool consumer processes** (`ExiftoolProcess.kill`) before joining them — a consumer wedged inside a 500-file `batch_read_extracts` only checks the cancel flag between queue gets, so without the kill the `join(timeout=5)` abandoned it, the `wait(3000)` overran (the "can't close" symptom), and the un-jailed exiftool orphaned. Killing the subprocess drops EOF into the consumer's reader so it unblocks immediately, the join completes fast, and nothing is left running on a dialog-close mid-scan. **Graceful close during EXIF post-HASH drain** (#607): #561 only covered the in-HASH-loop cancel branch — the post-HASH sentinel-and-join block had an unbounded `t.join()` with no `isInterruptionRequested()` check, so a user-close while the consumer was mid-`ExiftoolProcess.execute()` wedged the worker for the full exiftool read-timeout (~60s). The dialog's `wait(3000)` timed out, the QThread orphaned, and on a job-nested launch context (where `_process_in_any_job()` returns True and #558 skips `KILL_ON_JOB_CLOSE` by design) the live exiftool subprocesses survived past app exit at 100 % CPU. #607 mirrors the #561 pattern on this branch: the final consumer join is now cancel-aware (`while consumer.is_alive(): if isInterruptionRequested(): _kill_exif_procs(); break`) and emits the same clean `"Scan cancelled."` marker the in-HASH branch does.
- **Last verified:** 2026-06-08 (#607 — cancel-during-EXIF-post-drain now hard-kills exiftool too; #558 reaping still job-nesting-gated; #561 in-HASH cancel-kill unchanged)

---

### Main window — column order/width persistence

- **Entry point:** Tree header drag/resize signals in `MainWindow`, persisting to `QSettings` via the helpers in [app/views/window_state.py](../app/views/window_state.py).
- **Trigger:** User drags a column header to reorder or resizes a column boundary.
- **Behaviour:** Saves the new column order and widths on every drag/resize signal — not only at `closeEvent` — so the layout survives force-quits and OS-level kills, not just clean exits. Re-applies on launch.
- **Conditions / variants:** Persisted alongside main-window geometry under `PHOTO_MANAGER_HOME` (when set) so QA scenarios and dev runs stay isolated from any installed-app state.
- **Related:** [PR #227](https://github.com/jackal998/photo-manager/pull/227) (closes [#214](https://github.com/jackal998/photo-manager/issues/214)); QA scenario [`qa/scenarios/s47_column_layout_persist.py`](../qa/scenarios/s47_column_layout_persist.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Main window — geometry + splitter persistence

- **Entry point:** `MainWindow.closeEvent` saves geometry; the matching restore runs in `__init__`. Shared helpers in [app/views/window_state.py](../app/views/window_state.py).
- **Trigger:** Window position, size, maximize state, and splitter ratio round-trip on every clean exit and restore on the next launch.
- **Behaviour:** Geometry stored under QSettings key `geometry/main_window` (`saveGeometry()` bytes); splitter state under `geometry/main_splitter` (`saveState()` bytes). Stored under `PHOTO_MANAGER_HOME` when set; otherwise under repo root in `window_state.ini`. The splitter also enforces a 200 px floor on each pane and disables collapse, so the preview pane can no longer be squeezed to invisibility ([#136](https://github.com/jackal998/photo-manager/issues/136)).
- **Conditions / variants:** Position tolerance on round-trip is ~50–60 px on Win10 due to DWM's invisible-frame extension and high-DPI rcNormalPosition rounding — the contract is "reopens where it was," not pixel-perfect. Off-screen guard (rect <25% visible on any connected screen) falls back to widget defaults.
- **Related:** [PR #191](https://github.com/jackal998/photo-manager/pull/191) (closes [#141](https://github.com/jackal998/photo-manager/issues/141), [#136](https://github.com/jackal998/photo-manager/issues/136)); QA scenario [`qa/scenarios/s39_window_geometry_persist.py`](../qa/scenarios/s39_window_geometry_persist.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Main window — keyboard navigation

- **Entry point:** Main result tree's built-in `QTreeView` keyboard handling, augmented in [tree_controller.py](../app/views/components/tree_controller.py).
- **Trigger:** User presses arrow keys / Home / End / Page Up / Page Down with the tree focused.
- **Behaviour:** Navigate rows with arrow keys; expand/collapse groups with Left/Right at group-header rows. Selected row is preserved across model rebuilds (e.g. after a decision change) so keyboard-driven review doesn't lose place.
- **Conditions / variants:** Multi-select works with Shift+arrow and Ctrl+click as in the standard Qt tree behaviour. The selection model is preserved across `setModel` calls so the highlighted row survives a tree refresh.
- **Decision shortcuts (#615):** `d` marks selected file rows as delete; `k` clears the action (empty/keep). Implemented as a `keyPressEvent` override on a QTreeView subclass (`DecisionTreeView`) — bypasses Qt's shortcut framework, which silently failed to dispatch K under this app's runtime state (root-cause investigation is a #626 follow-up). The override lives on the tree widget itself so the shortcuts fire only when the tree has keyboard focus — text-edit widgets elsewhere are immune. Multi-selection writes one batched SQLite transaction (inherited from `set_decision_to_highlighted`). Replaces QTreeView's default first-letter type-ahead navigation for these two letters (acceptable tradeoff because dedup filenames are mostly numeric); all other letters still fall through to `super().keyPressEvent` so type-ahead works for the rest. Pressing the shortcut with no selection shows a transient status-bar toast. The shortcuts are also shown as cosmetic hints in the context-menu Set Action submenu (`Delete D` / `Keep K`).
- **Play / pause shortcut (#624 follow-up):** Bare `p` toggles playback on the single-view video player. PR #624 killed video autoplay; this shortcut is the no-mouse path to control playback while reviewing on the keyboard. Wired via the same `DecisionTreeView.keyPressEvent` override that handles d/k: bare P emits `playPauseRequested` (no payload), connected to `PreviewPane.toggle_play_pause`. The toggle reads the current `is_playing()` state and branches to `pause()` or `play()`. Single-view only — in grid mode the shortcut is a silent no-op since there is no unambiguous "focused" player. The slot is wrapped in a defensive try/except so a crashed media player can never propagate a stack trace through the keyboard event loop. Modifier-bearing presses (Ctrl+P / Shift+P / Alt+P) fall through to `super().keyPressEvent` so they don't accidentally consume Ctrl+P (some users bind it to Print).
- **Related:** QA scenario [`qa/scenarios/s26_keyboard_navigation.py`](../qa/scenarios/s26_keyboard_navigation.py); play/pause unit tests at `tests/test_decision_tree_view.py` + `tests/test_preview_pane.py`.
- **Last verified:** 2026-06-11 (P play/pause shortcut)

---

### Main window — results tree double-click

- **Entry point:** `TreeController` double-click dispatcher — [tree_controller.py](../app/views/components/tree_controller.py) (dispatcher added in [PR #198](https://github.com/jackal998/photo-manager/pull/198)).
- **Trigger:** User double-clicks a row in the main result tree.
- **Behaviour:** **File row** → opens the file in the OS default viewer (`QDesktopServices.openUrl`). **Group header row** → toggles expand/collapse for that group. Qt's built-in `setExpandsOnDoubleClick` is disabled so the toggle path doesn't race the default expansion behaviour.
- **Conditions / variants:** The OS-spawn branch for files is layer-1 covered only — spawning a real viewer has no deterministic close-trigger across image apps. The Open Folder cascade is shared with the context menu via [app/views/handlers/file_opener.py](../app/views/handlers/file_opener.py).
- **Related:** [PR #198](https://github.com/jackal998/photo-manager/pull/198) (closes [#143](https://github.com/jackal998/photo-manager/issues/143)); QA scenario [`qa/scenarios/s40_results_tree_double_click.py`](../qa/scenarios/s40_results_tree_double_click.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Main window — row selection (no horizontal auto-scroll)

- **Entry point:** `TreeController.setup_tree_properties` — [tree_controller.py](../app/views/components/tree_controller.py) calls `setAutoScroll(False)` on the main result tree.
- **Trigger:** User clicks (or arrow-keys to) a row whose cells extend past the visible viewport on a horizontally-scrolled wide table.
- **Behaviour:** Selecting a row no longer jerks the viewport sideways to "align" the clicked column into view. Qt's default `autoScroll` calls `scrollTo(current)` on every selection change inside `currentChanged`; disabling it keeps the horizontal scroll position fixed where the user left it. Deliberate scroll-into-view paths are unaffected — re-select after manifest load and the post-scan auto-select still call `scrollTo()` explicitly (those are independent of the `autoScroll` property).
- **Conditions / variants:** Trade-off — drag-selecting near a viewport edge no longer auto-scrolls the contents. Negligible for this fully-expanded `ExtendedSelection` tree. Only the main tree is affected; the Execute Action and Scan dialog trees construct their own `QTreeView` and keep Qt defaults.
- **Related:** QA scenario [`qa/scenarios/s26_keyboard_navigation.py`](../qa/scenarios/s26_keyboard_navigation.py) exercises row selection on the main tree; unit invariant in [`tests/test_tree_controller.py`](../tests/test_tree_controller.py) (`TestSetupTreeProperties::test_autoscroll_disabled`).
- **Last verified:** 2026-06-02

---

### Main window — sort persistence within session

- **Entry point:** Header-click handler — `MainWindow._on_header_clicked` ([app/views/main_window.py:867](../app/views/main_window.py#L867)) stashes `(logical_index, order)` on the `TreeController`. `TreeController.refresh_model` ([tree_controller.py:225](../app/views/components/tree_controller.py#L225)) replays the stashed state on every model rebuild.
- **Trigger:** User clicks a column header to change sort field/direction.
- **Behaviour:** Within the session, the chosen sort survives every model rebuild — a File → Open Manifest, a decision change, an execute run — without reverting to defaults. Within-group rows always sort by score descending first (the keep-worthiness ranking), with the user's column sort layered on top.
- **Conditions / variants:** The across-launch surface (writing the sort state to `window_state.ini` so a fresh process restores it) is **not** implemented today — sort resets on app restart. Tracked separately from the within-session persistence.
- **Related:** [#121](https://github.com/jackal998/photo-manager/issues/121); QA scenario [`qa/scenarios/s45_sort_persistence.py`](../qa/scenarios/s45_sort_persistence.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Main window — status bar baseline

- **Entry point:** Persistent `QLabel` attached to `QStatusBar` via `addWidget` in `MainWindow`.
- **Trigger:** Always present — never expires. Temporary action toasts (`showMessage(text, timeout)`) layer on top.
- **Behaviour:** The baseline label always shows a resting message ("Ready" on startup, "Loaded manifest: <parts>" after a load). Qt's hide-during-temp / show-after-clear semantics fall back to the label so the bar never goes blank after a transient message expires or after a menu hover clears the bar.
- **Conditions / variants:** Pre-#138, startup `status_ready` was shown via `showMessage(text, 3000)` and the bar went blank after 3s. Pre-#140, opening any menu cleared the load-summary text permanently because Qt's `QAction` hover path calls `statusBar().showMessage(action.statusTip())` even when the tip is empty. The baseline label fixes both.
- **Related:** [#138](https://github.com/jackal998/photo-manager/issues/138), [#140](https://github.com/jackal998/photo-manager/issues/140); QA scenario [`qa/scenarios/s37_status_bar_baseline.py`](../qa/scenarios/s37_status_bar_baseline.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Open Manifest — base flow

- **Entry point:** Main window menu → **File > Open Manifest…** ([translations/en.yml:26](../translations/en.yml#L26)). Also reachable from the empty-state primary button (see [Empty-state action buttons](#empty-state-action-buttons)).
- **Trigger:** User picks **File > Open Manifest…** or clicks the empty-state **Open Manifest…** button.
- **Behaviour:** Opens the native file picker filtered to `*.sqlite`. On accept, loads the chosen manifest via `ManifestLoadWorker` (a background `QThread` so the UI stays responsive), then refreshes the tree. Status bar updates to "Loaded manifest: <name>". The preview pane is cleared at the tail of the load callback ([#431](https://github.com/jackal998/photo-manager/issues/431)) so a row that no longer exists in the new manifest can't leave its image/video rendered — `FileOperationsHandler._on_manifest_loaded` calls `UIUpdateCallback.clear_preview()` (implemented on `MainWindow.clear_preview`) after `set_baseline`, so any Qt widget-cleanup cost can't delay the status-baseline update that callers (and qa scenarios) poll for. Symmetric to the dialog-scope clear in `ExecuteActionDialog`.
- **Conditions / variants:** When the currently loaded manifest has unsaved decisions, a "Discard pending decisions?" confirm fires before the new manifest replaces it. Old manifests without the cached columns (`file_size_bytes`, `shot_date`, `creation_date`, `mtime`) auto-migrate and fall back to per-row filesystem reads transparently — re-scan once for the load-time speed benefit.
- **Related:** Foundation in [PR #12](https://github.com/jackal998/photo-manager/pull/12); preview-pane clear via [#431](https://github.com/jackal998/photo-manager/issues/431); QA scenario [`qa/scenarios/s16_open_manifest.py`](../qa/scenarios/s16_open_manifest.py); stale-path handling exercised in [`qa/scenarios/s24_stale_manifest_paths.py`](../qa/scenarios/s24_stale_manifest_paths.py).
- **Last verified:** 2026-05-27 (#431)

---

### Save Manifest Decisions — base flow

- **Entry point:** Main window menu → **File > Save Manifest Decisions…** ([translations/en.yml:27](../translations/en.yml#L27)).
- **Trigger:** User picks **File > Save Manifest Decisions…**.
- **Behaviour:** Opens a file picker. Choosing the same path saves in-place; choosing a new path exports a copy. Decisions are written to the chosen file, and subsequent saves default to that location. Clears the dirty flag on success (see [Exit dirty-flag prompt](#exit-dirty-flag-prompt)).
- **Conditions / variants:** Decisions also auto-persist to the loaded manifest as soon as they're set — Save Manifest Decisions is the explicit "save to a different path" / "snapshot" affordance, not the only persistence path. A silent variant (`save_manifest_decisions_silent`) writes to the loaded manifest path with no picker — used by the exit prompt's **Save & leave** branch.
- **Related:** Dirty-flag plumbing in [PR #158](https://github.com/jackal998/photo-manager/pull/158); QA scenario [`qa/scenarios/s12_save_manifest.py`](../qa/scenarios/s12_save_manifest.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Scan dialog — auto-select after scan

- **Entry point:** "Auto select after scan" checkbox under Advanced Settings in [app/views/dialogs/scan_dialog.py](../app/views/dialogs/scan_dialog.py).
- **Trigger:** User expands **Advanced settings** in the Scan dialog and ticks **Auto select after scan**. Setting persists across sessions via `ui.scan_dialog.auto_select_enabled` (defaults `False`).
- **Behaviour:** When enabled, the scan worker promotes the top-scored row in each duplicate group to `action="KEEP"` before writing the manifest, AND (since #393) writes `user_decision=""` (the canonical keep state — empty string) plus `is_locked=1` on that same row so the tree's lock badge gives a visible signal that the keeper was chosen. The Action column reads as empty + 🔒 — same render as picking "Set Action → keep" via the right-click menu, so the two paths converge on identical state ([#425](https://github.com/jackal998/photo-manager/issues/425) fixed the previous literal-`"keep"` write that leaked as raw text). The lock also composes with the [#182](https://github.com/jackal998/photo-manager/issues/182) `LockedRowsConfirmDialog` flow — a subsequent bulk-regex Apply against the keeper surfaces the existing overwrite-confirm UX rather than silently clobbering the auto-pick. Other duplicates retain their classifier action (`MOVE` / `EXACT` / `REVIEW_DUPLICATE`) and stay un-decided (`user_decision=''`) so deletions still require explicit user confirmation. Auto-select picks keepers, never deleters — unless the user also opts into the aggressive sub-option below.
- **Conditions / variants:** Default is off — pre-#212 behaviour is preserved for users who don't opt in. Ranking semantics match the regex dialog's "Top 1 by score" rule (see [Set Action dialog — numeric comparison panel](#set-action-dialog--numeric-comparison-panel)): `score=None` rows excluded, ties break by `source_path` ascending — so manual and auto runs converge on the same keeper. The keep+lock writes happen AFTER `write_manifest` via `core.services.auto_select.apply_auto_select_decisions` (which composes `ManifestRepository.batch_update_decisions` + `batch_update_lock_state`), so the durable on-disk state matches the visible UI state on first manifest load. Pairs with the post-scan visual-selection feature (see [Scan flow — visual selection of KEEP rows after scan](#scan-flow--visual-selection-of-keep-rows-after-scan)).
- **Related:** [PR #232](https://github.com/jackal998/photo-manager/pull/232) (closes [#212](https://github.com/jackal998/photo-manager/issues/212)); [#393](https://github.com/jackal998/photo-manager/issues/393) added the keep+lock writes and aggressive sub-option; QA scenarios [`qa/scenarios/s49_scan_auto_select.py`](../qa/scenarios/s49_scan_auto_select.py) (keep+lock) and [`qa/scenarios/s57_scan_auto_select_aggressive.py`](../qa/scenarios/s57_scan_auto_select_aggressive.py) (aggressive).
- **Last verified:** 2026-05-24 (#393)

---

### Scan dialog — auto-select aggressive ("delete all others")

- **Entry point:** "Also mark all other files for delete" checkbox under Advanced Settings in [app/views/dialogs/scan_dialog.py](../app/views/dialogs/scan_dialog.py), indented beneath the parent "Auto select after scan".
- **Trigger:** User expands **Advanced settings**, ticks **Auto select after scan**, then ticks the indented **Also mark all other files for delete** sub-option. Setting persists across sessions via `ui.scan_dialog.auto_select_aggressive_delete` (defaults `False`).
- **Behaviour:** Destructive-leaning, opt-in. When enabled alongside the parent, every non-keeper row in a scored group receives `user_decision='delete'` in addition to the parent's keep+lock writes on the keeper. The user opens **Execute Action** and sees the full triage pre-populated — keepers locked, non-keepers tagged for deletion — and one click ships the sweep. The aggressive flag does NOT lock non-keepers; locking would block the standard Execute Action confirmation flow. The actual file deletion still goes through the standard Execute Action path (move-to-recycle-bin + the [#182](https://github.com/jackal998/photo-manager/issues/182) lock-confirm dialog if any rows were locked between scan and execute).
- **Conditions / variants:** The sub-checkbox is gated on the parent: disabled when **Auto select after scan** is off. Toggling the parent off after both were on disables the sub-checkbox but preserves its setting value (a parent-on toggle later re-enables it at the previously-chosen state). Non-keepers with `score=None` (Live Photo MOV passengers, all-MOV groups) are EXCLUDED from the aggressive delete tag — they aren't candidates for an explicit delete decision; they inherit their partner's decision at execute time per the existing Live Photo cluster rule. **Since [#517](https://github.com/jackal998/photo-manager/issues/517), non-keepers flagged `match_confidence="low"` are ALSO excluded** — a near-duplicate matched on pHash alone (no independent dHash agreement) is never auto-tagged for deletion, so a shaky perceptual match is left for the user to confirm manually. Byte-identical (SHA) and dHash-confirmed near-dups are `"high"` confidence and remain eligible. **Since [#536](https://github.com/jackal998/photo-manager/issues/536), eligibility is restricted to rows the classifier positively flagged as a duplicate (`action` = `EXACT` or `REVIEW_DUPLICATE`)** — a Ref-tier row (`""` / `KEEP` / `UNDATED`) pulled into a group by the unconditional, filename-based pair edge (same-stem RAW+JPG / HEIC+JPG, Live Photo) renders as the `"—"` passenger and is never auto-tagged for deletion, so a complementary original can't be silently swept.
- **Related:** [#393](https://github.com/jackal998/photo-manager/issues/393); [#517](https://github.com/jackal998/photo-manager/issues/517) (multi-hash confidence gate); [#536](https://github.com/jackal998/photo-manager/issues/536) (Ref-tier "—" passenger exclusion — only `EXACT`/`REVIEW_DUPLICATE` eligible); composes with [Scan dialog — auto-select after scan](#scan-dialog--auto-select-after-scan); QA scenario [`qa/scenarios/s57_scan_auto_select_aggressive.py`](../qa/scenarios/s57_scan_auto_select_aggressive.py).
- **Last verified:** 2026-06-03 (#536 Ref-tier passenger exclusion; #517 confidence gate; #393 base behaviour 2026-05-24)

---

### Scan dialog — stage / throughput / ETA progress (#424)

- **Entry point:** Progress frame above the log box in `ScanDialog` ([app/views/dialogs/scan_dialog.py](../app/views/dialogs/scan_dialog.py)). Three rows: bold stage label, `QProgressBar`, mono "files/sec — ETA" line. Hidden until the first `stage_progress` signal of a scan; **hidden again the moment the scan ends** — every terminal handler (`_on_finished` / `_on_completed_empty` / `_on_failed`) calls `_reset_progress_ui()`, which hides the frame and clears the stage labels (#510). Previously the reset only ran at the TOP of the *next* Start Scan, so an empty-sources or failed scan that leaves the dialog open left the bar + "scanning…" label stuck — making a benign empty result (or a scan aborted by an unreadable entry, #509) look frozen.
- **Trigger:** Fires automatically while a scan is running — `ScanWorker._emit_stage` pushes a typed `stage_progress(stage_name: str, completed: int, total: int, files_per_sec: float)` signal at stage boundaries and inside hot loops, throttled to ≤1 emit/sec (`_STAGE_EMIT_INTERVAL_SECONDS`). The existing string `progress(str)` log stream stays untouched for power users who want the per-line trace.
- **Behaviour:** Six stages — `WALK`, `HASH`, `EXIFTOOL`, `CLASSIFY`, `SCORE`, `WRITE` — passed as canonical strings; receiver localises via `scan_dialog.stage_<name_lower>` translation keys. Streaming stages with a known total (`HASH` per-file, `EXIFTOOL` per-chunk) render a determinate bar with completed/total + a per-second throughput line. Atomic / unknown-total stages (`WALK` now per-file with no upfront total per #448; `CLASSIFY`, `SCORE`, `WRITE`) emit `total=0` so the receiver flips the `QProgressBar` into indeterminate mode (`setRange(0, 0)`). When `total=0` AND the emitted `completed` is non-zero (the WALK case), the label shows `({completed:,})` as a live running count so the user sees the walker progressing rather than staring at a bare "…" for minutes. Throughput is computed worker-side as `(latest_count - oldest_count) / (latest_ts - oldest_ts)` over a 5-second rolling deque (`_THROUGHPUT_WINDOW_SECONDS`); clamps to 0 when the deque has <2 samples or `dt < 0.1s` (so a fast SSD scan doesn't claim a million files/sec on the first emit, and a stall surfaces as "—" rather than a stale rate). ETA is computed receiver-side as `remaining / files_per_sec` and suppressed to "—" until ≥5s have elapsed since the current stage started (the `_ETA_MIN_SAMPLES_SECONDS` gate — matches the worker's deque window so both sides agree on "enough samples"). Stage transitions reset the elapsed-time gate so a freshly-started stage doesn't inherit the prior stage's settled throughput.
- **Conditions / variants:** `WALK` reports a live per-file running count via the walker's `progress_callback` hook (#448) — replaces the pre-#448 per-source-boundary jumps that left a NAS-rooted single-source scan apparently frozen for minutes until `rglob` returned. The total is unknown until each source completes, so the bar stays indeterminate while the label increments. Cancellation latency on `WALK` is now bounded by one `rglob` tick (#491): the walker accepts a `cancel_check` predicate wired to `self.isInterruptionRequested`, polled at the top of the per-file loop, so a title-bar X / Cancel during WALK breaks out within ~one file-iteration (typically <1ms local, <100ms NAS). A new post-WALK gate in `ScanWorker._run_pipeline` then short-circuits the pipeline with the same `failed.emit("Scan cancelled.")` shape used by the HASH / CLASSIFY / SCORE / WRITE gates so `scan_dialog` distinguishes the clean cancel from a red-modal error string. The frame is hidden by the terminal handler (#510) when the scan ends rather than left showing the last stage — on the `finished` path the dialog closes on load anyway, and on the `empty` / `failed` paths (which keep the dialog open) leaving the bar visible read as "still scanning / frozen".
- **Related:** [#424](https://github.com/jackal998/photo-manager/issues/424), [#491](https://github.com/jackal998/photo-manager/issues/491) (WALK cancel checkpoint), [#509](https://github.com/jackal998/photo-manager/issues/509) (WALK no longer aborts the whole scan on an unreadable reparse point — see [Scan walk — skip-on-error traversal](#scan-walk--skip-on-error-traversal)), [#510](https://github.com/jackal998/photo-manager/issues/510) (terminal handlers reset the progress frame); pairs with [#423](https://github.com/jackal998/photo-manager/issues/423) (the sibling Advanced-settings layout fix from the same drive-by feedback batch). Worker contract pinned by `tests/test_scan_worker_progress.py` (throughput math + emit throttle) and `tests/test_scan_worker.py::TestScanWorkerWalkCancel` (#491 walk cancel); dialog formatting pinned by `tests/test_scan_dialog_progress.py` (`_format_throughput` / `_format_eta`). Walker contract pinned by `tests/test_walker.py::TestCancelCheck`. Progress-frame reset on empty scan pinned by [`qa/scenarios/s02_empty_folder.py`](../qa/scenarios/s02_empty_folder.py).
- **Last verified:** 2026-06-01 (#509 / #510)

---

### Scan dialog — collapse Advanced Settings

- **Entry point:** **Advanced settings** collapsible panel in the Scan dialog — [app/views/dialogs/scan_dialog.py](../app/views/dialogs/scan_dialog.py).
- **Trigger:** User clicks the **Advanced settings** disclosure to expand or collapse the panel.
- **Behaviour:** Tech-detail settings (pHash similarity threshold, **dHash confidence threshold (#517)** directly below it, mean-color threshold, auto-select toggles, hash-pool re-calibration) live under a single collapsible panel rather than cluttering the main scan UI. New users see only the source list and the **Start Scan** button by default; power users expand to tune. Each setting shows a **short one-line description** under a bold title, with the **full technical detail in a hover tooltip on the description line only** — the title itself does not pop a tooltip. The tooltip text is wrapped in a fixed-width rich-text cell (`_tip()`) so long descriptions — especially CJK locales (zh_TW), which have no spaces to break on — word-wrap to a tidy ~360px block instead of rendering as one over-long line. The inline descriptions deliberately do **not** word-wrap: a single line can't be vertically clipped (PySide6 6.11 stopped flagging `hasHeightForWidth` on wrapped QLabels, so the earlier full-text-inline + `setWordWrap(True)` layout silently truncated multi-line descriptions to one line). The **dHash** slider (range 1–20, default 10) sets how close the second, independent perceptual hash must be for a pHash near-dup to count as high-confidence — its value threads `scan_dialog → ScanWorker(dhash_threshold=…) → classify`.
- **Conditions / variants:** Expanded/collapsed state is not persisted today — opens collapsed every time. The manual **Hash workers** spinbox was removed (see [hash workers](#scan-dialog--hash-workers-nas-aware-auto-no-ui-control)) — the count is auto-picked.
- **Related:** [PR #179](https://github.com/jackal998/photo-manager/pull/179) collapsed grouping parameters; [#163](https://github.com/jackal998/photo-manager/issues/163) drove the original consolidation. Short-desc + hover-tooltip design first shipped in [#520](https://github.com/jackal998/photo-manager/pull/520), inlined in [#521](https://github.com/jackal998/photo-manager/pull/521), then restored after PySide6 6.11 clipped the wrapped inline text.
- **Last verified:** 2026-06-02 (restored concise-label + hover-tooltip design)

---

### Scan dialog — folder list (no priority arrows)

- **Entry point:** Source list widget in the Scan dialog (`_SourceListWidget`) — [app/views/dialogs/scan_dialog.py](../app/views/dialogs/scan_dialog.py).
- **Trigger:** Always — applies to every interaction with the source list.
- **Behaviour:** Clean 3-column list (path / Recursive checkbox / × remove). Display is sorted alphabetically by path (case-insensitive). The underlying entries list stays insertion-ordered so the duplicate-path check and the scanner's source-priority inference (top of scan = highest priority) still work.
- **Conditions / variants:** Replaces the pre-#213 5-column table that had ↑/↓ priority arrows. Per-row callbacks receive the entries-index (not the display row) so clicking row 0 after the alphabetical sort still targets the alphabetically-first entry. ⚠ The README's Step 1 wording still mentions the removed arrows — tracked in [#264](https://github.com/jackal998/photo-manager/issues/264).
- **Related:** [PR #223](https://github.com/jackal998/photo-manager/pull/223) (closes [#213](https://github.com/jackal998/photo-manager/issues/213)); QA scenario [`qa/scenarios/s17_scan_dialog_widgets.py`](../qa/scenarios/s17_scan_dialog_widgets.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Scan dialog — exiftool workers (setting-only)

- **Entry point:** ``scan.exif_workers`` key in ``settings.json`` — read in [app/views/dialogs/scan_dialog.py](../app/views/dialogs/scan_dialog.py) at scan start and passed to ``ScanWorker``.
- **Trigger:** Always — value is consumed on every Start Scan.
- **Behaviour:** Sets how many parallel ``ExiftoolProcess`` instances the EXIF stage spawns. Default 2; clamped to ``[1, min(4, cpu_count() // 2)]`` at ``ScanWorker`` construction. exiftool itself is single-threaded within one ``-stay_open`` instance; running N instances in parallel scales near-linearly up to ~4 on a modern CPU. The N consumer threads all pull from the same producer-consumer queue established in [#450](https://github.com/jackal998/photo-manager/issues/450).
- **Conditions / variants:** No UI — operators raise the value via ``settings.json`` only. This is intentionally a *safe rollout knob* per the issue body, not a power-user control. Cancel posts one sentinel per consumer + joins all with a 5s timeout — no zombie exiftool processes.
- **Related:** [#451](https://github.com/jackal998/photo-manager/issues/451); worker contract pinned by ``tests/test_scan_worker.py::TestScanWorkerExifWorkers``.
- **Last verified:** 2026-05-28 (#451)

---

### Scan dialog — read-knee autotune opt-out

- **Entry point:** "Auto-tune reader concurrency (experimental)" checkbox under Advanced Settings in [app/views/dialogs/scan_dialog.py](../app/views/dialogs/scan_dialog.py).
- **Trigger:** Autotune runs by **default** on every scan — no action needed. A user who wants the old static reader count expands **Advanced settings** in the Scan dialog and **unticks** **Auto-tune reader concurrency**. Setting persists across sessions via `ui.scan_dialog.autotune_read_knee` (defaults `True` since #551 Phase 4).
- **Behaviour:** The scan measures each physical device's read-concurrency knee at scan start instead of using the static `NAS=8 / spinning-HDD=1 / else min(4, cpu)` guess: it ramps that device's reader threads `1→2→4→8`, measures files/s per level, and settles on the concurrency where doubling stops paying off. The measured knee is cached per `device_key` in `scan.read_knee_cache` and reused on later scans of that device, so the ramp runs at most once per device (learn once, reuse forever). Spinning HDDs stay pinned at a single reader. Reader concurrency never changes which duplicates are found — only read speed — because `idx` is threaded through the read→compute pipeline so completion order can't reach `group_id` ([#526](https://github.com/jackal998/photo-manager/issues/526)/[#538](https://github.com/jackal998/photo-manager/issues/538) lex-min determinism).
- **Conditions / variants:** Default **on** (opt-out, experimental). Scans below `_RAMP_MIN_SCAN_FILES` eligible image files (1584 — the conservative N=8 floor that bounds the worst-case first-scan sub-MAX read tax to 12.5%), and confirmed spinning HDDs, fall open to the static reader count (no ramp). A per-device measurement failure also falls open to the static value, and the ramp is monotone-up (it can only under-utilise, never over-subscribe), so default-on never reads slower than the static guess beyond the bounded first-scan ramp tax — and never changes results. The case it helps is hardware the static guess mis-fits (a low-channel NAS that 8 over-subscribes, an SSD under-served by `min(4, cpu)`); on hardware the static guess already fits, the cached knee equals the static value and it is a no-op.
- **Related:** [#551](https://github.com/jackal998/photo-manager/issues/551) (Phase 4 — flipped the default to ON/opt-out; Phase 3 added the dialog control; Phases 1–2 landed the pure read-knee logic and the in-pipeline ramp); QA scenario [`qa/scenarios/s66_autotune_read_knee.py`](../qa/scenarios/s66_autotune_read_knee.py) (autotune at default-ON still yields the correct grouping); GATE-1 (`tests/test_scan_worker.py` — the real ramp finds the knee on a synthetic cliff) + GATE-2 (`tests/integration/test_autotune_ab.py` — the no-regression A/B).
- **Last verified:** 2026-06-06 (#551 Phase 4)

---

### Scan dialog — hash pool mode

- **Entry point:** ``scan.hash_pool`` key in ``settings.json`` (power-user escape hatch, default ``"auto"``) — read in [app/views/dialogs/scan_dialog.py](../app/views/dialogs/scan_dialog.py) at scan start (``_resolve_hash_pool``) and passed to ``ScanWorker``. #560 removed the user-facing "Re-calibrate hash pool on scan" checkbox: calibration is now **always-on** (the ``"auto"`` default), non-user-facing.
- **Trigger:** Always — value is consumed on every Start Scan.
- **Behaviour:** Selects the executor for the HASH stage. ``"thread"`` (default) runs the per-file hash compute across a ``ThreadPoolExecutor`` in-process (pre-PR2 behaviour). ``"process"`` runs the picklable ``run_hash_for_record`` across a ``ProcessPoolExecutor`` so CPU-bound hashing escapes the GIL across cores. ``"auto"`` times a sample (up to 96 files) of the *real* scan data through both executors at scan start, **projects each to the actual file count** (``thread_per_file × N`` vs ``spawn_cost + process_per_file × N``), and runs the lower-projected one — so process's one-time spawn cost is charged once against the whole run, not against the sample. Process's per-file rate and spawn cost are measured separately via a cold/warm two-batch split; the projected totals + components are logged. The hash log line reports the active mode (``pool=thread`` / ``pool=process``); ``"auto"`` is resolved to one of those before the stage runs, so the line never shows ``auto``. Outcome routing (corrupt-file skips, EXIF-queue handoff) is identical in both modes — in process mode the parent drains completed futures and routes, since the worker subprocess can't touch the thread-only cancel flag / queue.
- **Fingerprint cache (PR3b):** the first ``"auto"`` scan caches its measured rates (``thread_per_file`` / ``process_per_file`` / ``spawn``) in ``settings.json`` under ``scan.hash_pool_cache``, keyed by a fingerprint of ``cpu_count`` + the sorted source paths/recursive flags. A later scan of the same library on the same machine reuses the cached rates — re-projecting them to the *current* file count without re-running the ~2s measurement, so the pick still adapts if the file count changed. A different machine or folder set misses the cache and re-measures. The cache write is flushed to disk the moment calibration finishes (before the long hash pass), so it survives a mid-scan cancel.
- **Always-on calibration (#560):** ``"auto"`` is the default mode and there is **no user-facing toggle** — the per-scan calibration cost is low, so every scan either **reuses the cached rates** (cache hit) or **calibrates silently** (cache miss) before the hash pass. The earlier "Re-calibrate hash pool on scan" checkbox and the cache-miss "Calibrate now / Use thread (safe)" modal were both removed. Silent-on-miss is safe because the #554/#609 multi-device+NAS shortcut short-circuits the one risky case (mixed HDD+NAS) to process pool inside the worker, and a fresh sample is cheap. ``scan.hash_pool`` = ``"thread"`` / ``"process"`` in settings.json still overrides ``"auto"`` verbatim (no fingerprint, no calibration).
- **Conditions / variants:** ``"auto"`` is the default (#560) — calibration decides thread-vs-process on every scan, so the Windows ``spawn`` cost (re-importing PIL/rawpy per worker, ~150–300ms each) is only paid when it actually wins. Unknown ``scan.hash_pool`` values fall back to ``"thread"`` at ``ScanWorker`` construction. ``"auto"`` calibration is skipped (→ ``thread``) when fewer than 24 files are queued (and there's no cached entry), since the spawn/pickle overhead swamps the signal on tiny scans. Cancellation in process mode stops submitting new work and cancels queued files (``cancel_futures=True``). #549(b): the process branch uses an explicit ``try/finally`` with ``shutdown(wait=False)`` rather than ``with ProcessPoolExecutor()`` — the latter's ``__exit__`` runs ``shutdown(wait=True)``, which on cancel would block the dialog's 3s teardown budget until every in-flight ``read_bytes()`` finished (a slow-HDD/large-file stall after the user confirmed exit). With ``wait=False`` the worker returns promptly and the still-running children are reaped by the #549(a) job assignment on parent exit — same ~1-file user-visible latency as thread mode.
- **Multi-device + NAS shortcut (#554 / #609 — direction inverted 2026-06-08):** when ``"auto"`` is active and the scan spans **two or more physical devices with at least one remote (NAS)**, the flat thread-vs-process calibration is **skipped** and ``"process"`` is chosen unconditionally. #554 originally returned ``"thread"`` here on the bet that per-device thread I/O overlap (HDD 1 reader + NAS 8 readers) would dominate. The 2026-06-08 remediation measured the bet on real D+J workload with per-second instrumentation (``scripts/probe_pipeline_timeline.py``) and found the opposite: the shared ``compute_pool`` is GIL-bound, effective parallelism plateaus at ~2-4 slots, and mixed big+small file interleave (NAS small JPEGs queueing behind D: 100-130 MB ProRAW DNGs in the shared ``hash_in_q``) triggers 70-second stretches with zero compute completions. The per-device read overlap is real but downstream-gated by the GIL-bound compute pool, so it doesn't pay back. D+J 3400-file apples-to-apples: thread → 601s timeout (89% done, projects to ~678s full) vs process → 421s complete = **1.6× faster end-to-end**. The shortcut now picks process for this topology. Single-device and all-local scans are unaffected — calibration still runs and thread can legitimately win (no GIL contention, spawn cost > GIL escape gain). Decision record: ``docs/audits/scanner-perf-mixed-workload-process-pool-2026-06-08.md``.
- **Related:** [#486](https://github.com/jackal998/photo-manager/issues/486) (PR1 extracted the picklable compute path); worker contract pinned by ``tests/test_scan_worker.py::TestHashPoolSetting`` + ``TestHashPoolCalibration``; cache persistence by ``test_store_hash_pool_rates_round_trips_through_settings``; the always-auto resolution (default auto, cache hit/miss, thread/process overrides) by ``tests/test_scan_dialog.py::TestResolveHashPool``. Real cross-process spawn is validated by real-world runs, not CI.
- **Last verified:** 2026-06-08 (#609 — multi-device+NAS shortcut direction inverted to "process" based on real-rig timeline measurement; previous "thread" direction was 1.6× slower on D+J 3400 files)

---

### Scan dialog — hash workers (NAS-aware auto, no UI control)

- **Entry point:** None — the **Hash workers** spinbox was removed from the Advanced settings panel (it duplicated automation that already runs). The count comes from [`scanner/workers.py`](../scanner/workers.py) `default_hash_workers`.
- **Trigger:** Computed fresh from the current sources at **Start Scan** and passed to [`ScanWorker`](../app/views/workers/scan_worker.py); no manual override.
- **Behaviour (#548 — per-device concurrent pools):** In **thread** mode the HASH stage no longer runs one flat `ThreadPoolExecutor` over all sources concatenated in source order. Records are partitioned by physical device (`os.path.splitdrive` key, e.g. `D:` vs `J:`), and one `ThreadPoolExecutor` runs **per device, all concurrently**, each with its own worker count from [`scanner/workers.py`](../scanner/workers.py) `hash_workers_for_root`: NAS (Windows network drive) → 8, local **spinning HDD** → 1 (single sequential reader, seek-minimising — the disk is the bottleneck at ~97% active / 38% CPU, and one reader keeps the head moving sequentially without inter-file seek bouncing; two readers still bounce the head between two concurrently-open files), other local (SSD / NVMe / unknown) → `min(4, os.cpu_count())`. On a mixed scan (local HDD + NAS) the NAS-latency-bound reads overlap the HDD-seek-bound reads instead of queueing behind them, and the HDD itself is no longer over-subscribed. The worker emits one log line listing the per-device count and file count (e.g. `D:=1×N f, J:=8×M f`). The HASH **pool type** (thread vs process) is a separate axis chosen by the [#486 re-calibration](#scan-dialog--hash-pool-mode); the auto **calibration sample** is now stratified across devices so the thread-vs-process pick is fair on a mixed scan. **Process** mode keeps the single flat pool (the seek-thrash win is I/O-bound and lives in the thread path; per-device process pools are a follow-on). The legacy single auto count (8 if any source remote, else `min(4, cpu)`) is still passed to the worker for process mode + calibration sizing. **NAS server collapsing (#565):** `device_key` now maps all Windows drive letters that resolve to the same physical NAS server into **one `\\\\SERVER` bucket** — e.g. H: and J: on `\\\\LinXiaoYun` both become `\\\\LINXIAOYUN` and share a single 8-reader pool. Before #565 each mapped letter produced its own device bucket (H: = 8 readers, J: = 8 readers = 16 concurrent SMB reads hammering one NAS box — over-subscription). The collapsing uses `WNetGetConnectionW` via an injectable resolver, memoized per letter per process. Native UNC source paths on the same server (e.g. `\\\\SRV\\share1` and `\\\\SRV\\share2`) are also collapsed to `\\\\SRV` without a resolver call. `is_remote_drive` returns True for bare `\\\\SERVER` keys so `hash_workers_for_root` still yields `_NAS_WORKERS` (8) for the collapsed bucket. Fail-open: any resolver error, non-Windows, or unresolvable letter → falls back to the per-letter key (H: and J: stay distinct), never crashes.
- **Read/compute pipeline split (#566):** in **thread** mode the HASH stage no longer fuses I/O and CPU in one per-file task. Each file flows through a two-stage pipeline: per-device **reader** pools (the #548 counts — HDD=1, NAS=8) each issue one `read_bytes()` (`read_for_record`) into a bounded `queue.Queue(maxsize=128)`, and a single `cpu_count`-wide **compute** pool drains it (`compute_from_bytes` — SHA + decode + pHash/dHash, the same 7-field recipe). This decouples disk-read latency from CPU decode: a reader blocked on an HDD seek no longer stalls a core that could be decoding, and the NAS readers keep the buffer full while the CPU saturates. On a real mixed HDD+NAS scan this measured **~2.3× faster with the throughput sawtooth (忽高忽低) roughly halved** — the fused path serialised read+decode within each task, so utilisation oscillated. Invariants preserved: the single-read guarantee (#446) holds (`read_for_record` reads once; `compute_from_bytes` works from those bytes, RAW via `rawpy.open_buffer(data)`; video/gif/skip carry `data=None` and stream SHA from disk so the #453 RAM ceiling holds); `idx` is threaded through both stages so results scatter to `hash_results[idx]` in walk order, keeping `classify()` determinism (lex-min `group_id`, the rescore key) unchanged. Cancellation composes with #561 — a cooperative `put(timeout=)` + `cancel_flag` recheck plus `_drain_queue_nowait` release any reader wedged on a full queue before the compute sentinel and the existing exif kill/sentinels run. **Process** mode + calibration keep the fused `run_hash_for_record`; the split lives only in the thread path. **#570 — RAM backpressure made real:** `ThreadPoolExecutor.submit()` never blocks, so the `hash_in_q` maxsize alone could not bound memory (the dispatcher drained it instantly into the pool's unbounded work queue, where in-flight image bytes could pile up without limit if compute fell behind — a fast-disk/slow-decode OOM risk). A `compute_inflight` semaphore now caps submitted-but-unfinished compute tasks at the queue size, keeping peak in-flight bytes bounded. **#569 — JPEG decode shrink-on-load:** `compute_from_bytes` calls `Image.draft("RGB",(256,256))` before `convert()`, decoding JPEG/MPO at ~1/4 resolution (a no-op on PNG/WebP/HEIC/RAW) — ~4× faster JPEG decode. pHash/dHash/mean-color all downsample far below 256px so grouping is unchanged (A/B on 597 real JPEGs: 0 over the threshold, 0 group-membership flips; true `px_w`/`px_h` are read before `draft()` mutates the size). The win lands on **warm re-scans (~5–6×) and compute-bound hardware**; a read-bound first scan is unchanged (its bottleneck is disk/NAS read, not decode). Bumps `HASH_RECIPE_VERSION` → the calibration cache re-measures once after upgrade.
- **Conditions / variants:** NAS detection uses Windows `GetDriveTypeW`; the local spinning-disk cap uses the `IncursSeekPenalty` storage IOCTL (`disk_incurs_seek_penalty`, behind an injectable seam). Both fail open: off Windows, or on any detection failure / non-drive-letter root, the device falls through to the SSD-safe `min(4, cpu)` — so a single-device SSD-only scan is unchanged (one pool, same count as before, zero regression) and a misdetected device is never throttled below today's default. Only a **confirmed** spinning HDD gets the single-reader cap. The #449 spinbox removal still holds and any legacy `scan.workers` setting is ignored (orphaned, harmless). Runtime per-device concurrency *calibration* (measuring each device's knee instead of classifying it) was considered and deferred to [#551](https://github.com/jackal998/photo-manager/issues/551), gated on a named failure mode.
- **Related:** [#449](https://github.com/jackal998/photo-manager/issues/449) (introduced the spinbox, since removed); [#548](https://github.com/jackal998/photo-manager/issues/548) (per-device concurrent pools); [#452](https://github.com/jackal998/photo-manager/issues/452) (the WALK stage's sibling per-source parallelism); [#566](https://github.com/jackal998/photo-manager/issues/566) (read/compute pipeline split — ~2.3× on mixed HDD+NAS). Helper tests in [`tests/test_scanner_workers.py`](../tests/test_scanner_workers.py); per-device partitioning + ordering + cancellation + the read/compute split's deadlock-safety and idx-order determinism gate tests in [`tests/test_scan_worker.py`](../tests/test_scan_worker.py).
- **Last verified:** 2026-06-05 (#565 NAS server collapsing — H: + J: on one Synology = one 8-reader pool, not two; #566 read/compute pipeline split — decouples disk-read latency from CPU decode, ~2.3× faster + sawtooth halved on mixed HDD+NAS)

---

### Scan dialog — multi-source scan

- **Entry point:** Source list widget in the Scan dialog plus the **+ Add Selected Folder** button — [app/views/dialogs/scan_dialog.py](../app/views/dialogs/scan_dialog.py).
- **Trigger:** User browses the embedded folder tree, double-clicks a folder or clicks **+ Add Selected Folder** to add it to the source list. Repeats for unlimited folders. Toggles **Recursive** per source as needed.
- **Behaviour:** Walks every source folder, hashes every file, and writes one consolidated `migration_manifest.sqlite` covering the whole set. Recursive sources walk subdirectories; non-recursive scan only the immediate folder. Layout is two-column (folder tree on the left, source list also on the left below it — see [PR #160](https://github.com/jackal998/photo-manager/pull/160)).
- **Conditions / variants:** Source paths persist to `settings.json` (`sources.list`) between sessions. The Scan dialog accepts invalid / missing paths but surfaces a validation toast on Start Scan ([`qa/scenarios/s38_scan_dialog_invalid_path.py`](../qa/scenarios/s38_scan_dialog_invalid_path.py)).
- **Related:** [PR #17](https://github.com/jackal998/photo-manager/pull/17) (dynamic multi-source scan); [PR #160](https://github.com/jackal998/photo-manager/pull/160) (two-column layout); QA scenarios [`qa/scenarios/s10_multi_source.py`](../qa/scenarios/s10_multi_source.py), [`qa/scenarios/s17_scan_dialog_widgets.py`](../qa/scenarios/s17_scan_dialog_widgets.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Scan flow — manifest summary in progress log

- **Entry point:** [scanner/manifest.py:92](../scanner/manifest.py#L92) `print_summary()` — output captured by [app/views/workers/scan_worker.py](../app/views/workers/scan_worker.py) `_emit` and routed to the scan-dialog progress log.
- **Trigger:** Scan finishes and the worker emits the summary block.
- **Behaviour:** The progress log prints a "Migration Manifest Summary" table with one row per action bucket (kept, exact duplicates, near-duplicates (review), no shot date), each row showing the count and percentage. Since #433 dropped the `MOVE` action, unique non-duplicate files carry the empty action (`""`) and roll up into the trailing `other` line rather than a dedicated `dated files` bucket. The headline `Indexed in manifest` line counts manifest rows; `Skipped (unreadable)` reconciles the headline against the per-step "Hashed N/M" log line earlier. A second "Group Summary" block follows with group count, files-in-groups, and isolated-file counts.
- **Conditions / variants:** Action-bucket row labels are localised via [`translations/en.yml`](../translations/en.yml) / [`translations/zh_TW.yml`](../translations/zh_TW.yml) `manifest_summary:` keys — raw internal action strings (`KEEP` / `EXACT` / `REVIEW_DUPLICATE` / `UNDATED`) no longer leak into the log. The legacy `MOVE` bucket (and its `manifest_summary.move` label) were removed in #433 along with the photo-transfer `dest_path` contract. The `Skipped (unreadable)` row is omitted when the count is zero.
- **Related:** [PR #310](https://github.com/jackal998/photo-manager/pull/310) (fix for [#242](https://github.com/jackal998/photo-manager/issues/242)); also [#87](https://github.com/jackal998/photo-manager/issues/87) (headline-label + skipped reconciliation); [#433](https://github.com/jackal998/photo-manager/issues/433) (MOVE bucket + `dest_path` column removal).
- **Last verified:** 2026-06-01 (#433 — MOVE / dest_path contract removal)

---

### Scan walk — skip-on-error traversal

- **Entry point:** `_iter_tree` in [scanner/walker.py](../scanner/walker.py) — the directory traversal primitive behind the WALK stage.
- **Trigger:** Fires automatically during every scan when the walker descends into a source tree that contains an inaccessible reparse point (a broken symlink / junction, e.g. `node_modules\.bin\acorn`).
- **Behaviour:** The walker previously used `root.rglob("*")` / `root.glob("*")`. `rglob` is a generator: when recursive descent reached an unreadable reparse point, `os.scandir` raised `OSError` [WinError 1920] *from inside* the generator, the exception propagated out of the walk loop, `ScanWorker.run()` emitted `failed`, and the **entire scan aborted on the first bad entry** — even though thousands of good files remained, and the per-path guards (symlink / skip-dir / extension filters) never got a chance to run. The traversal is now a manual `os.scandir` stack: each `scandir` call and each `entry.is_dir()` probe is wrapped in `try/except OSError`, so one inaccessible entry is logged once (a `WARNING` naming the path) and skipped while the walk keeps going. All the per-path guards (the #491 cancel checkpoint, the #169 Win32-unsafe-name warning, the symlink / skip-directory / `SKIP_FILENAMES` / `MEDIA_EXTENSIONS` filters, the #448 progress callback, and the `limit` cutoff) are re-homed unchanged onto the new traversal.
- **Conditions / variants:** A symlinked/junctioned *directory* is no longer descended into at all (`entry.is_dir(follow_symlinks=False)`), so files buried under it are skipped earlier — same end result the per-file `_traverses_symlink` guard produced before, one step sooner. An unreadable *root* (or a subdirectory that becomes unreadable mid-descent) is logged once and yields its partial result rather than aborting.
- **Related:** [#509](https://github.com/jackal998/photo-manager/issues/509); the stuck-progress-bar symptom this looked like is fixed alongside in [#510](https://github.com/jackal998/photo-manager/issues/510) (see [Scan dialog — stage / throughput / ETA progress](#scan-dialog--stage--throughput--eta-progress-424)). Walker contract pinned by `tests/test_walker.py::TestScanAbortResilience`.
- **Last verified:** 2026-06-01 (#509)

---

### Scan flow — rescan confirm

- **Entry point:** Confirm dialog fired before a re-scan replaces the currently loaded manifest.
- **Trigger:** User starts a scan while a manifest with pending decisions is already loaded.
- **Behaviour:** A confirm dialog asks the user to acknowledge that re-scanning will replace the loaded manifest. If the scan output path matches the loaded manifest path, those decisions will be permanently lost on disk; otherwise the previous manifest is preserved on disk but no longer visible in this window.
- **Conditions / variants:** Cancel keeps the loaded manifest intact and aborts the scan. Confirm proceeds with the scan.
- **Related:** QA scenario [`qa/scenarios/s27_rescan_confirm.py`](../qa/scenarios/s27_rescan_confirm.py).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Scan flow — visual selection of KEEP rows after scan

- **Entry point:** Post-scan tree-selection hook in `MainWindow` after the manifest loads via **Close & Load**.
- **Trigger:** Scan with auto-select enabled finishes and the user clicks **Close & Load**.
- **Behaviour:** The rows that auto-select marked `KEEP` are visually highlighted in the result tree so the user can see at a glance which keepers the scorer picked — eliminating the "what just happened?" moment after a silent auto-select.
- **Conditions / variants:** Only fires when auto-select after scan is enabled (see [Scan dialog — auto-select after scan](#scan-dialog--auto-select-after-scan)). Without auto-select, no rows are pre-marked, so nothing to highlight.
- **Related:** [PR #255](https://github.com/jackal998/photo-manager/pull/255) (fix for [#239](https://github.com/jackal998/photo-manager/issues/239)).
- **Last verified:** 2026-05-21 (sweep for [#326](https://github.com/jackal998/photo-manager/issues/326))

---

### Set Action dialog — dual-section Simple + Regex view

- **Entry point:** Vertically-stacked Simple section (op combo + text edit) and Regex section (line edit + cheatsheet chips) in the Set Action dialog — [app/views/dialogs/select_dialog.py](../app/views/dialogs/select_dialog.py).
- **Trigger:** User opens **Action > Set Action by Field…** (or right-clicks a row → **Set Action by Field…**). Both sections render side-by-side (Simple on top, Regex below) every time.
- **Behaviour:** **Simple section** carries op combo + text edit ("Find rows where it [contains | starts with | ends with | exactly matches] [text]"). Typing in Simple writes through to the Regex line edit immediately via `re.escape` (so plain user text stays literal — no need to know `()/.` are special). **Regex section** carries the raw pattern line edit + a cheatsheet chip row (`.*`, `\d`, `\w`, `^`, `$`, `\.`, `[abc]`) for power users. `self.regex.text()` is the single source of truth — whichever section the user is editing, the other one reflects the current state (Regex always shows the canonical pattern; Simple shows the parsed `(op, plain_text)` decomposition when the pattern is Simple-representable, or stays at its prior state when not). The **Recent** button (labelled with text plus a theme-aware `QStyle.SP_ArrowDown` dropdown indicator — Wave 8 C16) sits in its own row above the Field combo; picking from Recent calls `_apply_recent_pattern` which sets `self.regex` — the textChanged → `_reverse_parse_to_simple` wiring then auto-syncs the Simple inputs when applicable. Recent entries are stored as `(field, pattern)` tuples (capped at 10, deduped by pair, persisted under `ui.action_dialog.recent_patterns`) and the menu only shows entries matching the active field (or legacy `None`-field entries that apply to any field). Invalid regex patterns are filtered out of Recent at write time so the dropdown never carries unparseable patterns.
- **Conditions / variants:** Selected field (`ui.action_dialog.{context_id}.field`) and Simple operator (`ui.action_dialog.{context_id}.simple_op`) persist per context (`context_id` is `"main"` from the main-window entry point and `"execute"` from the Execute Action dialog) so the two surfaces carry independent preferences; field persistence is overridden by an `initial_field` argument (column-click). Without a `match_fn` (the "C1" no-live-preview entry point), the **Simple section renders as an informational placeholder**: op combo + text edit visible but disabled, with a small italic note above them ("Write-through preview unavailable — Simple inputs are read-only on this entry point"). The Regex section remains fully interactive on this branch. Numeric-capable field choices (Size, Score, Resolution, Group Count, Similarity, Creation Date, Shot Date) pre-empt both sections with the dedicated numeric panel ([Set Action dialog — numeric comparison panel](#set-action-dialog--numeric-comparison-panel)). The match counter sits in a dedicated row visible regardless of which section the user is in. After Apply the match counter briefly flashes "Applied to N rows" (Wave 9b-trim B9) so the user gets in-dialog confirmation; the downstream receiver also emits "Decision set to '<decision>'" on the main-window status bar (#316/#318). The dialog stays open after Apply (intentional — supports batch-apply / iterative regex exploration). The "delete" action surfaces a confirmation modal ([`DeleteRegexConfirmDialog`](../app/views/dialogs/delete_regex_confirm_dialog.py), Wave 10 D3) before emitting — body shows the matched count + human-readable pattern summary (Simple-style "File Name contains 'IMG'" when the current regex is Simple-representable, raw regex otherwise). Wording is decision-explicit (#415): confirm button reads "Mark N files for deletion" and the body names Execute Action as the actual mover ("Files will be sent to the Recycle Bin when you run Execute Action") — Apply only writes the `user_decision='delete'` rows; no files move from this dialog. The action-combo label reads "Set action for each match:" (Wave 9b-trim B12 introduced the per-row-scope phrasing; #407 briefly relabelled it to "Set status…" but #416 reverted that since "action" is the canonical user-facing noun across sibling labels — `action_dialog.title`, `execute_dialog.set_action_menu`, `context_menu.set_action`) so the per-row scope is explicit. When a row was highlighted at dialog open, the pre-fill seeds the Simple inputs as `("contains", value)` — matches the documented default Simple op. A custom regex typed by the user is preserved across field-combo changes; only the auto-default refreshes. Keyboard polish (Wave 9a from #350): both regex and Simple line edits expose a native `×` clear button (D2); `[abc]` cheatsheet chip selects the inner `abc` after insert so the user's next keystroke replaces them (D6); focus lands on Simple text on dialog open when `match_fn` is supplied, on the regex line edit otherwise (B14); `Ctrl+Enter` triggers Apply from any focused input (D9); `Alt`-letter mnemonics on action buttons — `Alt+A` Apply, `Alt+R` Recent, `Alt+W` reset Window size (D10). (`Alt+C` Close dropped in #391; `Alt+S` Switch-to-Regex dropped in #396.)
- **Related:** [PR #167](https://github.com/jackal998/photo-manager/pull/167) (Phase B — Simple/Beginner mode, cheatsheet, recent patterns, match highlight); [PR #168](https://github.com/jackal998/photo-manager/pull/168) (Phase C — Simple rename + 3-col cheatsheet); [#396](https://github.com/jackal998/photo-manager/issues/396) dropped the Simple/Regex mode toggle in favour of the dual-section view; [#382](https://github.com/jackal998/photo-manager/issues/382) added the opt-in `ui.action_dialog.window_modality` setting; QA scenario [`qa/scenarios/s31_simple_mode_regex.py`](../qa/scenarios/s31_simple_mode_regex.py).
- **Modality (opt-in, #382):** The dialog defaults to `Qt.ApplicationModal` (the documented Windows-correct posture since #139/#151 — blocks all clicks on the main window via `WS_DISABLED`). Setting `ui.action_dialog.window_modality` to `"window"` switches to `Qt.WindowModal`, allowing the user to interact with OTHER top-level windows (e.g. a separate viewer) while the dialog is open. Windows caveat: `WindowModal` does NOT set `WS_DISABLED` on the parent the way `ApplicationModal` does (PR #151 empirical finding), so the main window's menu bar stays clickable when this opt-in is on — this is exactly the cross-window unblock the user asked for, but surfaces clicks the default suppresses. Unrecognised values fall back to `ApplicationModal` (fail-safe; does NOT silently downgrade to `NonModal`).
- **Last verified:** 2026-05-26 (#416 reverted action-combo label from "Set status…" back to "Set action…")

---

### Set Action dialog — live preview + validation

- **Entry point:** Right-side `QListWidget` + match counter + validation icon in the Set Action dialog — [app/views/dialogs/select_dialog.py](../app/views/dialogs/select_dialog.py). Match closure built by `build_match_fn` in [app/views/handlers/file_operations.py](../app/views/handlers/file_operations.py).
- **Trigger:** User types in the Simple section's text input or the Regex section's pattern input (both sections always visible after #396). Debounced 150 ms after the last keystroke.
- **Behaviour:** Preview shows up to 50 matched values with a "…and N more" footer — for File Name regex the value is the basename, for other fields (Folder, Score, Date, Lock, Action, Resolution) it's the matched-field string itself so the bold-span highlight lands on the actual regex hit (A2 from #347, Wave 4). Match counter shows "N of M match". (The "Test against:" playground that shipped in Wave 10 D4 was removed in #395 — the live preview against the loaded manifest covers the same iterative-tuning need with real data, and the second input added clutter without payoff.) Live validation surfaces a theme-aware system icon (`QStyle.SP_DialogApplyButton` on valid, Wave 8 C8) with a hover toolTip mirroring its screen-reader accessibleName (Wave 9a B11 — "Regex valid" / "Threshold valid" or the specific failure for the numeric threshold) and, on invalid input, a bold error label ("Invalid regex: unmatched ')' at position 7") the moment `re.compile` fails. The validation icon is hidden when the error label is visible (Wave 8 B3) so the regex row doesn't crowd icon + error + Recent button on the same line. The closure short-circuits on invalid regex so the preview never iterates the record set with a broken pattern. The same `build_match_fn` closure is shared by both Apply and preview so what you see is byte-for-byte what `set_decision_by_regex` will match. **The Apply button is always enabled** (#397 dropped the pre-Apply gate); empty / invalid input surfaces as a receiver-side `QMessageBox` ("No matches" for empty / no-match, "Invalid Regex" for `re.compile` failures) at click-time — visible feedback beats a silently-disabled button. Empty pattern is defended specifically by `file_operations.set_decision_by_regex`: an early-reject converts it to the "No matches" UX so a destructive `delete` decision cannot tag every row via `re.search("", anything)` being truthy. Validator and receiver share `re.IGNORECASE`, so what the system icon validates is what Apply will match.
- **Conditions / variants:** Right-click parity — the Execute Action dialog's tree context menu and the main window's multi-selection right-click both offer **Set Action by Field…**, opening the same dialog with the same live preview. Both routes also emit the same `Decision set to '<decision>'` status-bar confirmation on Apply ([#316](https://github.com/jackal998/photo-manager/issues/316)); the parity was extended in [#318](https://github.com/jackal998/photo-manager/issues/318) to every other decision-changing path inside the Execute Action dialog — single-row right-click lock/unlock, single-row right-click decision-set, multi-row remove-from-list, and bulk-regex lock — so users get the same status-bar feedback regardless of entry point.
- **Related:** [PR #162](https://github.com/jackal998/photo-manager/pull/162) (Phase A — live preview, validation, right-click parity); [#316](https://github.com/jackal998/photo-manager/issues/316) + [#318](https://github.com/jackal998/photo-manager/issues/318) (status-bar parity for every Execute Action dialog decision path); QA scenarios [`qa/scenarios/s14_action_by_regex.py`](../qa/scenarios/s14_action_by_regex.py), [`qa/scenarios/s30_execute_dialog_regex_right_click.py`](../qa/scenarios/s30_execute_dialog_regex_right_click.py).
- **Last verified:** 2026-05-24 (#395 removed the test-against playground; #397 removed the pre-Apply gate)

---

### Set Action dialog — numeric comparison panel

- **Entry point:** Numeric panel that replaces the regex input when a numeric-capable field is chosen — [app/views/dialogs/select_dialog.py](../app/views/dialogs/select_dialog.py).
- **Trigger:** User opens the Set Action dialog from **either route** — main-window Action menu OR Execute Action dialog's Select by Field/Regex button — and picks one of the numeric-capable fields: Size (Bytes), Group Count, Similarity, Score, Creation Date, Shot Date. Both routes pass `groups` to the dialog (#237 fixed the main-window route; the Execute route always has).
- **Behaviour:** Two modes — **Threshold comparison** (`>`, `>=`, `<`, `<=`, `==`, `!=`) against a typed value (date fields accept ISO `YYYY-MM-DD`); **Top N / Bottom N within group** ranked by the selected field with stable `file_path` tiebreak so the same configuration always selects the same rows. Both modes ride through the existing `setActionRequested(field, pattern, decision)` signal as encoded pseudo-patterns (`__cmp__:OP:VALUE`, `__top_n__:N:asc|desc`). Both routes dispatch the pseudo-patterns to `select_paths_by_threshold` / `select_paths_top_n` so Apply correctly writes decisions for every numeric field — `file_operations.set_decision_by_regex` (main-window route) and `execute_action_dialog._set_decision_by_regex` (Execute route) share identical match semantics (#392 closed the dispatch gap on the main-window side; before that fix, Apply via the menu route silently no-op'd for every non-Size numeric field).
- **Conditions / variants:** The "top 1 by score within group" configuration is the supported way to apply best-copy to a group — the standalone right-click "Apply best-copy decisions to this group" action was removed in [PR #224](https://github.com/jackal998/photo-manager/pull/224). Threshold input shows a date-format hint (YYYY-MM-DD) when the active field is Creation Date / Shot Date, and a number hint otherwise — the placeholder swaps in `_on_field_changed` based on `_DATE_NUMERIC_FIELDS`. Top-N preview rows are labeled `Group N — basename (value)` so the per-group semantic is visible (D5) and the field value the ranking selected on is surfaced (D8 — also exposes the stable `file_path` tiebreaker context when two records share a value). Threshold input also surfaces a ✓/✗ icon plus a friendly error label when the typed value can't parse as a number (or as an ISO date for the two date fields) — pre-Wave-5 unparseable input silently produced 0 matches with no signal that the threshold, not the data, was the problem. Top-N counter switches from the generic `{matched} of {total} match` to `{matched} matched (≤N per group × G groups)` so the per-group bound is explicit. Top-N spinbox now caps at 10,000 (was 999) so large-group manifests can "keep everything" via Top-N when a group has more than 999 records.
- **Related:** [PR #221](https://github.com/jackal998/photo-manager/pull/221) (closes [#209](https://github.com/jackal998/photo-manager/issues/209)); main-window-route Apply dispatch fix — [#392](https://github.com/jackal998/photo-manager/issues/392). QA scenarios [`qa/scenarios/s43_numeric_condition.py`](../qa/scenarios/s43_numeric_condition.py) (Execute-route Apply with Size field), [`qa/scenarios/s50_select_numeric_panel_from_main_window.py`](../qa/scenarios/s50_select_numeric_panel_from_main_window.py) (menu-route reachability), [`qa/scenarios/s56_action_dialog_apply_by_score.py`](../qa/scenarios/s56_action_dialog_apply_by_score.py) (menu-route Apply with Score field — pins the #392 fix).
- **Last verified:** 2026-05-24 (#392 main-window-route Apply dispatch — `file_operations.set_decision_by_regex` extended with `__cmp__:` / `__top_n__:` prefix dispatch mirroring the Execute-route handler)

---

### Set Action dialog — Score / Lock / Resolution fields

- **Entry point:** Field dropdown in the Set Action dialog — [app/views/dialogs/select_dialog.py](../app/views/dialogs/select_dialog.py); field metadata in [app/views/constants.py](../app/views/constants.py); canonical field list in [app/views/handlers/dialog_handler_helpers.py](../app/views/handlers/dialog_handler_helpers.py) `default_action_dialog_fields()`.
- **Trigger:** User opens the Set Action dialog from either route (main-window menu OR Execute Action dialog's Select by Field/Regex button) and expands the field combo.
- **Behaviour:** Score, Lock, and Resolution each appear as fields the user can match against, reachable from BOTH open routes. Score auto-opens the numeric comparison panel (it's in `_NUMERIC_FIELDS`). Lock is matched as a stringified flag ("Locked" / ""). Resolution is matched as `WIDTH×HEIGHT` (e.g. `^1920×1080$`) to mirror the tree's Resolution column rendering exactly. The Execute-route dialog (`execute_action_dialog.py:_show_select_dialog`) reads the canonical field list from `default_action_dialog_fields()` — the same source the main-window route uses — so both routes expose identical field surfaces. Before #392's secondary fix the Execute route's hard-coded list omitted Score / Group Count / Similarity / Resolution; users could only reach those four fields via the main-window route.
- **Conditions / variants:** All three labels go through `t()` so they translate correctly. Without this addition, picking Score from the combo would have rendered untranslated; Lock would have been picker-visible but raw-English; Resolution wasn't there at all.
- **Related:** [PR #250](https://github.com/jackal998/photo-manager/pull/250) (closes [#238](https://github.com/jackal998/photo-manager/issues/238)); Execute-route field-list parity — [#392](https://github.com/jackal998/photo-manager/issues/392). QA scenarios [`qa/scenarios/s50_select_numeric_panel_from_main_window.py`](../qa/scenarios/s50_select_numeric_panel_from_main_window.py), [`qa/scenarios/s56_action_dialog_apply_by_score.py`](../qa/scenarios/s56_action_dialog_apply_by_score.py).
- **Last verified:** 2026-05-24 ([#392](https://github.com/jackal998/photo-manager/issues/392) — Execute-route field list normalised to `default_action_dialog_fields()`)

---

### Set Action dialog — geometry persistence

- **Entry point:** `done(result)` override on `ActionDialog`, plus the `restore_widget_geometry` + `restore_splitter_state` pair at the end of `__init__` — [app/views/dialogs/select_dialog.py](../app/views/dialogs/select_dialog.py). Shared helpers + the `QSETTINGS_KEY_ACTION_DIALOG_GEOM` / `QSETTINGS_KEY_ACTION_DIALOG_SPLITTER_STATE` keys live in [app/views/window_state.py](../app/views/window_state.py).
- **Trigger:** Every dismissal of the dialog (Apply, **Esc**, or title-bar X) funnels through `done()`, which calls `save_widget_geometry` + `save_splitter_state`. The next `__init__` call restores the saved rect + handle position on top of the hardcoded `setMinimumSize(720, 380)` / `setSizes([420, 380])` defaults. Esc-key dismissal works because Qt's default `QDialog` event handling routes Esc to `reject()`; #391 removed the explicit Close button (the OS title-bar X covers the same path).
- **Behaviour:** User-resized dialog reopens at the same size and pane balance within the session and across app restarts (state stored via `QSettings` under the path centralised in [window_state.py](../app/views/window_state.py)). The splitter handle width is set to 8 px (Wave 8 D1) so the handle is comfortably grabbable instead of disappearing into the surrounding chrome at the Qt default ~1-5 px. A **Reset window size** button at the top-right of the preview-header row (or **Ctrl+0** shortcut, Wave 8 E5; #391 moved the button from the close-row to the preview-header because the action only affects the preview-side resizable surface) wipes the persisted geometry + splitter blobs and immediately resizes the dialog back to the hardcoded defaults — the reset only touches `window_state.ini` keys, so mode/field/simple_op preferences in `settings.json` survive.
- **Conditions / variants:** Geometry + splitter persistence + the reset affordance only apply when the dialog is opened with `match_fn` supplied (i.e. has a preview pane and a resizable splitter layout). The flat-layout branch (no `match_fn`) has no splitter, no save-on-close, and the **Reset window size** button is parentless (created but never added to a visible layout) — there is nothing user-resizable to persist or reset. If the saved rect would land off-screen (e.g. multi-monitor disconnect — <25% of the rect visible on any connected screen), the helper falls back to widget defaults rather than reopening on a disconnected monitor (same off-screen guard as [Execute Action — dialog geometry persistence](#execute-action--dialog-geometry-persistence)).
- **Related:** Geometry — [PR #228](https://github.com/jackal998/photo-manager/pull/228) (closes [#215](https://github.com/jackal998/photo-manager/issues/215)), QA scenario [`qa/scenarios/s48_dialog_geometry_persist.py`](../qa/scenarios/s48_dialog_geometry_persist.py). Splitter persistence + handle width + reset affordance — Wave 8 (C13 + D1 + E4 + E5 from #349/#350/#351). [#391](https://github.com/jackal998/photo-manager/issues/391) moved Reset to the preview-header and dropped the explicit Close button (Esc / title-bar X cover dismissal).
- **Last verified:** 2026-05-24 (#391)

---

### Similarity column

- **Entry point:** First column of the main result tree (COL_GROUP at index 0) — [app/views/tree_model_builder.py](../app/views/tree_model_builder.py). On the group header row it shows the localised "Group N" label; on each file row it shows one of: `Ref`, `100%`, an `N%` similarity, `—`, or `~dup`.
- **Trigger:** Populated automatically when a manifest is loaded; no user action.
- **Behaviour:** Per file row, the cell renders one of five values: (a) `Ref` — exactly one row per group, picked via the score-aware tie-break (highest score among Ref-tier action rows, lex name as final tiebreaker); (b) `100%` — `action='EXACT'` (SHA / format duplicate); (c) `N%` — `action='REVIEW_DUPLICATE'`, computed at render time as `round((64 - hamming) / 64 * 100)` where `hamming` is the pHash Hamming distance between the row's pHash and **the displayed Ref's pHash**, not the scanner's anchor's pHash; (d) for a Ref-tier sibling row that did not win the Ref pick (a "passenger" — e.g. a same-stem RAW+JPG companion or a #538-reconnected near-dup), the cell shows `N%` against **the displayed Ref** (the same reference as a REVIEW_DUPLICATE row) with a **trailing star** — `N*%` ([#536](https://github.com/jackal998/photo-manager/issues/536) Direction A) — marking it as an indirect/transitive member; hovering shows a tooltip naming the **nearest** group member (`"N% similar to <file>"`, key `tree.similarity_passenger_tooltip`). Falls back to `—` only when the passenger has no comparable pHash (a Live Photo MOV); (e) `~dup` — fallback placeholder when neither pHash can be read.
- **Conditions / variants:** The render-time recomputation requires both the displayed Ref's pHash and the row's pHash to be populated. When either is missing (old manifests pre-phash column, video rows, or imagehash not installed), the cell falls back to the scanner's stored `hamming_distance` so old manifests degrade gracefully. The manifest's `hamming_distance` column is still written by the scanner but is no longer the source of truth for the rendered % when phashes are available — the rendered value is always relative to the row the user sees as `Ref`.
- **Related:** [#253](https://github.com/jackal998/photo-manager/issues/253) (render against displayed Ref); [#241](https://github.com/jackal998/photo-manager/issues/241) (score-aware Ref tie-break); [#536](https://github.com/jackal998/photo-manager/issues/536) (Direction A — passenger shows `N*%` vs the Ref with a nearest-member tooltip, not bare `—`); QA scenarios [`qa/scenarios/s52_similarity_against_displayed_ref.py`](../qa/scenarios/s52_similarity_against_displayed_ref.py); helper module [`scanner/phash_distance.py`](../scanner/phash_distance.py).
- **Last verified:** 2026-06-03 (#536 Direction-A passenger relabel; #253 displayed-Ref render 2026-05-19)

---

### Preview pane — byte-budget LRU cache

- **Entry point:** `infrastructure/image_service.py` — `ImageService.__init__` and `_ByteBudgetLRUCache`.
- **Trigger:** Populated automatically as the user navigates the result tree (each row selection or grid display triggers background image loads).
- **Behaviour:** The in-memory image cache uses a byte-budget eviction policy instead of a fixed item count. Two independent tiers: a thumbnail tier (≈64 MB) for grid thumbnails (longest side ≤ 256 px) and a preview tier (≈192 MB) for single-view previews. Total budget = `min(256 MB, RAM // 32)`. When a tier exceeds its budget the least-recently-used entry is evicted. The on-disk cache writes versioned files under `~/AppData/Local/PhotoManager/thumbs/v1/<sha1>.jpg`; a recipe-version bump invalidates the old namespace. On first launch after upgrade, any unversioned `.jpg` files under `thumbs/` root are automatically deleted and a one-time status-bar notice is shown.
- **Conditions / variants:** DNG files use the embedded JPEG fast path (rawpy `extract_thumb`) if the embedded thumbnail's longest side ≥ viewport cap (2048 px default); falls through to full `postprocess` decode only when the embedded thumb is too small or absent. **EXIF Orientation is applied** to the embedded JPEG via Pillow's `ImageOps.exif_transpose` before conversion to QImage — without this, portrait-grip iPhone ProRAW DNGs (which store landscape pixels + Orientation=6/8 in the embedded JPEG's EXIF) would render 90° rotated relative to Lightroom / File Explorer (`QImage.loadFromData` does not honour the Orientation tag; only `QImageReader.setAutoTransform` does, and the fast path uses neither). The bitmap (non-JPEG) thumb branch is unaffected — rawpy delivers the array in correct orientation natively.
- **Related:** [#622](https://github.com/jackal998/photo-manager/issues/622) Phase 1; `infrastructure/image_service.py`; `tests/test_image_service.py`.
- **Last verified:** 2026-06-10 (DNG embedded-JPEG orientation fix)

---

### Preview pane — full-resolution viewer

- **Entry point:** Double-click on a single-view image label or a grid image tile — `app/views/preview_pane.py` emits `requestFullRes(path)` signal; `app/views/main_window.py::on_open_full_res_viewer` opens the dialog.
- **Trigger:** User double-clicks any image tile in the grid view or the single-image label in single-view mode.
- **Behaviour:** Opens `FullResViewerDialog` — a non-modal window showing the full raw-decoded image (side=0 → bypass viewport cap). Pan: drag with left mouse button. Zoom: Ctrl+scroll-wheel (scale clamped 5%–800%). Esc or window close dismisses it. The dialog's QImage is released on close; it is NOT stored in the byte-budget LRU (the dialog owns its own reference). Window title shows filename + pixel dimensions.
- **Conditions / variants:** Each double-click opens a new viewer window (non-modal — multiple files can be viewed simultaneously). Video tiles do not trigger the full-res viewer (videos have their own click-to-play behaviour). Double-click before any preview is loaded is a no-op (path is None, signal is not emitted).
- **Related:** [#622](https://github.com/jackal998/photo-manager/issues/622) Phase 1; `app/views/dialogs/full_res_viewer.py`; `app/views/preview_pane.py`; `tests/test_dialogs/test_full_res_viewer.py`.
- **Last verified:** 2026-06-09 (#622 Phase 1)

---

### Preview pane — no-autoplay video default

- **Entry point:** `app/views/preview_pane.py` — `show_single`, `autoplay_all_videos_when_ready`.
- **Trigger:** Selecting a group row (grid view) or a video file row (single view).
- **Behaviour:** Videos do NOT auto-play when a row is selected. Single-view video shows the player widget but waits for the user to click Play. Grid video tiles show a thumbnail (Shell/WIC where available) and play only when the user clicks the tile. `autoplay_all_videos_when_ready` is a no-op (kept for API compatibility).
- **Conditions / variants:** Explicit Play click in the player starts playback normally. The group media controller is still created when videos are present (for coordinated play/pause), but is not auto-triggered.
- **Related:** [#622](https://github.com/jackal998/photo-manager/issues/622) Phase 1; `app/views/preview_pane.py`.
- **Last verified:** 2026-06-09 (#622 Phase 1)

---

### Developer tool — memory probe

- **Entry point:** `scripts/memory_probe.py`; activated via `PHOTO_MANAGER_MEMORY_PROBE=1`.
- **Trigger:** Off by default (zero overhead when env var is unset). Enabled by setting `PHOTO_MANAGER_MEMORY_PROBE=1` before launch. CLI arg `--manifest <path>` (or env `PHOTO_MANAGER_PROBE_MANIFEST`) auto-loads a fixture manifest; `PHOTO_MANAGER_PROBE_RELOAD_COUNT=N` fires N reload cycles to measure per-reload growth.
- **Behaviour:** Captures five in-process memory snapshots (Python `tracemalloc` + Windows ctypes RSS + `gc` typed counts + Qt heap counters via `destroyed` signal) and appends JSONL rows to `~/AppData/Local/PhotoManager/logs/memory_probe_<RUN_ID>.jsonl`. Qt allocation counters track live `QStandardItem` and `QImage` objects across `refresh_model` cycles — the primary regression signal for #619 (`QStandardItem` leak) and #624 (preview LRU byte-budget). Optional TRIM mode (`PHOTO_MANAGER_MEMORY_PROBE_TRIM=1`) fires `SetProcessWorkingSetSize` after the idle snapshot to distinguish H2 (allocator hoarding) from H3 (Qt heap) leaks. Optional referrer dump (`PHOTO_MANAGER_MEMORY_PROBE_REFERRERS=<type,...>`) walks `gc.get_referrers()` for named types and writes a sibling JSONL.
- **Conditions / variants:** No-op on every code path when the env var is unset — all insertion points are guarded by `try/except ImportError`. Fixture generator at `scripts/generate_probe_fixture.py` produces a reproducible ~13k-row SQLite manifest (seed 42).
- **Related:** `docs/audits/memory-probe.md` (usage guide, row schema, 4-hypothesis decision table); `tests/test_memory_probe.py`; issues #614, #619, #624.
- **Last verified:** 2026-06-09 (regression-guard PR)

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
