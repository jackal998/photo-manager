# photo-manager — Feature Inventory

Canonical catalogue of user-visible behaviour. Each section names one
feature: where it is reachable, what the user does to trigger it, what
happens, and which conditions change the result.

This file is the answer to "is this behaviour documented?" and
"what's the expected UX for X?". It complements — does not replace —
the happy-path walkthrough in [`README.md` § Usage — GUI](../README.md#usage--gui).

**This is PR A — schema validation against the Execute Action cluster.**
PR B will backfill the rest of the app (scan, review, save/load,
preview, i18n). PR C will harden `docs_guard` to enforce updates here
whenever user-visible behaviour changes.

---

## Index

| Feature | Area |
|---|---|
| [Execute Action — base flow](#execute-action--base-flow) | Execute Action |
| [Execute Action — complete-group delete confirm](#execute-action--complete-group-delete-confirm) | Execute Action |
| [Execute Action — complete-group warning banner with jump-to](#execute-action--complete-group-warning-banner-with-jump-to) | Execute Action |
| [Execute Action — dialog geometry persistence](#execute-action--dialog-geometry-persistence) | Execute Action |
| [Execute Action — lock-confirm dialog](#execute-action--lock-confirm-dialog) | Execute Action |
| [Execute Action — preview pane](#execute-action--preview-pane) | Execute Action |
| [Execute Action — scope to highlighted rows](#execute-action--scope-to-highlighted-rows) | Execute Action |

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
