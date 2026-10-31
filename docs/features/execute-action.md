# Features — Execute Action

Entries for the **Execute Action** area of the feature inventory. The index and the section schema are in [`../features.md`](../features.md).

### Execute Action — base flow
- **Area:** Execute Action

- **Entry point:** Menu bar **Action → Execute Action…** (`menu-action-execute`, [frontend/src/components/MenuBar.tsx](../../frontend/src/components/MenuBar.tsx)) and the toolbar **Execute** button (`main-execute-button`, [frontend/src/components/Toolbar.tsx](../../frontend/src/components/Toolbar.tsx)) — both disabled until a manifest is loaded — plus the toolbar's danger button **"⊗ Delete N files…"** (`main-delete-cta`, disabled while N is 0). All three call `store.openExecuteDialog()` ([frontend/src/store/useAppStore.ts](../../frontend/src/store/useAppStore.ts)) with no scope. The dialog is [frontend/src/components/execute/ExecuteDialog.tsx](../../frontend/src/components/execute/ExecuteDialog.tsx), mounted once in [frontend/src/App.tsx](../../frontend/src/App.tsx). The selection-scoped variant is [Web — Execute (only selected)](web-ui.md#web--execute-only-selected).
- **Trigger:** User picks any of the three entry points.
- **Behaviour:** Opens the **"Execute Actions — Review"** modal (`execute-dialog`). Its tree (`execute-tree`, [frontend/src/components/execute/ExecuteTree.tsx](../../frontend/src/components/execute/ExecuteTree.tsx)) lists every row with a pending **Delete** or **Skip** decision (`user_decision` `delete` / `ignore`) under a `Group N` header row. Keep rows (`""`) are not listed, and a group with no decided row is left out. Each row (`execute-row-{group}-{basename}`) shows a 40 px thumbnail, the basename, the folder and a Delete / Skip badge. Above the tree sit the **Show:** type filter and a count of decided rows. A preview pane sits to its right. The footer holds **Cancel**, **Execute selected** and **Execute**.
  - **Execute** sends `POST /api/execute` ([app/web/routes/execute.py](../../app/web/routes/execute.py)) with `recycle: true`; with the filter on "All decided" and no scope it sends `scope_paths: null`. The route runs `execute_decisions` ([core/app_service/execute_service.py](../../core/app_service/execute_service.py)) off the event loop. The service re-reads decisions and lock state from SQLite, never trusting the client's copy, and considers only in-review rows (`outcome=''`) and applies `R-EXEC-2`, so a singleton the review hides is never executed (`_load_decided_rows`, [#941](https://github.com/jackal998/photo-manager/issues/941)). It then:
    - sends each **Delete** file to the Recycle Bin under `R-DELETE-1` and writes `outcome='deleted'` for that file straight after it succeeds. Because the write is per file, a crash mid-batch still leaves the DB consistent (D7).
    - finalizes every **Skip** row as `outcome='ignored'`. Those files stay on disk.
    - refuses any Delete path outside the scan's allowed roots and reports it as failed with reason `outside_allowed_roots`.
    - writes an audit CSV `delete_<timestamp>.csv` under `%LOCALAPPDATA%\PhotoManager\delete_logs`. The web path writes `0` in its GroupNumber column.
    - returns the reloaded groups.

    Locked Delete and Skip rows stop the run first, as `R-LOCK-4` states — see [Execute Action — lock-confirm dialog](#execute-action--lock-confirm-dialog).
  - The store replaces `manifest.groups` with the response and clears the main-tree selection. It then runs the [singleton-prune offer](#singleton-prune-offer-after-destructive-ops-426). **The dialog stays open after Execute.** Finalized rows drop out of the review, so both trees now show only what is still pending.
  - **Per-file outcome ([#742](https://github.com/jackal998/photo-manager/issues/742)).** `ExecuteResultSummary` ([frontend/src/components/execute/ExecuteResultSummary.tsx](../../frontend/src/components/execute/ExecuteResultSummary.tsx)) shows two sections inside the dialog:
    - **"Files Not Found"** (`execute-result-missing`, caution style) lists `executeResult.missing`: files already gone from disk. These are skipped and not marked deleted.
    - **"Files Failed to Delete"** (`execute-result-failed`, danger style) lists `executeResult.failed`, each path followed by its reason. A Skip row whose outcome write failed is listed here too, with the write error as its reason. `DeleteService.delete_to_recycle` ([infrastructure/delete_service.py](../../infrastructure/delete_service.py)) decodes `OSError.winerror` through [infrastructure/winerror_reasons.py](../../infrastructure/winerror_reasons.py), which holds the only reason table ([#757](https://github.com/jackal998/photo-manager/issues/757)). Examples are "file is in use by another process" (0x80270027), "access denied (source)" and "path too long for Recycle Bin". An unmapped error keeps its raw message.

    Missing and failed rows keep their decision (Delete, or Skip for a failed Skip write) and stay in the tree, so a later Execute retries them. A locked row among them keeps its lock. Nothing renders when both lists are empty. Errors that stop the whole operation show on a separate red alert line above the summary. Examples are a second Execute while one is running (409 `execute_already_running`) and a 422. The retired desktop client showed the two lists as blocking warning boxes.
  - **Cancel** or Escape closes the dialog without executing. Decisions are untouched because each one is already saved when it is set (see [Web — manifest decision persistence](web-ui.md#web--manifest-decision-persistence)).
- **Conditions / variants:**
  - **Execute** is disabled when no decided row is visible under the current filter. It is also disabled while a run is in flight: the label then reads "Executing…", Cancel is disabled and Escape is ignored. An outside interaction does not close it, as `R-DIALOG-1` states ([#721](https://github.com/jackal998/photo-manager/issues/721), see the lock-confirm entry).
  - Before the request, the client may show a confirm — see [Execute Action — complete-group delete confirm](#execute-action--complete-group-delete-confirm). The type filter and the in-dialog selection narrow what gets executed — see [filter by action type](#execute-action--filter-by-action-type-502) and [Execute selected](#execute-action--partial-execution-via-execute-selected).
  - **Row menu.** Right-clicking a row opens the dialog's own menu ([frontend/src/components/execute/ExecuteContextMenu.tsx](../../frontend/src/components/execute/ExecuteContextMenu.tsx)):
    - **Delete** / **Keep** set a decision via `PATCH /api/decision`.
    - **Skip** finalizes the row at once via `POST /api/remove`, after a "Skip this file?" confirm (`execute-remove-confirm`).
    - **Lock** / **Unlock**.
    - **Set Action by Field…** opens the same Set Action dialog the toolbar opens, with no row seed ([#893](https://github.com/jackal998/photo-manager/issues/893)). Its match runs over the whole manifest, not only the Execute dialog's groups. The retired desktop client limited its in-dialog "Select by Field/Regex…" to the groups shown ([#443](https://github.com/jackal998/photo-manager/issues/443)).
  - **One source of truth ([#444](https://github.com/jackal998/photo-manager/issues/444)).** The Execute tree and the main result tree both render from the store's `manifest.groups`. A decision or lock changed inside the dialog therefore shows in the main tree at once, whether or not the dialog is then executed.
  - The last run's per-file summary stays in the store until the next Execute starts. Closing and reopening the dialog does not clear it.
  - Moving, resizing and scrolling: see [Execute Action — dialog geometry persistence](#execute-action--dialog-geometry-persistence).
- **Related:** [#742](https://github.com/jackal998/photo-manager/issues/742) (Not Found / Failed split with decoded reasons), [#757](https://github.com/jackal998/photo-manager/issues/757) (single reason table), [#673](https://github.com/jackal998/photo-manager/issues/673) (toolbar Execute gated on a loaded manifest). Layer-1: `tests/test_winerror_reasons.py`, `tests/test_delete_service.py`, `tests/test_web_execute_service.py`, `tests/test_web_execute_routes.py`, `frontend/src/components/execute/ExecuteDialog.test.tsx`. QA scenarios:
  - [`qa/web/scenarios/s13_execute_action.py`](../../qa/web/scenarios/s13_execute_action.py): the destructive round-trip, plus the mixed-manifest and missing-file phases.
  - [`qa/web/scenarios/s30_execute_dialog_regex_right_click.py`](../../qa/web/scenarios/s30_execute_dialog_regex_right_click.py): row menu → Set Action by Field.
  - [`qa/web/scenarios/s54_execute_dialog_remove_from_list.py`](../../qa/web/scenarios/s54_execute_dialog_remove_from_list.py): row menu → Skip.

---

### Execute Action — complete-group delete confirm
- **Area:** Execute Action

- **Entry point:** `DeleteConfirmDialog` ([frontend/src/components/dialogs/DeleteConfirmDialog.tsx](../../frontend/src/components/dialogs/DeleteConfirmDialog.tsx), `execute-all-delete-confirm`). The Execute dialog's **Execute** and **Execute selected** handlers open it when `completeDeleteGroupIds(groups, scopePaths)` ([frontend/src/components/execute/ExecuteDialog.tsx](../../frontend/src/components/execute/ExecuteDialog.tsx)) returns at least one group.
- **Trigger:** User clicks **Execute** or **Execute selected** while at least one group would have every in-scope member deleted. The check runs in the browser before any request is sent.
- **Behaviour:** A blocking modal titled **"Entire group(s) will be deleted"** reads: "Group(s) N, M will have EVERY file deleted (K files will be deleted). Files will be sent to the Recycle Bin. Continue?" K counts every Delete row in the pending call's scope, not only the complete groups.
  - **Yes, delete all** (`execute-all-delete-confirm-yes`) sends the queued `POST /api/execute` with the same options.
  - **Cancel** (`execute-all-delete-confirm-no`) or Escape sends nothing. The Execute dialog stays open with its decisions intact.
  - **The auditable body ([#917](https://github.com/jackal998/photo-manager/issues/917), design REPLY Q6).** The dialog shows *what* is about to be deleted, not only how many.
    - **Layout.** The modal is at least **60vh** tall. The row list scrolls inside it, while the totals line and the confirm button stay pinned outside the scroll area, so the number never scrolls out of view.
    - **Folder buckets.** The Delete rows are **grouped by folder**. Each folder header (`execute-delete-confirm-folder`) shows the folder path plus its own count and size subtotal. This is the cue Q6 wants for "why is anything from *that* folder in here".
    - **One reason line per file** (`execute-delete-confirm-reason`, 12px muted). It spells out, in sentence form, the relationship the Similarity badge encodes (the badge itself reads 100% / N% / ★ N% / ~dup / — / Ref): "Exact duplicate of `<ref>`"／「與 `<ref>` 完全重複」, "N% match — same group"／「N% 相似 — 同一群組」, "Linked match, N%*"／「間接比對，N%*」. There is also a phrase each for `~dup`, "—", and a Ref row that is itself marked delete.
    - **Totals line.** A pinned line (`execute-delete-confirm-totals`) reads "N files · X MB", computed by the same rule as `lib/deleteTotals` — the same pair the status bar carries ([#906](https://github.com/jackal998/photo-manager/issues/906)/[#916](https://github.com/jackal998/photo-manager/issues/916)).
    - **Safety sentence.** **"Files are moved to the Recycle Bin and can be restored."**／**「檔案將移至資源回收筒，可還原。」** is always present.
    - **The list follows the execute SCOPE, not the group.** It is built from the same `scope_paths` the pending execute call will send, so its total matches what the server deletes. The footer's **Execute selected** lists the scope `R-EXEC-1` defines ([#940](https://github.com/jackal998/photo-manager/issues/940)). A dialog opened with **Execute (only selected)** is scoped to whole groups, so its **Execute** lists every Delete row of those groups. Unscoped Execute lists every Delete row, as before. A row's reason line still names its group's Ref even when that Ref is itself out of scope — the keeper a file duplicates does not stop existing because it was not selected.
    - Bucketing and the reason wording live in [`frontend/src/lib/deleteConfirmRows.ts`](../../frontend/src/lib/deleteConfirmRows.ts).
    - The whole body is additive. A caller that passes no groups (the Set Action dialog's bulk-decide gate) gets the unchanged compact dialog, and every existing testid, handler and gate condition is untouched.
- **Conditions / variants:**
  - **Which groups count ([#733](https://github.com/jackal998/photo-manager/issues/733)).** One qualifying group is enough to block.
    - **Unscoped Execute**, and a group-scoped dialog under the "All decided" filter, check each group as a whole: a group qualifies only when every member is Delete, undecided rows included.
    - **Other filters** check the visible decided rows instead (the #502 analog). Under "Delete only" any group with a visible Delete row qualifies; under "Skip only" none does.
    - **Execute selected** checks the scope `R-EXEC-1` defines. A group qualifies when those rows are all Delete, even if other rows of that group are outside them. The list then shows exactly those rows.
  - **Order.** This confirm comes before the request. A locked Delete row therefore raises its lock prompt only after **Yes** (`R-LOCK-4`, see [Execute Action — lock-confirm dialog](#execute-action--lock-confirm-dialog)). The retired desktop client asked about locks first; the outcome is the same.
  - The passive, non-blocking version of this warning is [the complete-group banner](#execute-action--complete-group-warning-banner-with-jump-to).
- **Related:** [#733](https://github.com/jackal998/photo-manager/issues/733) (per-group gate), [#917](https://github.com/jackal998/photo-manager/issues/917) (auditable body); desktop precursors [PR #30](https://github.com/jackal998/photo-manager/pull/30) (single confirm) and [PR #219](https://github.com/jackal998/photo-manager/pull/219) (scope-narrowed check). Layer-1: `frontend/src/components/execute/ExecuteDialog.test.tsx` (`completeDeleteGroupIds`), `frontend/src/components/dialogs/DeleteConfirmDialog.test.tsx`, `frontend/src/lib/deleteConfirmRows.test.ts`. Layer-3: [`qa/web/scenarios/s13_execute_action.py`](../../qa/web/scenarios/s13_execute_action.py) (mixed-manifest phase, en + zh_TW).

---

### Execute Action — complete-group warning banner with jump-to
- **Area:** Execute Action

- **Entry point:** `AllDeleteBanner` ([frontend/src/components/execute/AllDeleteBanner.tsx](../../frontend/src/components/execute/AllDeleteBanner.tsx), `execute-all-delete-banner`) above the Execute dialog's tree. `findAllDeleteGroupIds` in [frontend/src/components/execute/ExecuteDialog.tsx](../../frontend/src/components/execute/ExecuteDialog.tsx) supplies the groups.
- **Trigger:** Shown whenever at least one group in the dialog's scope is all-Delete under the active filter. It is recomputed on every decision, filter or scope change.
- **Behaviour:** A caution strip (▲ glyph plus **"Warning:"**) reads "Group(s) N, M will have ALL files deleted. Review decisions below before clicking Execute."
  - **One translated template.** The sentence is `web.execute_dialog.warning_complete_groups` (copy audit E2), with a singular or plural "Group"/"Groups". Each group number is a clickable button (`execute-all-delete-jump-{N}`) inserted at the template's `{groups}` marker. A locale can therefore reorder the sentence without losing the buttons (zh_TW currently keeps the group list in the same leading slot as en), and a translation that drops the marker still renders the full sentence with the buttons at the end.
  - **Jump.** Clicking a number scrolls the virtualized tree so that group's header row is at the top. It does not select a row; the retired desktop client also selected the group's row.
- **Conditions / variants:**
  - Which groups count depends on the filter ([#676](https://github.com/jackal998/photo-manager/issues/676)):
    - "All decided": a group counts only when every member is Delete, undecided rows included.
    - "Delete only": any group with a visible Delete row counts.
    - "Skip only": no group counts.

    So the buttons only point at groups the filtered tree actually shows.
  - In a dialog opened with [Execute (only selected)](#execute-action--scope-to-selected-groups-only-selected-entry-410-430-429), only the scoped groups count.
  - A number with no header row in the current tree does nothing, silently.
  - The banner only informs. The blocking gate is [the complete-group delete confirm](#execute-action--complete-group-delete-confirm). The separate "pending deletes hidden by the filter" banner is described under [filter by action type](#execute-action--filter-by-action-type-502).
- **Related:** [PR #181](https://github.com/jackal998/photo-manager/pull/181) (closes [#166](https://github.com/jackal998/photo-manager/issues/166)); [#676](https://github.com/jackal998/photo-manager/issues/676) (filter-narrowed). Layer-1: `frontend/src/components/execute/banners.test.tsx`, `frontend/src/components/execute/ExecuteDialog.test.tsx`. QA scenario [`qa/web/scenarios/s33_execute_dialog_jump_to_all_delete.py`](../../qa/web/scenarios/s33_execute_dialog_jump_to_all_delete.py) clicks the real jump button (the retired desktop run could not click its label link on hosted CI).

---

### Execute Action — dialog geometry persistence
- **Area:** Execute Action

- **Entry point:** The Execute dialog's header (`execute-title-bar`) doubles as a drag bar, and a bottom-right grip (`execute-resize-handle`) resizes it. Both come from `useOverlayGeometry("execute", …)` in [frontend/src/components/execute/ExecuteDialog.tsx](../../frontend/src/components/execute/ExecuteDialog.tsx) — the shared mechanism documented in [Web — overlay window geometry (movable / resizable / persisted)](web-ui.md#web--overlay-window-geometry-movable--resizable--persisted).
- **Trigger:** User drags the header or the grip. The saved geometry is applied every time the dialog opens, including after a page reload.
- **Behaviour:**
  - Geometry lives under its own localStorage key, `pm.overlay-geometry.execute.v1`.
  - A **move** stores position only; the dialog keeps sizing itself to its content.
  - Only a **resize** pins width and height. After that the body (`execute-body`) scrolls while the header and the Cancel / Execute selected / Execute footer stay in view.
  - Clamping to the viewport, the 320×280 px minimum and the fallback on unreadable storage follow the shared rules in the linked entry.
  - With nothing stored, the dialog opens at its default centered `max-w-4xl` layout.
- **Conditions / variants:** There is no divider between the tree and the preview pane — the preview has a fixed width — so there is no splitter position to save. The retired desktop client saved one. The same mechanism also serves the Set Action dialog and the full-resolution viewer; the Scan dialog stays centered and cannot be moved.
- **Related:** [#739](https://github.com/jackal998/photo-manager/issues/739) (divergence row D6); QA scenario [`qa/web/scenarios/s48_dialog_geometry_persist.py`](../../qa/web/scenarios/s48_dialog_geometry_persist.py) (Execute and Set Action dialogs: drag, resize, reopen, reload, own key); unit tests [`frontend/src/lib/overlayGeometry.test.ts`](../../frontend/src/lib/overlayGeometry.test.ts) and [`frontend/src/hooks/useOverlayGeometry.test.tsx`](../../frontend/src/hooks/useOverlayGeometry.test.tsx).

---

### Execute Action — lock-confirm dialog
- **Area:** Execute Action

- **Entry point:** `LockConfirmDialog` ([frontend/src/components/dialogs/LockConfirmDialog.tsx](../../frontend/src/components/dialogs/LockConfirmDialog.tsx), `lock-confirm-dialog`), mounted once in [frontend/src/App.tsx](../../frontend/src/App.tsx) and driven by `store.execute.lockConflict` (`{paths, op, originalPaths}`). It opens when a mutating request is refused under `R-LOCK-1` or `R-LOCK-4`, with the body **`{"code": "locked_paths", "locked_paths": [...]}`**:

  | Request | `op` |
  |---|---|
  | `POST /api/execute` | `execute` |
  | `POST /api/remove` | `remove` |
  | `PATCH /api/decision` | `decision` ([#733](https://github.com/jackal998/photo-manager/issues/733)) |
  | `POST /api/action/apply-best-copy` | `apply-best-copy` ([#744](https://github.com/jackal998/photo-manager/issues/744)) |
  | `POST /api/restore` | `restore` ([#981](https://github.com/jackal998/photo-manager/issues/981)) |

  It also opens with no request at all, for locked singletons before a prune (`prune`, [#686](https://github.com/jackal998/photo-manager/issues/686)).
- **Trigger:** The server re-reads `is_locked` from SQLite on each such call and never trusts the client. In user terms, any of:
  - **Execute** / **Execute selected** with a locked Delete or Skip row in scope.
  - A finalizing **Skip** that includes a locked row — from the Execute dialog's row menu, the main-tree context menu, or **List → Skip Selected now**.
  - The **Undo** of an immediate Skip that includes a locked dismissed row — restore is gated like every other locked action (`R-LOCK-1`, #981).
  - A decision change on a locked row. Right-click Keep/Delete in either tree and the `d`/`k` shortcuts go through `store.setDecisions`. The row's own Keep/Delete/Skip control and the preview pane's decision buttons are disabled on a locked row, so they never reach the gate.
  - **Apply best-copy decisions to this group** (group context menu) on a group with locked rows.
  - A [singleton-prune offer](#singleton-prune-offer-after-destructive-ops-426) that includes locked singletons.
- **Behaviour:** The dialog lists the locked paths and offers three buttons:
  - **Unlock & Apply** (`lock-confirm-btn-unlock-apply`) re-runs the same operation over the **same original scope** with `force_locked` (the forced re-send of `R-LOCK-2`; the Skip-now force of `R-LOCK-5`): Execute then deletes the locked Delete rows and skips the locked Skip rows; each row is unlocked in the same write as its outcome, so a row whose action did not apply (failed delete, missing file, failed write) stays locked and in review. A decision write lands with the row unlocked; its re-send names the rows the dialog showed (`confirmed_locked`, A6), so a row locked after the dialog opened answers a second 409 that names only that row, and the dialog reopens for it. Apply best copy also unlocks its previously-locked delete-targets. Re-using the original scope is deliberate — re-running unscoped after an "Execute selected" would execute the whole manifest. Its weight follows the locked files' fate (`F5`, #984): **red** with a leading ⊗ glyph only for an `execute` that deletes at least one file (Delete-only or mixed); every other op, and a Skip-only execute, uses the default weight with no glyph.
  - **Unlocked only** (`lock-confirm-btn-unlocked-only`) re-runs over the original scope minus the locked paths (`apply-best-copy` re-runs with `skip_locked`). Locked rows keep their lock and decision.
  - **Cancel** (`lock-confirm-btn-cancel`) clears the conflict; nothing is written.
  - **The wording states the consequence** (the desktop's [#417](https://github.com/jackal998/photo-manager/issues/417) principle). Title and body depend on `op` (`web.lock_confirm.*`):
    - `execute` — **"Locked files in delete scope"**: "N locked file(s) is/are about to be deleted (sent to the Recycle Bin). Unlock and proceed, or leave the locked files out." A Skip-only set uses **"Locked files in skip scope"** and reads "N locked file(s) is/are about to be skipped (left on disk, dropped from this review)"; a mixed set states both counts. An earlier "permanently DELETED" claim was corrected; s34 asserts "permanent" is absent.
    - `remove` — **"Locked files in skip scope"**: "…about to be skipped (left on disk, dropped from this review)…"
    - `decision` — **"Locked rows affected"**: "Setting this decision would change M row(s); N is/are locked. **Nothing is deleted yet — this only queues the decision.** Unlock and set all, set only the unlocked rows, or cancel."
    - `apply-best-copy` — **"Locked rows in this group"**: "…Nothing is deleted yet — this only queues keep/delete decisions…"
    - `prune` — **"Locked singleton groups"**: "N locked singleton group(s) would be pruned. Unlock and prune it/them, or keep it/them in the list."
    - `restore` — **"Locked rows going back into review"**: "N locked file(s) is/are about to go back into review and be unlocked. **Nothing is deleted: restored files stay on disk.** Unlock and restore, restore only the unlocked files, or cancel." Unlock & Apply unlocks and restores the same list (`force_locked`); Unlocked only restores the unlocked subset; Cancel leaves the toast up with its Undo.
    - `undo` (A7, #999) — the Undo of a Keep best whose lock was set after the toast: the decision wording ("Locked rows affected"). Unlock & Apply re-sends the Undo with these rows confirmed, added to those earlier rounds confirmed; Unlocked only undoes the rest; Cancel writes nothing and leaves the toast up. See [review.md](review.md).
- **Conditions / variants:**
  - **Lock / Unlock never prompt** — `PATCH /api/lock` is not gated.
  - **At Execute, locked Delete and locked Skip rows both trip the gate** (`R-LOCK-4`).
  - **Not every path uses this dialog.**
    - The toolbar's bulk Keep/Delete/Skip verbs and the group header's Keep-best button are the `R-LOCK-7` exception: they leave locked rows out with no 409 and no dialog. Both report "N locked file(s) unchanged" in their undo toast.
    - The Set Action by Field dialog has its own inline three-button prompt (`action-lock-confirm-dialog`). Its **Apply to Unlocked Only** is disabled when the 409's `matched_total` leaves no unlocked row. Cancelling that prompt keeps the Set Action dialog open with the user's field, regex and action ([#764](https://github.com/jackal998/photo-manager/issues/764), the twin of #721): `ActionDialog.onInteractOutside` always calls `preventDefault`, and Escape is guarded only while the dialog is applying.
  - **Unlocked only is never disabled here.** When every targeted row is locked it re-runs over whatever is left (for `decision`, nothing). The retired desktop client disabled it in that case.
  - **`prune`:** Cancel does not abort the prune (pre-request gate; a late 409's Cancel writes nothing). Cancel and **Unlocked only** both keep the locked singletons and go on to the unlocked ones; **Unlock & Apply** unlocks and prunes them.
  - **Order at Execute:** the [complete-group delete confirm](#execute-action--complete-group-delete-confirm) runs in the browser first; the 409 and this dialog come after **Yes**. The retired desktop client asked about locks first; the outcome is the same.
  - **Cancel keeps the Execute dialog open ([#721](https://github.com/jackal998/photo-manager/issues/721)).** The user adjusts and retries. The lock prompt is a separate Radix modal mounted beside the Execute dialog, not inside it. When it closes on Cancel it fires two "outside" events on the Execute dialog: a `focusin` as focus returns, then, once closed, a `pointerdown` that nothing blocks any more. `ExecuteDialog.onInteractOutside` therefore calls `preventDefault` **every time**, so `R-DIALOG-1` holds for a stray backdrop click too. `onEscapeKeyDown` blocks only while `executeRunning`, so an idle dialog still closes on Escape; Radix sends Escape to the topmost layer only. This is proven live by s34 — jsdom cannot reproduce the focus-return / `pointerdown` sequence.
- **Related:** [PR #183](https://github.com/jackal998/photo-manager/pull/183) (closes [#182](https://github.com/jackal998/photo-manager/issues/182)); [#417](https://github.com/jackal998/photo-manager/issues/417), [#733](https://github.com/jackal998/photo-manager/issues/733), [#721](https://github.com/jackal998/photo-manager/issues/721), [#764](https://github.com/jackal998/photo-manager/issues/764), [#674](https://github.com/jackal998/photo-manager/issues/674). Layer-1: `tests/test_review_service.py`, `tests/test_web_review_routes.py`, `tests/test_web_execute_routes.py`, `frontend/src/components/dialogs/LockConfirmDialog.test.tsx`. QA scenarios:
  - [`qa/web/scenarios/s34_lock_confirm_at_execute.py`](../../qa/web/scenarios/s34_lock_confirm_at_execute.py): `execute`, Cancel — the Execute dialog stays open; asserts the delete wording; second flow: a locked Skip row shows the skip wording, and Unlock & Apply finalises it with the file left on disk (#972).
  - [`qa/web/scenarios/s36_lock_confirm_destructive_execute.py`](../../qa/web/scenarios/s36_lock_confirm_destructive_execute.py): `execute`, Unlock & Apply.
  - [`qa/web/scenarios/s53_execute_dialog_lock_decision.py`](../../qa/web/scenarios/s53_execute_dialog_lock_decision.py): `decision`, via the Execute dialog's row menu.
  - [`qa/web/scenarios/s32_lock_confirm_bulk_regex.py`](../../qa/web/scenarios/s32_lock_confirm_bulk_regex.py): the Set Action inline prompt — #764 Cancel plus Apply to Unlocked Only.
  - [`qa/web/scenarios/s61_actioned_singleton_prune.py`](../../qa/web/scenarios/s61_actioned_singleton_prune.py) / [`qa/web/scenarios/s67_locked_singleton_prune_always.py`](../../qa/web/scenarios/s67_locked_singleton_prune_always.py): `prune`.

---

### Execute Action — preview pane
- **Area:** Execute Action

- **Entry point:** The `execute-preview-pane` panel to the right of the Execute dialog's tree ([frontend/src/components/execute/ExecuteDialog.tsx](../../frontend/src/components/execute/ExecuteDialog.tsx)).
- **Trigger:** Always shown while the dialog is open. Clicking a row makes it the preview target.
- **Behaviour:** A fixed-width (224 px) panel shows the **thumbnail** of the last-clicked row as a still image (`execute-preview-image`). The image comes from the row's `/api/image?path=…&size=512` URL and is at most 192 px tall. Before any click the panel reads "Select a row to preview". It has no metadata, zoom or video playback; it is not the main window's preview pane. The preview follows the last-clicked row even with several rows selected: a Ctrl+click that removes a row from the selection still previews it.
- **Conditions / variants:** The pane cannot be resized; there is no divider (see [dialog geometry persistence](#execute-action--dialog-geometry-persistence)). If the previewed row leaves the dialog's groups — for example because it was executed — the placeholder returns. The retired desktop client embedded its full preview pane here.
- **Related:** [#165](https://github.com/jackal998/photo-manager/issues/165) ([PR #260](https://github.com/jackal998/photo-manager/pull/260)) introduced the pane. QA scenario [`qa/web/scenarios/s51_execute_dialog_preview.py`](../../qa/web/scenarios/s51_execute_dialog_preview.py) clicks a row, asserts the image is visible, closes with Escape and checks decisions are unchanged. Layer-1: `frontend/src/components/execute/ExecuteDialog.test.tsx` ("preview pane is rendered").

---

### Execute Action — partial execution via "Execute selected"
- **Area:** Execute Action

- **Entry point:** The **Execute selected** button (`execute-btn-execute-selected`) in the Execute dialog's footer, between Cancel and Execute (`handleExecuteSelected` in [frontend/src/components/execute/ExecuteDialog.tsx](../../frontend/src/components/execute/ExecuteDialog.tsx)).
- **Trigger:** User selects one or more rows in the dialog's tree and clicks the button.
  - **Selection.** The tree allows several rows at once ([#697](https://github.com/jackal998/photo-manager/issues/697), [frontend/src/lib/multiSelect.ts](../../frontend/src/lib/multiSelect.ts)). A plain click replaces the selection, Ctrl/Cmd+click adds or removes a row, and Shift+click selects the range from the last click over the visible row order. Selected rows carry `aria-selected="true"`.
  - This selection belongs to the dialog and is separate from the main tree's.
- **Behaviour:** Runs the standard pipeline narrowed to the scope `R-EXEC-1` defines (`visibleSelectedPaths`, [#940](https://github.com/jackal998/photo-manager/issues/940)):
  1. The [complete-group confirm](#execute-action--complete-group-delete-confirm) is checked over those rows.
  2. `POST /api/execute` is sent with `scope_paths` set to those rows.
  3. The lock gate, per-file delete (`R-DELETE-1`), Skip finalize, audit CSV and prune offer then run as in the [base flow](#execute-action--base-flow).

  The server acts only on decided, in-review rows in that list, so every row outside it keeps its decision. Executed rows leave both trees. The dialog stays open so the user can keep reviewing — on the web, full Execute leaves it open as well.
- **Conditions / variants:**
  - Disabled when none of the selected rows is visible under the active filter (nothing selected included), or while a run is in flight.
  - **The selection is intersected with the type filter (#940).** Execute selected is scoped as `R-EXEC-1` states. A row selected under one filter stays selected after a switch to a filter that hides it, so it rejoins the scope when a filter shows it again. The selection is also not cleared after a run; rows that were executed leave the tree and drop out of the scope.
  - It combines with the dialog's own scope. [Execute (only selected)](#execute-action--scope-to-selected-groups-only-selected-entry-410-430-429) first limits the dialog to the selected rows' groups, and Execute selected narrows further inside them. The in-dialog selection survives closing the dialog, but what **Execute selected** sends is clamped to the groups in the current scope, so a row selected during an earlier, unscoped open is not sent when its group is not on screen.
  - Partial execution is the main source of **actioned** singletons — one row left in a group with a decision that was not executed. See [Singleton-prune offer after destructive ops](#singleton-prune-offer-after-destructive-ops-426).
- **Related:** [#697](https://github.com/jackal998/photo-manager/issues/697) (in-dialog multi-select); [#410](https://github.com/jackal998/photo-manager/issues/410) (a second button rather than a relabelled Execute). QA scenario [`qa/web/scenarios/s64_execute_selected_partial.py`](../../qa/web/scenarios/s64_execute_selected_partial.py): a plain click plus a Ctrl+click selects two rows; Execute selected; asserts those two are gone and the three survivors keep Delete with the dialog still open; then a full Execute clears the rest. Layer-1: `frontend/src/components/execute/ExecuteDialog.test.tsx`, `frontend/src/lib/multiSelect.test.ts`.

---

### Execute Action — filter by action type (#502)
- **Area:** Execute Action

- **Entry point:** The **Show:** select (`execute-type-filter`, [frontend/src/components/execute/TypeFilter.tsx](../../frontend/src/components/execute/TypeFilter.tsx)) in the Execute dialog's toolbar row. It has three options — **All decided** (default), **Delete only** and **Skip only** — whose wire values are `all` / `delete` / `ignore`. The labels are translated and use the Keep / Delete / Skip wording (copy audit E1).
- **Trigger:** User changes the selection.
- **Behaviour:** The filter narrows what is shown and what is executed; it never changes a decision.
  - **Display.** The tree shows only decided rows of the chosen type, leaving out groups with none. The decided-row count updates and both banners are recomputed.
  - **"Visible = committed" ([#502](https://github.com/jackal998/photo-manager/issues/502), web [#676](https://github.com/jackal998/photo-manager/issues/676)).** Under any filter other than "All decided", **Execute** sends `scope_paths` set to exactly the visible decided rows. Rows of the other type keep their decisions for a later run. Before #676 the web sent `scope_paths=null` and executed every decided row whatever the filter — a user filtered to "Delete only" still executed the hidden Skip rows, and vice versa.
  - **Hidden-destructive banner.** Under **Skip only**, `HiddenDestructiveBanner` ([frontend/src/components/execute/HiddenDestructiveBanner.tsx](../../frontend/src/components/execute/HiddenDestructiveBanner.tsx), `execute-hidden-destructive-banner`) warns: "N pending delete row(s) hidden by the current filter — switch to All decided or Delete only to see them." So a Skip-only view never hides that deletions are staged. The text is `web.execute_dialog.warning_hidden_destructive`, with "row"/"rows" chosen by count (copy audit E3); it used to be hardcoded English with a flattened "row(s)".
  - **All-delete banner.** It is narrowed to the filter as well — see [the complete-group banner](#execute-action--complete-group-warning-banner-with-jump-to).
- **Conditions / variants:**
  - **No Keep or Lock option.** Keep (`""`) rows never appear in the Execute tree, and a lock is not a decision.
  - **With the lock gate:** a filtered Execute covers only the visible type, so locked rows of the hidden type do not raise the [lock prompt](#execute-action--lock-confirm-dialog); a locked row of the visible type, Skip included, does (`R-LOCK-4`).
  - **With the complete-group confirm:** under a filter other than "All decided", the [confirm](#execute-action--complete-group-delete-confirm) checks the visible rows.
  - **With "Execute selected":** the selection **is** intersected with the filter — [Execute selected](#execute-action--partial-execution-via-execute-selected) is scoped as `R-EXEC-1` states (#940, below).
  - The filter lives in the dialog and is not reset when the dialog closes. It stays set across close and reopen and returns to "All decided" only on a page reload. The retired desktop client reset it on every reopen.
- **Web parity — Execute selected (#940):** the highlight and the filter now intersect on the web too. `handleExecuteSelected` applies `R-EXEC-1` to the highlight (`visibleSelectedPaths` = the highlight ∩ `decidedPaths`), the complete-delete confirm judges that same narrowed set, and the button follows `R-EXEC-1`. Previously the raw highlight was sent: a delete row highlighted under "All decided" and then hidden by "Skip only" was executed alongside the visible skip row, with no confirm, right under the banner that said it was out of scope. Switching back to a filter that shows the row makes it part of the scope again, because it is still highlighted. The set is also clamped to the dialog's in-scope groups. The dialog stays mounted, so a highlight survives Cancel, and reopening via "Execute (only selected)" on other groups could otherwise send a row that is not in the tree. Layer-1 coverage: `frontend/src/components/execute/ExecuteDialog.test.tsx`. Layer-3: s60 asserts that the button is disabled when its only highlighted row is hidden.
- **Related:** [#502](https://github.com/jackal998/photo-manager/issues/502), [#676](https://github.com/jackal998/photo-manager/issues/676). Composes with [Execute selected](#execute-action--partial-execution-via-execute-selected), [scope to selected groups](#execute-action--scope-to-selected-groups-only-selected-entry-410-430-429), the [lock-confirm dialog](#execute-action--lock-confirm-dialog) and the [complete-group banner](#execute-action--complete-group-warning-banner-with-jump-to). Layer-1: `frontend/src/components/execute/ExecuteDialog.test.tsx`, `frontend/src/components/execute/banners.test.tsx`. Layer-3: [`qa/web/scenarios/s60_execute_filter_by_action_type.py`](../../qa/web/scenarios/s60_execute_filter_by_action_type.py).

---

### Execute Action — scope to selected groups (only-selected entry, #410, #430, #429)
- **Area:** Execute Action

- **Entry point:** Menu bar **Action → Execute Action (only selected)…** (`menu-action-execute-selected`), and the item with the same label in a file row's context menu (`ctx-execute-selected`, [#735](https://github.com/jackal998/photo-manager/issues/735), the web counterpart of [#429](https://github.com/jackal998/photo-manager/issues/429)). Full description: [Web — Execute (only selected)](web-ui.md#web--execute-only-selected).
- **Trigger:** At least one row selected in the main result tree, with a manifest loaded. The menu-bar item is disabled otherwise.
- **Behaviour:** Opens the Execute dialog scoped by **group membership** ([#430](https://github.com/jackal998/photo-manager/issues/430)). Every selected row pulls in its whole group, recorded as `store.execute.scopeGroupNumbers`. Everything the dialog derives from groups is limited to those groups: the tree, both banners, the complete-group confirm and what Execute commits. The Execute label stays "Execute" ([#410](https://github.com/jackal998/photo-manager/issues/410)). The scope is cleared when the dialog closes.
- **Conditions / variants:** Groups arrive whole. Under "All decided", the [complete-group confirm](#execute-action--complete-group-delete-confirm) therefore fires only when every row of a group, undecided rows included, is Delete. To narrow further to individual rows inside the dialog, use [Execute selected](#execute-action--partial-execution-via-execute-selected).
- **Related:** [#410](https://github.com/jackal998/photo-manager/issues/410), [#430](https://github.com/jackal998/photo-manager/issues/430), [#429](https://github.com/jackal998/photo-manager/issues/429) / [#735](https://github.com/jackal998/photo-manager/issues/735). QA scenarios:
  - [`qa/web/scenarios/s44_execute_selected_only.py`](../../qa/web/scenarios/s44_execute_selected_only.py): pulls in a whole group and confines the dialog to it, across two groups.
  - [`qa/web/scenarios/s15_context_menu.py`](../../qa/web/scenarios/s15_context_menu.py): #735 context-menu phase.

---

### Singleton-prune offer after destructive ops (#426)
- **Area:** Execute Action

- **Entry point:** `store.maybeOfferPrune` ([frontend/src/store/useAppStore.ts](../../frontend/src/store/useAppStore.ts)). It runs at the end of every successful `executeDecisions` (Execute / Execute selected) and every finalizing `removeFromList`: **Skip** from the Execute dialog's row menu, the main-tree context menu, or **List → Skip Selected now**.
  - **Dialogs.** `PruneConfirmDialog` ([frontend/src/components/dialogs/PruneConfirmDialog.tsx](../../frontend/src/components/dialogs/PruneConfirmDialog.tsx), `prune-confirm-dialog`) and, for locked singletons, the [lock-confirm dialog](#execute-action--lock-confirm-dialog) with `op="prune"`.
  - **Setting.** **Settings → Prune singletons** (`settings-prune-select`, [frontend/src/components/SettingsDialog.tsx](../../frontend/src/components/SettingsDialog.tsx)) offers **Ask each time / Always prune / Never prune**. It is stored as `ui.prune_singletons` in `settings.json` (`"ask"` in `settings.json.example`); any other value is read as "ask".
- **Trigger:** A destructive op leaves one or more groups with exactly one row still under review.
  - The review drops single-member groups, so the browser cannot see singletons itself. Instead `POST /api/prune/candidates` (`classify_singletons` in [core/app_service/execute_service.py](../../core/app_service/execute_service.py)) sorts **every** current singleton — not only those the last op created — into three groups:
    - **locked** — locked, whatever its decision;
    - **actioned** — unlocked, with a pending Delete or Skip;
    - **plain** — unlocked, with no decision.

    Rows outside the allowed roots are left out.
  - Nothing happens when all three are empty, or when that call fails.
- **Behaviour:**
  - The setting is re-read from `GET /api/settings` every time. With **Never prune**, nothing happens.
  - **Locked singletons first ([#584](https://github.com/jackal998/photo-manager/issues/584) D6).** On both **Ask** and **Always**, locked singletons first raise the lock prompt:
    - **Unlock & Apply** adds them to the prune set, whatever the next dialog decides. The prune request carries `force_locked`, and the server unlocks each one in the same write as its ignored outcome (no separate unlock request, #983).
    - **Unlocked only** and **Cancel** both keep them and continue with the unlocked ones.
  - **Always prune** prunes the plain and actioned singletons, plus any just unlocked, in one call.
  - **Ask each time** opens **"Prune singleton groups?"**, listing the candidate paths, in one of three layouts:
    - *plain only* — "N singleton group(s) now have only one file remaining. Prune … from the list?"
    - *actioned only* — "N singleton group(s) with an un-executed delete / skip action remain. Prune them from the list?" Here **Prune** itself includes the actioned ones.
    - *mixed* — the plain count, plus "…and M more carry an un-executed delete / skip action", plus a checkbox **"Also prune M actioned singleton(s)"** (`prune-include-actioned`). The checkbox starts **unchecked** and is not remembered.
  - **Buttons.** **Keep all** (`prune-btn-keep`) and **Prune N** (`prune-btn-remove`). N is the plain count, or the actioned count when there are no plain singletons. With **Don't ask again** (`prune-remember`) ticked, Prune switches the setting to Always and Keep all switches it to Never. Escape or a click outside counts as Keep all.
  - **Applying the prune.** `POST /api/prune` receives the exact path list (the explicit mode of `prune_singletons`). It re-checks that each path is still a singleton inside the allowed roots. A locked one in scope is refused with 409 `locked_paths` and nothing is written, unless the lock-confirm was accepted (`force_locked`); then it is finalized as `outcome='ignored'` and unlocked in the same write (#983). The file on disk is not touched. The returned groups replace the store's. A late 409 (a row locked after the candidates read) opens the lock-confirm dialog with `op="prune"` instead of an error (#1000). On the always pref, Unlock & Apply re-sends at once with `force_locked` for the confirmed row only, Unlocked only prunes the unlocked rows, and Cancel writes nothing more. On the ask pref, Unlock & Apply re-opens the prune prompt with the confirmed row in its `lockedToPrune`; Unlocked only re-opens it with the unlocked rows only; nothing is written until the user confirms there, and Cancel writes nothing more. A failed prune shows on the status bar's error line.
- **Conditions / variants:**
  - **Keep all** and **Never prune** leave the singleton in the manifest with `outcome=''`. It stays hidden, as a single-member group, and is offered again after the next destructive op.
  - A kept **actioned** singleton keeps its pending decision while hidden, and no Execute, scoped or unscoped, acts on it, as `R-EXEC-2` states (#941, below).
  - Partial execution via [Execute selected](#execute-action--partial-execution-via-execute-selected) is the most common source of actioned singletons.
- **Related:** [#426](https://github.com/jackal998/photo-manager/issues/426), [#686](https://github.com/jackal998/photo-manager/issues/686) (web port), [#584](https://github.com/jackal998/photo-manager/issues/584) (D6 locked-singleton gate), [#589](https://github.com/jackal998/photo-manager/issues/589), [#713](https://github.com/jackal998/photo-manager/issues/713) / [#702](https://github.com/jackal998/photo-manager/issues/702) (no second prune after the dialog closes itself); precedent for a batched confirm is [#417](https://github.com/jackal998/photo-manager/issues/417). Layer-1: `tests/test_web_execute_service.py` (classify + explicit prune), `tests/test_web_execute_routes.py`, `frontend/src/lib/prune.test.ts`, `frontend/src/components/dialogs/PruneConfirmDialog.test.tsx`, `frontend/src/store/useAppStore.test.ts`. QA scenarios:
  - [`qa/web/scenarios/s61_actioned_singleton_prune.py`](../../qa/web/scenarios/s61_actioned_singleton_prune.py): **Ask** — seven variants, including all three lock-prompt answers.
  - [`qa/web/scenarios/s67_locked_singleton_prune_always.py`](../../qa/web/scenarios/s67_locked_singleton_prune_always.py): **Always** — the lock prompt still fires.
- **A kept singleton is never executed (#941):** Execute acts only on rows the review shows. `_load_decided_rows` ([core/app_service/execute_service.py](../../core/app_service/execute_service.py)) applies `R-EXEC-2`, the same survivor rule as the review's orphan-skip. An actioned singleton the user keeps (Keep all, Prune with the opt-in unchecked, or `"never"`) therefore stays exactly as the prune offer left it: outcome `''`, its pending decision intact, and a prune candidate at the next offer. Previously the server executed every in-review decided row. A plain Execute then deleted the hidden row, even though it had appeared in no tree, file count or #733 complete-delete confirm. The common producer was a partial "Execute selected" that left the second copy of an all-delete group alone. This matches Qt after a reload, where `load()` drops the singleton from `vm.groups` too. Layer-1 coverage: `tests/test_web_execute_service.py::TestExecuteDecisions::test_unscoped_execute_skips_actioned_singleton_hidden_from_review` (real SQLite + real files).

---
