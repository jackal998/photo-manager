# Behaviour rules register

One authoritative statement per cross-cutting rule: what the app does, which
code decides it, and which test or scenario pins it.

- Edit a rule HERE, never a copy. A feature entry in `docs/features.md` cites
  the rule by its ID (for example `R-LOCK-1`) instead of restating it.
- `scripts/check_rules_register.py` (CI job `rules-register`) fails when a
  block is malformed, an owning symbol or pin is missing, or a cited ID does not
  resolve.
- `Status: unpinned` is visible debt, not a pass. The count prints on every CI
  run. An unpinned rule has Pins `none`; no test or scenario asserts it yet.
- Owning code is `path::symbol`, comma-separated. The symbol must occur in that file.
- Pins are comma-separated, each one of:
  - a Python test `tests/<file>.py::<Class>::<test_fn>` or `tests/<file>.py::<test_fn>`;
  - a vitest test `frontend/src/<file>.test.ts(x)::"<exact it() or test() title>"`;
  - a scenario file `qa/web/scenarios/sNN_<name>.py`;
  - or the literal `none`.
- Area codes: LOCK (lock gate), DIALOG (dialog dismissal), EXEC (Execute scope),
  SKIP (Skip now), DELETE (delete mechanics), SCAN (scan dialog), SORT (result
  sorting), LANG (language switch), LAYOUT (persisted widths and drags).

## LOCK

### R-LOCK-1 — A decision write or Skip now on a locked row is refused with 409

- **Rule:** A decision write (PATCH /api/decision or bulk decide), an apply best copy, or a Skip now that targets a locked row is refused with HTTP 409 code `locked_paths` and writes nothing unless `force_locked` is set (a decision write and apply best copy also accept `skip_locked`), and the web client answers that 409 by opening the lock-confirm dialog.
- **Owning code:** `core/app_service/review_service.py::set_decisions`, `core/app_service/action_service.py::bulk_decide`, `core/app_service/action_service.py::apply_best_copy`, `core/app_service/execute_service.py::remove_from_review`, `frontend/src/store/useAppStore.ts::lockConflict`
- **Pins:** `tests/test_web_review_routes.py::TestPatchDecisionLockedRowGate::test_locked_row_without_flags_returns_409`, `tests/test_web_action_routes.py::TestBulkDecideLockedRowGate::test_locked_row_without_force_returns_409`, `tests/test_web_action_routes.py::TestApplyBestCopy::test_locked_peer_without_override_returns_409`, `tests/test_web_execute_service.py::TestRemoveFromReview::test_locked_row_raises_without_force`, `qa/web/scenarios/s32_lock_confirm_bulk_regex.py`
- **Status:** pinned

### R-LOCK-2 — Force-locked re-send of a decision write or Execute unlocks its rows

- **Rule:** Re-sending a refused decision write (PATCH /api/decision or bulk decide) or an Execute with `force_locked` set applies it to the locked rows and clears their lock in the same write; apply best copy is not covered by this rule.
- **Owning code:** `core/app_service/review_service.py::set_decisions`, `core/app_service/action_service.py::bulk_decide`, `core/app_service/execute_service.py::execute_decisions`
- **Pins:** `tests/test_web_review_routes.py::TestPatchDecisionLockedRowGate::test_force_locked_applies_decision_and_unlocks`, `tests/test_web_action_routes.py::TestBulkDecideLockedRowGate::test_locked_row_with_force_true_applies_decision`, `qa/web/scenarios/s36_lock_confirm_destructive_execute.py`
- **Status:** pinned

### R-LOCK-3 — The lock toggle shows the padlock in warm ink when locked and muted ink when unlocked

- **Rule:** The lock toggle draws its padlock in the warm accent colour when the row is locked and in the muted ink colour when the row is unlocked.
- **Owning code:** `frontend/src/components/result/LockToggle.tsx::LockToggle`
- **Pins:** `qa/web/scenarios/s74_daylight_theme.py`, `frontend/src/components/result/LockToggle.test.tsx::"renders the unlocked padlock in ink-muted, not the hairline"`, `frontend/src/components/result/LockToggle.test.tsx::"renders the warm accent when locked"`
- **Status:** pinned

