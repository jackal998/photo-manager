# Behaviour rules register

One authoritative statement per cross-cutting rule: what the app does, which
code decides it, and which test or scenario pins it.

- Edit a rule HERE, never a copy. A feature entry in `docs/features/<area>.md` cites
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

- **Rule:** A decision write (PATCH /api/decision or bulk decide), an apply best copy, a Skip now, an Undo of a Skip now (POST /api/restore of a locked dismissed row, #981), or an Execute (of a locked row decided Skip as well as Delete, `R-LOCK-4`) or a Prune (POST /api/prune, #983) that targets a locked row is refused with HTTP 409 code `locked_paths` and writes nothing unless it is confirmed by name. A decision write (PATCH /api/decision) names the locked rows it confirms in `confirmed_locked` (A6): a locked row in its write set that is not named answers 409 naming only those rows, so a row locked after the dialog opened is confirmed again (D7), and no boolean force is accepted for it. The other verbs still set `force_locked` until A8. A decision write and apply best copy also accept `skip_locked`. The web client answers that 409 by opening the lock-confirm dialog. Execute-skip joins this flow (#972). A prune has no exception: a locked singleton in scope is refused the same way. For a prune the dialog opens before the request, from the locked bucket of `classify_singletons`. A late 409 (for example after another tab locked a row) opens the same lock-confirm dialog (`op="prune"`, #1000), so no raw error reaches the status bar. On the always pref, Unlock & Apply re-sends at once with `force_locked` for the confirmed row only, Unlocked only prunes the unlocked rows, and Cancel writes nothing more. On the ask pref, Unlock & Apply re-opens the prune prompt with the confirmed row in its `lockedToPrune`; Unlocked only re-opens it with the unlocked rows only; nothing is written until the user confirms there, and Cancel writes nothing more. A decision write (PATCH /api/decision, bulk decide, apply best copy) re-checks the lock in the statement that writes each row, inside one `BEGIN IMMEDIATE` transaction (`ManifestTxn.write_decisions`): the locked set includes hidden rows, and a lock set after the screen was read is still refused. A request that names both `force_locked` and `skip_locked` is refused with 422 for bulk decide and apply best copy; a decision write that names both `confirmed_locked` and `skip_locked` is refused with 422 (A6). A `force_locked` sent to PATCH /api/decision is ignored (the model has no such field), so it can never write a locked row: that request is refused with 409 like an unforced one. The outcome writes re-check the lock the same way, inside one `BEGIN IMMEDIATE` transaction (`ManifestTxn.transition`, A4): Skip now, an Undo of a Skip now, a prune, and Execute's up-front gate. Only a lock on a row in the verb's own starting outcome counts, and a lock set after the plan is refused (409) with the row left as it was. Execute's skip batch runs after its files are deleted, so a lock set after the gate is not a 409 there: see `R-LOCK-4`.
- **Owning code:** `core/app_service/lock_gate.py::apply_gated_writes`, `core/app_service/lock_gate.py::apply_gated_transition`, `core/app_service/lock_gate.py::refuse_gated_outcome`, `infrastructure/manifest_repository.py::ManifestRepository.write_txn`, `infrastructure/manifest_repository.py::ManifestTxn.transition`, `core/app_service/review_service.py::set_decisions`, `core/app_service/action_service.py::bulk_decide`, `core/app_service/action_service.py::apply_best_copy`, `core/app_service/execute_service.py::remove_from_review`, `core/app_service/execute_service.py::restore_to_review`, `core/app_service/execute_service.py::execute_decisions`, `core/app_service/execute_service.py::prune_singletons`, `frontend/src/store/useAppStore.ts::lockConflict`, `frontend/src/api/client.ts::postPrune`, `app/web/routes/review.py::patch_decision`, `app/web/routes/action.py::post_bulk_decide`, `app/web/routes/action.py::post_apply_best_copy`, `app/web/routes/execute.py::post_execute`, `app/web/routes/execute.py::post_remove`, `app/web/routes/execute.py::post_restore`, `app/web/routes/execute.py::post_prune`
- **Pins:** `tests/test_decision_chokepoint.py::TestStalePlanCannotWriteLockedRow::test_plan_that_sees_no_locks_still_gets_409`, `tests/test_decision_chokepoint.py::TestHiddenLockedRow::test_targeted_hidden_locked_row_gets_409`, `tests/test_decision_chokepoint.py::TestBothFlagsRefused::test_route_both_flags_answer_422`, `tests/test_web_review_routes.py::TestPatchDecisionLockedRowGate::test_locked_row_without_flags_returns_409`, `tests/test_web_review_routes.py::TestPatchDecisionLockedRowGate::test_force_locked_alone_never_writes_a_locked_row`, `tests/test_web_review_routes.py::TestPatchDecisionLockedRowGate::test_row_locked_after_the_dialog_gets_a_second_409`, `tests/test_review_service.py::TestSetDecisionsLockedRowGate::test_row_locked_after_dialog_is_refused_and_named_alone`, `tests/test_source_probes.py::test_probe_decision_builder_must_pass_on_current_tree`, `frontend/src/store/useAppStore.test.ts::"Unlock & Apply names the dialog's row; a second 409 reopens the dialog for the new row only"`, `tests/test_web_action_routes.py::TestBulkDecideLockedRowGate::test_locked_row_without_force_returns_409`, `tests/test_web_action_routes.py::TestApplyBestCopy::test_locked_peer_without_override_returns_409`, `tests/test_web_execute_routes.py::TestPostRemove::test_locked_row_without_force_returns_409`, `tests/test_web_execute_service.py::TestRestoreToReview::test_locked_ignored_row_is_refused_and_nothing_is_written`, `tests/test_web_execute_routes.py::TestExecuteLockedGate::test_locked_skip_row_without_force_returns_409`, `tests/test_web_execute_routes.py::TestPostRestore::test_locked_ignored_row_gets_409_and_nothing_changes`, `tests/test_web_execute_service.py::TestPruneSingletonsLockGate::test_locked_singleton_refused_without_force_nothing_written`, `tests/test_web_execute_routes.py::TestPostPrune::test_locked_singleton_refused_409_nothing_written`, `frontend/src/components/dialogs/LockConfirmDialog.test.tsx::"states that the rows go back into review, unlocked, and nothing is deleted"`, `frontend/src/store/useAppStore.test.ts::"a locked row routes the Undo to lockConflict op=restore and keeps the toast"`, `frontend/src/store/useAppStore.test.ts::"a 409 sets lockConflict op=prune with the locked row and no manifest.error"`, `frontend/src/store/useAppStore.test.ts::"Unlock & Apply re-sends once with force_locked, carrying the confirmed row"`, `frontend/src/store/useAppStore.test.ts::"Cancel after a late 409 writes nothing more, even on the always pref"`, `frontend/src/store/useAppStore.test.ts::"on the ask pref, Unlock & Apply after a late 409 re-opens the prompt with the confirmed row and writes nothing until the user confirms"`, `tests/test_source_probes.py::test_probe_client_plain_transport_is_default_deny`, `tests/test_outcome_chokepoint.py::TestStalePlanCannotWriteLockedRow::test_skip_now_refuses_a_row_locked_after_the_plan`, `tests/test_outcome_chokepoint.py::TestStalePlanCannotWriteLockedRow::test_restore_refuses_a_dismissed_row_locked_after_the_plan`, `tests/test_outcome_chokepoint.py::TestStalePlanCannotWriteLockedRow::test_prune_refuses_a_singleton_locked_after_the_plan`, `tests/test_outcome_chokepoint.py::TestGuardedOutcomeSql::test_guarded_update_leaves_a_locked_row_untouched_when_not_confirmed`, `tests/test_outcome_chokepoint.py::TestTxnMarkerAndTransition::test_open_txn_marker_is_cleared_after_an_exception`, `tests/test_outcome_chokepoint.py::TestTxnMarkerAndTransition::test_transition_from_ignored_does_not_refuse_a_locked_row_in_review`, `qa/web/scenarios/s32_lock_confirm_bulk_regex.py`
- **Status:** pinned

### R-LOCK-2 — Force-locked re-send of a decision write or Execute unlocks its rows

- **Rule:** Re-sending a refused decision write (PATCH /api/decision or bulk decide), an Execute, a Prune (POST /api/prune, #983), or an Undo of a Skip now (POST /api/restore, #981) with `force_locked` set applies it to the locked rows and clears each row's lock in the same write as its decision or outcome; a row whose Execute action does not apply keeps its lock. A prune's force path ignores each locked singleton and clears its lock in the same write, and a failed write keeps every lock. A restore clears the lock in the same transaction as it returns the row to review, so a restored row is never left locked. Apply best copy is not covered by this rule. A decision write's force path (A3) names the locked rows it confirms and clears each one's lock in the same statement that writes its decision. A decision write's force path (A6) names the rows it confirms in `confirmed_locked`: only the named locked rows are written, and each is unlocked in the same statement that writes its decision. A locked row in the write set that is not named is refused with 409 naming only those rows, and nothing is written, not even the named rows. Until A8 the other verbs still confirm every locked row in the write set with the boolean `force_locked`.
- **Owning code:** `core/app_service/lock_gate.py::plan_locked_write`, `infrastructure/manifest_repository.py::ManifestTxn.write_decisions`, `core/app_service/review_service.py::set_decisions`, `core/app_service/action_service.py::bulk_decide`, `core/app_service/execute_service.py::execute_decisions`, `core/app_service/execute_service.py::restore_to_review`, `core/app_service/execute_service.py::prune_singletons`, `infrastructure/manifest_repository.py::ManifestTxn.transition`, `core/app_service/lock_gate.py::apply_gated_transition`
- **Pins:** `tests/test_web_review_routes.py::TestPatchDecisionLockedRowGate::test_confirmed_locked_applies_decision_and_unlocks`, `tests/test_web_action_routes.py::TestBulkDecideLockedRowGate::test_locked_row_with_force_true_applies_decision`, `qa/web/scenarios/s36_lock_confirm_destructive_execute.py`, `tests/test_web_execute_routes.py::TestExecuteLockedGate::test_force_locked_unlocks_and_deletes`, `tests/test_web_execute_routes.py::TestPostRestore::test_force_locked_unlocks_and_restores`, `tests/test_web_execute_routes.py::TestPostPrune::test_force_locked_prunes_and_unlocks_locked_singleton`, `tests/test_web_execute_service.py::TestPruneSingletonsLockGate::test_force_locked_prunes_and_unlocks_in_same_write`, `tests/test_web_execute_service.py::TestPruneSingletonsLockGate::test_failed_write_keeps_lock`, `tests/test_web_execute_service.py::TestRestoreToReview::test_force_locked_unlocks_and_restores_in_one_write`, `tests/test_web_execute_service.py::TestLockedRowUnlockOnlyWhenApplied::test_locked_delete_whose_delete_fails_keeps_lock`, `tests/test_web_execute_service.py::TestLockedRowUnlockOnlyWhenApplied::test_locked_delete_of_missing_file_keeps_lock`, `tests/test_web_execute_service.py::TestLockedRowUnlockOnlyWhenApplied::test_locked_skip_whose_outcome_write_fails_keeps_lock`, `tests/test_decision_chokepoint.py::TestHiddenLockedRow::test_confirmed_hidden_locked_row_is_unlocked`
- **Status:** pinned

### R-LOCK-3 — The lock toggle shows the padlock in warm ink when locked and muted ink when unlocked

- **Rule:** The lock toggle draws its padlock in the warm accent colour when the row is locked and in the muted ink colour when the row is unlocked.
- **Owning code:** `frontend/src/components/result/LockToggle.tsx::LockToggle`
- **Pins:** `qa/web/scenarios/s74_daylight_theme.py`, `frontend/src/components/result/LockToggle.test.tsx::"renders the unlocked padlock in ink-muted, not the hairline"`, `frontend/src/components/result/LockToggle.test.tsx::"renders the warm accent when locked"`
- **Status:** pinned

### R-LOCK-4 — An Execute that would delete or skip a locked row is refused with 409

- **Rule:** An Execute whose scope includes a locked row decided `delete` or `ignore` (Skip) answers HTTP 409 code `locked_paths` and writes nothing unless `force_locked` is set; with `force_locked` each locked row is deleted or set to outcome `ignored`, and its lock is cleared in that same write. A locked row whose action does not apply keeps its lock and stays in review. A row locked after the request was read and before the gate is refused by the gate's own transaction, before any file is touched, with 409 `locked_paths`. A row decided Skip that is locked after the gate, while the deletes run, is left alone when `force_locked` is not set: the skip batch is one transaction and writes nothing, so that row keeps its lock and stays in review, and every Skip row of the batch is reported in `failed` as `outcome write failed: …` (HTTP 200, not 409, because the deletes that ran before it cannot be undone); with `force_locked` the skip batch confirms it, sets it to outcome `ignored` and clears its lock. A row decided Delete that is locked after the gate is still deleted and its lock cleared; re-checking each delete row right before its file call is `A9` (see D6 in the design). A locked row outside the allowed roots is not offered for unlock: it is filtered out before the gate, is not listed in `locked_paths`, and keeps its lock. An Execute whose scope leaves the locked rows out executes the unlocked rows and leaves the locked rows in review, still locked.
- **Owning code:** `core/app_service/execute_service.py::execute_decisions`, `core/app_service/execute_service.py::_gate_execute`, `core/app_service/lock_gate.py::refuse_gated_outcome`, `infrastructure/manifest_repository.py::ManifestTxn.transition`
- **Pins:** `tests/test_web_execute_routes.py::TestExecuteLockedGate::test_locked_delete_row_without_force_returns_409`, `tests/test_outcome_chokepoint.py::TestStalePlanCannotWriteLockedRow::test_execute_gate_refuses_a_delete_row_the_plan_saw_as_unlocked`, `tests/test_outcome_chokepoint.py::TestStalePlanCannotWriteLockedRow::test_execute_skip_batch_leaves_a_row_locked_after_the_gate`, `tests/test_web_execute_routes.py::TestExecuteLockedGate::test_locked_skip_row_without_force_returns_409`, `tests/test_web_execute_service.py::TestExecuteDecisions::test_locked_rows_raise_without_force`, `tests/test_web_execute_service.py::TestExecuteLockedSkipRows::test_locked_skip_row_raises_without_force`, `tests/test_web_execute_service.py::TestExecuteLockedSkipRows::test_force_locked_skip_row_unlocks_and_finalises_ignored`, `tests/test_web_execute_service.py::TestExecuteLockedSkipRows::test_locked_skip_row_left_out_of_scope_is_not_finalised`, `tests/test_web_execute_service.py::TestLockedRowUnlockOnlyWhenApplied::test_locked_delete_outside_roots_is_not_gated_and_keeps_lock`, `tests/test_web_execute_service.py::TestLockedRowUnlockOnlyWhenApplied::test_locked_skip_outside_roots_is_not_gated_and_keeps_lock`, `qa/web/scenarios/s34_lock_confirm_at_execute.py`
- **Status:** pinned

### R-LOCK-5 — Skip now with force_locked unlocks the locked rows and removes them

- **Rule:** Skip now with `force_locked` set accepts only the rows inside the allowed roots and only rows still in review; a locked row outside them is not accepted and keeps its lock, and a locked row already dismissed or deleted is left as it is with its lock (no 409). Each accepted locked row is unlocked in the same write that sets it outcome `ignored`, so a row whose write fails keeps its lock.
- **Owning code:** `core/app_service/execute_service.py::remove_from_review`, `infrastructure/manifest_repository.py::ManifestTxn.transition`
- **Pins:** `tests/test_web_execute_service.py::TestRemoveFromReviewLockedRows::test_locked_out_of_root_row_keeps_lock_under_force`, `tests/test_web_execute_service.py::TestRemoveFromReviewLockedRows::test_locked_in_root_row_is_ignored_and_unlocked_under_force`, `tests/test_web_execute_service.py::TestRemoveFromReviewLockedRows::test_failed_ignore_write_keeps_locked_row_locked`, `tests/test_outcome_chokepoint.py::TestStalePlanCannotWriteLockedRow::test_skip_now_refuses_a_row_locked_after_the_plan`, `tests/test_web_execute_service.py::TestRemoveFromReview::test_locked_dismissed_row_keeps_its_lock_under_force`, `tests/test_web_execute_service.py::TestExecuteRecycleLockedRow::test_recycle_success_clears_lock_with_deleted_outcome`
- **Status:** pinned

### R-LOCK-6 — A manifest open never dismisses a row of a group that holds a lock

- **Rule:** The skip-directory reconcile on a manifest load leaves every group that contains a locked row untouched, so no row of such a group is dismissed and a locked row stays in review, locked and visible. The load logs how many skip-directory rows it left in place. The dismissal write re-checks the lock at group level inside the statement, so a lock set on any partner after the candidate read also blocks it. No user action sits behind this write, so it must never clear a lock or hide a locked row (#982). **Side-effect change (A3, BE-20):** a decision write (PATCH /api/decision) no longer runs this reconcile, because it no longer loads the manifest. The reconcile still runs on every manifest open (GET /api/manifest) and on the reloads after Execute, Skip now, prune and restore. The decision write itself still auto-migrates the manifest before it writes (`ManifestRepository.write_txn` runs `ensure_schema`).
- **Owning code:** `infrastructure/manifest_repository.py::ManifestRepository.reconcile_skip_directory_rows`, `infrastructure/manifest_repository.py::ManifestRepository._dismiss_skipped_rows`
- **Pins:** `tests/test_manifest_skip_reconcile.py::TestSkipDirectoryReconcile::test_locked_skip_row_keeps_its_unlocked_group_partner_visible`, `tests/test_manifest_skip_reconcile.py::TestSkipDirectoryReconcile::test_locked_non_skip_partner_keeps_skip_row_visible`, `tests/test_manifest_skip_reconcile.py::TestSkipDirectoryReconcile::test_group_without_a_locked_row_is_dismissed_as_before`, `tests/test_manifest_skip_reconcile.py::TestSkipDirectoryReconcile::test_locked_row_under_skip_directory_stays_in_review_and_locked`, `tests/test_manifest_skip_reconcile.py::TestSkipDirectoryReconcile::test_reconcile_reports_how_many_locked_rows_it_left`, `tests/test_manifest_skip_reconcile.py::TestSkipDirectoryReconcile::test_lock_on_the_partner_after_the_candidate_read_blocks_the_dismiss`, `tests/test_manifest_skip_reconcile.py::TestSkipDirectoryReconcile::test_lock_set_after_the_candidate_read_is_not_dismissed`
- **Status:** pinned

### R-LOCK-7 — The toolbar bulk verbs and the group header's Keep best · delete rest leave locked rows out, with no 409 and no lock-confirm dialog

- **Rule:** A deliberate exception to `R-LOCK-1` (owner decision 2026-10-16: these verbs never act on a locked row, so there is nothing to confirm). Two entry points on the review screen leave locked rows out of their write, raise no 409 and open no lock-confirm dialog: (a) the toolbar's counted bulk verbs **Keep**, **Delete** and **Skip** (the "Set N selected:" group): the browser drops every locked row from the selection before it sends `PATCH /api/decision`, so the request carries no locked path and `force_locked` is false, and only the selected rows the toolbar filter shows and that are unlocked are written; (b) the group header's **Keep best · delete rest** button: it sends `skip_locked`, so the server narrows the write to the unlocked rows of that group. In both, each locked row keeps its decision and its lock. The toolbar verbs report the skipped count in their undo toast, including when every selected row is locked. Keep best reports the skipped count in its undo toast, except when every row it would write is locked: then nothing is written and no toast appears. Not part of this exception; these stay under `R-LOCK-1`: every other decision write, including the right-click **Keep** / **Delete** on a multi-selection (`frontend/src/components/ContextMenu.tsx::handleSetKeep`, `handleSetDelete`) and the **K** / **D** shortcuts on a multi-selection (`frontend/src/hooks/useDecisionShortcuts.ts`); the right-click **Apply best-copy decisions to this group** item (a locked row in its write still gets 409 and the lock-confirm dialog); and the Set Action dialog's bulk apply. One gap in (a): a row locked by another surface between the browser's read and the request is not filtered, so the server answers 409 and the lock-confirm dialog opens (the stale-lock path); (b) reads the lock on the server, so it has no such gap.
- **Owning code:** `frontend/src/store/useAppStore.ts::applyBulkDecision`, `frontend/src/store/useAppStore.ts::applyBestCopy`, `frontend/src/components/ResultTree.tsx::ResultTree`, `core/app_service/action_service.py::apply_best_copy`, `app/web/routes/action.py::post_apply_best_copy`
- **Pins:** `frontend/src/store/useAppStore.test.ts::"writes only the UNLOCKED rows and never sends the locked one"`, `frontend/src/store/useAppStore.test.ts::"leaves the locked row's decision untouched and reports it in the toast"`, `tests/test_web_action_routes.py::TestApplyBestCopy::test_skip_locked_leaves_locked_peer_untouched`, `qa/web/scenarios/s75_keep_best_button.py`
- **Status:** pinned

## DIALOG

### R-DIALOG-1 — The Action and Execute dialogs do not close on an outside interaction

- **Rule:** The Action and Execute review dialogs stay open when an interaction lands outside them, including the focus return from a lock-confirm dialog that closes on top of them.
- **Owning code:** `frontend/src/components/action/ActionDialog.tsx::ActionDialog`, `frontend/src/components/execute/ExecuteDialog.tsx::ExecuteDialog`
- **Pins:** `qa/web/scenarios/s32_lock_confirm_bulk_regex.py`, `qa/web/scenarios/s34_lock_confirm_at_execute.py`, `frontend/src/components/action/ActionDialog.test.tsx::"an outside pointer-down on the overlay leaves the dialog open"`, `frontend/src/components/execute/ExecuteDialog.test.tsx::"an outside pointer-down on the overlay leaves the dialog open"`
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

- **Rule:** Skip now on rows that include no locked row sets their outcome to `ignored` in one request with no confirmation dialog, deletes no file, and raises an undo toast that restores them to review. Skip now acts only on rows still in review (outcome `''`) at write time: a row already `deleted` or `ignored` is left exactly as it is (same outcome, `executed`, and lock), is not counted in the response, and raises no 409 even when locked. The response counts only the rows actually written.
- **Owning code:** `core/app_service/execute_service.py::remove_from_review`, `core/app_service/execute_service.py::restore_to_review`, `infrastructure/manifest_repository.py::ManifestTxn.transition`, `frontend/src/store/useAppStore.ts::removeFromList`
- **Pins:** `tests/test_web_execute_service.py::TestRemoveFromReview::test_removes_sets_ignored_outcome`, `tests/test_web_execute_service.py::TestRemoveFromReview::test_deleted_row_is_left_as_it_is`, `tests/test_web_execute_service.py::TestRemoveFromReview::test_mixed_batch_writes_only_the_in_review_row`, `tests/test_web_execute_routes.py::TestPostRemove::test_skip_now_reports_and_writes_only_the_in_review_rows`, `tests/test_web_execute_service.py::TestRestoreToReview::test_undoes_a_skip_and_the_rows_come_back_as_they_were`, `tests/test_outcome_chokepoint.py::TestBE16OutcomeVerbsWriteNoAuditCsv::test_skip_now_writes_no_delete_csv`, `tests/test_outcome_chokepoint.py::TestBE16OutcomeVerbsWriteNoAuditCsv::test_restore_writes_no_delete_csv`, `qa/web/scenarios/s20_multi_remove_from_list.py`
- **Status:** pinned

## DELETE

### R-DELETE-1 — Executing a delete sends each file to the Recycle Bin by default

- **Rule:** Executing a delete through `execute_decisions` sends each file to the operating system Recycle Bin via send2trash by default, rather than unlinking it.
- **Owning code:** `infrastructure/delete_service.py::delete_to_recycle`, `core/app_service/execute_service.py::execute_decisions`, `infrastructure/manifest_repository.py::ManifestTxn.record_deleted`, `app/web/models.py::ExecuteRequest`, `frontend/src/store/useAppStore.ts::executeDecisions`
- **Pins:** `tests/test_web_execute_service.py::TestExecuteRecycleDefault::test_default_sends_delete_to_recycle_not_unlink`, `tests/test_web_execute_routes.py::TestExecuteRecycleDefault::test_body_without_recycle_key_recycles_each_file`, `tests/test_outcome_chokepoint.py::TestBE15FileDeletedButOutcomeWriteFails::test_deleted_file_with_failed_outcome_write_is_success_and_db_write_failed`, `tests/test_outcome_chokepoint.py::TestRecordDeleted::test_record_deleted_marks_the_row_deleted_and_clears_its_lock`, `frontend/src/store/useAppStore.test.ts::"executeDecisions sends recycle: true by default"`
- **Status:** pinned

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
