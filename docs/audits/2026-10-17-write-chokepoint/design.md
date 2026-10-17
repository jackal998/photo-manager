# Design: one owner per write rule (lock gate, roots, errors, undo)

Status: proposal for owner review, **revision 3** (rev 3 widens the A5
write-path probe, adds a PATCH-during-Execute measurement to A4, corrects the
named-confirmation claim in 3.2, notes that A4 supersedes A2, and records the
reviewer's view on D10). No code has changed. Base:
master `aaf240d`. Inputs: `audit-be_inventory.md`, `audit-fe_inventory.md`, the
fresh review `rev-design_review.md`, and the code at `aaf240d`. Every claim
this design depends on was re-read there, and file:line refers to that
checkout. `docs/behaviour/rules.md` and `docs/design/rulings.md` were also read.

Reading guide: sections 1-3 are the design, section 5 shows nothing is lost,
section 7 is the plan (Track A required, Track B optional), and section 8 holds
the only questions for you. Sections marked "(rev 2)" changed after the review.

**What changed in rev 2.** (1) The rule "an unconfirmed locked row is never
touched" now lives in the SQL and in the primitive's own refusal, not in a
dataclass check. (2) The 409 refusal moved *into* the write primitive, so a
future path that forgets the gate still gets a 409 and never a silent 200.
(3) There is an ordering rule against self-deadlock. (4) Locks are read as
`WHERE is_locked = 1` instead of an IN-list. (5) A `db_write_failed` pin is
added, and auto-migrate is in the checklist. (6) The Execute re-check is its
own PR and is measured. (7) The client layer is described as a tripwire.
(8) The labels are corrected. (9) The plan is now Track A (9 PRs) plus an
optional Track B. (10) Section 8 has two new questions.

---

## 1. Diagnosis

1. The problem is a **missing chokepoint**, not copied code. Very little text
   is duplicated. What is duplicated is the *decision* "may this write touch a
   locked row?", and each write path makes that decision its own way.
2. Seven service writers gate locks (`set_decisions`, `bulk_decide`,
   `apply_best_copy`, `execute_decisions`, `remove_from_review`,
   `restore_to_review`, `prune_singletons`). They find locks in six different
   ways: `load()`, `_load_vm` records, `_load_decided_rows`, an inline SELECT,
   `_locked_skipped_paths`, and `_fetch_singleton_rows`.
3. The repository offers four public ways to clear a lock:
   `batch_update_lock_state` (`manifest_repository.py:766`),
   `batch_update_decisions_and_lock` (`:785`), `restore_to_review(unlock_paths)`
   (`:973`), and `finalize_outcome(clear_lock)` (`:920`). Three of them take an
   unlock list that is separate from the rows being written, so "unlock without
   applying" is one wrong argument away. That is the #985 shape.
4. The 409 contract is the string `ValueError("locked_paths", ...)`. It is
   parsed in 7 route branches (`review.py:122`, `action.py:90`, `:169`,
   `execute.py:146`, `:212`, `:275`, `:320`). The browser routes the 409 three
   ways: store `lockConflict`, a component-local prompt, and not at all for
   prune. It re-sends from a 5-way `if` in a component
   (`LockConfirmDialog.tsx:110-183`).
5. SOLID/DRY verdict: the *knowledge* of the lock rule has no single home, and
   the repository's interface allows the illegal state "lock cleared, value not
   written". Each of #981 #982 #983 #985 #999 #1000 is one path that made the
   decision differently. The fix is one owning write primitive per kind of
   write, which refuses unconfirmed locks itself. It is not a framework.

---

## 2. Server design (rev 2)

### 2.1 Shape of every guarded write

```
 service ──(scope, verdict)──> plan_locked_write          (SKIP narrowing,
   │  all load()/_load_vm/ensure_schema BEFORE the txn      matched_total)
   v
 with repo.write_txn(manifest) as tx:     ensure_schema, then BEGIN IMMEDIATE
   │ tx.write_decisions(writes, confirmed) | tx.transition(paths, confirmed)
   │   ├─ SELECT source_path WHERE is_locked = 1 [AND outcome = from]
   │   │  ∩ write set − confirmed ≠ ∅ ──> LockedRowsError ──> rollback ──> 409
   │   └─ guarded UPDATE (lock + outcome condition in the statement)
 COMMIT ──> service reloads groups (after the txn) ──> route ──> 200
```

The primitive owns the refusal, so a caller cannot forget it. The service owns
only the narrowing. That covers which rows are in scope, whether SKIP drops the
locked ones, and which locked rows the user confirmed.

### 2.2 The gate type and planner (new `core/app_service/lock_gate.py`, ~50 lines)

```python
class LockedRowsError(ValueError):
    """The one lock-conflict signal. Subclasses ValueError and keeps
    args == ("locked_paths", paths[, matched_total]), so every existing
    service pin (e.g. test_web_execute_service.py:969, :1082, :1133) and the
    7 route branches keep working unchanged."""
    locked_paths: list[str]
    matched_total: int | None

class LockVerdict(Enum):
    ASK = "ask"; FORCE = "force"; SKIP = "skip"

def verdict_from_flags(force_locked, skip_locked) -> LockVerdict:
    """Both set -> plain ValueError (-> 422), for every verb (fixes B-6)."""

def plan_locked_write(scope, locked_now: set[str], verdict, *, skip_allowed,
                      named: Collection[str] | None = None) -> tuple[list[str], frozenset[str]]:
    """Returns (paths to write, confirmed locked paths). It does NOT refuse.
    ASK   -> (scope, {})                    the primitive refuses if any is locked
    FORCE -> (scope, named or locked_now∩scope)   a boolean force confirms what it
                                            finds, until A6/A8 make it a list
    SKIP  -> (scope - locked_now, {})       only where skip_allowed"""
```

`locked_now` comes from `tx.locked_paths()` inside the transaction, so the plan
and the write see the same state.

### 2.3 The guarded write primitives (`infrastructure/manifest_repository.py`) (rev 2)

```python
@contextmanager
def write_txn(self, manifest_path) -> Iterator[ManifestTxn]:
    """ensure_schema first (auto-migrate, as finalize_outcome :949 and
    restore_to_review :993 do today); then one connection, BEGIN IMMEDIATE;
    commit on exit, rollback on any exception. Marks the manifest as having an
    open txn on this thread (see the ordering rule in 2.4)."""

@dataclass(frozen=True)
class DecisionWrite:
    path: str
    decision: str
    lock_after: bool | None = None
    # None  = unlock if the row is confirmed, otherwise leave the lock as it is
    # True  = lock it (Keep best's keeper; Undo restoring a prior lock)
    # False = unlock it; on an unlocked row this is a no-op, and on a locked
    #         row it needs that row to be confirmed, like any other write.
    # No check here: the dataclass cannot see the row's state. The SQL guard
    # and tx.refuse_unconfirmed enforce the rule.

class ManifestTxn:
    def locked_paths(self, *, outcome: str | None = None) -> set[str]
    def refuse_unconfirmed(self, paths, confirmed, *, outcome=None, matched_total=None) -> None
    def write_decisions(self, writes, *, confirmed, matched_total=None) -> int
    def transition(self, paths, *, from_outcome, to_outcome, confirmed,
                   lock_after=False) -> int            # rows changed (rowcount)
    def record_deleted(self, path) -> None              # Execute, after the file is gone
```

**One lock read, no IN-list.** `locked_paths` runs
`SELECT source_path FROM migration_manifest WHERE is_locked = 1 [AND outcome = ?]`
and the caller intersects it in Python. The locked set is small, and the query
is one table pass whatever the scope size. An IN-list over a 40,774-row
manifest could exceed SQLite's variable limit. `source_path` also has no index
(`manifest_repository.py:191-198`), so a per-path lookup would cost one scan per
path. Today's IN-list reads (`execute_service.py:347-351`, `:391-399`) go away.

**The refusal is inside the primitive.** `write_decisions` and `transition`
first call `refuse_unconfirmed`. It takes
`locked_paths(outcome=…) ∩ set(paths) − confirmed`, and if that is non-empty it
raises `LockedRowsError(sorted(...), matched_total)`, which rolls the
transaction back. Only then does the UPDATE run. The check is set-based, not a
`rowcount` comparison, because `source_path` is not unique
(`manifest_repository.py:197-198`), so a rowcount can exceed the path count.
For `transition` the refusal reads only locks in `from_outcome`. That keeps
BE-7 (restore gates only rows both `ignored` and locked) and means a locked
`deleted` row never blocks a Skip now that would not touch it anyway.

The SQL, one statement per row (executemany):

```sql
-- decision write: value, lock and lock condition in ONE statement
UPDATE migration_manifest
   SET user_decision = :decision,
       is_locked = CASE WHEN :lock_after IS NOT NULL THEN :lock_after
                        WHEN :confirmed = 1          THEN 0
                        ELSE is_locked END
 WHERE source_path = :path
   AND (is_locked = 0 OR :confirmed = 1);

-- outcome transition: Skip now, Execute skip, prune ('' -> 'ignored'), restore ('ignored' -> '')
UPDATE migration_manifest
   SET outcome = :to_outcome, executed = :executed, is_locked = :lock_after
 WHERE source_path = :path
   AND outcome = :from_outcome
   AND (is_locked = 0 OR :confirmed = 1);

-- Execute delete, after send2trash succeeded: records a fact, so no guard
UPDATE migration_manifest SET outcome = 'deleted', executed = 1, is_locked = 0
 WHERE source_path = ?;
```

The SQL condition is the second line of defence. Inside `BEGIN IMMEDIATE`, no
other writer can lock a row between the refusal read and the UPDATE. The
condition still stops a caller that passes a wrong `confirmed` set from
clearing any lock outside it.

**Lock verbs** stay as they are: `_UPDATE_LOCK_SQL` via `batch_update_lock_state`,
called only by `set_locks` and `bulk_decide`'s `__lock__` / `__unlock__`. A
probe restricts its callers.

**Deleted or made private** (A5). The first four have no production caller; at
`aaf240d` only tests call them, verified by grep over `*.py`.

| Primitive | Fate |
|---|---|
| `ManifestRepository.update_decision` `:746` | delete with its tests |
| `ManifestRepository.save` `:728` | delete with its tests |
| `core/services/auto_select.py::apply_auto_select_decisions` `:162` | delete with its tests (the post-scan path uses `build_auto_select_writes` directly) |
| `DeleteService.plan_delete` `delete_service.py:63-88` and its plan executor | delete with tests, after a grep confirms no production caller |
| `batch_update_decisions` `:755`, `batch_update_decisions_and_lock` `:785` | replaced by `write_decisions` (A3) |
| `finalize_outcome` `:920`, `remove_from_review` `:960`, `restore_to_review(unlock_paths)` `:973` | replaced by `transition` and `record_deleted` (A4) |
| `_UPDATE_DECISION_SQL` / `_UPDATE_LOCK_SQL` | kept; their one outside importer, `scanner/manifest.py:165-188` (fresh temp DB, nothing to gate), is allow-listed in the probe |
| `_RECONCILE_DISMISS_SQL` `:204` | kept as is; it is the model this design copies |

**Write-path probe (A5, `tests/test_source_probes.py`) (rev 3).** It scans
production `*.py` (not `tests/`) and fails on two things:
(a) any SQL text `UPDATE migration_manifest … SET` that assigns
`user_decision`, `outcome` or `is_locked`;
(b) any reference to `_UPDATE_DECISION_SQL` or `_UPDATE_LOCK_SQL`.
Each is allowed only in the guarded `ManifestTxn` statements (`write_decisions`,
`transition`, `record_deleted`) and in this explicit allow-list:

| Allowed | Where (at `aaf240d`) | Why |
|---|---|---|
| reconcile dismiss `_RECONCILE_DISMISS_SQL` | `manifest_repository.py:204-214` (UPDATE at `:205`) | system write; guarded in its own statement (R-LOCK-6) |
| lock verb `_UPDATE_LOCK_SQL` in `batch_update_lock_state` | `:160-162` (UPDATE at `:161`), `:766-783` | the lock toggle itself, never gated |
| scanner fresh-DB write | `scanner/manifest.py:163-188` (imports `_UPDATE_DECISION_SQL`, `_UPDATE_LOCK_SQL`) | a new temp DB, so nothing can be locked yet |

Pinning the constants as well as the SQL text matters for two reasons. First,
`_UPDATE_DECISION_SQL` (`:156-158`) stays in the repository for the scanner,
so a future caller could reach it by name and write a decision over a locked
row without any new SQL text. Second, that is exactly the #981/#983 shape: an
unguarded `outcome` or `user_decision` write. Once A4 and A5 land,
`_FINALIZE_OUTCOME_SQL` (`:164-166`), `_FINALIZE_OUTCOME_CLEAR_LOCK_SQL`
(`:170-173`) and `_RESTORE_TO_REVIEW_SQL` (`:219-223`) are gone. `rescore`'s
`UPDATE … SET score` (`:911`) writes none of the three columns, so it passes.
The probe gets a must-fail fixture for each case (a new UPDATE text, and a new
reference to each constant). It also gets a must-pass fixture: today's
allow-listed sites.

### 2.4 Ordering rule against self-deadlock (rev 2, new)

`load()` writes on connections of its own. It calls `ensure_schema`
(`manifest_repository.py:654`), which can commit a migration (`:339`), and then
the reconcile dismiss (`:670` → `:565-613`). `_load_vm`
(`action_service.py:53-66`) calls `load()`. If either runs while this thread
holds a `write_txn` on the same manifest (RESERVED lock), its write waits out
the default 5 s busy timeout (`sqlite3.connect(path)` sets none, `:48`). The
reconcile fails soft (`:556-562`), so the symptom would be a silent extra 5 s.

**Rule:** no `load()`, `_load_vm`, `ensure_schema` or
`reconcile_skip_directory_rows` while a `write_txn` is open on that manifest.
Reads that define scope run before the transaction. The post-write reload runs
after the commit. `write_txn` itself calls `ensure_schema` before
`BEGIN IMMEDIATE`.

**Enforced, not only written:** `write_txn` records the manifest path in a
thread-local set. `ensure_schema` and `reconcile_skip_directory_rows` raise
`RuntimeError("manifest write inside an open write_txn")` at once if their path
is in that set. Test (A3): `with repo.write_txn(p): repo.load(p)` raises
immediately (assert elapsed < 1 s), and `bulk_decide` on a manifest that holds a
skip-directory reconcile candidate completes with the candidate dismissed.

### 2.5 Error-to-HTTP mapping (Track B only) (rev 2)

The 7 routes map the 409 correctly today (`review.py:120-126`,
`action.py:89-98`, `execute.py:316-327`). Because `LockedRowsError` keeps the
`args` shape, Track A needs no route change. The single mapper
(`app/web/routes/_errors.py::service_errors`, with explicit per-route
parameters for 400 vs 422, the "Prune failed:" prefix, `group_not_found`, and
`execute_already_running`) moves the differences into one place but fixes no
bug. It is Track B item B1.

### 2.6 Roots policy (Track B only) (rev 2)

`split_by_roots(paths, roots: Sequence[str] | None, verb)` (`None` = trusted
in-process caller; a list is checked; an empty list lets nothing in) would
replace the six `if allowed_roots:` filters (`execute_service.py:195`, `:357`,
`:438`, `:527`, `:617`; `action_service.py:158`). Over HTTP the empty-list case
reaches only bulk-decide. The execute, remove, restore and prune routes return
403 first on `manifest_path` (`execute.py:65-67`, `_path_guard.py:69-74`).
Allowed roots are the folders of the manifest's own visible rows
(`review_service.py:80-88`). So this is consistency work, not a file-safety
hole. It is Track B item B2, together with D2, D3 and a proposed roots rule (SAFE-1, not yet in the register).

---

## 3. Client design (rev 2)

### 3.1 What Track A changes on the client

- **#1000 (A1).** `postPrune` goes through `postJsonOrConflict` (`client.ts:321`).
  A late 409 routes to op `prune` with a `prunePending` built from the request,
  so the existing verdicts work.
- **Named confirmation (A6, A8).** The dialog's "Unlock & Apply" re-sends
  `confirmed_locked: lockConflict.paths`, never a boolean. The store's
  `forceLocked?: boolean` becomes `confirmedLocked?: string[]`. Prune sends only
  the lock-confirmed singletons (`useAppStore.ts:1070-1091`).
- **Undo (A7, #999).** See 3.3.

### 3.2 What the client layer is, honestly (rev 2)

The client cannot make a blind force impossible. A TypeScript brand vanishes
at runtime, and `x as ConfirmedLocks` or a literal `{ kind: "force" }`
bypasses it. The **backstop is the server**. After A8 the HTTP models accept
no boolean force, and every forced write must name the locked paths it
confirms. A locked row that is not named answers 409. The honest claim is
**"no boolean force exists over HTTP"**. (rev 3) A client can still name too
much, for example a wrong `actionLocked`, or the full scope. The server can
only bound a confirmation to the paths named. It cannot tell whether the user
ever saw those rows as locked. Naming is therefore a narrower contract than
the old boolean, not a proof of consent. The tripwire probe below and review
of the few places that build `confirmed_locked` cover the rest.

The client probe (A1, extended in A8) is a **tripwire**. It catches the
likely regression, not every possible one. It fails if `force_locked`,
`forceLocked: true`, `confirmed_locked`, `as ConfirmedLocks` or
`kind: "force"` appears in `frontend/src` (excluding tests) outside the lock
module and `api/client.ts`. It also fails if a row-writing endpoint in
`client.ts` does not use `jsonOrConflict`. The one exception is `patchLocks`
(`client.ts:174`), because the lock toggle is never gated
(`execute-action.md:143`).

### 3.3 Undo under named confirmation (A7) (rev 2)

Today `undoKeepBest` sends `forceLocked: true` (`useAppStore.ts:1674-1680`).
Then a second, separate PATCH rewrites every snapshot lock (`:1685-1691`). New
shape, one request:

1. At action time the toast records `actionLocked`: the rows whose lock the
   action itself created (the keeper: `snapshot.locked === false` and
   `is_locked === true` in the response groups). The toolbar toast has none.
2. Undo sends one `PATCH /api/decision`. Each entry is
   `{ file_path, decision: before.decision, locked: before.locked }`, and the
   request carries `confirmed_locked: actionLocked`. For a toolbar row,
   `locked: false` on an unlocked row is a no-op on the lock. This is the case
   the rev-1 dataclass check wrongly rejected. Pin: a toolbar Undo of unlocked
   rows succeeds and writes the decisions.
3. Any row locked now and not named answers 409. That is a lock you set after
   the toast appeared. The dialog opens on op `undo`, which reuses the decision
   wording (the rows' decisions change, nothing is deleted). Unlock & Apply adds
   the dialog's rows to the confirmed set. Unlocked only undoes the rest.
   Cancel leaves the toast up.
4. Each row's `locked` value is written in the same statement as its decision.
   Setting a lock never needs confirmation. Clearing one needs it.
5. The pre-locked keeper case is owner question D10. Today a forced Keep best
   keeps an already-locked keeper locked (`test_web_action_routes.py:1034-1062`).
   That keeper is not in `actionLocked`, so its Undo would ask again.

Server change: `DecisionItem` (`app/web/models.py:160`) gains an optional
`locked: bool`. When it is omitted, today's behaviour holds (an unlocked row
stays unlocked, and a confirmed locked row is unlocked, R-LOCK-2).

The Skip-now Undo (`undoSkipNow` → `postRestore`, `useAppStore.ts:1728-1790`)
already sends no force. Its 409 opens the dialog and keeps the toast, which is
the R-LOCK-1 pin. A8 changes it only by passing `confirmed_locked` on the
re-send.

### 3.4 Fuller client helper (Track B only)

The rev-1 helper (`store/lockGate.ts`, `resolveLockConflict` with a per-op
re-send table, `WriteOutcome` return values, `raiseToast()`) stays the right
shape for consistency. It is Track B item B3. The bugs it carried (B-11..B-15)
are fixed in place in Track B item B4, so they do not wait for it.

---

## 4. Other behaviours with more than one home

| Behaviour | Homes today | Proposal |
|---|---|---|
| Scope under a filter | `visibleSelectedPaths` (`lib/rowFilter.ts:86`) used by the toolbar only (`Toolbar.tsx:165`, `useAppStore.ts:1569`); raw selection in right-click (`App.tsx:109-111`), K/D (`useDecisionShortcuts.ts:72-78`), Skip Selected now (`App.tsx:251-256`), Execute only selected (`App.tsx:241-245`) | One home, `visibleSelectedPaths`, for every bulk verb, **if D1 = (a)** (Track B, B8). Leave `ExecuteDialog.tsx:251-254`'s local namesake: it is the dialog's own selection (R-EXEC-1). Leave the Keep-best disabled-under-filter predicate (`ContextMenu.tsx:104-106`, `ResultTree.tsx:819`): two consistent sites. |
| Undo toast machinery | 3 counter sites; 2 undo functions with different lock policies | The lock policy is unified in A7. `raiseToast()` and the toast owner by id are Track B (B3, B4). The two undo functions stay two, because one reverses a decision and the other an outcome. |
| Error display | `StatusBar.tsx:174-184` prefixes every failure "Manifest error:"; `ExecuteDialog.tsx:581-583`, `ActionDialog.tsx:624-626` render raw strings; `revealInExplorer` writes `executeError` (`useAppStore.ts:1213-1221`) | One formatter, `describeApiError` (Track B, B10). Keep the three error slots. The prefix wording **needs a design ruling** (D9). |
| Plural / word keys | per-word keys in `LockConfirmDialog.tsx:201-235`; "(s)" in `LockConfirmDialog.tsx:286`, `ActionDialog.tsx:417`, `DeleteConfirmDialog.tsx:148,180,187`; three copies of the file/files keys | One shared per-word key set (Track B, B11). It follows the existing pattern, so no ruling is needed. |
| Button weight beyond F5 | F5 (`rulings.md:161-166`) covers only LockConfirmDialog; `ActionDialog.tsx:447-453`, `RemoveFromListConfirmDialog.tsx:69`, `DeleteConfirmDialog.tsx:280`, `RescanConfirmDialog.tsx:86` are always red | **Needs a design ruling** (D8; Track B, B12). |
| Copy | `DeleteConfirmDialog.tsx:193` vs `:263-264` contradict; `ActionDialog.tsx:415-418` states no consequence; two Skip labels (`ExecuteContextMenu.tsx:162` vs `ContextMenu.tsx:149-170`) | **Needs a design ruling** (D9). Out of scope; an issue plus a design request. |
| Group-survivor rule (R-EXEC-2) | `load()` `manifest_repository.py:690`; `_load_decided_rows` `execute_service.py:93-99`; `_fetch_singleton_rows` `:480-485` (disagree on `group_id=''`) | Out of scope; an issue with a data check first. |
| Recycle default | `models.py:39`, `execute_service.py:144`, `useAppStore.ts:848`; they agree and R-DELETE-1 pins all three | Leave as is. These are three pinned layers of one contract, not drift. |

---

## 5. Exception checklist (rev 2)

Every "must keep" item from both inventories and the review, with how the
design keeps it. "Pin" names the test that guards it; **NEW** = added by the
named PR before it touches the code.

| # | Must keep | How the design keeps it | Pin |
|---|---|---|---|
| BE-1 | `skip_locked` narrows the write; locked rows keep decision and lock (`set_decisions`, bulk decide, Apply best copy; R-LOCK-7b) | `plan_locked_write(SKIP)` drops locked rows before the primitive; `skip_allowed` only for those three | `test_web_action_routes.py::TestApplyBestCopy::test_skip_locked_leaves_locked_peer_untouched` |
| BE-2 | R-LOCK-7a happens in the browser | Toolbar keeps its client filter (`useAppStore.ts:1560-1608`) | R-LOCK-7 vitest pins |
| BE-3 | Apply best copy under force locks the keeper and unlocks only non-keepers; a locked keeper counts toward the 409 | Keeper `lock_after=True`; confirmed non-keepers default to unlock (`lock_after=None` + confirmed); the keeper is in the write set, so the refusal counts it | `test_web_action_routes.py:1034` |
| BE-4 | Execute unlocks a row only when its action lands; a failed delete, a missing file and a failed skip write keep the lock | `record_deleted` only after a successful delete; a missing file writes nothing (`execute_service.py:227-232`); the skip batch is one transaction | R-LOCK-2 `TestLockedRowUnlockOnlyWhenApplied` ×3 |
| BE-5 | Execute out-of-root: deletes reported `failed`, skips silent, out-of-root locked rows not in `locked_paths`, they keep their lock | The roots filter stays where it is (`:195-205`), before the refusal | R-LOCK-4 out-of-root pins |
| BE-6 | Skip now, restore, prune drop out-of-root rows and keep their locks | Filter before the refusal, unchanged | R-LOCK-5 out-of-root pin |
| BE-7 | Restore gates only rows both `ignored` and locked | `transition(from_outcome="ignored")`; the refusal reads `locked_paths(outcome="ignored")` | R-LOCK-1 restore pins |
| BE-8 | Prune: no exception for locked singletons; the dialog opens before the request | Server: the same primitive. Client: op `prune` keeps its pre-request dialog | R-LOCK-1/-2 prune pins |
| BE-9 | Reconcile: per group, never unlocks, never 409, fails soft, re-checks in the statement | A documented exception: it uses neither the primitive nor the refusal; `_RECONCILE_DISMISS_SQL` is unchanged | R-LOCK-6 pins |
| BE-10 | Lock/unlock verbs and preview never gated | The lock verb stays unguarded and its callers are probe-restricted; preview returns before any txn (`action_service.py:186-192`) | existing bulk-decide preview tests |
| BE-11 | Execute commits per file (D7) and holds `execute_lock` | One short txn per file; never held across a file call; `execute_lock` (`execute.py:119-131`) unchanged | R-LOCK-2 per-file pins |
| BE-12 | Post-scan lock write on a fresh DB: nothing to gate | `scanner/manifest.py:163-188` unchanged; allow-listed | existing scanner tests |
| BE-13 | `matched_total` only in decision-verb 409s | Primitive arg `matched_total`; `LockedRowsError` keeps the 3-tuple args | decision-verb 409 route tests |
| BE-14 | Route error differences (400 vs 422, "Prune failed:", `group_not_found`, `execute_already_running`) | Routes untouched in Track A | route test files |
| BE-15 | File deleted but DB write failed → still a success, listed in `db_write_failed` (`execute_service.py:242-247`, `:259-264`) | `record_deleted` raises; Execute catches it per file as today. **There is no pin today** (no `db_write_failed` hit in `tests/`) | **NEW, first commit of A4**: `record_deleted` patched to raise → file in `success_paths` and `db_write_failed` |
| BE-16 | Skip now, prune, restore write no audit CSV, on purpose (`execute_service.py:413-416`) | Untouched: audit writing stays in `execute_decisions` | **NEW, first commit of A4**: Skip now leaves no `delete_*.csv` |
| BE-17 | The manifest `.db` is not roots-gated on action/review routes (`action.py:11-17`) | Untouched | existing |
| BE-18 | **Auto-migrate before write**: `finalize_outcome` (`:949`) and `restore_to_review` (`:993`) run `ensure_schema` first, so old manifests gain columns | `write_txn` runs `ensure_schema` before `BEGIN IMMEDIATE` (2.3, 2.4) | **NEW in A3**: write through `write_txn` to a pre-`outcome` manifest |
| BE-19 | `source_path` is not unique (`:197-198`); `restored` reports rows changed (`cursor.rowcount`, `:1000-1004`) | `transition` returns rowcount, and `restored` keeps that meaning; the refusal is set-based | existing restore count tests |
| BE-20 | `set_decisions` ran `load()` per PATCH, so each decision PATCH also ran the reconcile and the auto-migrate (`manifest_repository.py:654`, `:670`) | Dropped by A3, an **observable side-effect change**: the reconcile still runs on every manifest open (`GET /api/manifest`) and after every Execute/Skip/prune/restore reload, and `write_txn` keeps the auto-migrate. Stated in A3's body and register update | **NEW in A3**: a PATCH on a pre-`outcome` manifest succeeds |
| FE-1 | Prune: dialog before the request from the classify buckets; Cancel continues with the unlocked buckets; toast owner through `resolvePruneLock`; pref always/ask | Unchanged; A1 adds the late-409 path into the same op | prune vitest |
| FE-2 | Execute: complete-group delete confirm first; `execute_already_running` → `executeError`; unscoped scope captured as an explicit list; F5 weight; dialog stays open on Cancel (R-DIALOG-1) | Unchanged; A8 only swaps the force flag for the named list | s34, s36, R-DIALOG-1 pins |
| FE-3 | Set Action: inline prompt; dialog survives Cancel (#764); Unlocked only disabled from `matched_total`; dialog closes on success; Recent patterns only after success | Unchanged in Track A (A8 swaps the flag). **Verified:** "Recent patterns only after success" is not true today; `applyBulkDecide` swallows non-409 errors (`useAppStore.ts:1410-1418`), so `doApply` records and closes (`ActionDialog.tsx:231-240`). That is #942, Track B item B6 | s32, `ActionDialog.test.tsx` |
| FE-4 | Keep best: re-run by `groupNumber`; `keepBestPending` released in `finally`; snapshot merges into the same group's standing toast; header always sends `skip_locked` | Unchanged; A7 adds `actionLocked` to the toast | s75 |
| FE-5 | Restore: toast stays on Cancel; toast cleared by its id; manifest-switch guard | Unchanged in Track A | R-LOCK-1 restore vitest pin |
| FE-6 | Toolbar verbs: client lock filter and `lockedCount` in the toast | Unchanged | R-LOCK-7 vitest pins |
| FE-7 | Keep-best Undo undoes the keeper lock its own action created, without forcing | `actionLocked` names exactly those locks (3.3) | **NEW in A7** |
| FE-8 | "Unlocked only" narrows per op (client filter vs server `skip_locked`) | Unchanged in Track A | LockConfirmDialog tests |
| FE-9 | Empty-scope guard (today "decision" only, `LockConfirmDialog.tsx:167`) | Unchanged in Track A; see B-14 | - |
| FE-10 | Lock toggle ungated, plain transport | `patchLocks` is the probe's one exception | - |
| FE-11 | Disabled controls on locked rows (`FileRow.tsx:308`, `PreviewPane.tsx:445,458`) | Untouched | existing |
| FE-12 | Optimistic apply plus revert on every failure | Untouched | existing |
| FE-13 | `recycle: true` single site (R-DELETE-1) | Untouched | R-DELETE-1 vitest pin |

Nothing on either list is dropped.

---

## 6. Bug list (rev 2)

Only real behaviour defects are listed. "Verified" means I re-read the code at
`aaf240d`. Nothing was run.

| # | Defect | Evidence | Fixed by |
|---|---|---|---|
| B-1 | A lock set between a write's lock read and its write is ignored: an unforced write overwrites the row, and a forced or clear-lock write clears the lock silently | separate connections, `execute_service.py:89-104` vs `:240`; `review_service.py:151` vs `:169`; no lock condition in `manifest_repository.py:170-173` (verified) | **A3** (decision writes), **A4** (outcome writes), **A9** (Execute deletes) |
| B-2 | A lock set after the lock dialog opened is cleared by the forced re-send with no second confirmation, because `force_locked` covers every locked row in scope | gates `execute_service.py:210`, `:365`, `:451`, `:629`; the dialog re-sends a bool (`LockConfirmDialog.tsx:133-143`) (verified) | **A6** (decision), **A8** (the other six verbs) |
| B-3 | Prune sends one `force_locked` for the whole list, so a singleton locked after the classify read is force-pruned and unlocked (a sub-case of B-2) | `useAppStore.ts:1070-1091` (verified) | **A8** |
| B-4 | Skip now writes every path whatever its outcome: a `deleted` row becomes `ignored` with `executed=0` (Undo then returns a deleted file to review), and a locked dismissed row is unlocked with no 409 | lock SELECT `outcome=''` (`execute_service.py:347-351`), write over all `safe_paths` (`:370`) (verified) | **A2** |
| B-5 | `set_decisions` finds locks through `load()`, which yields only visible rows; a hidden locked row bypasses the gate, and under force its lock stays | `review_service.py:151-154`, `manifest_repository.py:688-697` (verified) | **A3** |
| B-6 | `set_decisions` with both flags: skip wins silently; the other verbs answer 422 | `review_service.py:156` vs `action_service.py:148` (verified) | **A3** |
| B-9 | #1000: a late 409 on prune shows a raw error | `client.ts:321-323`, `useAppStore.ts:1115-1122` (verified) | **A1** |
| B-10 | #999: toast Undo forces, then rewrites every snapshot lock in a second PATCH | `useAppStore.ts:1674-1691` (verified) | **A7** |
| B-7 | Bulk decide with an empty roots list (restart, no manifest opened yet) writes with no roots filter. Consistency, not a file-safety defect (2.6) | `action_service.py:158-161`, `main.py:74` | Track B, **B2** (D2) |
| B-8 | `set_decisions`, `set_locks`, `apply_best_copy` never filter by roots. Not a bug if D3 = (b) | `review_service.py:139-195`, `action_service.py:313-382` | Track B, **B2** (D3) |
| B-11 | Skip-now re-send drops `undoable`: no undo toast. The "Unlocked only" half contradicts R-SKIP-1 | `LockConfirmDialog.tsx:143`, `:181` (verified) | Track B, **B4**: the "Unlocked only" half now; the "Unlock & Apply" half after D11 |
| B-12 | A toolbar stale-lock 409 re-sent from the dialog raises no toast | `LockConfirmDialog.tsx:135`, `:168` (verified) | Track B, **B4** |
| B-13 | A restore re-send clears *any* skip-now toast, not its own | `useAppStore.ts:1815` vs `:1767` (verified) | Track B, **B4** |
| B-14 | "Unlocked only" with nothing left sends an empty list for remove/restore. **Downgraded:** the server returns early on an empty list (`manifest_repository.py:946-947`, `:990-991`), so this is a wasted round-trip, not a defect. For Execute, today's empty re-send shows a result in the dialog, so "send nothing" would change what the user sees | `LockConfirmDialog.tsx:167` vs `:178`, `:181` | Track B, **B4**, remove/restore only |
| B-15 | `applyBulkDecision` infers "write landed" from state; an unrelated error suppresses the toast | `useAppStore.ts:1628-1633` (verified) | Track B, **B4** |
| B-16 | After a forced decision the padlock stays drawn although the server unlocked the row | `client.ts:145-147`; no refresh in `setDecisions` (verified statically) | Track B, **B5** |
| B-17 | Only the skip Undo and restore guard a manifest switched mid-flight | `useAppStore.ts:1764`, `:1811` (inventory) | Track B, **B5** |
| B-18 | #942: a failed Set Action apply closes the dialog and records the pattern | `useAppStore.ts:1410-1418`, `ActionDialog.tsx:231-240` (verified) | Track B, **B6** |
| B-19 | Undo after a forced Skip now brings the rows back unlocked | forced skip clears the lock (`execute_service.py:370`) | Track B, **B7** (if D5 = a) |
| B-20 | Bulk verbs act on filter-hidden selected rows | `App.tsx:109-111`, `:241-256`, `useDecisionShortcuts.ts:72-78` (verified) | Track B, **B8** (if D1 = a) |
| B-21 | Set Action bulk apply has no undo | inventory FE §2 | Track B, **B9** (if D4 = a) |
| B-22 | Raw error strings in two dialogs; reveal's error lands in the Execute slot | `ExecuteDialog.tsx:581-583`, `ActionDialog.tsx:624-626`, `useAppStore.ts:1213-1221` | Track B, **B10** |
| B-23 | "(s)" plurals in three dialogs | section 4 | Track B, **B11** |
| B-24 | Buttons always red although no file is deleted | section 4 | Track B, **B12** (D8) |
| B-25 | `apply_best_copy` addresses a group by positional `group_number` | `action_service.py:317-322` | Out of scope; issue `[BE] apply-best-copy addresses a group by position` |
| B-26 | Audit CSV writes group 0; a failed skip lands in the delete CSV | `execute_service.py:288-290` | Out of scope; issue `[BE] delete audit CSV loses the group and mixes skip rows` |
| B-27 | `group_id=''` rows count as a prune singleton but never as an execute group (existence unconfirmed) | `execute_service.py:93-99` vs `:480-485` | Out of scope; issue `[BE] one group-survivor predicate` (data check first) |
| B-28 | Undo overwrites a decision changed after the toast appeared | `useAppStore.ts:1674-1680` | Out of scope; issue `[FE] Undo overwrites a decision changed after the toast` |
| B-29 | Contradictory or consequence-free copy | section 4 | Out of scope; issue `[FE:copy]` plus a design request (D9) |

---

## 7. Plan (rev 2): Track A required, Track B optional

### Track A: the minimal program that makes the six-bug class impossible

Fixes come first, on today's code where they are small. The chokepoint is one
FIX per kind of write, with red-first tests. Splitting it into a REFACTOR that
ships today's unguarded SQL in a new shape, followed by a FIX, would double the
review for no safety gain, so the rev-1 transitional shims (`both="skip"`,
`from_outcome=None`) are gone. Every PR follows `/github-pr-create` (news
fragment, docs and qa tokens). Each FIX lands a red test first, then the fix,
then the register and feature-doc update. PRs that touch the same source file
are stacked. A `rules.md`-only conflict is not a dependency, so it is rebased.

| PR | Tag | What (one line) | Files | Size | Red-first / pins | Depends on |
|---|---|---|---|---|---|---|
| **A1** | FIX | #1000: `postPrune` via `postJsonOrConflict`; a late 409 opens the lock dialog; transport probe | `api/client.ts`, `store/useAppStore.ts`, tests, `tests/test_source_probes.py`, `rules.md` (R-LOCK-1 drops the "raw error" caveat), `docs/features/execute-action.md` | ~120 | the vitest in #1000; R-LOCK-1 pins | - |
| **A2** | FIX | B-4 on today's code: Skip now writes only the paths its SELECT found in review; `removed` = those paths. (rev 3) It ships early as a data-safety fix, because today a `deleted` row can come back to review. A4 supersedes its code: `transition(from_outcome='')` replaces it, and A2's red tests stay as A4's pins | `execute_service.py` (`:347-370`), `test_web_execute_service.py`, `rules.md` (R-SKIP-1, R-LOCK-5), `docs/features/file-operations.md` | ~80 | red: a `deleted` row stays deleted; a locked dismissed row keeps its lock; R-SKIP-1, R-LOCK-5 pins | - |
| **A3** | FIX | Chokepoint for decision writes: `LockedRowsError`, `plan_locked_write`, `write_txn` (BEGIN IMMEDIATE + ordering guard), `write_decisions` with in-primitive refusal; callers `set_decisions` (drops `load()`, fixes B-5, B-6), `bulk_decide`, `apply_best_copy` | `core/app_service/lock_gate.py` (new), `manifest_repository.py`, `review_service.py`, `action_service.py`, tests, `rules.md` (R-LOCK-1/-2 "the write re-checks the lock in the statement", as R-LOCK-6 does) | ~400 | red: a stale plan (`locked_paths` patched to `[]`) cannot write a locked row and gets 409; a hidden locked row gives 409, forced gives unlocked; both flags give 422; `load()` inside `write_txn` raises at once; BE-18/BE-20 pins; existing R-LOCK-1/-2/-7 pins; a timed decision PATCH on the 40k manifest (expected faster: no `load()`) | - |
| **A4** | FIX | Chokepoint for outcome writes: `transition` with in-primitive refusal and outcome condition for Skip now, restore (deletes `unlock_paths`), prune, Execute's up-front gate and skip batch; Execute's per-file delete write becomes `record_deleted` (same SQL, no branch fold) | `manifest_repository.py`, `execute_service.py`, tests, `rules.md` (R-LOCK-1/-2/-4/-5) | ~350 | **first commit: the BE-15 and BE-16 pins**; red: stale plan for each verb; R-LOCK-1, -2, -4, -5, R-SKIP-1, R-DELETE-1 pins; (rev 3) **rig measurement on D+H+J: decision-PATCH latency while an Execute runs, before and after**. From A3/A4 on, Execute's per-file `record_deleted` and its skip batch take short IMMEDIATE transactions, so a PATCH can wait on them | A3, A2 |
| **A5** | REFACTOR | Delete the dead unguarded writers (2.3 table) and their tests; (rev 3) the write-path probe (2.3): no `UPDATE migration_manifest SET` of `user_decision`, `outcome` or `is_locked`, and no reference to `_UPDATE_DECISION_SQL` / `_UPDATE_LOCK_SQL`, outside the `ManifestTxn` primitives and the allow-list (reconcile `:205`, lock verb `:161`, scanner fresh-DB write); must-fail and must-pass fixtures | `manifest_repository.py`, `delete_service.py`, `core/services/auto_select.py`, their tests, `tests/test_source_probes.py` | ~-350 / +40 | the commit names each deleted test and the grep evidence that its code has no production caller; per-file coverage still ≥ 70 | A4 |
| **A6** | FIX | Named confirmation for decision writes: `DecisionUpdate.confirmed_locked` replaces `force_locked` over HTTP; the dialog's decision re-send names `lockConflict.paths`; probe extended | `app/web/models.py`, `review_service.py`, `review.py`, `api/client.ts`, `store/useAppStore.ts`, `LockConfirmDialog.tsx`, tests (route tests using `force_locked`: 2 in `test_web_review_routes.py`), `rules.md` (R-LOCK-1/-2) | ~200 | red: a row locked after the 409 gives a second 409 naming only it; s53 | A3 |
| **A7** | FIX | #999: Undo without blind force, one PATCH with per-row `locked` and `confirmed_locked: actionLocked`; op `undo`; toast stays on Cancel | `models.py` (`DecisionItem.locked`), `review_service.py`, `client.ts`, `useAppStore.ts`, `Toast.tsx`, `LockConfirmDialog.tsx`, tests (the pin at `useAppStore.test.ts:1415` changes), `rules.md` (Undo sentence in R-LOCK-1, as #999 asks), `docs/features/review.md` | ~300 | red: the #999 vitest (lock after the toast → no write, dialog opens); a toolbar Undo of unlocked rows succeeds; s75 | A6, D10 |
| **A8** | FIX | Named confirmation for the other six verbs (bulk decide, apply best copy, execute, remove, restore, prune); no boolean force remains over HTTP; prune names only confirmed singletons (B-2, B-3) | `models.py`, `action_service.py`, `execute_service.py`, `action.py`, `execute.py`, `client.ts`, `api/types.ts`, `useAppStore.ts`, `LockConfirmDialog.tsx`, `ActionDialog.tsx`, tests (29 `force_locked` uses in `test_web_action_routes.py` + `test_web_execute_routes.py`), `rules.md` | ~400 | red per verb: a row locked after the 409 gives a second 409; s32, s34, s36 | A4, A6 |
| **A9** | FIX | Execute re-checks each delete row's lock and outcome right before its file call, on the same connection as `record_deleted` (a read, no IMMEDIATE); a row locked or dismissed meanwhile is left alone and reported | `execute_service.py`, `manifest_repository.py`, tests, `rules.md` (R-LOCK-4) | ~150 | red: a row locked after the gate is not deleted and keeps its lock (BE-15 pin from A4 stays green); **rig measurement, before and after, on D+H+J: (1) Execute wall time; (2) decision-PATCH latency while an Execute runs**, because after A3/A4 the PATCH is the side that waits | A4, **D6** |

Track A count: **9 PRs, 1 REFACTOR (A5) and 8 FIX.** Two lanes can start at
once: A1, and A2 → A3 → A4. A6 and A7 can follow A3 while A4 is in review.

What Track A guarantees once it lands:
- A lock is cleared only by the statement that writes that row, and only if the
  row is named as confirmed.
- A write path that forgets the gate still gets a 409 from the primitive,
  never a silent skip.
- No boolean force exists over HTTP.
- The browser routes every row-write 409 (prune included) to the dialog.

(rev 3) Once **A5's write-path probe** lands, each of the six shapes is either
structurally impossible or caught by a probe:
- #981 gate miss and #983 silent skip: the primitive refuses itself. Any
  unguarded `user_decision` / `outcome` / `is_locked` write outside it fails
  the probe.
- #982 system write: the reconcile is the probe's named exception, and it
  re-checks in its own statement.
- #985 detached unlock: no separate unlock list exists.
- #999 blind force: no boolean force exists over HTTP.
- #1000 unrouted 409: the transport probe.

Before A5 the claim holds only for the paths A3 and A4 have moved. In that
window, a new caller of `_UPDATE_DECISION_SQL` or of a not-yet-deleted
unguarded writer would go uncaught.

Residual window, stated honestly: a lock set during the `send2trash` call
itself (milliseconds, after A9's re-check) is cleared by `record_deleted`,
because the file is already gone. That is accepted, and the register says so.

Riskiest step: **A3** introduces `BEGIN IMMEDIATE` on a manifest that may live
on the NAS. Mitigations: the ordering guard (2.4), short transactions, and the
measurements in A3, A4 (rev 3: a PATCH during a running Execute) and A9. A9
carries the only extra per-file read.

### Track B: optional consistency work (each item is its own issue)

| Item | What | Value | Tag | Needs |
|---|---|---|---|---|
| **B1** | One route error mapper (`_errors.py::service_errors`) | One place that shapes the 409 and the 4xx details; fixes no bug | REFACTOR | - |
| **B2** | One roots policy: `split_by_roots`, proposed roots rule SAFE-1 (not yet in the register), empty list fails closed, decision writes recorded as exempt or filtered | Consistent and stated root handling; closes B-7 and B-8 | FIX (adds uniform log lines to classify and bulk decide, and changes empty-list behaviour) | D2, D3 |
| **B3** | Client helper: `lockGate.ts`, `resolveLockConflict` re-send table, `WriteOutcome`, `raiseToast()` | One re-send path, so new ops cannot drift; a stronger tripwire | REFACTOR | A8 |
| **B4** | The re-send keeps its caller: Skip-now toast after "Unlocked only" (B-11 half), toolbar stale-lock toast (B-12), restore clears its own toast only (B-13), empty narrowed scope for remove/restore (B-14), toolbar reads a result (B-15) | Toasts and undo stay true after a lock dialog | FIX | - (the "Unlock & Apply" toast half waits for D11) |
| **B5** | Forced decision clears the padlock (B-16); every write tail checks the manifest did not switch (B-17) | The screen matches the server after a forced write | FIX | - |
| **B6** | #942: a failed Set Action apply keeps the dialog open and records no pattern (B-18) | Closes an open issue | FIX | - |
| **B7** | Undo of a forced Skip now relocks (B-19) | Undo means "as it was" | FIX | D5 |
| **B8** | Every bulk verb acts on visible selected rows only (B-20) | One scope rule | FIX | D1 |
| **B9** | Set Action bulk apply raises an undo toast (B-21) | Every reversible bulk decision write can be undone | FIX | D4, A7 |
| **B10** | One error formatter on every surface; reveal's error to its own surface (B-22) | Readable errors where you acted | FIX | - (prefix copy waits for D9) |
| **B11** | Per-word plural keys, one shared file/files set (B-23) | Correct grammar in en, parity in zh_TW | FIX | - |
| **B12** | Button weight beyond F5 (B-24) | Weight follows the files' fate everywhere | FIX | D8 (design ruling) |
| **B13** | Move `execute_service`'s raw SELECTs and `_connect` into the repository; fold the recycle/unlink branches | One data path; less duplicated code. It touches unpinned reason strings (`execute_service.py:250-252` vs `:263-264`), so it needs a reason-string pin first | REFACTOR (after the pin) | A9 |

---

### A5 implementation notes (final review, orchestrator)

- Move the `_UPDATE_DECISION_SQL` constant (manifest_repository.py:156-158) into `scanner/manifest.py`, which is
  its only user after A5. Otherwise its own definition (`UPDATE migration_manifest SET user_decision`) fails the
  must-pass fixture at :157.
- Must-fail fixtures include layout-only variants: lowercase `update`, the statement split across lines, a table
  alias, and extra whitespace. Run both "must catch" and "must pass" on them (the project's guard rule: parse
  syntax units, not lines).
- State in the probe's docstring that it covers UPDATE only. `INSERT OR REPLACE` / `REPLACE INTO` are out of its
  scope. Today the only INSERT…SELECT is the schema rebuild (:261-299).

## 8. Owner decisions needed (rev 2)

Only product choices are listed here. My lean comes first in each.

| # | Question | Options | Lean, and why | Blocks |
|---|---|---|---|---|
| D7 | **Confirm (implied by your ruling 「鎖住的action需要嚴格遵守相同的flow(需要再次確認)」):** a forced re-send that meets a row locked *after* the dialog opened asks again for that row. | (a) Ask again. (b) The first confirmation covers it. | **(a).** Your ruling already says it; this row only asks you to confirm. | A6, A8 |
| D10 | **New.** Undo of a *forced* Keep best whose keeper was **already locked before** the action. Today the action keeps that keeper locked (`test_web_action_routes.py:1034-1062`). | (a) Undo asks again for the keeper, because you confirmed the action, not its reversal. (b) The action's own confirmation also covers its Undo, so the rows it forced are confirmed for the Undo. | **(b).** The Undo only reverses what you confirmed seconds earlier; asking again reads as "confirm a lock I already confirmed". With (b) the toast records the action's forced rows, and every *later* lock still asks. (rev 3) **Reviewer's view:** "the literal ruling points to confirm again; not asking is defensible only as a named exception to R-LOCK-1, limited to rows whose lock has not changed since the action." If you choose (b), it is written as that named exception: Undo sends the forced rows as confirmed only if their lock state still matches the toast's record, and A7's register sentence names the exception. **You decide.** | A7 |
| D11 | **New.** Should a Skip now re-sent through "Unlock & Apply" get an Undo toast before D5 is decided? Its Undo would bring the rows back *unlocked*. | (a) Not yet: give the toast to the "Unlocked only" re-send now (that is R-SKIP-1), and to "Unlock & Apply" once D5 is settled. (b) Both now, accepting the unlocked comeback until B7. | **(a).** It never offers an Undo whose result you might not expect. | B4 |
| D6 | A row is locked while an Execute is already running, after you confirmed it. | (a) Leave that file alone, keep its lock, list it in the result. (b) Delete it anyway. | **(a).** Mid-run there is no 409 to give; (a) is the closest thing to "confirm again". | A9 |
| D5 | Undo of a Skip now you forced through the lock dialog. | (a) The rows come back locked, as they were. (b) They come back unlocked (today). | **(a).** Undo should mean "as it was"; the Keep-best Undo already restores locks. | B7, D11 |
| D1 | Should right-click Keep/Delete, K/D, Skip Selected now and Execute only selected act on selected rows the filter hides? | (a) Visible rows only, like the toolbar and R-EXEC-1. (b) The whole selection, with the hidden count shown. | **(a).** One rule for every bulk verb; the store's own comment calls the other way "a bulk write whose extent the user cannot see" (`useAppStore.ts:1564-1568`). | B8 |
| D4 | Should the Set Action bulk apply get an Undo toast? | (a) Yes, like the toolbar and Keep best. (b) No; the dialog already previews and confirms deletes. | **(a).** Every other reversible bulk decision write has one. | B9 |
| D2 | **Deferred with the roots track (Track B).** What does an empty allowed-roots list mean? | (a) Nothing is allowed. (b) Everything is allowed (services today). | **(a)**, matching `is_under_roots` and `main.py:48` "secure-by-default". | B2 |
| D3 | **Deferred with the roots track (Track B).** Should decision, lock and Keep-best writes check allowed roots? | (a) Yes. (b) No, recorded as exempt in the proposed SAFE-1. | **(b).** They touch no file; Execute is the file boundary. | B2 |
| D8 | Button weight for ActionDialog "Unlock & Apply", RemoveFromList "Yes", DeleteConfirm "Mark N for deletion", Rescan confirm. | (a) Extend F5. (b) Keep them red. | **(a)**, but **needs a design ruling.** | B12 |
| D9 | Copy: the "Manifest error:" prefix; DeleteConfirm's contradiction; "Unlock and apply anyway?"; the two Skip labels. | (a) One copy pass under a design request. (b) Leave. | **(a)**, **needs a design ruling**. | out of scope |

You need to answer only **D7, D10 and D6** before Track A can finish. A1-A6
and A8 need no answer.

---

## 9. Out of scope, and issues to file

| Item | Why out | Issue to file |
|---|---|---|
| B-25 positional `group_number` | group identity, not locks | `[BE] apply-best-copy addresses a group by its position, which shifts after a prune or execute` |
| B-26 audit CSV | audit format | `[BE] the delete audit CSV loses the group number and mixes skip rows` |
| B-27 group-survivor predicate | needs a data check for `group_id=''` | `[BE] R-EXEC-2 lives in three predicates that disagree on group_id=''` |
| B-28 Undo overwrites a later decision | an undo product question, not a lock one | `[FE] Undo overwrites a decision changed after the toast appeared` |
| B-29 copy pass (D9) | needs a design ruling | `[FE:copy] delete-confirm and lock-confirm copy contradict or omit consequences` + a design request |
| Path-keyed writes are slow (`source_path` unindexed; 36 s vs 0.013 s, `manifest_repository.py:191-198`) | performance; Track A keeps path keys | `[BE:perf] key guarded writes by row id` (measure on the rig first) |
| Allowed roots come from the manifest being checked (`review_service.py:80-88`) | security design | `[BE:security] allowed roots come from the manifest being checked` |
| `ResultTree.tsx` over 800 lines | exists as #965 | none |
| WAL on a NAS-hosted manifest | pre-existing; one host, one process keeps it working | note in A3's body; no issue unless A3/A9's measurement shows a problem |
| Track B items B1-B13 | optional consistency work | one issue each, filed from the Track B table |

---

### Inventory and review claims checked on verification

1. FE §1d "applyPrune forces only confirmed rows" is wrong in effect. The flag is
   per request (`useAppStore.ts:1070-1091`). (B-3)
2. FE "Recent patterns recorded only after success" is the intent, not today's
   behaviour (#942, B-18).
3. BE §2 "every service skips the check on an empty roots list" is true in code.
   Over HTTP, though, only bulk-decide reaches a service with an empty list
   (`execute.py:65-67`, `_path_guard.py:69-74`).
4. BE §2 "Skip now / restore drop out-of-root rows silently" applies to direct
   callers only. Over HTTP each `file_path` gets a 403 at the route
   (`execute.py:195-196`, `:257-258`).
5. Review: "`db_write_failed` has no pin" is confirmed. `tests/` has no hit.
6. Review: "`load()` writes on its own connections" is confirmed:
   `ensure_schema` at `manifest_repository.py:654`, reconcile at `:670`.
7. Review: the pre-locked keeper behaviour is confirmed at
   `test_web_action_routes.py:1034-1062`.
