---
name: work
description: Universal entry point for any task — issue number, free text, branch name, or nothing. Spawns researcher-agent to investigate from three angles, presents a complexity-scored plan with recommended workflow, waits for human approval, then executes. Single trigger replaces manual decisions about which coordination mode to use.
origin: local
---

# /work — universal task entry point

One command that accepts any trigger, investigates before acting, and
routes to the right coordination mode automatically.

```
/work #257                     → GitHub issue
/work "fix the scanner bug"    → free-form intent
/work feature/my-branch        → investigate + review a branch
/work                          → infer from context (open issues, current branch)
```

## Phase 0 — parse the trigger

Before spawning anything, classify the input:

| Input shape | How to resolve |
|---|---|
| `#N` or `#N #M …` | Strip `#`; validate remainder is numeric. Then `gh issue view N --json number,title,body,labels,state` for each. |
| Quoted string | Treat as intent — grep codebase for related files |
| `feature/…` or `fix/…` or any branch name | **Short-circuit: skip Phases 1–3. Run `/pr-review <branch>` directly.** Confirm branch exists first: `git rev-parse --verify <branch>`. Reject names starting with `-`. |
| Nothing | `gh issue list --assignee @me --state open --limit 5` + `git branch --show-current` |

**`gh` preflight** (applies to `#N` and `Nothing` shapes): run `gh auth status`
before any `gh` call. If it fails, report "gh CLI unavailable — falling back
to grep-only resolution" and treat the input as free-text instead.

If multiple issues: fan-out to one researcher-agent invocation per
issue (parallel), merge briefs before planning.

**File-overlap check (multi-issue only).** Before recommending the
multi-issue parallel-sessions path in Phase 2, scan each researcher
brief's `affected_files` list. If any two issues touch the same file,
**force sequential-in-single-session** instead of parallel fan-out —
parallel sessions on overlapping files cause merge conflicts that
cost more than the parallelism saves. The Phase 3 plan should call
this out explicitly: "issues #N and #M both touch `<file>` →
sequencing in this session in order: #X → #Y → #Z."

## Phase 1 — spawn researcher-agent

Invoke `researcher-agent` as a subagent. Pass the resolved task spec
as the prompt:

```
Agent(
  subagent_type="researcher-agent",
  prompt="""
  Task spec:
  <paste resolved trigger here — issue body / intent / diff stat>

  Investigate all three angles and return your RESEARCH BRIEF.
  """
)
```

Wait for the `RESEARCH BRIEF` result. Do not proceed until it arrives.

## Phase 2 — build the plan

Read the brief. Extract:
- **Complexity score** (`simple` / `medium` / `complex` / `multi-issue`)
- **Affected files** and blast-radius flags
- **Coverage gaps** (drives whether QA subagent is needed)
- **Risk flags** (drives whether `/pr-review team` is warranted)

Map complexity → execution workflow:

| Score | Execution plan |
|---|---|
| **simple** | LEAD implements directly. `/pr-review` (single-session) at end. |
| **medium** | LEAD implements guided by brief. Dev subagent for isolated worktree if > 5 files. `/pr-review` at end. |
| **complex** | Dev subagent (`isolation: "worktree"`) → QA subagent loop → `/pr-review team`. |
| **multi-issue** | `/parallel-brief-generator` to fan out; each issue → its own session. |

**The complexity score IS the auto-decline gate for subagent spawn.**
`simple` and `medium` stay in LEAD; `complex` and `multi-issue` spawn
subagents at roughly 4× single-session cost. Same shape as `/pr-review`
team mode's auto-decline at ≤5 files / ≤300 diff lines — don't pay the
overhead unless the score justifies it.

## Phase 3 — present to human for approval

Print the plan in this format before doing anything else:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
/work plan — <task title>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Complexity: <score>
Affected:   <N files> — <highest blast-radius file>
Gaps:       <N test gaps / "none">
Risks:      <top risk flag / "none">

Workflow:
  1. <step>
  2. <step>
  …

Cost estimate: ~<N>× single-session
  (covers: researcher + dev iterations + qa iterations; excludes
  LEAD overhead and Phase-5 /pr-review-team multiplier)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Say "go" to execute, or adjust: "go but skip QA", "go with team review", etc.
