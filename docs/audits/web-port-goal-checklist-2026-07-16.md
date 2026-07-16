# Web-Port Program — Original Commitments Audit Checklist

Extraction only — no verdicts. Every item below is a commitment/claim that the
follow-up audit must independently check against the live code before the
owner spends personal testing time. Working tree: `.claude/worktrees/web-port-phase2`
(== integration tip `6d5e3ce`).

Legend: **GOAL** = outcome commitment · **CONTRACT** = binding design rule ·
**CLAIM** = asserted-done parity row · **OMISSION** = owner-endorsed
deliberate gap (audit = "still endorsed & correctly documented", not a failure).

---

## SURPRISE / DISCREPANCY FLAGGED DURING EXTRACTION (read first)

- `docs/design/web-port-tech-design.md:2076` carries a **2026-07-10 staleness
  note on Contract 4 §5** (the testid contract): "several controls listed
  here were deliberately **not shipped or deferred** ... e.g. ... `ctx-apply-best-copy`
  (§5.7), **which shipped only as a no-op stub**." But `qa/web/scenario_map.yml`
  (s72_apply_best_copy, lines 446-449) — dated one day later, 2026-07-11 —
  describes `ctx-apply-best-copy` as `status: done` with a fully working,
  asserted keeper/duplicate split and a durable-SQLite-write proof. **These
  two contemporaneous sources directly contradict each other on whether this
  control works at all.** Flag as audit item P-1 (see §Parity claims). This
  is exactly the class of silent regression the owner is worried about —
  surfaced by extraction, not yet resolved.

---

## §Contracts — the 5 keystone contracts (docs/design/web-port-tech-design.md)

Doc status line (L3): "DRAFT v0.2 — adversarially reviewed 2026-06-19 ...
NOT converged on first pass (judge tally: 17 load-bearing objections; 16
enumerated as binding corrections below, one sub-claim folded)." Status after
review (L2961): "The contracts are **stronger but not yet locked**."

Shared cross-contract conventions (must also be audited, they bind all 5
contracts): DTO canonical names, scan event name mapping table, SSE payload
schemas, error envelope shape, ID schemes (scan_id/group_id/cache
key/ETag), testid naming convention, settings persistence ownership, WIC/Shell
COM threading model, image-cache clear lifecycle, path encoding — all at
`web-port-tech-design.md:56-61` (single collapsed table+prose block).

1. **Contract 1 — Headless-Core Service API (MVC Seam)** (`web-port-tech-design.md:69`)
   Binding rule (L52): *"`AppService` (in `core/app_service/`) is the single
   Qt-free facade both views call. The current `ScanWorker` becomes a thin Qt
   shell over the extracted `run_pipeline()`; the web view is a thin
   FastAPI+React shell over the same surface. **This is the contract to get
   right.**"* Package home `core/app_service/` (L71-91); full method surface
   (scan lifecycle, manifest load/save, decide/execute/lock/get_image/browse)
   at L94 onward. `core/app_service/service.py` "imports nothing from
   PySide6, nothing from app.*" (L90).

2. **Contract 2 — Realtime Progress + Cancellation Protocol** (`web-port-tech-design.md:733`)
   Binding rule: every Qt signal maps 1:1 to a named SSE event
   (`log`/`stage`/`finished`/`failed`/`empty`/`hash_pool_measured`/`read_knee_measured`,
   table at L743-751); terminal events are `finished`/`failed`/`empty`, server
   closes stream after (L753-754). The existing `_StageTracker` 1 Hz throttle
   "moves, unchanged in logic" into the headless pipeline runner and stays
   worker-side (L775-782).