### R-LOCK-4 — An Execute that would delete a locked row is refused with 409

- **Rule:** An Execute whose scope includes a locked row decided `delete` answers HTTP 409 code `locked_paths` and deletes nothing unless `force_locked` is set.
- **Owning code:** `core/app_service/execute_service.py::execute_decisions`
- **Pins:** `tests/test_web_execute_routes.py::TestExecuteLockedGate::test_locked_delete_row_without_force_returns_409`, `tests/test_web_execute_service.py::TestExecuteDecisions::test_locked_rows_raise_without_force`, `qa/web/scenarios/s34_lock_confirm_at_execute.py`
- **Status:** pinned

### R-LOCK-5 — Skip now with force_locked unlocks the locked rows and removes them

- **Rule:** Skip now with `force_locked` set clears the lock of each locked row it is given and then sets the rows it accepts to outcome `ignored`; rows outside the allowed roots are not accepted.
- **Owning code:** `core/app_service/execute_service.py::remove_from_review`
- **Pins:** none
- **Status:** unpinned

## DIALOG

### R-DIALOG-1 — The Action and Execute dialogs do not close on an outside interaction

- **Rule:** The Action and Execute review dialogs stay open when an interaction lands outside them, including the focus return from a lock-confirm dialog that closes on top of them.
- **Owning code:** `frontend/src/components/action/ActionDialog.tsx::ActionDialog`, `frontend/src/components/execute/ExecuteDialog.tsx::ExecuteDialog`
- **Pins:** `qa/web/scenarios/s32_lock_confirm_bulk_regex.py`, `qa/web/scenarios/s34_lock_confirm_at_execute.py`
- **Status:** pinned

## EXEC

### R-EXEC-1 — Execute selected commits only the highlighted rows the filter still shows

- **Rule:** Execute selected commits only the highlighted rows that the active filter still shows, and its button is disabled when the filter hides every highlighted row.
- **Owning code:** `frontend/src/components/execute/ExecuteDialog.tsx::visibleSelectedPaths`
- **Pins:** `qa/web/scenarios/s60_execute_filter_by_action_type.py`
- **Status:** pinned

### R-EXEC-2 — Execute skips a row whose group has fewer than two in-review members

- **Rule:** Execute commits only rows whose group still has at least two in-review members, so a lone kept row keeps its pending decision.
- **Owning code:** `core/app_service/execute_service.py::_load_decided_rows`
- **Pins:** `tests/test_web_execute_service.py::TestExecuteDecisions::test_unscoped_execute_skips_actioned_singleton_hidden_from_review`
- **Status:** pinned

## SKIP

### R-SKIP-1 — Skip now on unlocked rows sets them ignored at once and its undo restores them

- **Rule:** Skip now on rows that include no locked row sets their outcome to `ignored` in one request with no confirmation dialog, deletes no file, and raises an undo toast that restores them to review.
- **Owning code:** `core/app_service/execute_service.py::remove_from_review`, `core/app_service/execute_service.py::restore_to_review`, `infrastructure/manifest_repository.py::remove_from_review`, `frontend/src/store/useAppStore.ts::removeFromList`
- **Pins:** `tests/test_web_execute_service.py::TestRemoveFromReview::test_removes_sets_ignored_outcome`, `tests/test_web_execute_service.py::TestRestoreToReview::test_undoes_a_skip_and_the_rows_come_back_as_they_were`, `qa/web/scenarios/s20_multi_remove_from_list.py`
- **Status:** pinned

## DELETE

### R-DELETE-1 — Executing a delete sends each file to the Recycle Bin by default

- **Rule:** Executing a delete through `execute_decisions` sends each file to the operating system Recycle Bin via send2trash by default, rather than unlinking it.
- **Owning code:** `infrastructure/delete_service.py::delete_to_recycle`, `core/app_service/execute_service.py::execute_decisions`
- **Pins:** none
- **Status:** unpinned

