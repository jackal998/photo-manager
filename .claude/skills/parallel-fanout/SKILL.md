---
name: parallel-fanout
description: Generate self-contained briefs for N cold Claude Code desktop sessions to work in parallel on N file-disjoint tasks. The orchestrator session (where this skill is invoked) does cross-bundle coordination — file-scope collision check, scenario slot pre-assignment, base SHA pinning — and outputs N copy-paste-ready prompts. Each prompt goes into a fresh "+ New session" → Select folder; the desktop app auto-creates a worktree per session, so the cold sessions are truly isolated. Use this when the user wants to ship multiple unrelated issues at once ("fan out X, Y, Z in parallel", "work on these N issues at the same time", "ship the status-bar bundle and the scan-dialog fix in parallel"), or when scope is file-disjoint and serializing would just waste wall-clock time. Do NOT use for: a single task (no coordination needed — just paste the task into a new session), or tasks where one depends on another merging first.
---

# parallel-fanout — brief generator for parallel desktop sessions

## What this skill actually does

The Claude Code desktop app creates a git worktree for every new
session automatically (under `.claude/worktrees/<session-name>/`).
That means the parallelism mechanism is built in: open N "+ New
session" windows on the same project, and you have N isolated
worktrees with N independent contexts, no orchestration overhead.

But N cold sessions can't coordinate with each other. They share no
state at startup. So three things still need a single orchestrator:

1. **File-scope collision check** — if Bundle A and Bundle B both
   plan to edit `main_window.py`, you want to know before fanning,
   so the second-merged branch's rebase cost is named up-front (not
   discovered at PR-merge time).
2. **Scenario slot pre-assignment** — if two cold sessions both grab
   `qa/scenarios/s37_*.py`, you get a name collision at PR time.
   Hand out s37, s38, s39 in advance.
3. **Base SHA pinning** — all N sessions branch off the same commit,
   so their work is comparable and merge-order-flexible.

This skill, run in the orchestrator session, does those three things
and **emits N briefs** — self-contained prompts you paste into cold
sessions. Each brief tells its session everything it needs (the
pre-flight checks, the task spec, the project conventions, the
through-PR workflow) without any reference back to this conversation.

## When to use vs when to skip

Fan out when:
- **Scope is file-disjoint** — agents editing different files in
  different modules won't conflict.
- **Tasks share no API surface** — if two bundles both extend
  `DeleteResult`, fan out one, do the other after.
- **You have ≥2 independent items** and serial would mostly be idle
  wait.

Skip the skill (just open one session) when:
- **You only have one task.** Open a session, work, ship. No skill
  needed.
- **One task's design depends on another's outcome** — e.g. #68 (UI
  bucket split) depends on #165 (execute-dialog redesign). Fan-out
  wastes the loser's work.
- **A shared module is about to change shape.** Pick the larger one
  first, merge it, then fan out the rest off the new master.

## Required one-time repo setup

The desktop's auto-worktree feature copies gitignored files into new
worktrees ONLY if they match a pattern in `.worktreeinclude` at the
repo root. Without that file, every new session lands in a worktree
without `.claude/settings.json`, which means **no hooks fire** —
`qa_scenario_guard`, `docs_guard`, `zombie_check` are all dormant.

Check before generating briefs:

```
test -f .worktreeinclude && grep -q "\.claude/settings\.json" .worktreeinclude \
  && echo "OK" || echo "MISSING — set up .worktreeinclude before fanning out"
```

If missing, stop and tell the user. Don't generate briefs against a
broken worktree config.

## Orchestrator workflow

### Step 1 — Gather inputs

Before generating briefs, get from the user (or infer from
conversation):

1. **Task list** — N concrete tasks. For each: issue numbers,
   one-line summary, files/modules expected to be in scope.
2. **Base ref** — usually `master`. Confirm up to date.
3. **Branch prefix** — `fix/`, `feat/`, `chore/`, `test/` per task type.
4. **PR pipeline scope** — does each cold session push + open PR
   end-to-end (default), or stop earlier? Default: full pipeline,
   with normal per-PR gated confirms in each cold session.

