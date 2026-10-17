# Backend cross-cutting rule inventory (audit-be, master aaf240d, read-only)

Assembled verbatim from audit-be's report (summary + parts 1-4). Paths relative to the repo root.
Labels: **B** = known bug, **X** = recorded exception, **U** = unrecorded divergence. Code-level facts; no test or
reproduction was run.

## 0. Summary

**Top 5 rules by bug risk**
1. **The lock check is read on one connection and the write runs on another, with no lock condition in the SQL.**
   True for all 7 gated writers. Only the reconcile re-checks inside its UPDATE
   (`infrastructure/manifest_repository.py:204-214`). Execute, Skip now and prune write with `clear_lock=True`, so
   they also silently clear a lock another tab set after the read. (U)
2. **Skip now refuses only locked rows still in review, but writes every path it is given, whatever its outcome**
   (`core/app_service/execute_service.py:347-370` → `infrastructure/manifest_repository.py:164-166`). A `deleted`
   row becomes `ignored` with `executed=0`, and Undo then puts a deleted file back into review. A locked row that is
   already dismissed is unlocked with no 409. (U; reachable from a stale tab in principle, not reproduced)
3. **`set_decisions` finds locks through `load()`, which yields only visible rows** (`review_service.py:151`). A
   locked row in a group with <2 in-review members, or no group, skips the gate. Under `force_locked` its decision is
   written but its lock is not cleared (breaks R-LOCK-2). No check that `force_locked` and `skip_locked` are not both
   set (skip wins silently), while the other two decision verbs answer 422. (U)
4. **Path safety behaves four ways:** 403 at the route, "failed" in the result, silently dropped, or not checked.
   `set_decisions`, `set_locks`, `apply_best_copy` never check roots. Every service skips the check when
   `allowed_roots` is empty, while `is_under_roots` refuses on an empty list. (U)
5. **Three unlock primitives take a separate unlock list instead of unlocking the row they write**, so "unlock but
   don't apply" is one wrong argument away. The error-to-HTTP mapping is copied into 8 handlers that disagree. (U)

**The only primitives that should write a locked-row change:** `finalize_outcome(clear_lock=…)` and its wrapper
`remove_from_review` have the right shape (unlock tied to the row in the same statement). They still need
`AND outcome = ''` (`'ignored'` for restore) and a lock condition in the SQL. Decision writes and restores should get
a matching single-statement primitive that sets the value and clears the lock together, conditional on the lock.
`batch_update_lock_state` should be reserved for the explicit lock/unlock verbs.

## 1. LOCK: every write path