## SCAN

### R-SCAN-1 — A new scan source row includes subfolders by default

- **Rule:** A new scan source row has its recursive box ticked, so the scan includes subfolders unless the user unticks it.
- **Owning code:** `frontend/src/components/ScanDialog.tsx::blankSource`
- **Pins:** `qa/web/scenarios/s02_empty_folder.py`
- **Status:** pinned

### R-SCAN-2 — A scan that finds no media files keeps the scan dialog open with a message

- **Rule:** A scan that finds no media files shows the no-files message inside the still-open scan dialog with Start Scan enabled, and loads no manifest.
- **Owning code:** `frontend/src/components/ScanDialog.tsx::ScanDialog`
- **Pins:** `qa/web/scenarios/s02_empty_folder.py`
- **Status:** pinned

### R-SCAN-3 — Start Scan saves the source list and output path for the next opening

- **Rule:** Starting a scan saves the source list and the output path to the settings keys `sources.list` and `sources.output`, and reopening the scan dialog reloads them.
- **Owning code:** `frontend/src/components/ScanDialog.tsx::ScanDialog`, `app/web/routes/settings.py::patch_settings`
- **Pins:** `qa/web/scenarios/s23b_verify_settings.py`
- **Status:** pinned

## SORT

### R-SORT-1 — Header clicks sort the result tree by File Name, Size and Action

- **Rule:** Clicking the header of the File Name, Size or Action column sorts the result tree by that column.
- **Owning code:** `frontend/src/lib/resultColumns.ts::COLUMNS`, `frontend/src/lib/resultColumns.ts::makeRowComparator`
- **Pins:** `qa/web/scenarios/s45_sort_persistence.py`, `qa/web/scenarios/s79_decision_sort.py`
- **Status:** pinned

### R-SORT-2 — Similarity, Score, Resolution and Shot Date have no header sort control

- **Rule:** The Similarity, Score, Resolution and Shot Date column headers have no sort control and do not respond to a header click.
- **Owning code:** `frontend/src/lib/resultColumns.ts::COLUMNS`, `frontend/src/components/result/ColumnHeaderRow.tsx::ColumnHeaderRow`
- **Pins:** `frontend/src/lib/resultColumns.test.ts::"marks exactly File Name, Size (s45) and Action (#923) sortable"`, `frontend/src/components/result/ColumnHeaderRow.test.tsx::"clicking a non-sortable header (Similarity) does NOT sort"`
- **Status:** pinned

## LANG

### R-LANG-1 — Choosing a language saves it as the server locale and re-renders the interface

- **Rule:** Choosing a language in any language control saves `ui.locale` as the server setting and re-renders the interface in that language.
- **Owning code:** `frontend/src/i18n/useI18nStore.ts::setLocale`, `frontend/src/components/MenuBar.tsx::MenuBar`
- **Pins:** `qa/web/scenarios/s22_language_switch.py`
- **Status:** pinned

## LAYOUT

### R-LAYOUT-1 — A column or preview-pane width set by dragging survives a reload

- **Rule:** A column width or preview-pane width set by dragging is still in effect after the page reloads.
- **Owning code:** `frontend/src/store/useAppStore.ts::setColumnWidth`, `frontend/src/store/useAppStore.ts::setPreviewWidth`
- **Pins:** `qa/web/scenarios/s47_column_layout_persist.py`, `qa/web/scenarios/s39_layout_persist.py`
- **Status:** pinned

### R-LAYOUT-2 — A preview-pane drag writes its final width once, when the drag ends

- **Rule:** Dragging the preview-pane divider writes the final width to localStorage once, when the drag ends, and writes nothing on each intermediate move.
- **Owning code:** `frontend/src/App.tsx::endDrag`, `frontend/src/store/useAppStore.ts::setPreviewWidth`
- **Pins:** `frontend/src/App.previewResize.test.tsx::"widens the pane live during the drag and persists the width once on mouseup"`
- **Status:** pinned
