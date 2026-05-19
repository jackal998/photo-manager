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

## Skill layout — manager + on-demand resources

This SKILL.md is the **manager** (Anthropic's documented pattern
per [code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills)).
It coordinates the workflow but does not carry the bulky content
that goes INTO each emitted brief. Two sibling resource files
hold that content; the orchestrator `Read`s them only at the step
that needs them:

- **[brief-template.md](brief-template.md)** — the canonical
  template the orchestrator emits per task (pre-flight,
  task-spec, conventions, PR workflow with all three gates,
  cleanup). Loaded in Step 3 below; substitute placeholders, wrap
  in `~~~~`, emit.
- **[pm-reminders.md](pm-reminders.md)** — photo-manager-specific
  content to bake into each brief: the three-gate breakdown
  (qa_scenario_guard, docs_guard's two triggers, news-gate),
  the behavioural-modify pre-staging rule, the pattern-PR
  cross-check, scanner-side gotchas. Loaded in Step 3.

Anti-patterns and orchestrator-side guidance stay here in SKILL.md.

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

**Load the resource files now**:

1. `Read` [brief-template.md](brief-template.md) — gets the
   canonical template into context with all the placeholders
   (`<base-sha-noted-by-orchestrator>`, `<your-branch>`, etc.) and
   the full PR-workflow step list including the three gates and
   step 8b (news fragment).
2. `Read` [pm-reminders.md](pm-reminders.md) — gets the
   photo-manager-specific guidance: which gates each task's scope
   will trigger, the behavioural-modify pre-staging rule, the
   pattern-PR cross-check, scanner-side gotchas.

For each task:

- Substitute every `<placeholder>` in the template with the
  task-specific content from Steps 1–2 (issue summary, files in
  scope / NOT in scope, slot, branch slug, acceptance).
- Apply the pm-reminders relevant to the task's scope:
  - **Universal**: name the three gates the cold session may hit
    (already in the template's step 8).
  - **If MODIFIED `app/views/{dialogs,handlers}/*.py` is
    in-scope**: apply the behavioural-modify pre-staging rule —
    name the trigger explicitly in the Acceptance section AND
    pre-write either the features.md action or
    `[docs-not-needed: <reason>]` into the `<body>` placeholder.
  - **If citing a prior-art PR**: run the pattern-PR cross-check
    (PR-X's files in the same gated subdir? if not, don't claim
    its pattern transfers wholesale).
  - **If scope touches `scanner/`**: bake in the scanner-side
    gotchas (`read_result_rows` y_min=600, trailing periods,
    Live Photo clusters).
  - **If scope adds qa scenarios**: name s13 / s36 as destructive
    if extending recycle-bin coverage.

**Fence-delimiter rule when you emit briefs** — wrap each
brief in a tilde fence (`~~~~`), NOT a backtick fence. Briefs
routinely contain unindented triple-backtick code blocks (the cold
session's cleanup snippets, repro commands, etc.), and Claude
Code's markdown renderer treats a nested ``` as a premature close
even inside a quad-backtick outer fence — earlier briefs rendered
"invisible" past the first nested block. Tilde fences are a
different fence character entirely, so any inner ``` is
unambiguously content.

### Step 4 — Hand briefs to user

Output each brief in its own clearly-labeled section. Tell the
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
- **Omitting the news-fragment step from the workflow.** The
  `news-gate` CI workflow runs on every PR and fails when no
  `news/<PR#>.<type>` is added. It is a CI workflow, not a
  PreToolUse hook — so it doesn't surface in the gh pr create
  output and a brief that lists only the local hooks
  (`qa_scenario_guard`, `docs_guard`) misses it. Cold session pays
  for the omission with a failed CI run + amendment commit. Filed
  via [#311](https://github.com/jackal998/photo-manager/issues/311).
  Always include step 8b (post-PR news fragment) in the brief
  template — the canonical template in brief-template.md has it.
- **Treating `docs_guard` as a single trigger.** The hook has
  two: a coarse new-file trigger (any doc touch satisfies) and a
  strict behavioural-modify trigger on `app/views/{dialogs,handlers}/`
  MODIFIED files (only `docs/features.md` satisfies). A brief that
  says "docs_guard fires on the new tests — docs/testing.md update
  satisfies it" for a task editing handler files is wrong on both
  counts: docs_guard fires on the SOURCE file location not the
  test, and docs/testing.md doesn't satisfy the strict trigger.
  Filed via [#312](https://github.com/jackal998/photo-manager/issues/312).
  See pm-reminders.md for the precise predicates and the pre-staging
  rule.
- **Inlining brief-template.md or pm-reminders.md content into
  SKILL.md.** This SKILL.md is intentionally thin (the manager
  layer). Bulk content lives in the sibling resource files and is
  loaded on-demand. Inlining defeats the on-demand load Anthropic's
  skill docs argue for and brings the recurring per-session
  context cost back. If you find yourself wanting to add 50 lines
  of brief content here, you almost certainly want to add them to
  one of the resource files instead.

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
   runs pre-flight (Steps 1–2 of this skill).
3. Orchestrator `Read`s [brief-template.md](brief-template.md) and
   [pm-reminders.md](pm-reminders.md), composes N briefs by
   substituting placeholders + applying the scope-relevant
   reminders, emits each wrapped in `~~~~`.
4. User opens N "+ New session" windows (or fewer, or one — N is
   the user's call) — enabling the worktree option for parallel
   work — and pastes briefs in as first messages.
5. Each cold session does its own pre-flight, work, and PR.
