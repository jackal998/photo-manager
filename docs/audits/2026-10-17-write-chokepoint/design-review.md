# Review: design-chokepoint.md (fresh, skeptical, read-only)

Reviewer: rev-design. Base: worktree `audit-1017` at `aaf240d`. Static read only; nothing was run.
Every file:line below was re-read at `aaf240d` unless marked "design's own claim".

## Verdict: ADOPT WITH CHANGES

The diagnosis is right. The six bugs come from a missing chokepoint, not from copied text, and the
central server move is the correct lever: tie the unlock to the statement that writes the row, and
put the lock condition inside that statement (the `_RECONCILE_DISMISS_SQL` model,
`manifest_repository.py:204-214`). The verified bugs are real. The exception table is mostly
complete. Three things need to change before code is written:

1. The program is about three times the size the bug class needs. Roughly 8 PRs fix the class;
   the other 14 are consistency work and should be a separate, optional program.
2. Two parts of the core mechanism have design defects: the `DecisionWrite` invariant is checked
   in the wrong layer, and the gate stays outside the primitive, so a future path that forgets it
   fails silently (the #983 shape) instead of failing loudly.
3. Fixes are queued behind refactors. The two OPEN bugs (#999 and #1000) and one real data bug (B-4)
   should land first, as small direct fixes.

---

## Axis 1: necessity and size

The six bugs fall into four shapes. #981, #982 and #983 are a gate missing or wrong on one path.
#985 is an unlock separated from its apply. #999 is a blind force from the client. #1000 is a 409
the client did not route. A PR is needed only if it removes one of those shapes or fixes a
verified bug of the same kind.

### Minimal program (makes the class impossible)

| Order | PR | Why it is needed |
|---|---|---|
| 1 | **PR-01** (#1000), plus the transport probe from 3.1 | open bug; it is small and independent |
| 2 | **PR-05**, done as a direct fix on today's code (`AND outcome = ''` on the Skip-now write, `removed` = rows written) | B-4 is real data corruption (a `deleted` row comes back to review), and the fix is a few lines. It does not need PR-02..04 first |
| 3 | **PR-03 + PR-07(a), merged and narrowed**: guarded statements (lock condition + outcome condition), unlock tied to the written row, `restore_to_review(unlock_paths)` removed, `LockedRowsError` type (the type only, not the route mapper), `BEGIN IMMEDIATE` for the non-Execute writes | this is the chokepoint. Do it as one FIX PR with red-first tests in the style of `test_manifest_skip_reconcile.py:484` (stale gate read). Splitting it into a REFACTOR that ships today's unguarded SQL in a new shape, and then a FIX, doubles the review cost for no safety gain |
| 4 | **PR-04** (delete the dead unguarded writers, plus the `is_locked`/`sqlite3` probes) | the dead writers are the bypass the probe would otherwise have to allow-list |
| 5 | **PR-06** (B-5, B-6) | B-5 is a #981-shape gate miss (`review_service.py:151-154` reads locks through `load()`, which yields only visible rows) |
| 6 | **PR-15a**: `confirmed_locked` for PATCH /api/decision only, then **PR-16** (#999) | the server's per-path confirmation is the real backstop for #999; the client brand is not (see axis 2). Splitting PR-15 lets #999 ship early instead of at position 16 |
| 7 | **PR-15b**: `confirmed_locked` for the other six verbs (B-2, B-3) | the owner's ruling (confirm every lock) |
| 8 | **PR-07(b)**: Execute's per-file re-check (D6), measured on the rig | the only part with real perf risk; keep it alone |

That is about 9 PRs (PR-15 split in two), against 22.

### Nice-to-have consistency (separate program, each optional)

- PR-02's `service_errors` mapper. The 7 routes map the 409 correctly today (verified:
  `review.py:120-126`, `action.py:89-98`, `execute.py:316-327`). The mapper takes five parameters
  to reproduce eight different handlers (2.4 table). That moves the differences around without
  removing them, and it fixes no bug. Keep only the `LockedRowsError` type (it goes into step 3).
- PR-08 and PR-09 (roots). B-7 needs a server restart followed by a bulk-decide before any
  GET /api/manifest, and the UI always opens the manifest first. B-8 is not a bug if D3 = (b),
  which is the design's own lean. No file boundary is involved: Execute 403s on `manifest_path`
  when the roots list is empty (`execute.py:65-67`, design's own claim, consistent with
  `main.py:74`). Defer the whole roots track.
- PR-10 and PR-11 (client `lockGate.ts`, `WriteOutcome`). They are useful, but the bugs they
  carry (B-11..B-15, PR-12) can be fixed in place: put `undoable`/`caller` on `lockConflict` and
  guard empty scopes in `LockConfirmDialog.tsx:110-183`. PR-13 (B-16, B-17) and PR-14 (#942) are
  independent small fixes.
- PR-17..22: these depend on decisions and are UX work. They are not lock-bypass work.

### Speculative or over-built pieces

- `service_errors` (above).
- `GatePlan` plus `LockVerdict` plus `verdict_from_flags` is acceptable (7 callers). The
  transitional `both="skip"` and `from_outcome=None` shims exist only because the design splits
  REFACTOR from FIX in PR-03. Merging PR-03 with PR-07(a) removes them.
- `SKIP_PER_R_LOCK_7B` and `ownLocks` as named mint points: they are fine, but they show that the
  rule "only the dialog mints a force" already has three exceptions.

---

## Axis 2: correctness of the core mechanism

**Sound:**
- The guarded UPDATE (`AND (is_locked = 0 OR :confirmed = 1)`, plus `AND outcome = :from`) does
  make "unlock without apply" impossible. The unlock lives in the same statement as the write, so
  no separate list can drift. This follows the reconcile model, which is pinned by R-LOCK-6.
- `BEGIN IMMEDIATE` is right for SQLite with WAL (`PRAGMA journal_mode = WAL`,
  `manifest_repository.py:49`). Readers are not blocked. It avoids the deferred read-to-write
  upgrade failing with SQLITE_BUSY_SNAPSHOT, which a busy handler cannot retry. The design is
  correct that the Python default `timeout=5.0` applies (`sqlite3.connect(path)`, `:48`, sets none).
  WAL on an SMB-hosted file is a risk that exists today. One host and one process keep it working.
  PR-07 does not make it worse.

**Defects:**

1. **HIGH: the `DecisionWrite.__post_init__` invariant sits in the wrong layer** (design 2.3).
   `lock_after is False and not confirmed -> ValueError` also fires on a row that is currently
   UNLOCKED, because the dataclass cannot know the row's state. The design's own Undo (3.3 step 2)
   sends `locked: before.locked`, which is `false` for every toolbar-Undo row (the toolbar filters
   locked rows out, R-LOCK-7a). So every such row would raise. The other way out is to mark them
   `confirmed`, which defeats the purpose. The SQL guard already enforces the real invariant ("an
   unconfirmed locked row is never touched"). Drop the dataclass check, or map
   `locked: false` on an unconfirmed row to `lock_after=None`, and pin that with a test.
2. **HIGH: the gate stays outside the primitive, so a missed gate fails silently.**
   `plan_locked_write` is called by the service, and the primitive only refuses inside SQL. A
   future write path that forgets `plan_locked_write` therefore gets a silently skipped row and a
   200. That is exactly #983 ("skips locked singletons silently instead of refusing with 409").
   `write_decisions` returns the rows that landed, but nothing in the design says a shortfall must
   raise. Fix: inside the open transaction, the primitive itself selects the locked,
   unconfirmed rows among its inputs and raises `LockedRowsError` (rollback, then 409).
   `plan_locked_write` keeps only the SKIP narrowing and `matched_total`. Do not compare against
   `rowcount`: `source_path` is not unique (`manifest_repository.py:197-198`), so the count can
   be larger than the number of paths.

   ```
   service --(scope, verdict)--> plan_locked_write --(writes, confirmed)--> tx.write_*()
                                   SKIP narrowing only                        | SELECT locked&~confirmed
                                                                              | non-empty -> LockedRowsError
                                                                              v guarded UPDATE
   ```
3. **MEDIUM: self-deadlock risk.** `load()` writes on its own connection: `ensure_schema` plus the
   reconcile dismiss (`manifest_repository.py:650-660`, `:563-613`). `_load_vm`
   (`action_service.py:53-66`) calls it. Suppose bulk_decide or apply_best_copy call `_load_vm`
   (or any `ensure_schema`) after `write_txn` has taken RESERVED. The second connection then waits
   the full 5 s and fails. The reconcile fails soft, so this shows up as a mysterious +5 s rather
   than an error. The design must state the ordering rule (no `load()` or `ensure_schema` while a
   `write_txn` is open), and a test must pin it.
4. **MEDIUM: `locked_in(scope)` should not be an IN-list.** A bulk decide over a large manifest
   (the author's has 40,774 rows) can pass the SQLite variable limit. `source_path` has no index
   (`:191-198`: 36 s for a path-keyed batch). Read `WHERE is_locked = 1` (a small set) and
   intersect in Python. That costs one cheap query whatever the scope size, and it is held under
   `BEGIN IMMEDIATE`.
5. **LOW: Execute's re-check still leaves a window, and its cost is unspecified.** A lock set
   during `send2trash` is cleared by the unguarded `record_deleted`. That is acceptable, since the
   file is gone, but "impossible" overstates it. The re-check is a read, so it needs no
   `BEGIN IMMEDIATE`. Run it on the same connection as `record_deleted` (or as one
   `is_locked = 1` set query) to avoid a second cold path-keyed scan per file on the NAS.
6. **Client "impossible" is overstated.** `unique symbol` brands vanish at runtime, and
   `x as ConfirmedLocks` or a literal `{kind: "force", ...}` bypasses them without using any key
   the probe greps for. Extend the probe to `as ConfirmedLocks` and `kind: "force"` outside
   `lockGate.ts`. More to the point, call the client layer a tripwire. PR-15's server-side named
   confirmation is what makes a blind force impossible. Even then a client can name too much
   (a wrong `ownLocks`), so the honest claim is "no boolean force exists".

---

## Axis 3: lost constraints

The section-5 table covers every item on both must-keep lists (BE §4, 10 items; FE "would LOSE",
7 items). Gaps:

| Item | Status |
|---|---|
| BE-15 `db_write_failed` (file gone, DB write failed, still a success) | **paper only.** `grep db_write_failed tests/` finds no hits. PR-03 folds the recycle and unlink branches, which is exactly this code (`execute_service.py:236-264`). Add a pin before PR-03 |
| BE-16 no audit CSV for Skip now, prune, restore | no pin found by grep (only `test_audit_csv_lands_in_pytest_temp...` `test_web_execute_service.py:661` for Execute). Low risk, but the table claims it as kept |
| Auto-migrate before write: `finalize_outcome` and `restore_to_review` call `ensure_schema` first (docstring at `manifest_repository.py:940-948`) | **missing from section 5.** `write_txn` must call it *before* BEGIN IMMEDIATE (see axis 2.3) |
| `source_path` not unique, so a path-keyed write touches every duplicate; `restore` returns `cursor.rowcount` as `restored` | missing. `transition -> list[str]` changes the `restored` count semantics |
| PR-06 drops `load()` from PATCH, and with it the reconcile and the auto-migrate per PATCH | stated in PR-06 but not in section 5; it is an observable side-effect change |
| BE-3 keeper stays locked under force | pinned (`test_web_action_routes.py:1034`) |

---

## Axis 4: REFACTOR versus FIX labels (spot-checked 5)

- **PR-02, REFACTOR: holds** if `except` order is preserved. `re.error` is not a ValueError, and
  `FileNotFoundError` is not one either. The route pins assert status and detail shape. Verdict:
  correctly labelled, but low value (axis 1).
- **PR-03, REFACTOR: doubtful.** (a) Section 2.3 defines `write_txn` as `BEGIN IMMEDIATE`, and
  PR-07 says it *adds* BEGIN IMMEDIATE. If PR-03 ships the 2.3 definition, that is a transaction
  timing change inside a REFACTOR. State it either way. (b) Folding the recycle and unlink branches
  touches failure-reason strings (`result.failed[0][1]` / `"unknown"` versus `str(exc)`,
  `execute_service.py:250-252`, `:263-264`) and BE-15, and BE-15 has no pin. (c) The cited pins
  (R-LOCK-*, R-SKIP-1) do not cover `db_write_failed` or the reason strings, so a regression there
  goes green.
- **PR-08, REFACTOR: small behaviour change.** "Uniform log line" adds a per-path warning to
  classify and bulk decide, which log nothing today (BE §2). On a large out-of-root bulk decide
  that floods the log. The behaviour is otherwise neutral.
- **PR-11, REFACTOR: plausible.** Error-path parity with #942 is stated explicitly. Pins: s32, s75,
  `ActionDialog.test.tsx`. Acceptable.
- **PR-10, REFACTOR: plausible.** Moving the table verbatim keeps the bugs (B-11..14) by design.
  Pins: `LockConfirmDialog.test.tsx`, s32/s34/s36/s53.

---

## Axis 5: bug list (verified 6)

| Bug | Verdict | Evidence |
|---|---|---|
| B-3 prune force covers the whole list | **real**, but a sub-case of B-2 | `useAppStore.ts:1069-1071`: `force` is one boolean, true if any confirmed path is in `paths`; sent as one `force_locked` (`:1088`) |
| B-4 Skip now rewrites `deleted` as `ignored` | **real** | lock SELECT `AND outcome = ''` (`execute_service.py:347-351`); write over all `safe_paths` (`:370`); SQL has no outcome condition (`manifest_repository.py:170-173`) |
| B-5 set_decisions misses hidden locked rows | **real** (needs a stale tab or a direct API call) | `review_service.py:151-154` builds `locked` from `load()`; unforced falls through to `batch_update_decisions` (`:174`) |
| B-6 both flags: skip wins | **real** | `review_service.py:156` checks `skip_locked` first, with no mutual-exclusion check |
| B-16 stale padlock after a forced decision | **real** (static) | `setDecisions` success path has no lock refresh (`useAppStore.ts:430-436`); re-send at `LockConfirmDialog.tsx:135` |
| B-1 read and write on separate connections | **real** | `_load_decided_rows` `execute_service.py:89-104` (own conn) versus `finalize_outcome` (`manifest_repository.py:951-957`, new conn) |

Overstated:
- **B-7 and B-8** are consistency issues, not defects. B-8 is not a bug at all under the design's
  own D3 lean (b).
- **B-14**: the server handles `IN ()` and empty lists harmlessly
  (`manifest_repository.py:946-947` returns early). The design itself says "server effect not
  traced". It is a wasted round-trip at most.

---

## Axis 6: owner decisions

- D7 is already answered by the owner's ruling 「…需要再次確認」. Confirm it; don't re-litigate it.
- D2 and D3 are technical policy, not product choices. With the roots track deferred, neither
  needs asking now.
- D1, D4, D5, D6 are genuine product choices with fair options and reasonable leans.
- **Product choices hidden inside PRs:**
  1. **PR-16**: an Undo of a *forced* Keep best whose keeper was locked before the action
     (pinned behaviour: `test_force_locked_keeper_already_locked_stays_locked`,
     `test_web_action_routes.py:1034`) will now answer 409 and re-ask, because `ownLocks` excludes
     pre-existing locks. That may be correct under the ruling, but it is user-visible: "Undo asks
     to confirm a lock I already confirmed". Ask the owner.
  2. **PR-12 / B-11**: adding the Undo toast after "Unlock & Apply" on Skip now offers an Undo that
     brings the rows back *unlocked* until D5 / PR-17 lands. Ship only the "Unlocked only" half
     (that half really is R-SKIP-1) until D5 is decided.
  3. **B-14 "send nothing for every op"**: for Execute, today's empty re-send still returns a
     result the dialog shows. Sending nothing changes what the user sees.
  4. **PR-07** carries D6 but its Depends-on column lists only PR-06. Add D6.

---

## Axis 7: sequencing

- Placing the riskiest step late and gating it on a measurement is sensible, but PR-07 bundles five
  changes: BEGIN IMMEDIATE, the guards, the restore migration, the bulk/keep-best lock read, and
  the Execute re-check (D6). Only the last has real NAS-perf exposure. Split it. Also measure a
  PATCH *during* a running Execute, not only Execute itself, because the PATCH is now the side
  that waits.
- Open bugs wait behind refactors: #999 sits at position 16 (behind PR-15, PR-13, PR-07) and #942
  at position 14. A program that stalls halfway leaves both open, which is today's state, so it is
  no worse. It is still the wrong order for a 22-PR effort. Fix first (PR-01, PR-05, PR-15a+16),
  then refactor onto the fixed code, so the pins protect the new behaviour.
- The "stacked for rules.md" chaining (PR-05 → PR-06 → PR-07) serialises PRs over trivial
  register conflicts. It is not a real dependency.
- A half-finished state that would be worse than today: PR-12 without PR-17 (see axis 6.2). Every
  other prefix of the plan leaves the app no worse than today.
- Transitional shims (`both="skip"`, `from_outcome=None`) survive if the program stops after PR-03
  or PR-04. Merging PR-03 with PR-07(a) avoids them.

## Not verified

- I did not run tests. The pin sufficiency for PR-10 and PR-11 is from their names only.
- I did not trace whether real manifests contain duplicate `source_path` rows. B-17, B-13 and
  B-20 were taken from the design and not re-read.
