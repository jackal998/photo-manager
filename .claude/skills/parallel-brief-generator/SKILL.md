---
name: parallel-brief-generator
description: Generate self-contained briefs for one or more cold Claude Code desktop sessions that will work in isolated parallel worktrees. Researches each task via `gh issue view` + codebase Read/Grep, runs pre-flight (clean tree, base SHA pin, scenario slot pre-assignment, file-disjoint collision matrix), and outputs N copy-paste-ready prompts. The user pastes each into a fresh "+ New session" with the "create worktree" option enabled so each session is truly isolated. Use whenever the user wants to scope out one or more issues for cold-session work; single-task input is valid (cross-bundle steps are no-ops at N=1) and parallel multi-issue is the primary use case.
---

# parallel-brief-generator — briefs for isolated parallel sessions

## What this skill actually does

The Claude Code desktop app can create a git worktree per session
(under `.claude/worktrees/<session-name>/`) when the user enables
the "create worktree" option at session-open time. That gives true
isolation: each session has its own checkout, own branch, own
context — never sees the others' work.

But cold sessions can't coordinate with each other. They share no
state at startup. So a few things still need a single orchestrator:

1. **Per-task research** — the user gives high-level intent ("brief
   #144" or "fan out the status bar bundle and scan dialog fix"),
   not pre-digested specs. The orchestrator runs `gh issue view`,
   reads issue bodies, Greps the codebase for the cited files, and
   builds a concrete task spec for each.
2. **File-scope collision check** — if Bundle A and Bundle B both
   plan to edit `main_window.py`, name the overlap up-front so the
   second-merged branch's rebase cost is visible at fan-out time,
   not discovered at PR-merge time.
3. **Scenario slot pre-assignment** — if two cold sessions both grab
   `qa/scenarios/s37_*.py`, you get a name collision at PR time.
   Hand out s37, s38, s39 in advance from the first free slot.
4. **Base SHA pinning** — all sessions branch off the same commit,
   so their work is comparable and merge-order-flexible.

This skill, run in the orchestrator session, does those things and
**emits one brief per task** — self-contained prompts the user
pastes into cold sessions. Each brief tells its session everything
it needs (the pre-flight checks, the task spec, the project
conventions, the through-PR workflow) without any reference back to
the orchestrator. Single-task input is valid; the cross-bundle
steps (collision check, slot pre-assignment) are no-ops at N=1, but
the research and brief-template benefits still apply.

## When to use vs when to skip

Trigger phrases that should activate this skill:

- "fan out X, Y, Z in parallel"
- "brief out these issues"
- "generate work brief for #N"
- "prepare a cold session to do …"
- "ship the status-bar bundle and the scan-dialog fix in parallel"

Use this skill whenever:
- **Multiple independent items** the user wants briefed for parallel
  work, especially file-disjoint scope.
- **One task the user wants written up as a hardened brief** with
  pre-flight baked in (e.g., they'll paste it into a new session
  later or share it with another developer).

Skip the skill (prompt the new session directly) when:
- The task is trivial (one-line config edit) and a full brief is
  ceremony, OR
- The user already has the task spec in their head and wants to
  just open the session and start typing, OR
- One task's design depends on another's outcome — e.g. #68 (UI
  bucket split) depends on #165 (execute-dialog redesign). Brief out
  one; defer the other until the design lands.

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

### Step 1 — Understand intent and research independently

The user gives high-level intent, not pre-digested task details.
Typical inputs:

- `"fan out the status bar bundle and the scan dialog fix"`
- `"brief out #138+#140, #144, #136+#141"`
- `"just brief #144 — I want to do it next"`
- `"prepare a session to tackle the locked-state preview pane idea (#165)"`

**Your job is to research the tasks yourself, not ask the user to
summarize them.** Asking the user to paste in issue bodies is a code
smell — the issues are right there on the remote.

For each issue mentioned (or implied by bundle name):

1. `gh issue view <N>` — read the body. Extract the user-visible
   problem, the suggested fix (if any), the files/line numbers the
   author cited.
2. If the body cites file paths or line numbers, `Read`/`Grep` those
   locations to verify currency (the body may be stale relative to
   master).
3. If the user names a bundle without numbers ("status bar bundle"),
   skim `gh issue list --state open --limit 30` and pair the
   bundle name to issue numbers by topic. The recent conversation
   history of the project (if available) is the next-best source.
4. If two open issues plausibly match the user's description, ask
   ONE clarifying question. Otherwise don't outsource research.