```

**Wait for explicit approval before Phase 4.** Do not self-approve.
Accepted phrases: "go", "yes", "do it", "proceed", "ship it", or any
instruction that modifies the plan ("go but only fix the README part").

## Phase 4 — execute

Execute the approved workflow. Route based on complexity score:

### simple / medium — LEAD implements

1. Read the research brief's affected-files list.
2. Run `/impact-map` on the first entry point if blast-radius is `high`.
3. Implement the fix/feature directly in LEAD's session.
4. Run `python -m pytest <affected test files> -x` to verify.
5. Run `/pr-review` (single-session, no `team` flag) — task is small
   enough that team overhead isn't justified.

### complex — subagent pipeline

1. **developer-agent** (worktree-isolated):
   ```
   Agent(
     subagent_type="developer-agent",
     isolation="worktree",
     prompt="""
     RESEARCH BRIEF:
     <paste full researcher-agent brief>

     TASK:
     <one-paragraph implementation instruction derived from the brief>

     WORKTREE:
     <this will be your working directory — probe for venv as per your instructions>
     """
   )
   ```
   Receive back: `IMPLEMENTATION REPORT` with status, changed files,
   test results, and worktree branch name.

2. **qa-agent** (validates the dev worktree):
   ```
   Agent(
     subagent_type="qa-agent",
     prompt="""
     RESEARCH BRIEF:
     <paste full researcher-agent brief>

     IMPLEMENTATION REPORT:
     <paste developer-agent's IMPLEMENTATION REPORT>

     WORKTREE PATH:
     <absolute path returned by the developer-agent's worktree>
     """
   )
   ```
   Receive back: `QA REPORT` with `PASS` / `FAIL` / `PASS_WITH_NOTES`.

3. **Loop** until qa-agent returns `PASS` or `PASS_WITH_NOTES`:
   - On `FAIL`: spawn a new `developer-agent` (fresh `isolation="worktree"`)
     with the original brief PLUS qa-agent's failure details appended.
     Each iteration gets a clean worktree — don't re-use the previous one.
   - On `PASS` / `PASS_WITH_NOTES`: proceed to step 4.
   - Hard limit: 4 iterations. On 4th `FAIL`, stop and surface to human:
     "QA failed after 4 attempts — remaining issues: <list>."

4. **Merge dev worktree into LEAD's branch** *(gated — surface before running):*
   ```bash
   git merge <worktree-branch>   # or cherry-pick if cleaner
   ```
   Per CLAUDE.md security gates, `git merge` modifies local state and
   requires an explicit "yes" from the user before running. Surface:
   "Merging branch `<name>` into current branch — proceed?"

5. Run `/pr-review team` if diff qualifies (> 5 behaviour-bearing files
   or > 300 lines); otherwise `/pr-review` single-session.

### multi-issue — fan out OR sequence

If Phase 2's file-overlap check found **no overlap**: parallel fan-out.

1. Run `/parallel-brief-generator` with the list of issue numbers.
2. Each generated brief becomes a self-contained prompt for a separate
   Desktop session.
3. Emit the briefs and stop — parallel Desktop sessions are
   human-initiated.

If overlap **was** found: sequence in this session.

1. Pick an order (smallest blast-radius first usually wins —
   reduces rebase pain on the larger PRs).
2. Run Phase 4's simple/medium or complex path for the first issue
   end-to-end, including Phase 5 (delegated `/github-pr-create`). Don't start
   the next issue until the previous PR's CI is green or the human
   explicitly says to proceed.
3. Repeat for each remaining issue. The branch base for issue #2 is
   the **merged** main branch, not the open PR — wait for human
   merge before starting #2.

## Phase 5 — Open the PR and drive it green (delegated)

Phase 5 starts when Phase 4 is ready to open the PR — right after the
`/pr-review` step. The simple, medium, and complex paths all funnel
through Phase 5. Multi-issue fan-out skips it (each parallel session
runs its own).

**Phase 5 is delegated wholesale to [`/github-pr-create`](../github-pr-create/SKILL.md).**
That skill is the single source of truth for opening a PR that goes
green — it owns `gh pr create`, the news fragment, the CI watch, the
one-shot red-fix auto-iteration, and the merge-ready handoff. `/work`
does **not** inline those steps; duplicating them is exactly the
scatter that used to drop the news fragment. The skill's exit condition
is CI green (or a surfaced failure after one auto-iteration), which is
also Phase 5's — `/work` is done when `/github-pr-create` returns.

Hand the skill the context Phase 4 already has:

- the feature branch (pushed),
- the docs decision (features.md updated via `/update-docs`, or the
  `[docs-not-needed: <reason>]` token),
- the qa decision (driver added, or the `[qa-not-needed: <reason>]`
  token),
- the news decision (changelog line, or `[skip-news: <reason>]`).

Everything downstream of that — title/body composition, create, news
`news/<PR>.<type>`, `gh pr checks --watch` with the 20-min timeout, the
single auto-iteration on red, "ready for your merge" — is the skill's
gate map and workflow. Read it there, not here.

### 5.1 — `/work watch <N>` resume mode (Phase 0 extension)

The CI-watch listener can stall (see `/github-pr-create` Step 5). To
support the "I had to step away mid-watch" case, Phase 0 also accepts:

| Input shape | How to resolve |
|---|---|
| `watch #N` or `watch <N>` | **Skip Phases 1–4 entirely.** Re-enter `/github-pr-create` at its Step 5 (CI watch) for PR `<N>`. No researcher, no plan, no implementation. |