If any of these is vague, ask the user. A fan-out where the second
session has to come back to the user mid-flight to clarify scope is
worse than the 30 seconds of clarification up-front.

### Step 2 — Cross-bundle pre-flight (orchestrator side)

```
git fetch origin && git status -sb       # confirm clean tree
git rev-parse origin/master               # note base SHA for all briefs
ls qa/scenarios/sNN_*.py | tail -5        # find highest taken slot
```

Then build the **file-scope matrix**: for each bundle, list the
files it plans to touch. If two bundles touch the same file,
SURFACE this to the user before continuing — name the overlap and
the likely rebase cost. Don't silently fan out overlapping work.

Pre-assign slot numbers: if bundles A, B, C each need a new
`qa/scenarios/sNN_*.py`, hand out `sNN`, `sNN+1`, `sNN+2` from the
first free slot.

### Step 3 — Generate one brief per task

Each brief is **fully self-contained**. The cold session has no
memory of this conversation; everything it needs must be in its
brief. Template structure:

```
You are working on photo-manager issue(s) #<N> in a fresh Claude Code
desktop session. You have your own auto-worktree under
`.claude/worktrees/<your-session-name>/`. The orchestrator session
fanned out parallel work; you have no visibility into the other
sessions and they have none into you.

## Pre-flight — verify before touching code

  - [ ] `git status -sb` — should be clean. If not, ask user.
  - [ ] `git rev-parse HEAD` — should be `<base-sha-noted-by-orchestrator>`
        (or ahead via origin fetch). If behind, ask user.
  - [ ] `test -f .claude/settings.json` — must exist. If missing,
        `.worktreeinclude` isn't carrying it; STOP and tell user
        before doing anything else (hooks won't fire).
  - [ ] `ls qa/scenarios/<your-pre-assigned-slot>_*.py` — should be
        empty (slot is yours). If a file already exists there, slot
        was contested; ask user how to renumber.

## Your task

Issue: <one-paragraph summary including the user-visible problem>

Files in scope:
  - <path:line> — <what to change>
  - <path:line> — <what to change>

Files NOT in scope (other cold sessions own these — touching them
will cost a rebase at PR-merge time):
  - <path> — owned by sibling session doing #<M>
  - <path> — owned by sibling session doing #<P>

Pre-assigned scenario slot: <sNN> (if you add a new layer-3 scenario)
Suggested branch: <prefix>/<slug>

Acceptance:
  - <test-layer requirements, e.g. layer-1 + layer-3 scenario>
  - <coverage floor, e.g. 70% per-file on every file touched>
  - <docs that must update if applicable>
  - <any task-specific gotchas>

## Project conventions (non-negotiable)

  - Python: `.venv/Scripts/python.exe` always (system Python lacks PySide6).
  - Branch off `<base-ref>` at SHA `<base-sha>`.
  - No `--no-verify` under any circumstances.
  - No mock-driven test padding — every assertion must catch a real
    user-visible bug. (`CLAUDE.md` "Testing hard floor" applies.)
  - Bridge pattern: if you add a new method on a handler class
    surfaced via the main-window context menu, also add the proxy on
    `ActionHandlersImpl` (`feedback_action_handlers_bridge` in
    auto-memory).

## Workflow through PR

  1. `git checkout -b <your-branch>` (your worktree starts on master).
  2. Implement the change.
  3. `.venv/Scripts/python.exe -m pytest` — must pass.
  4. `.venv/Scripts/python.exe scripts/check_coverage_per_file.py` —
     70% per-file floor on every file touched.
  5. If you added a new layer-3 scenario, run it:
     `.venv/Scripts/python.exe -m qa.scenarios._batch <sNN>_<name>`
  6. Commit with conventional-commit message + `Closes #<N>` trailer
     + `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`.
  7. **Gated:** surface push intent to user, on approval
     `git push -u origin <your-branch>`.
  8. **Gated:** surface PR-create intent, on approval
     `gh pr create --title "<title>" --body "<body>"`. Hooks
     (`qa_scenario_guard`, `docs_guard`) will fire — if blocked,
     surface the hook message and decide with user:
       a) hand-fix and retry
       b) bypass with `[qa-not-needed: <reason>]` or
          `[docs-not-needed: <reason>]` in the gh command
       c) skip this branch
  9. Capture PR URL, report to user.