5. **Whenever you write `#N` in a brief or in your own output,
   prefix it with the object kind: `[PR #N]` or `[issue #N]`.**
   Bare `#N` is ambiguous — a number in the user's intent might
   be either, and the cold session reading the brief cannot
   re-derive which. (Filed via
   [#292](https://github.com/jackal998/photo-manager/issues/292)
   after a brief named `#245` as a PR when it was the issue
   closed by PR #256.) The matching `gh` check that validates
   each label lives in Step 2.

You also need (these are usually trivial to infer):

- **Base ref** — usually `master`. Confirm up to date with
  `git fetch origin`.
- **Branch prefix** — `fix/`, `feat/`, `chore/`, `test/` based on
  task type. Each cold session picks its own slug.
- **PR pipeline scope** — default: each cold session pushes + opens
  its own PR end-to-end with the normal per-PR gated confirms. Only
  override if the user explicitly says "stop at branches, I'll
  push myself".

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

**Validate every `#N` reference before emitting briefs.** For each
number you've decided to put in a brief — sample sets ("run
`/pr-review` against PRs #A, #B, #C"), `Closes #N` trailers, "see
PR #N for prior art", "this depends on issue #N" — run the check
that matches the kind you've labelled it (Step 1 bullet 5):

```
# If you wrote [PR #N]:
gh pr view <N> --json number,state,title >/dev/null \
  || echo "MISMATCH: #N is not a PR — check gh issue view <N>"

# If you wrote [issue #N]:
gh issue view <N> --json number,title >/dev/null \
  || echo "MISMATCH: #N is not an issue — check gh pr view <N>"
```

On mismatch, do NOT emit the brief — either swap for the correct
object (e.g. the PR that closed the issue, or vice versa) or
surface to the user. Do not pass-through a labelled `#N` that
doesn't resolve to that kind. (See [#292](https://github.com/jackal998/photo-manager/issues/292)
for the recurrence the check defends against.)

### Step 3 — Generate one brief per task

Each brief is **fully self-contained**. The cold session has no
memory of this conversation; everything it needs must be in its
brief.

**Fence-delimiter rule when you emit briefs** — wrap each brief in
a tilde fence (`~~~~`), NOT a backtick fence. Briefs routinely
contain unindented triple-backtick code blocks (the cold session's
cleanup snippets, repro commands, etc.), and Claude Code's
markdown renderer treats a nested ``` as a premature close even
inside a quad-backtick outer fence — earlier briefs rendered
"invisible" past the first nested block. Tilde fences are a
different fence character entirely, so any inner ``` is
unambiguously content. (The template below is shown inside a
backtick fence for skill readability — but when you OUTPUT each
brief to the user, wrap with `~~~~`.) Template structure:

```
You are working on photo-manager issue(s) #<N> in a fresh Claude Code
desktop session. If the user opened this session with the
"create worktree" option enabled, you're in your own auto-worktree
under `.claude/worktrees/<your-session-name>/` — isolated from any
other sessions. If the option was off, you share the current
checkout — fine for sequential work, risky for true parallel work.

The orchestrator session that generated this brief is independent;
you have no visibility into it or into any sibling sessions, and
they have none into you.

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

  - Python — the venv lives at the **main repo root**, not inside the
    worktree. From inside a worktree (cwd is
    `.../<repo>/.claude/worktrees/<name>/`) the correct path is
    `../../../.venv/Scripts/python.exe`. From a normal checkout it's
    `.venv/Scripts/python.exe`. Don't burn turns trying both — pick
    based on `pwd`. (System Python lacks PySide6.)
  - Branch off `<base-ref>` at SHA `<base-sha>`.
  - No `--no-verify` under any circumstances.
  - No mock-driven test padding — every assertion must catch a real
    user-visible bug. (`CLAUDE.md` "Testing hard floor" applies.)
  - Bridge pattern: if you add a new method on a handler class
    surfaced via the main-window context menu, also add the proxy on
    `ActionHandlersImpl` (`feedback_action_handlers_bridge` in
    auto-memory).

## Workflow through PR

In every command below, **PY** is the venv python — from a worktree
that's `../../../.venv/Scripts/python.exe`, from a normal checkout
it's `.venv/Scripts/python.exe`. Resolve once at the start of your
session, then reuse.

  1. `git checkout -b <your-branch>` (your worktree starts on master).
  2. Implement the change.
  3. `PY -m pytest` — must pass.
  4. `PY scripts/check_coverage_per_file.py` — 70% per-file floor on
     every file touched.
  5. If you added a new layer-3 scenario, run it:
     `PY -m qa.scenarios._batch <sNN>_<name>`
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

## Cleanup — after the PR merges

The cold session that did the work (this one, or any future session
opened against the same worktree) is responsible for cleanup. The
orchestrator never does it. Workflow:

1. Confirm the merge landed — proves the work is preserved before
   you delete anything:
   ```
   git fetch --prune origin
   git log origin/master --grep="#<N>" --oneline | head -3
   ```
   If nothing matches, STOP — the PR may have been closed without
   merge, or you're looking at the wrong issue. Don't delete.

2. Check the remote branch — GitHub usually auto-deletes on merge:
   ```
   git ls-remote --heads origin <your-branch>
   ```
   Empty output means GitHub already cleaned it up. If a SHA prints,
   the remote branch survived — **gated:** surface intent, on
   approval `git push origin --delete <your-branch>`.

3. Delete the local branch. You can't delete the branch that's
   currently checked out in your worktree, so detach HEAD first:
   ```
   git checkout --detach HEAD
   git branch -D <your-branch>
   ```
   Use `-D` (force) because upstream is `gone` after merge (or after
   step 2), which makes `-d`'s upstream-merge check unresolvable.
   Work IS preserved on origin/master per step 1, so `-D` is safe —
   **gated:** surface intent briefly before running per CLAUDE.md.

4. Remove the worktree itself. This step has to run from the MAIN
   repo, not from inside the worktree — you can't remove your own
   cwd. Tell the user; they run from the main checkout:
   ```
   git worktree remove .claude/worktrees/<your-session-name>
   ```
   Or right-click the session in the desktop sidebar → Delete.
```

### Step 4 — Hand briefs to user

Output each brief in its own clearly-labeled code block. Tell the
user the desktop flow:

> Open Claude Code desktop → click **+ New session** in the sidebar →
> **Select folder** (the photo-manager repo) → **enable the "create
> worktree" option for parallel work** (leave off only if you intend
> to run the briefs sequentially in one checkout). Paste one brief in
> as the first message.
>
> Briefs are time-independent — paste them when and in whatever
> order you want. You don't have to open all N sessions at once;
> the briefs all branch off the same SHA so they're merge-order
> flexible.

