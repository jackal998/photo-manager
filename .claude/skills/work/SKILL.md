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
| `#N` or `#N #M …` | `gh issue view N --json number,title,body,labels,state` for each |
| Quoted string | Treat as intent — grep codebase for related files |
| `feature/…` or `fix/…` or any branch name | **Short-circuit: skip Phases 1–3. Run `/pr-review <branch>` directly.** (Use `git diff origin/master...<branch> --stat` only to confirm the branch exists.) |
| Nothing | `gh issue list --assignee @me --state open --limit 5` + `git branch --show-current` |

If multiple issues: fan-out to one researcher-agent invocation per
issue (parallel), merge briefs before planning.

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

### multi-issue — fan out

1. Run `/parallel-brief-generator` with the list of issue numbers.
2. Each generated brief becomes a self-contained prompt for a separate
   Desktop session.
3. Emit the briefs and stop — parallel Desktop sessions are
   human-initiated.

## Self-management rules

**Context budget:** After each major phase (research, dev iteration,
QA), check whether the context window is getting heavy. If the
conversation has > ~40 tool calls, run `/compact` before the next phase.

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

## Invocation examples

```
/work #257                     → simple: 2 doc edits, LEAD handles directly
/work #326                     → complex: 35 features.md entries to verify
/work "add dark mode"          → medium: researcher maps affected files first
/work #323 #324 #325           → multi-issue: fan out to 3 parallel sessions
/work feature/my-branch        → review only: skip research, go to /pr-review
```