## Cleanup

When PR is merged (later, possibly in a different session), the
worktree can be removed via the desktop sidebar (right-click session
→ Delete) or `git worktree remove .claude/worktrees/<your-session-name>`.
The orchestrator session is not responsible for your cleanup; this
cold session (or any future session) handles it.
```

### Step 4 — Hand briefs to user

Output each brief in its own clearly-labeled code block. Tell the
user the desktop flow:

> Open Claude Code desktop → click **+ New session** in the sidebar →
> click **Select folder** → pick the photo-manager repo. Paste
> brief #1 into the first new session. Repeat for briefs #2 and #3.
> Each new session lands in its own auto-worktree under
> `.claude/worktrees/`.

### Step 5 — Stop

The orchestrator session's job is done. It does not monitor the
cold sessions, does not aggregate their results, does not push their
branches. Each cold session ships its own PR end-to-end with the
normal gates intact.

If a cold session gets stuck and asks you (via the user relaying)
for help, treat that as a normal one-off question — answer it for
that bundle, don't try to re-orchestrate.

## Anti-patterns — do not do these

- **Generating briefs without the file-scope collision check.** Two
  cold sessions blindly editing the same file means a rebase fight
  at PR-merge time you didn't see coming.
- **Skipping the `.worktreeinclude` precondition check.** Without
  that file, the new sessions have no hooks active and the security
  model silently degrades.
- **Embedding "ask the orchestrator session" in the brief.** Cold
  sessions are independent; if they need a decision the brief
  didn't anticipate, they ask the user, not the orchestrator.
- **Trying to fan out from a sub-agent.** Sub-agents in this harness
  have permission constraints (Edit/Write denied by hooks like
  `suggest-compact`, Bash sometimes denied). Fan-out is a
  user-driven, session-driven operation. See
  `feedback_sub_agent_write_denied` in auto-memory if you've
  forgotten.

## Photo-manager-specific reminders to bake into every brief

- **Hooks gating `gh pr create`** — `qa_scenario_guard` blocks PRs
  that change `app/views/{handlers,dialogs,components,workers}/`
  without a `qa/scenarios/sNN_*.py` change. `docs_guard` blocks PRs
  that add new modules under `app/`, `infrastructure/`, `scanner/`,
  `core/services/` (or new tests, or qa-scenario changes) without
  touching `README.md` / `docs/*.md` / `CLAUDE.md` / `pyproject.toml`.
- **`read_result_rows` is broken on CI** — has a `y_min=600` filter
  that drops all rows on the smaller CI render. Use sqlite reads
  (pattern: s14, s32, s35) for tree-content assertions.
- **Trailing-period Windows paths**, **Live Photo pair-clusters**,
  **case-insensitive pathlib** — these are scanner-side gotchas;
  only bake into briefs whose scope touches `scanner/`.
- **Destructive QA scenarios** — `s13`, `s36` send real files to the
  recycle bin per run. Do NOT extend destructive coverage without
  explicit user agreement.

## Output shape — what the orchestrator's final message looks like

```
Cross-bundle pre-flight:
  - Base: master @ <SHA>
  - Working tree: clean
  - .worktreeinclude: OK (covers .claude/settings.json)

File-scope matrix (collisions named):
  | Bundle | Files | Overlap with |
  | ...    | ...   | ...          |

Slot pre-assignment:
  - Bundle A → s<NN>
  - Bundle B → s<NN+1>
  ...

## Brief for Bundle A (paste into a new "+ New session")

<brief text>

## Brief for Bundle B (paste into a new "+ New session")

<brief text>

## Brief for Bundle C (paste into a new "+ New session")

<brief text>

Done. Open Claude Code desktop → + New session × N → Select folder
(photo-manager) → paste one brief into each.
```

## Quick reference — minimal flow

1. User: "fan out these N issues in parallel"
2. Orchestrator runs steps 1–3 of this skill in current session.
3. Orchestrator outputs N briefs + collision report.
4. User opens N "+ New session" windows, pastes one brief into each.
5. Each cold session does its own pre-flight, work, and PR.