### Step 5 — Done

Once briefs are handed off, the orchestrator's primary job is done.
Each cold session does its own pre-flight, work, and PR with the
normal gates intact.

You can optionally stay open as a coordination point — if a sibling
session asks (via the user) "how do I resolve a docs-guard block on
my branch?", answer it. But don't try to drive the cold sessions
from here; they own their work end-to-end.

## Anti-patterns — do not do these

- **Hardcoding `.venv/Scripts/python.exe` in brief commands.** Cold
  sessions opened with "create worktree" run from
  `.claude/worktrees/<name>/` where that path doesn't exist — the
  venv lives at the main repo root, so it's `../../../.venv/...`
  from there. Use a `PY` placeholder in the brief's workflow section
  and tell the cold session to resolve it once based on `pwd`. (This
  was a recurring time-sink across multiple sessions before the
  template was fixed.)
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
- **Wrapping output briefs in a backtick fence (` ``` ` or even
  `````` quad).** Briefs contain unindented ``` code blocks
  internally; Claude Code's renderer treats those as a premature
  close and everything past the first nested block renders blank.
  Always wrap each emitted brief in `~~~~` (tilde fence) — tilde and
  backtick are different fence chars, so inner ``` is unambiguously
  content. This was caught mid-session on 2026-05-15 (briefs for
  #230 and #212 rendered invisible).
- **Emitting bare `#N` references in a brief, or labelling one as
  `[PR #N]` / `[issue #N]` without running the matching `gh pr
  view` / `gh issue view` check first.** The two fixes are
  redundant on purpose — the prefix is for the human reader and the
  cold session, the validation is for the orchestrator. Filed as
  [#292](https://github.com/jackal998/photo-manager/issues/292)
  after a brief listed issue #245 as a PR in a "5 recent clean PRs"
  sample set; the executing session caught the mismatch in-band
  and substituted PR #255, but burned context doing it.

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

~~~~
<brief text — inner ``` blocks render fine inside this tilde fence>
~~~~

## Brief for Bundle B (paste into a new "+ New session")

~~~~
<brief text>
~~~~

## Brief for Bundle C (paste into a new "+ New session")

~~~~
<brief text>
~~~~

Done. Open Claude Code desktop → + New session × N → Select folder
(photo-manager) → paste one brief into each.
```

## Quick reference — minimal flow

1. User: high-level intent — "fan out X, Y, Z" / "brief #N" / "prep
   a session to do …".
2. Orchestrator researches each task (`gh issue view`, Read/Grep),
   runs pre-flight (steps 1–3 of this skill).
3. Orchestrator outputs N briefs + collision report.
4. User opens N "+ New session" windows (or fewer, or one — N is
   the user's call) — enabling the worktree option for parallel
   work — and pastes briefs in as first messages.
5. Each cold session does its own pre-flight, work, and PR.