3. **CONTRACT 3 — Image-Serving Contract (QImage → bytes rewrite)** (`web-port-tech-design.md:1225`)
   Binding rule: **single unified endpoint**, not two — `GET /api/image?path=&size=&v=` where
   `size=0` is the full-res signal (L1233-1242, "mirrors the existing
   `_get_image(path, requested_side)` dispatch exactly"). Cache key preserved
   exactly from the existing `_compute_cache_key` (`sha1(path|size)`,
   L1256-1263: "For the web port, preserve this exactly"). ETag derived from
   disk-cache file mtime+size, NOT the cache key (L1265-1270) — **this
   specific rule was later found internally contradicted three ways; see
   Correction #11 below.**

4. **Contract 4 — QA-Harness Architecture (UIA → Playwright)** (`web-port-tech-design.md:1705`)
   Binding rule: `_batch.py` → `qa/web/_batch.py`, five-step Qt lifecycle
   replaced wholesale for steps 2-5, step 1 (`configure()`, writes
   `qa/settings.json`) kept as-is (L1709-1732). `_uia.py` (72 functions) →
   `qa/web/_pw.py`, function-for-function where semantics are equivalent
   (L1738-1742). **§5 (the literal `data-testid` inventory, L2072 onward) now
   carries a 2026-07-10 staleness disclaimer** (`web-port-tech-design.md:2076`):
   *"Treat any mismatch between this section and `testids.ts`/`features.md`
   as this doc being stale, not the code."* Named drift: the id convention
   was reworked from `{surface}-btn-{action}` to `{surface}-{action}[-button]`;
   the List/Log top-menus and File→Save-Manifest are deliberately unshipped;
   `ctx-apply-best-copy` (§5.7) is called out by name as "shipped only as a
   no-op stub" — **contradicted by scenario_map.yml's s72 entry, see the flag
   above.** Audit must treat `frontend/src/testids.ts` +
   `docs/features.md` as authoritative over this contract's literal §5 text.

5. **Contract 5 — Eval Gates: Perf A/B + QA-Parity Counter** (`web-port-tech-design.md:2327`)
   Binding rule: two independent metrics — scan throughput (files/s, NOT
   bytes/s, L2341-2344) and thumbnail p50/p95 latency (L2342). Pass
   thresholds (L2410-2414, owner's own numbers, quoted): **"Scan
   throughput: `web_files_per_s / qt_files_per_s >= 0.95`"** and **"Thumbnail
   latency: `p95_ms <= 200` on the qa sandbox corpus served from
   localhost."** Both corpora (qa sandbox — determinism gate; synthetic
   large — throughput signal) at L2399-2408. An **implementation note dated
   2026-06-29** (L2915, embedded inside the correction record) documents a
   later, measured supersession of the naive "N concurrent postprocess"
   OOM sub-run — audit item, see Correction #9's note.

---

## §Corrections — the 16 binding corrections (adversarial review, 2026-06-19)

Source: `web-port-tech-design.md:2829-2958`, "Adversarial review — decision
record (binding corrections)". Verdict line (L2832), quoted: *"NOT
CONVERGED on first pass — 19 objections raised, 17 load-bearing, 17
unrebutted (real) (16 enumerated as individual binding corrections below, one
sub-claim folded into another), 2 refuted."* Doc's own numbering uses named
objection tags grouped under T1/T2/T3/T4/T5/T6/NEW section headers, not
sequential numbers — the sequence below preserves that grouping and adds a
running index for audit tracking. Also captured: 2 explicitly **refuted**
objections (recorded so they are NOT mis-audited as still-open — T1-SSE-terminal-event,
T3-DCL, both at L2838-2842).

Cross-reference: GitHub issue **#647** groups these same 16 by *owning
phase* with its own tracking checkboxes (Phase 0 → #642: 6 items; Phase 1 →
#643: 6 items; Phase 4 → #646: 2 items; refuted 2 listed separately, no
action) — use #647's checklist as the second ground-truth for "was this
folded in," not just the design doc.

### T2 group (`web-port-tech-design.md:2847-2872`) — 4 corrections
1. **T2-cancel_flag** — the two-tier cancel signal (`isInterruptionRequested`
   ×11 executable sites **and** a separate module-local `cancel_flag =
   threading.Event()` at `scan_worker.py:873`, polled by daemon threads) must
   collapse to ONE `_cancel_token` reference, preserving the #594 ordering
   (token observed before the bounded `exif_queue` put-loops drain).
   Binding fix at L2853.
2. **T2-write-premise** — doc-accuracy fix: `write_manifest` is already
   crash-atomic (`tmp.sqlite` + `os.replace`, #464, `manifest.py:52-132`);
   drop the redundant outer-wrapper the draft proposed. Binding fix L2859.
3. **T2-apply_auto_select** — the post-write `apply_auto_select_decisions`
   block (`scan_worker.py:1759→1786`) runs OUTSIDE the atomic write window;
   a cancel landing there can leave a structurally-complete but
   semantically-incoherent manifest (keepers unlocked). Binding fix L2865:
   fold it into the atomic section, OR treat a cancel there as `finished`,
   OR document the window as an accepted rare gap.
4. **T2-site-count** — reconcile "~15 isInterruptionRequested sites"
   phrasing to "15 raw = 11 executable + 4 comment-only (956/976/1590/1665)"
   everywhere it's cited. Binding fix L2871.

### T1 group (`web-port-tech-design.md:2875-2886`) — 2 corrections
5. **T1-session-race** — `_Session` (holds `groups`/`path_index`) has no
   lock; the doc's "functionally identical to Qt" claim (L555) is **false**
   on the concurrency axis (web mutates via an asyncio callback + reads via
   executor threads; Qt is single-threaded). Also: the Phase-A
   `run_pipeline(..., cancel_token: threading.Event, ...)` signature at L628
   contradicts the Binding Reconciliation's `_CancelToken` type. Binding fix
   L2879: pin all `_session` mutation+reads to one thread OR add an explicit
   lock/copy-on-write swap; reconcile the cancel_token type.
6. **T1-SSE-iteration** — the SSE replay sketch (L1128) iterates
   `task.event_buffer` (a bare deque) directly while a reader thread appends
   concurrently — a CPython concurrent-iteration hazard. Binding fix L2885:
   mandate `list(task.event_buffer)` snapshot before iteration, stated as
   binding not sketch-level.

### T3 group (`web-port-tech-design.md:2889-2899`) — 2 corrections
7. **T3-CoUninitialize** — `ThreadPoolExecutor.shutdown()` runs no per-thread
   teardown; `CoUninitialize` is never called on the WIC STA threads (leaks
   apartments on executor replacement/uvicorn graceful restart, benign on
   clean process exit). Binding fix L2893: explicit cleanup (per-thread
   Future submitting `CoUninitialize`, or a FastAPI lifespan hook).
8. **T3-STA-unenforced** — the STA-executor invariant is convention-only;
   `_load_via_shell_thumbnail_sync` stays directly callable with no
   apartment/thread-name guard. Binding fix L2899: runtime assert
   (`threading.current_thread().name.startswith('wic-sta')`) at the COM
   entry point, PLUS a `tests/test_ui_probes.py` static probe asserting the
   WIC sync fn is only invoked via the executor.

### T4 group (`web-port-tech-design.md:2903-2921`) — 3 corrections
9. **T4-OOM** — CACHE-retention bytes are bounded (LRU at put) but
   PEAK-DECODE bytes are not; `size=0` decodes ran on the unbounded default
   executor with no semaphore, risking N×~300-800MB concurrent ProRAW
   decode OOM. Binding fix L2907: concurrency/in-flight-bytes bound
   (semaphore or dedicated small executor, or a byte-budget back-pressure
   mirroring `scanner/` #587). **Embedded 2026-06-29 measured implementation
   note (L2915):** both defenses shipped (40 MiB source-size cap → 413
   before decode at `app/web/routes/image.py:24,69-82`; `_FULLRES_DECODE_SEM
   = BoundedSemaphore(2)` at `infrastructure/image_service.py:74`) — but
   measurement showed the feared regime is **unreachable through the web
   route** for real ProRAW (every real ProRAW >40 MiB → 413; `size=0` mostly
   hits the embedded-thumb fast path, never reaching `postprocess`). The
   shipped bench instead tests (A) the 413 cap, (B) real-ProRAW peak-RSS
   under budget (measured 627 MB delta), (C) warm p95≤200ms. Audit item:
   confirm this shipped-gate description still matches `scripts/bench_thumbnail_latency.py`.
10. **T4-bench-warm-cache** — the thumbnail latency bench only measures
    warm-cache `size=thumb`; the p95≤200ms gate never exercises the cold
    `size=0` ProRAW decode path that triggers the OOM it's meant to guard.
    Binding fix L2913: add a cold-cache, `size=0`, large-DNG sub-run
    (superseded/addressed per the note under #9 above — audit must confirm).
11. **NEW-ETag/stale-cache** (grouped under the T4 header, L2917-2921) — the
    path-only disk-cache key and the ETag disagree on identity (in-place
    file edit serves a stale image forever while 304ing); THREE
    incompatible ETag formulas exist in the doc simultaneously (§5
    conventions, §3.2 example, the actual route handler). Binding fix
    L2921: pin ONE mtime_ns+size-derived ETag scheme; remove the other two;
    make the freshness-bearing property explicit.

### T5 group (`web-port-tech-design.md:2923-2935`) — 2 corrections
12. **T5-testid-contradiction** — §6/reconciliation/Appendix-B mandate
    `row-file-{group_id}-{basename}`, but Contract 4's OWN body (the
    declared single source of truth) still specified bare
    `row-file-{basename}` in §1.3/§5.1/§5.3 — a latent merge conflict that
    would ship the broken (non-unique) key. Binding fix L2929: propagate the
    group_id-keyed convention into the C4 body itself, not just the
    reconciliation note. **Cross-ref:** Contract 4's §5 is now flagged
    stale entirely (see §Contracts item 4) — audit against `testids.ts`,
    not the literal C4 text.
13. **T5-Playwright-regression** — dup basenames are guaranteed by the
    multi-source fixture (`shared.jpg` in two roots); Playwright's strict
    mode RAISES on 2+ matches where Qt's lenient `_row_anchor` silently
    returned the first — a real selection-semantics regression the
    "parity" framing hides. Binding fix L2935: document the semantics
    change explicitly; `s10_multi_source` must use the group_id-keyed
    testid before its port.

### NEW group (`web-port-tech-design.md:2939-2943`) — 1 correction
14. **NEW-scenario-count** — three different scenario-count claims existed
    in the doc (C4 said 68, C5 said 64 with a FALSE "s02 skipped" claim,
    `PHASE_TARGETS[4]` hard-coded 64) against an AST-verified real count of
    67 at review time; `--require-all` hard-coding 64 would let Phase-4's
    cutover gate pass with 3 scenarios silently unported. Binding fix
    L2943: reconcile every count to 67 (at review time), and — the stronger
    fix — **derive the Phase-4 total from `len(ALL_SCENARIOS)` at runtime**
    in `check_qa_parity.py` rather than hard-coding. Audit item: confirm
    `check_qa_parity.py` actually derives the count at runtime today (the
    live scenario file now has 71 rows / 67 done — see §Scenario ledger —
    so any surviving hard-coded "64" or "67" would already be wrong).

### T6 group (`web-port-tech-design.md:2947-2957`) — 2 corrections
15. **T6-CI-noop** — the C5 Qt-arm perf measurement needs a live
    `QApplication`/event loop for `QThread`+`DirectConnection` capture; the
    `scan-bench-sanity` CI job has no `QApplication` bootstrap shown and
    used `continue-on-error: true`, so the Qt baseline arm could silently
    no-op with no gate failure. Binding fix L2951: assert Qt-arm
    `files_per_s>0`, remove `continue-on-error` for that step (or split it
    into its own non-swallowed step).
16. **T6-deadlock-mechanism** — corrects Appendix B's own framing: a
    surviving `isInterruptionRequested()` call in the QThread-stripped
    pipeline raises `AttributeError` (loud), not a silent deadlock as
    originally described. The REAL residual gap: no Phase-0 exit-check
    asserts ZERO surviving `isInterruptionRequested()`/QThread-method refs
    after extraction (a partial extraction, e.g. 10-of-11 sites, isn't
    caught). Binding fix L2957: add a Phase-0 exit-checklist item + a
    `tests/test_ui_probes.py` AST probe for zero survivors.

**Refuted (recorded so not re-raised, L2838-2842):** T1-SSE-terminal-event
(the §5.4/§2.4 terminal-event fallback DOES exist; only a minor
first-connection write-ordering gap survives) — T3-DCL (`_get_wic_executor()`'s
sole call site is the asyncio-thread-evaluated first arg to
`run_in_executor`; no cross-thread interleave is possible on the design's own
code path).

---

## §Goals & Scope — `docs/audits/web-port-feasibility-2026-06-19.md`

**GOAL-1 (the question, quoted, L15-22):** *"Can photo-manager ... become a
localhost web app ... under two hard constraints set by the owner: 1. Core
functionality unchanged; performance equal or better. 2. An equivalent QA
system after the change ..."* Plus a forward-looking third goal: *"cross-platform
down the road, and an MVC-style architecture so that future tech debt stays
small."*

**GOAL-2 (verdict, quoted, L28):** *"Feasible — and this codebase is
unusually well-suited to it — but it is a rewrite of the view layer + QA
harness, not a 'port'."* Reuse ratio: ≈11% reuse / 89% new code; estimate
18-24 person-weeks (L39).

**GOAL-3 (constraint 1 answered, L43):** Scan throughput — *"yes, guaranteed
parity (or marginally better)."* Thumbnail browsing — *"parity is achievable
but not free — it needs deliberate engineering."* Full-resolution RAW review
— *"the one structurally weaker surface in a browser-delivery model."*

**GOAL-4 (constraint 2 answered, L44):** *"You cannot have an identical QA
system (UIA is desktop-only), but you get an equivalent-or-better one for
~94% of scenarios ... and lose true end-to-end coverage on ~6% of native-OS
scenarios, which downgrade to API/property assertions."*

**GOAL-5 (design rule, L77):** *"run scans in a dedicated worker process, not
inline in the uvicorn worker, so HTTP serving does not contend on the GIL
with PIL decode. This is the single most important perf decision in the
port."*

**GOAL-6 (migration path table, L310-323)** — 5 phases, each with an explicit
gate (quoted from the table):
  - Phase 0 — Service extraction (2-4 wk). Gate: "Qt + qa-batch."
  - Phase 1 — Scan API + SSE + QA parallel start (3-4 wk). Gate: "Qt primary;
    Playwright shadow."
  - Phase 2 — Frontend core (4-5 wk). Gate: "Playwright reaches core parity."
  - Phase 3 — Remaining dialogs + OS integration (2-3 wk). Gate: "Playwright
    ≥90%."
  - Phase 4 — Cutover (1-2 wk). Gate: "Playwright 100% (minus the 4
    native-only)."

**GOAL-7 (recommendation, L346-348, quoted):** *"The go/no-go is the owner's
... The recommended low-regret first step is the Phase 0 headless-core spike
... reversible, improves the current Qt app, and is the cross-platform MVC
foundation regardless of the eventual UI decision."*

**Non-goal / risk register items (L327-337, audit-relevant):** QA parity
under-counted risk (serious, "manageable" — budget 4-8wk standalone, accept
property-test downgrade on ~6%); perf "unchanged" claim is FALSE for the
thumbnail path specifically (manageable, needs engineering not automatic);
hidden Qt coupling (15 cancel sites, `QGuiApplication.primaryScreen()` in
`image_tasks`) — manageable, systematic fix required.

**§9 design-prototype findings (audit-relevant claims, L227-307):** the 4
custom QPainter cells (similarity badge, decision control, score bar, lock
icon) are confirmed **trivial DOM** in the reference prototype — but the
decision model is **4-state** (keep/delete/remove/undecided), not Qt's
3-segment control (L239-242); two distinct row layouts (Aperture flat table
vs Daylight card-per-group) are **added scope** not in the original estimate
(L283-289); the prototype has **no virtualisation, no keyboard model** — both
still net-new build (L291-297).

---

## §Phase criteria — epics #641-#647 + #744

Each epic's own "Acceptance criteria" checkboxes, quoted/paraphrased with
issue number. **Cross-check note (from `web-port-program-status-2026-07-02.md`
§3, quoted):** *"The epic issues #641–#647 have every checkbox UNCHECKED,
even though Phases 0–4 have shipped. Do not trust the epic checkboxes for
progress."* — so the audit must verify each item below against code, not
against the GitHub checkbox render state.

- **#641** (CLOSED) — CI:pr-gates widen triggers to `docs/web-port-feasibility`.
  Criteria: "A sub-PR targeting `docs/web-port-feasibility` runs pytest +
  qa-batch + pr-gates + news-gate." / "`master`-targeted PRs are unaffected."
- **#642** (OPEN) — Phase 0, headless-core extraction. Scope criteria:
  "`core/app_service/` imports with zero Qt ... the current Qt app consumes
  it as a thin client." / "`image_service` returns bytes; in-memory LRU
  budget uses `len(bytes)`." PLUS 6 binding-correction checkboxes owned by
  this phase (= Corrections #1, #16, #3, #7+#8, #9, #4 above — issue body's
  own T-tags: T2-cancel_flag, T6-deadlock-mechanism, T2-write-premise+T2-apply_auto_select,
  T3-STA-unenforced+T3-CoUninitialize, T4-OOM(core side), T2-site-count).
- **#643** (OPEN) — Phase 1, scan API + SSE + image + Playwright bootstrap.
  Scope criterion: "Scan drives end-to-end over `/api/scan/*` with SSE
  progress + working cancel; a browser refresh resumes via Last-Event-ID
  without aborting the scan." PLUS 6 binding-correction checkboxes (=
  Corrections #5, #6, #11, #9(serving side), #12+#13, #14 above).
- **#644** (OPEN) — Phase 2, React frontend core. Criteria: "Decision tree
  renders virtualized over a large library; D/K/P + multi-select behave;
  Aperture + Daylight + Compact/Comfortable match the prototype." / "Every
  interactive element carries the `data-testid` surface C4 requires
  (`row-file-{group_id}-{basename}` + sub-cells), so Phase-1's Playwright
  harness can drive it."
- **#645** (OPEN) — Phase 3, remaining dialogs + OS integration. Criteria:
  "All Qt dialogs have web equivalents; OS-integration actions work on
  localhost and degrade gracefully otherwise." / "The 'Unassigned concerns
  (gaps)' from the tech-design are each resolved with a documented
  decision." (9 unassigned concerns listed in the design doc, L2744-2757:
  CORS policy, error envelope shape, settings persistence ownership, i18n
  delivery, session lifecycle/singleton enforcement, path
  security/allowed-roots, UNC/NAS path handling, pywebview integration
  contract, video streaming endpoint, manifest upload/download — 10 items
  actually listed, audit should verify the exact count.)
- **#646** (OPEN) — Phase 4, cutover. Criterion: "Playwright parity ≥
  (runtime-derived scenario total − the documented no-analog set)." PLUS 2
  binding-correction checkboxes (= Corrections #10, #15 above) PLUS "PySide6
  removed; the web app is the shipped artifact."
- **#647** (OPEN) — the program epic/tracker. Decision milestones marked
  done (checked): feasibility eval, design-prototype analysis (§9),
  5-contract tech design, adversarial review (16 corrections recorded),
  program backlog filed. Build-phase checklist (#641-#646) all UNCHECKED in
  the raw issue body regardless of actual ship state (per the program-status
  doc's own warning above) — audit each phase against code, not this
  checkbox render. #647 also duplicates the full 16-correction
  phase-assignment table (Phase 0: 6 / Phase 1: 6 / Phase 4: 2 / refuted: 2,
  no action) — use as cross-check against §Corrections above.
- **#744** (OPEN) — reconcile web UI with the DesignSync Daylight/Aperture
  prototype. Deliverable definition (quoted, "How" section): *"1. Pull the
  prototype (DesignSync get_file, 'Photo Dedup Review.dc.html'). 2. Diff key
  surfaces: result tree, scan/execute/action dialogs, buttons, status bar;
  light + dark themes. 3. Produce a divergence list; owner marks each item
  fix-to-prototype vs sign-off-as-is; implement the fix bucket."* Context:
  *"2026-07-02 owner smoke finding 2 ('按鈕設計跟原本 Design 不同')."*
  **Audit-critical:** per the T2-1 census row for #744 (parity matrix,
  below), this deliverable is verdicted **"partial"** — only the one named
  finding was fixed; the full reconciliation pass / divergence list
  artifact does not exist. The design-doc's own §5 staleness note (added by
  the SAME #744 audit effort, per its commit message) is the closest thing
  to a divergence list currently on record, and it is prose, not the
  structured owner-sign-off artifact #744 asked for.

---

## §Parity claims — `docs/audits/web-parity-matrix-2026-07.md`

Two independent tracks, both self-consistent and cross-verified by
recomputation during this extraction (row/issue counts below were
independently re-tallied from the raw tables, not just copied from the
doc's own headline).

### Track T1 — features.md sweep: 70/70 rows, grouped by slice

| Slice | features.md lines | Rows | parity | deliberate-omit | qt-only-n/a | GAP |
|---|---|---|---|---|---|---|
| T1A | 91-268 | 15 | 14 | 0 | 1 | 0 |
| T1B | 269-477 | 17 | (no per-slice summary printed in doc; re-tallied: 12 parity, 3 deliberate-omit, 2 qt-only-n/a) | | | 0 |
| T1C | 478-637 | 14 | 14 | 0 | 0 | 0 |
| T1D | 638-956\* | 13 | 11 | 1 (split-verdict row) | 1 | 0 |
| T1E | 812-956 | 11 | 10 | 0 | 1 | 0 |
| **Total** | | **70** | **61** | **4** | **5** | **0** |

\*T1D's own header says lines 638-811; two rows (789/798, invalid-path +
scan-progress) fall in the 812-956 range T1E's header also claims —
overlapping line ranges between T1D and T1E are a doc-formatting artifact,
not a double-counted row (verified: no title string repeats).

Totals match the doc's own headline exactly (L14-15, quoted): *"70/70 rows
classified, ZERO unrecorded gaps. parity 61 · deliberate-omit 4 ·
qt-only-n/a 5."* **Every parity-verdict row is a CLAIM the audit must spot
verify** — each row in the source doc carries its own file:line evidence
citation; re-open `web-parity-matrix-2026-07.md` directly for the full
per-row table rather than re-deriving it here (69 individual claims × full
evidence text would bloat this checklist without adding audit value — the
row TITLES below are the enumeration; the source doc has the anchors).

**Full row-title enumeration (audit must check all 70, none silently
dropped):** T1A — bulk regex remove-from-list; context-menu lock/unlock;
context-menu open-folder; context-menu set-action; empty-area context menu;
empty-state action buttons; execute base flow; execute complete-group
delete-confirm; execute complete-group warning banner; execute dialog
geometry persistence (qt-only-n/a); execute lock-confirm dialog; execute
preview pane; execute partial-via-selected; execute filter-by-action-type;
execute scope-to-selected-groups. T1B — exit dirty-flag prompt
(qt-only-n/a); singleton-prune offer; keep-worthiness scoring; language
switch; List-menu remove (deliberate-omit); Log menu (deliberate-omit);
close-during-scan confirm; column order/width persistence; window
geometry+splitter (qt-only-n/a); preview-panel resizable+persist; keyboard
navigation; results-tree double-click; row-selection no-auto-scroll
(qt-only-n/a); sort persistence; status-bar baseline; Open-Manifest base
flow; Save-Manifest base flow (deliberate-omit). T1C — auto-select after
scan; auto-select aggressive; stage/throughput/ETA progress; collapse
Advanced-Settings; folder list (no priority arrows); exiftool workers
(setting-only); read-knee autotune opt-out; hash-pool mode; hash workers
(NAS-aware, no UI); multi-source scan; manifest-summary in progress log;
walk skip-on-error; rescan confirm; visual-selection-of-KEEP-rows.
T1D — Set-Action dual-section Simple+Regex; live preview+validation;
numeric comparison panel; Score/Lock/Resolution fields; Set-Action geometry
persistence (qt-only-n/a); Web Action-dialog (Set-Action-by-Field); Web
main-tree multi-selection; Web Execute-only-selected; Web ScanDialog
persistence; Web column sort/resize persistence; Web manifest decision
persistence; Web scan-dialog invalid path (deliberate-omit + parity split);
Web scan-progress throughput/ETA/localized stages. T1E — SSE
connection-drop watchdog; desktop shell (pywebview launcher); similarity
column; preview-pane byte-budget LRU cache; full-resolution viewer;
no-autoplay video default; video transcode fallback (V2); video playback
(V1); group-grid view (multi-tile); group-sync controller
(GroupMediaController); developer memory-probe tool (qt-only-n/a).

### Track T2 — issue census: 51/51 triaged, 4 batches

| Batch | Issues | implemented-admin-open | real-gap | partial | tracker/epic |
|---|---|---|---|---|---|
| T2-1 | #764,757,744,743,742,741,740,739,738,737,736 (11) | 6 | 1 (#757) | 4 (#744,743,739,737) | 0 |
| T2-2 | #735,734,733,721,718,713,712,709,708,703,702,699 (12) | 8 | 3 (#713,709,699) | 1 (#702) | 0 |
| T2-3 | #697,694,692,689,688,687,686,685,680,678,676,674 (12) | 10 | 1 (#680) | 1 (#678) | 0 |
| T2-4 | #673,669,662,661,658,653,652,651,647,646,645,644,643,642,641,622 (16) | 4 (#661,652,651,641) | 4 (#669,662,658,653) | 2 (#673,622) | 6 (#647,646,645,644,643,642) |
| **Total** | **51** | **28** | **9** | **8** | **6** |

Totals independently re-verified against the doc's own headline (L16-19):
28 close-ready, 9 real remaining work (3 ship-relevant #662/#658/#653, 5
low-priority #709/#699/#713/#680/#669, 1 Qt-side-only #757), 8 partial
(#744,#743,#739,#737,#673,#622,#702,#678), 6 epics/trackers (#642-#647).
Match confirmed.

**Ship-relevant real-gaps to prioritize in the audit (owner-flagged, L18):**
- **#662** — CSRF Origin/Host guard + manifest-root trust boundary. No
  middleware found rejecting cross-origin on mutating routes as of the audit
  date; `allowed_roots` still runtime-seeded with no independent
  OS-configured allow-list. **NOTE:** `git log` (see WORKING DIR check
  during this extraction) shows commits `6d5e3ce`/`dbb0c1d`
  *"feat(web-security): Origin/Host CSRF guard on mutating routes (#662)"*
  postdate the parity matrix (2026-07-11) and are the current tree tip —
  **this real-gap verdict is likely stale; the audit's first job on #662 is
  to check whether it has since shipped.**
- **#658** — `JsonSettings.set` clobbers a non-dict intermediate node
  silently (no raise).
- **#653** — `JsonSettings.save` is not atomic (no temp+`os.replace`,
  contrast with `scanner/manifest.py`'s existing atomic pattern).

**All P-1 flagged item (from the Surprise section above):** the
`ctx-apply-best-copy` / s72 contradiction is NOT a census row in this
matrix (s72/#744-web-only postdates it) — audit must resolve it directly
against current code, it has no existing verdict to check.

### Deliberate omissions — owner endorsement list (G1 sign-off table)

`web-parity-matrix-2026-07.md:24-32`, 4 rows, quoted:

| # | Omission | Recorded where | Fallback |
|---|---|---|---|
| 1 | Inline (pre-scan) source-path validation | declined in commit `eac87ca`; `s38_scan_dialog_invalid_path.py:34-40` | post-submit SSE `failed` event names the bad path |
| 2 | List menu — Remove from List (menu-bar entry) | `MenuBar.tsx:13` | context menu + multi-select cover it — **"scheduled to be implemented in P3.2(c) instead of endorsed"** (audit: check whether P3.2(c) landed) |
| 3 | Log menu | `MenuBar.tsx:14` | scan-log pane covers live-log need; no `/api/log*` endpoint |
| 4 | Save Manifest menu entry | `MenuBar.tsx:10-12` | decisions persist synchronously per `PATCH /api/decision` |

**qt-only-n/a (no endorsement needed — concept has no web analog, 5 items,
L33-36):** Execute-dialog geometry persistence; Set-Action-dialog geometry
persistence; Exit dirty-flag prompt; Qt row-selection auto-scroll bug class;
developer memory probe (removed by design, `web-port-tech-design.md:1318`).

---

## §Endorsed omissions — audit framing reminder

Every item in the G1 table above and the qt-only-n/a list is to be audited
as **"still endorsed & correctly documented"**, i.e.: (a) does the omission
still hold true in the current code (nobody silently half-built it since)?
(b) is the fallback claim still accurate? (c) for omission #2, specifically
check whether the "scheduled for P3.2(c)" note has since been fulfilled or
is still pending — this is the one omission on this list explicitly framed
as temporary, not permanent.

---

## §Scenario ledger — `qa/web/scenario_map.yml`

Invariant stated in the file's own header (L15-16, quoted): *"the number of
rows in this file must equal len(ALL_SCENARIOS). Enforced by
`scripts/check_qa_parity.py`."*

**Mechanically counted (not eyeballed):** 71 total scenario entries.
Status breakdown: **67 `done`, 4 `skip`, 0 `todo`, 0 `in_progress`.**

Two scenario numbers are intentionally absent from `ALL_SCENARIOS` (not
missing rows — explicit design decisions, each noted inline at its
neighbor): **s46** (noted in s47's entry, L275: "s46 intentionally absent
from ALL_SCENARIOS") and **s62** (noted in s63's entry, L391: "s62
intentionally absent from ALL_SCENARIOS").

**Every non-done (skip) id, with its note (all 4):**

1. **s18_log_menu** (L104-107) — SKIP, no web equivalent surface. Desktop
   Log menu has no web analog (MenuBar deliberate deferral, no
   `/api/log*` endpoint, no on-disk app log file on the web layer).
2. **s28_exit_dirty_prompt** (L159-162) — SKIP, desktop-only (#688). No
   interceptable OS close with a custom 3-button dialog in a browser tab;
   close-during-scan guard covered separately by s63 (#687/#703, done).
3. **s48_dialog_geometry_persist** (L277-280) — SKIP, desktop-only (#688).
   Radix/DOM dialog overlays have no user-resizable geometry or persistence
   contract to round-trip. (Note: explicitly distinguishes this from s47
   and s39, which ARE real ported web features, not skips.)
4. **s59_execute_dialog_select_by_main_tree_sync** (L332-335) — SKIP,
   "web-architecture-eliminated (NOT blocked)." The Qt bug class this
   scenario pins (stale main-tree Action cells after an Execute-dialog
   reject path) cannot exist on the web: `state.manifest.groups` is the
   single reactive source of truth for both views, so there is no separate
   render artifact to desync. A web port "would only assert React's own
   reactivity ... = metric-gaming, banned by the project no-padding rule."
   Scoped 2026-06-27 via a 4-candidate parity workflow. Load-bearing data
   assertion already covered by s30.

**Cross-reference note captured en route (parity matrix L48-49, not a
gap):** `scenario_map.yml`'s s44 key name (`s44_execute_highlighted_rows`)
is stale versus its module (`s44_execute_selected_only`) — cosmetic
mismatch, resolves correctly, flagged for rename at next touch, not a
functional gap.

**Historical scenario-count drift (context for Correction #14 above):** the
16-corrections review (2026-06-19) found the AST-verified count was 67 at
that time; the doc's own conflicting claims were 68 (C4) and 64 (C5). The
CURRENT live count is 71 (67 done + 4 skip) — 4 higher than the review-time
67, due to the later video-scenario workstream (s69-s71, V1/V2/V3) and
s72 (#744, web-only, no Qt analog). **Audit item:** confirm
`scripts/check_qa_parity.py` and any `PHASE_TARGETS`/`--require-all`
threshold in that script currently derive the total at runtime
(`len(ALL_SCENARIOS)`) rather than a hard-coded number — Correction #14's
binding fix explicitly demanded this, and a stale hard-coded number would
be silently wrong today given the count has moved twice since the review.