## Self-management rules

**Context budget:** After each major phase (research, dev iteration,
QA), check whether the context window is getting heavy. If the
conversation has > ~40 tool calls, tell the user: "Context is getting
heavy — please run `/compact` before we continue." (`/compact` is a
user-triggered CLI command; LEAD cannot invoke it directly.)

**Loop guard:** Never iterate more than 4 dev→QA cycles without
surfacing to the human. Autonomous loops that spin indefinitely are
worse than a pause for input.

**Partial completion:** If a phase fails (researcher-agent times out,
dev subagent errors), report what succeeded and what failed. Don't
silently skip a phase.

**Scope discipline:** If the researcher-agent's brief surfaces
unrelated issues (e.g. a coverage gap in a file you're not changing),
note them as "out of scope — file as follow-up issue" and don't act on
them unless the human says to.

## Anti-patterns

- ✗ Don't start Phase 4 without human approval of Phase 3's plan.
- ✗ Don't skip the researcher-agent to "save time" — the brief is what
  makes the routing decision reliable.
- ✗ Don't use Agent Teams for the dev subagent (worktree isolation not
  yet solved for teammates). Use `Agent(isolation: "worktree")` instead.
- ✗ Don't run `/pr-review team` on a trivially small diff — team mode
  auto-declines anyway, but don't waste the spawn attempt.
- ✗ Don't fan out to parallel Desktop sessions for a single issue —
  that's only for truly independent multi-issue work.
- ✗ Don't declare /work done at "PR opened" — Phase 5's exit condition
  is CI green or a surfaced failure after one auto-iteration. PR-open-
  and-hope is the leak this skill is built to close.
- ✗ Don't loop on `--watch` after a timeout. The listener is known
  flaky; loop guard is one watch + one one-shot status read, then
  exit with the resume hint.
- ✗ Don't fan out multi-issue tasks in parallel sessions when the
  briefs show file overlap — sequence them in a single session
  instead. Parallel sessions on shared files cost more in merge
  conflicts than they save in latency.
- ✗ Don't add subagents to a "medium" task to "be thorough". Google
  Research (arxiv 2512.08296, 2026) shows independent agents amplify
  errors 17.2× vs 4.4× for centrally-coordinated ones, and adding
  agents degrades performance up to 70% on sequential tasks. The
  complexity table is the gate.

**MAST failure-category coverage** (arxiv 2503.13657, 14 modes in 3
categories — Multi-Agent System Failure Taxonomy):
(i) system design issues → addressed by Phase 2 complexity gate +
Phase 3 human plan-approval. /pr-review's gate decomposition is the
same pattern at the manager level (narrow trigger conditions, sub-skills
independently editable).
(ii) inter-agent misalignment → addressed by single-direction brief
passing (researcher → developer → qa, no loops) and central LEAD
ownership of merges and remote writes.
(iii) task verification → addressed by qa-agent (Generator-Verifier)
and the 4-cycle dev↔QA loop cap.

## Invocation examples

```
/work #257                     → simple: 2 doc edits, LEAD handles directly
/work #326                     → medium: ~30 features.md entries to verify
/work "add dark mode"          → medium: researcher maps affected files first
/work #323 #324 #325           → multi-issue, no overlap: fan out to 3 parallel sessions
/work #324 #325 #326           → multi-issue, file overlap on _batch.py: sequence in this session
/work feature/my-branch        → review only: skip research, go to /pr-review
/work watch #342               → resume Phase 5 watch on PR #342 (timeout/interrupt recovery)
```
