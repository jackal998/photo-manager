# Brief template — emitted verbatim to cold sessions

This is the canonical template the orchestrator emits per task in
Step 3 of [SKILL.md](SKILL.md). Substitute every `<placeholder>`
with task-specific content, then output the whole thing wrapped in
a **tilde fence** (`~~~~`) — not a backtick fence — because the
template contains inner ``` code blocks that would prematurely
close a backtick fence (see SKILL.md's "Fence-delimiter rule").

The photo-manager-specific bits the orchestrator must bake into
this template per task — three-gate breakdown, behavioural-modify
trigger, scanner-side gotchas — live in
[pm-reminders.md](pm-reminders.md). Cross-reference there when
filling placeholders.

---

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
     `gh pr create --title "<title>" --body "<body>"`. Three gates
     can fail this PR — know which one your scope tripwires:
       * `qa_scenario_guard` (PreToolUse hook) — fires on diffs
         touching `app/views/{handlers,dialogs,components,workers}/`
         without a `qa/scenarios/sNN_*.py` change.
         Bypass: `[qa-not-needed: <reason>]` in title or body.
       * `docs_guard` (PreToolUse hook) — **two triggers**:
         - **New files** under `app/views/{dialogs,handlers,workers,components,widgets,layout,viewmodels}/`,
           `infrastructure/`, `scanner/`, `core/{models,services/}`,
           `tests/test_*.py`, or `qa/scenarios/s*.py` → satisfied
           by ANY edit to `README.md` / `docs/*.md` / `CLAUDE.md`
           / `pyproject.toml`.
         - **Modified files** under `app/views/{dialogs,handlers}/`
           crossing the behavioural threshold (≥10 added+deleted
           lines OR any `def` signature change) → **specifically
           require `docs/features.md`**. Other doc touches do NOT
           satisfy this stricter trigger.
         Bypass for either: `[docs-not-needed: <reason>]` in title
         or body.
       * `news-gate` (CI workflow, not a local hook — fires on
         every PR after creation) — requires
         `news/<PR#>.{feature,bugfix,doc,removal,misc}` to exist
         in the diff. PR# isn't known until `gh pr create` returns,
         so this step has to follow PR creation — see step 8b.
         Bypass: `[skip-news: <reason>]` in title or body.
     If a PreToolUse gate blocks the gh command, decide with user:
       a) hand-fix and retry
       b) include the relevant bypass token in `--title` or `--body`
       c) skip this branch
  8b. **Add the news fragment for this PR.** After `gh pr create`
      returns the PR number N:
        - Pick the type: `feature` for user-visible new behaviour;
          `bugfix` for user-hit bugs; `doc` for doc-only; `removal`
          for removed feature / breaking change; `misc` for
          refactor / CI / tooling / no user diff. (Test-only
          refactors with no user impact → `misc`. Pure-rename
          refactors → `misc`. Behaviour-preserving helper
          extraction → `misc`.)
        - Write `news/<N>.<type>` with ONE LINE, present-tense
          imperative, ending in `(#<N>)`. Examples in `news/`:
          ```
          Add `/pr-review` skill for catching semantic drift … (#272).
          Extend `/pr-review` with Gates 7-10 … (#288).
          ```
        - `git add news/<N>.<type> && git commit -m "news(#<N>): ..."`
        - **Gated:** surface push intent to user, on approval
          `git push`.
      If the change genuinely has no user-recordable diff and no
      fragment makes sense, you can amend the PR title or body to
      include `[skip-news: <reason>]` instead. Don't ship a PR
      that fails news-gate in CI; fix it before reporting done.
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
