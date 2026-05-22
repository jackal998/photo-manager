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
   end-to-end, including Phase 5 (Post-PR completion). Don't start
   the next issue until the previous PR's CI is green or the human
   explicitly says to proceed.
3. Repeat for each remaining issue. The branch base for issue #2 is
   the **merged** main branch, not the open PR — wait for human
   merge before starting #2.

## Phase 5 — Post-PR completion

Phase 5 starts the moment `gh pr create` returns a PR URL — i.e.
right after the human approves the `gh pr create` gate that flows
out of Phase 4's `/pr-review` step. The simple, medium, and complex
paths all funnel through Phase 5. Multi-issue fan-out skips Phase 5
(each parallel session runs its own).

Phase 5's exit condition is **CI green on the open PR**, not "PR
opened" — the leak this section closes is the post-`gh pr create`
tail that today gets improvised into ad-hoc follow-up commits
(news fragment, CI-failure fix-up, etc.).

### 5.1 — Capture PR number

`gh pr create`'s stdout is the PR URL. Parse the trailing integer:
`https://github.com/<owner>/<repo>/pull/<N>` → N. Store as `$PR`.

### 5.2 — Write news fragment inline

The repo's CI requires `news/<PR>.<type>` matching the type table in
`news/README.md`. Steps:

1. Read the head commit's conventional-commits type:
   ```bash
   git log -1 --format=%s | grep -oE '^(feat|fix|docs|chore|test|refactor|perf|ci|build|revert)'
   ```
2. Map conventional-commits type → news fragment suffix
   (canonical table in [`news/README.md`](../../../news/README.md)):
   `feat → feature`, `fix → bugfix`, `docs → doc`,
   `chore → misc`, `refactor → misc`, `test → misc`,
   `perf → misc`, `ci → misc`, `build → misc`,
   `revert → misc`. Breaking schema changes or removed features
   that don't fit any cc type → `removal` (judge by content, not
   commit prefix).
3. Write `news/<PR>.<type>` with a one-line imperative-mood
   description ending in `(#<issue>)`. The text should be derived
   from the PR title / commit message — not invented fresh.
4. Don't ask the human to confirm the fragment text unless the
   PR title is ambiguous (single-issue, well-titled PR → just write
   it).
5. **Skip-news bypass.** If the PR body or title contains the
   literal token `[skip-news: <reason>]`, the news-gate CI check
   passes without a fragment. Don't write one in that case — Phase
   5.2 is a no-op and Phase 5.3's commit/push is skipped.

### 5.3 — Commit and push the fragment

```bash
git add news/<PR>.<type>
git commit -m "docs(news): add fragment for #<issue>"
```

Then surface the push gate per CLAUDE.md (mandatory; news-fragment
push is still a `git push`):

> **Gated action — `git push` (news fragment)**
> - What: push the news-fragment commit to `origin/<branch>`.
> - Risk: single-line text file, no secrets, additive.
> - Verdict: safe.

After "yes": `git push`.

### 5.4 — Watch CI with timeout

```bash
gh pr checks <PR> --watch --interval 30
```

Wrap in a Bash call with `timeout: 1200000` (20 minutes). Known
flakiness: the `--watch` listener can stall silently. If Bash
returns from the timeout instead of from the `gh` exit, fall through
to step 5.5's **timeout** branch and surface the current state from
a one-shot `gh pr checks <PR>`.

The user may also interrupt the watch manually (Ctrl-C / "stop").
Treat that the same as the timeout branch — re-fetch state and
surface, don't loop on watch.

### 5.5 — Branch on CI result

**Green** — `gh pr checks --watch` exits 0:
> Emit one line: `PR #<N> CI green — ready for human review.`
> EXIT Phase 5. /work is done.

**Red** — `gh pr checks --watch` exits non-zero:
1. Run `gh pr checks <PR> --output json` to enumerate failed checks
   with their names and conclusions.
2. Classify the failure: lint? unit test? `require-news-fragment`?
   integration? Each class has a known fix recipe.
3. **One auto-iteration** only — fetch the relevant log
   (`gh run view <run-id> --log-failed`), implement the fix, commit,
   surface push gate, push.
4. After the iteration's push: loop back to step 5.4 ONCE. If the
   second `--watch` returns red again, EXIT Phase 5 with:
   > `CI failed after 1 auto-iteration on PR #<N>. Failing checks:
   > <list>. Surfacing for human review — fix manually or say
   > "retry" to attempt another auto-iteration.`
5. Hard limit: never auto-iterate more than once per Phase 5 entry.
   The 4-cycle dev↔QA limit in the complex path is independent.

**Timeout / interrupt** — Bash killed the watch:
1. One-shot `gh pr checks <PR>` to get current state.
2. Surface:
   > `Watch timed out at 20min on PR #<N>. Current checks: <state>.
   > Resume the watch ("/work watch #<N>") or surface now.`
3. EXIT Phase 5 — don't loop. The user decides whether to wait.

### 5.6 — `/work watch <N>` resume mode (Phase 0 extension)

To support the timeout/interrupt case, Phase 0 also accepts:

| Input shape | How to resolve |
|---|---|
| `watch #N` or `watch <N>` | **Skip Phases 1–4 entirely.** Re-enter Phase 5 at step 5.4 for PR `<N>`. No researcher, no plan, no implementation. |

This is the "I had to step away mid-watch" affordance the listener
flakiness makes necessary.

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
