# Frontend cross-cutting rule inventory (audit-fe, master aaf240d, read-only)

Assembled verbatim from audit-fe's summary + full tables. Paths relative to frontend/src/ unless noted.
Labels: #999/#1000 = known bug; REC-EXC = recorded exception (R-LOCK-7 or an explicit features-doc statement);
UNREC = unrecorded. Static read only; nothing was run.

## Summary — top 5 by bug risk
1. Undo forces without asking (#999). undoKeepBest (store/useAppStore.ts:1674-1681) sends force_locked:true, then
   patchLocks (:1685-1691) writes back pre-action lock state; a row locked after the action is unlocked silently.
   Toast.tsx:112 uses the same Undo for the toolbar bulk-decision toast. useAppStore.test.ts:1415 pins the force flag.
2. The 409 is routed three ways: store lockConflict (decision, execute, remove, apply-best-copy, restore); a
   component-local inline prompt (applyBulkDecide re-throws to ActionDialog.tsx:242); not routed (postPrune plain
   postJson client.ts:321 → manifest.error, #1000; R-LOCK-1 rules.md:27 describes this as the rule).
3. A re-send drops feature side effects (UNREC): Unlock & Apply / Unlocked only for "remove" call removeFromList
   without {undoable:true} (LockConfirmDialog.tsx:143,181) → no undo toast (breaks R-SKIP-1 for the unlocked re-run);
   a "decision" re-send from the toolbar stale-lock path gets no toast; a forced decision never refreshes is_locked on
   the client (PATCH returns only {updated}) so the padlock stays drawn after the server unlocked.
4. Five different pre-request lock gates: disabled control (FileRow.tsx:308, PreviewPane.tsx:445); client filter
   (toolbar, R-LOCK-7); skip_locked (header Keep best, ResultTree.tsx:829); client pre-classification (prune,
   useAppStore.ts:996-1037); none / server 409 (context menu, K/D, ExecuteContextMenu). Disabled controls are recorded
   only in the features doc, not rules.md.
5. Bulk scope ignores the filter outside the toolbar (UNREC): only the toolbar uses visibleSelectedPaths
   (lib/rowFilter.ts:86). Raw selection incl. filter-hidden rows: right-click verbs (App.tsx:109-111), K/D
   (useDecisionShortcuts.ts:72-78), Skip Selected now (App.tsx:253-255), Execute only selected (App.tsx:242-244). The
   store's own comment at useAppStore.ts:1564-1568 calls that "a bulk write whose extent the user cannot see".
Runner-up: F5 button weight applied only in LockConfirmDialog; ActionDialog inline Unlock & Apply (:448),
RemoveFromListConfirm Yes (:69), DeleteConfirm "Mark N for deletion" (:280) are always red though none deletes a file.

## What ONE client-side conflict helper must own
- The only mutating transport: every write through jsonOrConflict, incl. prune.
- 409 → dialog routing, storing a re-send descriptor {op, originalScope, callerOpts (undoable, toast owner,
  pendingDecision, groupNumber), matchedTotal}, not just paths.
- Sole authority over force: force_locked / skip_locked only from a dialog verdict (e.g. a branded token); no caller,
  Undo or retry can pass them (makes #999 impossible).
- The success tail, identical on first send and every re-send: replace groups or refresh the lock column, raise the
  undo toast, offer the prune.
- Unlocked-only narrowing declared per op (client path filter vs server skip_locked); the empty-scope guard (today
  only for "decision", LockConfirmDialog.tsx:167).

## Per-feature behaviour the helper would LOSE if not kept explicitly
- Prune: dialog BEFORE the request from classify buckets; Cancel = continue with unlocked buckets, not "write
  nothing"; toast owner travels through resolvePruneLock; pref branches always/ask.
- Execute: complete-group delete confirm first; execute_already_running 409 → executeError; unscoped scope captured
  as explicit list; F5 fate-based weight and copy; Execute dialog stays open on Cancel (R-DIALOG-1).
- Set Action: inline prompt, dialog survives Cancel (#764); Unlocked only disabled from matched_total; Recent patterns
  recorded only after success; dialog closes on success.
- Keep best: re-run uses groupNumber; keepBestPending released in finally; snapshot merges into the standing toast for
  the same group; header variant always sends skip_locked (R-LOCK-7).
- Restore: toast stays up on Cancel; toast cleared by its id; manifest-switch-in-flight guard.
- Toolbar verbs: client-side lock filter and lockedCount in the toast.
- Keep-best Undo legitimately must undo the keeper lock its own action created: the helper must tell "lock this
  action set" from "lock the user set" without forcing.

## 1a Transport
- api/client.ts:149 patchDecisions — patchJsonOrConflict — consistent.
- client.ts:303 postExecute, :309 postRemove, :315 postRestore, :347 bulkDecide, :353 applyBestCopy —
  postJsonOrConflict — consistent.
- client.ts:321 postPrune — plain postJson → Error("HTTP 409: {json}") — #1000; R-LOCK-1 text must change with fix.
- client.ts:174 patchLocks — plain patchJson — REC-EXC (lock toggle ungated, execute-action.md:143).

## 1b 409 handling per store caller
- useAppStore.ts:441-465 setDecisions: reverts optimistic apply, lockConflict{op:"decision", originalPaths,
  pendingDecision}; consistent.
- :864-889 executeDecisions: lockConflict{op:"execute", originalPaths: scopedPaths}; other 409 codes → executeError;
  complete-group delete confirm runs FIRST (execute/ExecuteDialog.tsx:352-413). REC.
- :959-976 removeFromList: lockConflict{op:"remove"}; does NOT record opts.undoable. UNREC.
- :1532-1548 applyBestCopy: lockConflict{op:"apply-best-copy", groupNumber}; header sends skipLocked
  (ResultTree.tsx:829), right-click sends no flag (ContextMenu.tsx:220). REC-EXC (rules.md R-LOCK-7 (b)).
- :1769-1783 undoSkipNow, :1817-1832 restoreToReview: lockConflict{op:"restore"}; toast kept. consistent.
- :1401-1416 applyBulkDecide: RE-THROWS to ActionDialog.tsx:242-246 (conflict in component state, never lockConflict)
  — a second dialog. REC-EXC (execute-action.md:147).
- :1115-1122 applyPrune: every error incl. 409 → manifest.error; pre-classified via postPruneCandidates (:996),
  lockConflict{op:"prune"} set BEFORE any request (:1023-1037). #1000.
- :1569-1608 applyBulkDecision (toolbar): client filter on is_locked, then setDecisions. REC-EXC (R-LOCK-7 (a)).
- :364-404 setLock, :469-513 setLocks: plain error → manifest.error. REC-EXC (ungated).
- Pre-gates: FileRow.tsx:308, PreviewPane.tsx:445,458 disable on locked row (REC-EXC in execute-action.md:128 only).
  ContextMenu.tsx:139-147, ExecuteContextMenu.tsx:93-101, useDecisionShortcuts.ts:72-78: no gate (server 409).

## 1c Verdicts (LockConfirmDialog.tsx: Unlock&Apply :110-145 / Unlocked only :147-183 / Cancel :185-193)
- execute: U&A executeDecisions({scopePaths: original, forceLocked}) :133; UO ({scopePaths: unlocked}) :163; empty
  list executes nothing; unscoped re-sent as explicit list (scope captured :826-832). REC.
- remove: U&A removeFromList(original, true) :143 with NO {undoable:true}; UO :181, no undoable, no empty guard.
  Contradicts R-SKIP-1 (rules.md:103). UNREC.
- decision: U&A :135, UO :168 (guards empty). (i) forced success never updates is_locked on the client (PATCH returns
  {updated} only, client.ts:145-147; no refresh in setDecisions :435-440) — padlock stays drawn; no vitest pin.
  (ii) a conflict from the toolbar stale-lock path is re-sent without a toast; its undo is lost. UNREC both.
- apply-best-copy: U&A {forceLocked} :138; UO {skipLocked} :174 (server-narrowed). REC.
- restore: U&A :141; UO :178 (no empty guard). restoreToReview success clears ANY skip-now toast (:1815, by kind)
  while undoSkipNow clears only its own id (:1767). UNREC.
- prune: setter :1031 before the request only; all verdicts → resolvePruneLock (:1125-1187); Cancel does NOT abort.
  REC-EXC (execute-action.md:149).
- Set Action inline: ActionDialog.tsx:276-286; Unlocked only DISABLED when matchedTotal - n <= 0 (:440-443);
  LockConfirmDialog never disables it. REC-EXC (execute-action.md:147-148).

## 1d Force without confirm
- useAppStore.ts:1674-1681 undoKeepBest patchDecisions forceLocked:true (#999); :1685-1691 then patchLocks with
  pre-action lock values (#999 second half); Toast.tsx:112 shares it with the bulk-decision toast; test pin
  useAppStore.test.ts:1415 must change with the fix.
- undoSkipNow (:1745-1759) no force; 409 opens dialog — consistent. applyPrune (:1059-1072) forces only confirmed rows.
- Stale comment useAppStore.ts:1750-1755 (refers to an unlock PATCH removed by #983; Undo doesn't restore that lock).

## 2 Undo toast
- removeFromList :900-955: snapshot only when opts.undoable; flag dropped on lock re-send. UNREC.
- applyBestCopy :1445-1531: before/after diff; merges with standing toast for the same group. REC.
- applyBulkDecision :1594-1650: infers "write landed" from manifest.error / lockConflict after setDecisions
  (:1628-1633) — an unrelated error suppresses the toast. UNREC (single home: setDecisions returns a result).
- Three `_toastSeq += 1` sites (:931, :1520, :1637) → single raiseToast().
- undoKeepBest vs undoSkipNow differ in lock policy (#999), result source (local merge vs server groups),
  manifest-switch guard (only skip undo, :1764), atomicity (keep-best undo = two non-atomic PATCHes).
- applyBulkDecide (Set Action) raises no toast and has no undo. UNREC.

## 3 Scope
- Reference visibleSelectedPaths (lib/rowFilter.ts:86) used by Toolbar.tsx:165, useAppStore.ts:1569.
- Raw selection incl. filter-hidden rows: App.tsx:109-111 → ContextMenu verbs :140,145,169,174,179;
  useDecisionShortcuts.ts:72-78; App.tsx:251-256 Skip Selected now (finalizes); App.tsx:241-245 →
  useAppStore.ts:795-804 Execute only selected (pulls in groups of hidden rows). UNREC.
- ExecuteDialog.tsx:251-254 local useMemo also named visibleSelectedPaths, different semantics. REC (R-EXEC-1).
- Keep-best "disabled under a filter" predicate twice: ContextMenu.tsx:104-106, ResultTree.tsx:819 (consistent).

## 4 Delete/skip confirm copy
- recycle:true single site (useAppStore.ts:848).
- DeleteConfirmDialog.tsx:193 "cannot easily be undone in bulk" + lowercase "recycle bin", vs :263-264 "can be
  restored" in the same dialog. UNREC.
- Execute-dialog row Skip: confirm, no undo (ExecuteDialog.tsx:337-346), staging label
  decision_long.remove_from_list (ExecuteContextMenu.tsx:162); main-tree Skip: no confirm, undo toast,
  context_menu.skip_now label (ContextMenu.tsx:149-170, comment :165-168 forbids the staging label). Label UNREC.
- ActionDialog.tsx:415-418 "Unlock and apply anyway?" states no consequence. UNREC.

## 5 Error display
- StatusBar.tsx:174-184 renders manifest.error (~10 writers) via describeApiError but prefixes every failed action
  with "Manifest error:". UNREC.
- ExecuteDialog.tsx:581-583, ActionDialog.tsx:624-626 render the RAW error string (no describeApiError). UNREC.
- revealInExplorer (useAppStore.ts:1213-1221) writes executeError → shows in the Execute dialog alert. UNREC.
- postPruneCandidates failure swallowed (:996-1000). REC (code comment).

## 6 Plurals
- Reference: per-word keys LockConfirmDialog.tsx:201-235.
- "(s)" survives: LockConfirmDialog.tsx:286 + translations/en.yml:913 (body_decision "row(s)"); ActionDialog.tsx:417
  + en.yml:696; DeleteConfirmDialog.tsx:148,180,187 + en.yml:1098 (also uses fileWord at :140). UNREC.
- Toast.tsx word keys at :76-79 but whole-sentence keys at :113-123. UNREC.
- Three copies of file/files: web.lock_confirm.file_*, web.toast.file_*, web.delete_confirm.file_*. UNREC.

## 7 Optimistic update
- setLock / setLocks / setDecisions optimistic + revert on any failure (consistent), except stale is_locked after a
  forced decision (1c).
- Only undoSkipNow and restoreToReview guard a manifest switch in flight (:1764, :1811). UNREC.
- undoKeepBest merges its own snapshot locally instead of server groups (:1692-1714). UNREC.

## 8 Button weight (F5, docs/design/rulings.md:161-166)
- Reference LockConfirmDialog.tsx:242, :339-347.
- Always red though none deletes a file: ActionDialog.tsx:447-453 Unlock & Apply; RemoveFromListConfirmDialog.tsx:69;
  DeleteConfirmDialog.tsx:280 "Mark N for deletion" mode (:151-157, :180). UNREC (F5 names only LockConfirmDialog).
- PruneConfirmDialog.tsx:222 default weight — consistent.

## Unconfirmed
- Group-header menu scope under a filter (memberPaths App.tsx:139 ← GroupRow.tsx:61-63 ← orderedItemsByGroup
  ResultTree.tsx:817; not read).
- Stale padlock after a forced decision: static read only.
- manifest.error visibility behind the Execute modal on a row-menu Skip failure: not checked visually.
- Empty Unlocked only for remove/restore sends file_paths []; models have no min_length (app/web/models.py:47,55);
  server result not traced.
- RescanConfirmDialog.tsx:86 red; no ruling found covering it.