| Site | How it finds locks | Refuse or skip | force / skip_locked | Unlock primitive, same txn? | Lock condition in SQL | Root filter vs gate | Label |
|---|---|---|---|---|---|---|---|
| `set_decisions` `review_service.py:139-175` | `repo.load()` (visible only) :151-154 | 409 :165 | skip narrows :156-161; force :166-172; both set → skip wins, no 422 | `batch_update_decisions_and_lock` :169, one commit, two statements | none (`manifest_repository.py:156-162`) | no root filter | U: hidden locked rows bypass; no mutual-exclusion check |
| `bulk_decide` decision `action_service.py:201-239` | `_load_vm` records (visible only), consistent with its write set | 409 :223 | both set → 422 :148; skip :210-218; force :229-237 | `batch_update_decisions_and_lock` :233 | none | root :158-161 then gate :202 ✓ | consistent |
| `bulk_decide` lock/unlock :227-228 | n/a | never gated | n/a | `batch_update_lock_state` | none | root filtered ✓ | X (the lock verb itself) |
| `bulk_decide` preview :186-192 | not checked | never gated | n/a | no write | n/a | root ✓ | X (documented :182-185) |
| `apply_best_copy` `action_service.py:313-382` | group items from `_load_vm`; locked keeper counts toward 409 | 409 :370 | both → 422 :313; skip :363-366; force `setdefault(p, False)` :378-379 so keeper stays locked | `batch_update_decisions_and_lock` :382 (adds keeper lock=True) | none | no root filter | X (R-LOCK-2 excludes it; R-LOCK-7b); U: no root check; U: positional `group_number` stale after prune/execute (:317-322) |
| `set_locks` `review_service.py:194-195` | n/a | n/a | n/a | `batch_update_lock_state` | none | no root check | U (vs bulk lock) |
| `execute_decisions` `execute_service.py:181-282` | `_load_decided_rows` SQL :89-101 | 409 :210-211 | force: locked rows join batch :216-217 | `finalize_outcome(clear_lock=True)` per file :240, :257; batch skip :272; same statement ✓ | none (`manifest_repository.py:170-173`, no `AND outcome=''`) | root :195-205 then gate :209 ✓ (R-LOCK-4) | X: lock kept on failed/missing delete and failed skip write (#985); U: a lock set after the read is cleared |
| `remove_from_review` (Skip now) `execute_service.py:344-370` | SELECT by path where `outcome=''` :347-351 (no group rule) | 409 :366 | force → write | `repo.remove_from_review(clear_lock=True)` :370 | none: writes every safe path, any outcome | root :357-360 then gate :363 ✓ | U: rewrites deleted→ignored; unlocks an ignored locked row with no 409 |
| `restore_to_review` (Undo) `execute_service.py:438-456` | `_locked_skipped_paths` :391-399 | 409 :452 | force → `unlock_paths` | `restore_to_review(unlock_paths=…)` `manifest_repository.py:996-1003`: one txn, separate unconditional `_UPDATE_LOCK_SQL` | restore SQL `AND outcome='ignored'` :219-223; unlock unguarded | root :438-446 then gate :450 ✓ | consistent (#981). U: forced Skip now then Undo comes back unlocked (maybe part of #999, unconfirmed) |
| `prune_singletons` `execute_service.py:591-635` | `_fetch_singleton_rows` :475-488 | 409 :630 | force → write | `finalize_outcome(…,"ignored",clear_lock=True)` :635 | none (singleton status not re-checked) | scope + root :606-621 then gate :628 ✓ | X: dialog before request; B #1000 |
| `classify_singletons` `execute_service.py:517-538` | reads `is_locked` | read only | n/a | n/a | n/a | out-of-root dropped silently, not logged :527 | consistent |
| Reconcile `manifest_repository.py:541-563`, `_dismiss_skipped_rows` :592-613 | Python group-level lock check :541-554 | silent skip, logged :556-562 | none | never unlocks | `_RECONCILE_DISMISS_SQL` :204-214: `AND is_locked=0` + NOT EXISTS locked peer | n/a | X (R-LOCK-6); strongest model in the codebase |
| Post-scan auto-select `scanner/manifest.py:163-188` | n/a (fresh temp DB) | n/a | n/a | private `_UPDATE_DECISION_SQL` / `_UPDATE_LOCK_SQL` imported from another module | none | n/a | X in practice |
| `apply_auto_select_decisions` `core/services/auto_select.py:162` | none | none | n/a | `batch_update_decisions_and_lock` | none | none | U: no caller outside tests |
| `ManifestRepository.update_decision` :746, `.save` :728 | none | none | n/a | n/a | none | none | U: no caller outside tests; unguarded writers exposed |
| `DeleteService.plan_delete` `infrastructure/delete_service.py:63-88` | records | silently drops locked rows, logs warning | none | n/a | n/a | n/a | U: a third lock policy; no caller outside tests |

**Lock primitives and callers**

| Primitive | Callers | Risk |
|---|---|---|
| `batch_update_lock_state` :766 | `review_service.py:195`, `action_service.py:228` | Stand-alone, so "unlock first, write later" is easy (the pre-#985 shape). **Highest risk.** |
| `batch_update_decisions_and_lock` :785 | `review_service.py:169`, `action_service.py:233`, `:382`, `auto_select.py:162` | `lock_states` independent of `decisions`; can unlock paths it doesn't write. |
| `restore_to_review(unlock_paths)` :973 | `execute_service.py:454` | Unlock list independent; unlock not tied to the restore match. |
| `finalize_outcome(clear_lock)` :920 / `remove_from_review(clear_lock)` :960 | `execute_service.py:240, 257, 272, 370, 635` | Coupled to the row (good), but clears every row in the list; no outcome/lock check. |
| `_RECONCILE_DISMISS_SQL` :204 | reconcile only | Guarded in the statement; the model to copy. |

`execute_service` runs raw SQL reads outside the repository (:89, :344, :391, :475) and keeps its own `_connect`
(:130), so the repository is not the only path to the data.

## 2. Path safety and allowed roots

Route checks (`validate_under_roots` → 403): `post_execute` scope paths `execute.py:84-86` ✓; `post_remove`
`:195-196` ✓; `post_restore` `:257-258` ✓; `post_prune` only `manifest_path`, explicit `paths` not validated
`:302-306` (U); `patch_decision`, `patch_lock`, bulk-decide, apply-best-copy: nothing (U).

Service checks: Execute delete → `failed` as `outside_allowed_roots` (`execute_service.py:196-200`, X). Execute skip →
dropped silently (`:201-205`, U, asymmetric with delete). Skip now / restore / prune → dropped silently + logged
(`:357`, `:438`, `:617`, X, R-LOCK-5). classify / bulk decide → dropped silently, not logged. `set_decisions`,
`set_locks`, `apply_best_copy` → not checked (U).

Fails open: every service guards with `if allowed_roots:` (`execute_service.py:195, 357, 438, 527, 617`;
`action_service.py:158`), so an empty list skips the check, while `is_under_roots` refuses on an empty list
(`path_safety.py:49-50`) (U). `app.state.allowed_roots` starts `[]` (`app/web/main.py:74`); bulk decide runs with no
root filter after a server restart before any `GET /api/manifest` (U). Allowed roots come from the manifest's own
visible rows (`review_service.py:80-88` → `review.py:65-68`): the manifest authorises itself (U; design note).
Execute/Skip now/restore/prune/save check `manifest_path` against roots; action and review routes don't, on purpose
(`action.py:11-17`), not in rules.md (U).

## 3. Other rules with more than one home

Error-to-HTTP mapping copied per handler: `review.py:120-137`, `action.py:81-100`, `:165-181`, `execute.py:142-153`,
`:208-219`, `:271-282`, `:316-327`. Divergences (all U): bad decision value 400 on PATCH (`review.py:127`) vs bad bulk
action 422 (`action.py:98`); `matched_total` only in decision-verb 409s; prune prefixes "Prune failed:" on non-lock
ValueErrors; the `args[0] == "locked_paths"` string contract repeated 7 times.

Group-survivor rule (R-EXEC-2) implemented 3 times: `load()` `manifest_repository.py:690` (Python `len<2`; `if gid:`
excludes `''`); `_load_decided_rows` `execute_service.py:93-99` (`HAVING >= 2`, `group_id != ''`);
`_fetch_singleton_rows` `:480-485` (`HAVING = 1`, does not exclude `''`). Rows with `group_id=''` would count as a
singleton for prune only (U; existence unconfirmed).

Scope: Skip now and `set_decisions` accept any row (hidden, any outcome); Execute and bulk decide use visible rows;
prune uses singletons. `execute_lock` only serialises executes (`execute.py:119-131`); Skip now, prune, restore and
decision writes can run alongside an execute.

Recycle default in 3 places, all agree (`models.py:39`, `execute_service.py:144`, frontend `executeDecisions`).
Recycle and unlink branches duplicate the outcome-write and error code (`execute_service.py:239-247`, `:256-264`).

Audit log: live path writes group 0 on every row (`execute_service.py:288-290`); the dead path maps the real group
(`delete_service.py:196-205`). A failed skip write lands in the delete CSV, a successful skip does not; missing files
are left out. Skip now, prune, restore write no audit on purpose (`execute_service.py:413-416`). (U)

Transactions: every gated write reads and writes on separate connections. `set_decisions` and Execute call `load()`,
which runs the reconcile, so the reconcile also writes during a decision PATCH and Execute's reload. Execute commits
once per file on purpose ("D7" guarantee) — X, must not be folded into one transaction.

## 4. Per-caller rules a centralised mechanism must keep

- `skip_locked`: narrow the write; leave decision and lock as they are (`set_decisions`, bulk decide, Apply best copy;
  R-LOCK-7b). R-LOCK-7a happens in the browser.
- Apply best copy under force locks the keeper and only unlocks non-keepers; a locked keeper still counts toward 409.
- Execute unlocks a row only when its action lands; failed delete / missing file / failed skip write keep the lock.
- Execute out-of-root: deletes reported, skips silent, out-of-root locked rows not in `locked_paths`, keep their
  lock. Skip now, restore, prune drop out-of-root rows and keep their locks.
- Restore gates only rows both `ignored` and locked.
- Prune: no exception for locked singletons; dialog before the request.
- Reconcile: per group, never unlocks, never 409, fails soft, re-checks in the statement.
- Lock/unlock verbs and preview never gated.
- Execute commits per file and holds `execute_lock`.
- Post-scan lock write on a fresh DB: nothing to gate.

Not confirmed: whether `group_id=''` rows exist in real manifests; whether forced-Skip-then-Undo lock loss is #999.
