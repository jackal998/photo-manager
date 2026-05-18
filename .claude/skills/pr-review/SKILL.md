---
name: pr-review
description: Use after `git push` (or against an existing PR number) to catch semantic drift between code, docs/features.md, and qa/scenarios/sNN_*.py that the file-touch hooks (docs_guard, qa_scenario_guard) cannot see. Acts as a **manager** that dispatches to per-gate sub-skills based on what the diff touches. Reports findings in chat; defaults to posting them to the PR (pending draft for human-in-loop, submitted review for agent-driven flows) unless the user said "preview only" or there are no thread-worthy findings.
origin: local
---

# PR review — manager that composes lens skills

The file-touch gates (`scripts/hooks/docs_guard.py`,
`scripts/hooks/qa_scenario_guard.py`) check **presence**: did the PR
touch `docs/features.md` / a qa scenario when it touched a behaviour
file? They cannot check **content**:

- features.md entry was touched but with stale wording
- qa scenario exists but doesn't exercise the new branch
- new conditional dialog with no trigger-explaining docstring
- new f-string SQL or `pickle.load()` slipping into the diff
- mid-list migration insertion that would corrupt old manifests

This skill layers semantic-content checking on top, run manually
after `git push` (or against an open PR number) by Claude in the
same session that wrote the code.

**Architectural note.** This skill is a **manager**, not a monolith.
It owns gate dispatch (which gate's trigger fires on this diff?),
findings aggregation, and the output template. Each gate's *rubric*
lives in its own sub-skill (`docs-features-drift`,
`qa-scenario-drift`, `app-security-patterns`, `sqlite-migration-safety`,
`scanner-perf-patterns`, `test-padding-patterns`, `skill-pii-audit`)
plus the existing `/security-scan` (global, AgentShield) for
harness-config audit. The manager invokes those sub-skills via the
Skill tool when their conditions match.

## When to invoke

- After `git push -u origin <branch>` and BEFORE `gh pr create`, on
  the current branch. Default invocation.
- After opening a PR, to spot-check via `/pr-review <PR-number>`.
- During code review of someone else's PR — `/pr-review <PR-number>`
  pulls the diff via `gh pr diff <N>` and applies the same rubric.

Do NOT auto-invoke `/pr-review` itself — the user (or a calling
agent) decides when to run it. Once running, **the post-back to
the PR fires by default** in whichever mode the context picks
(see "Post-back to the PR" below). The user can opt out per
invocation by saying "preview only" / "dry run" / "don't post"
before or during the chat report.

## Invocation contract

```
/pr-review                   # current branch vs origin/master
/pr-review <PR-number>       # `gh pr diff <N>`
```

The skill reads:

1. **The diff.**
   - No args: `git diff origin/master...HEAD --stat` and
     `git diff origin/master...HEAD` (full hunks).
   - With PR number: `gh pr diff <N>` and `gh pr view <N> --json
     title,body,baseRefName,headRefName,headRefOid,url,state,files,additions,deletions,author,closingIssuesReferences`.
   - **Run these in parallel** when invoked with a PR number: `gh pr
     diff`, `gh pr view --json`, and (if Gate 0 fires) `gh issue view`
     calls have no dependencies on each other. Fire all of them in a
     single message with multiple tool calls and synthesise after
     all return — don't serialise.
2. **`docs/features.md`** at the working tree's HEAD — the canonical
   feature inventory used by `docs-features-drift/`.
3. **`qa/scenarios/sNN_*.py`** files named in features.md entries —
   used by `qa-scenario-drift/`.
4. **`README.md § Usage — GUI § Step 1-4`** ONLY if the diff touches
   the documented happy-path surfaces (scan dialog, save flow,
   execute flow). Skip otherwise — the inventory in features.md is
   the canonical answer.

Side effects: **none by default** beyond inline sub-skill
invocations. The skill produces a report in chat. When a gate's
condition fires (see Composition graph below), the corresponding
sub-skill is invoked via the Skill tool, its rubric applied, and
its findings folded into the report. The optional post-back to
the PR routes through `github-pr-review-pending/SKILL.md` (creates
a pending draft review only — human submits in the GitHub UI) and
is a separate, explicitly-gated step.

## Composition graph

For each gate, the trigger condition and the sub-skill (or inline
logic) that handles it:

| Gate | Trigger | Handler |
|---|---|---|
| **0** — task alignment | PR body has `Fixes #N` / `Closes #N` / `Resolves #N`, OR user supplied issue number | **inline** — direct `gh issue view <N> --json title,body,labels` |
| **1** — behaviour-bearing classifier | always runs (dispatcher) | **inline** — file-glob classification; output gates which lenses fire downstream |
| **2** — features.md drift | Gate 1 emitted any behaviour-bearing file | → `docs-features-drift/` (project skill) |
| **3** — qa scenario coverage | Gate 2 matched any features.md entry naming a `qa/scenarios/sNN_*.py` driver | → `qa-scenario-drift/` (project skill, chains with Gate 2's match list) |
| **4** — historical-drift caveat | invoked with a PR number AND that PR predates `docs/features.md` (introduced in PR #263) | **inline** — `git show <pr-head>:docs/features.md` probe + caveat handling |
| **5** — drive-by observations | always runs after Gates 0-4 | **inline** — catch-all bucket (limit 3) |
| **6** — harness security | diff touches `.claude/**`, `scripts/hooks/**`, `settings.json` / `settings.local.json` / `.mcp.json`, or `CLAUDE.md` permissions/install lines | → `/security-scan` (global skill, AgentShield) — auto-invoked, findings folded in |
| **7** — app-level security | diff has behaviour-bearing Python source files | → `app-security-patterns/` (project skill) → composes `security-review` (global lens) |
| **8** — SQLite migration safety | diff touches `_MIGRATIONS` list in `infrastructure/manifest_repository.py` OR `CREATE TABLE migration_manifest` in `scanner/manifest.py` | → `sqlite-migration-safety/` (project skill) |
| **9** — scanner / threading perf | diff touches `scanner/**.py`, `app/views/workers/**.py`, or adds a `QThread` / `QRunnable` / `ThreadPoolExecutor` | → `scanner-perf-patterns/` (project skill) → composes `photo-scanner-patterns` (global lens) |
| **10** — test padding patterns | diff adds or modifies `tests/test_*.py` or `tests/integration/test_*.py` | → `test-padding-patterns/` (project skill) → composes `python-testing` (global lens) |
| **11** — PII audit on project skills | diff adds or modifies files under `.claude/skills/<name>/` (NOT `.claude/skills/personal/`) | → `skill-pii-audit/` (project skill) |
| **Post-back, Mode A** (human-in-loop) | default in human session AND user didn't say "preview only" | → `github-pr-review-pending/` (project skill) → composes `conventional-comments` |
| **Post-back, Mode B** (agent-driven) | default in autonomous context (`/loop`, `/schedule`, `$CI`, agent-to-agent), OR user says "submit / publish / send to PR" | → `github-pr-review-submitted/` (project skill) → composes `conventional-comments` |
| **Reading back feedback** | dev agent resumes work on a PR that has reviews posted on it | → `github-pr-review-fetch/` (project skill) — inbound counterpart |

Composition depth = 2 at most. If a gate's condition does NOT
fire, skip its handler entirely — don't load the sub-skill, don't
emit its section in the output.

## How to apply the rubric

When the user runs `/pr-review` (with or without a PR number):

1. **Resolve the diff.**
   - No args: run `git diff origin/master...HEAD --stat` then
     `git diff origin/master...HEAD`.
   - With number: run `gh pr diff <N>` and
     `gh pr view <N> --json title,body,baseRefName,headRefName,headRefOid,url,state,files,additions,deletions,author,closingIssuesReferences`
     in parallel.

2. **Gate 0 — task alignment** (inline). If the PR body has
   `Fixes #N` / `Closes #N` / `Resolves #N`, OR the user supplied
   an issue number directly, run `gh issue view <N> --json
   title,body,labels` (in parallel with step 1's other fetches).
   Compare:
   - Issue title vs PR title.
   - Issue body's "what we want" / acceptance criteria vs the
     diff's visible behaviour changes.

   Emit one of:
   - ✓ aligned: PR addresses what the issue describes. No output
     unless something's noteworthy.
   - ⚠ scope: PR delivers more than the issue asks, or different.
     One-line: `⚠ scope: PR adds X; issue #N asks for Y`. Rule:
     **issue wins** — recommend aligning the PR or updating the
     issue.
   - ⚠ unclear-issue: issue body is empty / vague / contradicts
     itself. One-line `note:` and continue.
   - `note: no linked issue` if PR body has no issue reference
     and none was supplied. Don't block.

3. **Gate 1 — behaviour-bearing classifier** (inline). State
   explicitly: "behaviour-bearing: [list]. Out of scope: [list]."
   A PR is behaviour-bearing when it could change what a user
   sees, clicks, or what happens when they act. Concretely:

   - Touches `app/views/dialogs/**.py`, `app/views/handlers/**.py`,
     `app/views/workers/**.py`, `app/views/main_window.py`, or
     `app/views/window_state.py` with non-trivial diff (>10
     added+deleted lines OR a signature change OR a new
     conditional branch OR a new string literal that surfaces in
     the UI).
   - Touches `core/services/**.py` or `core/models.py` in a way
     that changes a return shape, raised exception, or
     side-effect signature that flows to a UI surface.
   - Adds or renames a `qa/scenarios/sNN_*.py` driver (signals a
     new user-visible flow worth recording in features.md).
   - Adds or removes a `settings.json` key visible to the user.
   - Adds, renames, or removes a translation key referenced from
     `app/views/`.

   A PR is **NOT behaviour-bearing** (→ CLEAN, stop) when it ONLY
   touches:

   - `docs/*.md`, `README.md`, `CONTRIBUTING.md`, `CLAUDE.md` —
     documentation only.
   - `scripts/hooks/*.py`, `.github/workflows/*.yml`,
     `pyproject.toml`, `.gitignore`, `Makefile` — tooling / CI /
     build.
   - `tests/test_*.py` or `tests/integration/test_*.py` only (no
     source files alongside) — test-only changes.
   - `translations/*.yml` only (no Python touched) — i18n
     catalogue refresh. Note: if the translation keys are also
     added/renamed in Python at the same time, that's
     behaviour-bearing.
   - `.claude/skills/**`, `.claude/agents/**`, `news/**` — meta /
     tooling files.

   If the diff is a mix, treat any behaviour-bearing file as a
   behaviour-bearing PR — but in the output's CLEAN summary
   explicitly identify the meta-only files as "not subject to
   features.md scope".

   If Gate 1 emits zero behaviour-bearing files AND the diff has
   no harness-config files AND no scanner / migration / test
   files, short-circuit to the CLEAN output. Don't load any
   sub-skills.

4. **Dispatch to sub-skills** for every gate whose condition
   fires on this diff. For each:
   - Invoke the corresponding skill via the Skill tool (see the
     Composition graph table above).
   - Apply that skill's rubric to the diff.
   - Emit findings in the corresponding section of the output
     template, prefixed with the gate's existing icons (`✗` / `⚠`
     / `ℹ️` / `note:`).
   - **Omit any gate's section if its sub-skill produced zero
     findings.**

   Order: 2 → 3 → 6 → 7 → 8 → 9 → 10 → 11. Gate 3 chains on Gate
   2's matched-entry list, so run it after Gate 2.

5. **Gate 4 — historical-drift caveat** (inline, conditional). If
   invoked with a PR number, run `git show <pr-head-sha>:docs/features.md`
   to detect pre-features.md PRs (the file was introduced in
   [PR #263](https://github.com/jackal998/photo-manager/pull/263),
   backfilled in [PR #267](https://github.com/jackal998/photo-manager/pull/267)).
   - If the file didn't exist at PR head AND the current
     features.md HAS an entry referencing this PR number → emit
     ℹ️ informational note: "This PR predates the features.md
     inventory; entry was added in a later backfill PR." **Do
     NOT count as ⚠ or ✗.**
   - If the file didn't exist at PR head AND the current
     features.md has NO entry referencing this PR number AND
     the diff is behaviour-bearing → emit ✗ "no features.md
     entry exists for the new behaviour introduced by this PR".

6. **Gate 5 — drive-by observations** (inline). Limit to 3
   incidental observations:
   - New conditional dialog/branch with no docstring explaining
     the trigger.
   - README.md § Usage — GUI § Step N text that contradicts the
     diff (cross-check only if Step-1-4 surface touched).
   - Settings-key changes not documented in `README.md §
     Configuration`.

7. **Emit the report** in the output template (below) and end
   with the Verdict line.

8. **Post-back to the PR.** Per "Post-back to the PR" below, pick
   Mode A (pending) or Mode B (submitted) from context and invoke
   the corresponding sub-skill. Skip the post-back when the user
   said "preview only" / "dry run" / "don't post", or when the
   verdict is CLEAN with no thread-worthy findings.

## Output template

```
PR review — <branch-name> (<commit-count> commits, <file-count> files touched)
Diff: origin/master...HEAD   |   Files in scope: <N behaviour-bearing> / <total>

## Task alignment (Gate 0)
✓ PR matches issue #N
⚠ scope: PR adds X; issue #N asks for Y — issue wins
note: no linked issue / unclear issue body

## docs/features.md coverage
✓ <feature-name>: <one-line summary of why it's covered>
⚠ <feature-name>: <one-line drift description> — see <file:line>
✗ <touched-file>: no features.md entry — appears user-visible
    suggested entry name: "<area> — <behaviour>"

## qa/scenarios/ coverage
✓ sNN: <one-line summary>
⚠ sNN: exists but doesn't exercise <new-branch> at <file:line>
⚠ no scenario: <touched-file> — consider extending sNN or adding new

## Other observations
- <observation 1>
- <observation 2>

## Harness security (Gate 6)
[/security-scan findings folded in]

## App-level security (Gate 7)
⚠ <file:line> — <pattern>: <evidence quote>

## SQLite migration safety (Gate 8)
⚠ <line> — <issue>: <evidence>

## Performance / threading (Gate 9)
⚠ <file:line> — <pattern>: <evidence>

## Test quality (Gate 10)
⚠ <file:line> — <anti-pattern>: <evidence>
note: <file:line> — generic regression-test name: <evidence>

## PII audit (Gate 11)
⚠ <file:line> — <category>: <evidence>
ℹ️ <file:line> — possible <category>: <evidence> — confirm placeholder vs real

## Verdict
<one-line summary: CLEAN / N⚠ / M✗ / N ⚠ + M ✗>
```

**Omit any section whose gate produced zero findings.** The
template lists every possible section for reference; the actual
report only includes sections with content.

For a clean PR:

```
PR review — <branch-name> (<commit-count> commits, <file-count> files touched)
Diff: origin/master...HEAD   |   Files in scope: 0 / <total> behaviour-bearing

## CLEAN
No behaviour-bearing changes — diff touches only <docs / tests / hooks / translations / etc>.
No features.md or qa scenario coverage to verify.
```

## Anti-patterns — what NOT to flag

Misuses that erode trust in the manager (a noisy manager gets
ignored):

- ✗ Don't flag a refactor. If the diff changes signatures internally
  but every user-visible string/condition is unchanged, it's a
  refactor. Features.md is about user-visible behaviour, not code
  shape.
- ✗ Don't flag missing features.md on a doc-only PR. Gate 1 catches
  this; if it gets past Gate 1, your gate is too loose.
- ✗ Don't flag missing features.md on a hooks/CI/build-only PR
  (e.g. `scripts/hooks/`, `.github/workflows/`, `pyproject.toml`,
  `Makefile`).
- ✗ Don't flag missing features.md on a test-only PR.
- ✗ Don't flag missing features.md on a translation-only PR.
- ✗ Don't flag entries you "would have written differently". Drift
  is about behaviour the entry **does not describe**, not about
  prose style.
- ✗ Don't open new findings on README.md unless the diff touches a
  Step-1-4 happy-path surface AND the README copy directly
  contradicts the diff.
- ✗ Don't flag pre-features.md PRs as ✗ when current features.md
  has an entry referencing the PR (Gate 4). Use ℹ️ informational
  instead.
- ✗ Don't load a sub-skill whose trigger condition didn't fire on
  this diff. Composition is conditional — the whole point of the
  manager architecture is to NOT pay the cost of loading every
  rubric on every PR.
- ✗ Don't recommend running any other skill in the report itself.
  Skill cross-references belong in the sub-skill's "See also"
  section, not in pr-review's output.

## Post-back to the PR (default on)

After the chat report, `/pr-review` **posts findings to the PR by
default**. The mode (pending draft vs submitted review) is picked
from context, not from an extra confirmation question:

- **Mode A (pending draft)** — when a human is in the loop and
  will click Submit themselves. Reversible (`DELETE` works), no
  notifications fire, visible only to the author's `gh` identity.
  Default for solo / human sessions.
- **Mode B (submitted review)** — when no human will click Submit.
  Goes live in one call, fires notifications, visible to anyone
  with PR read access. Default for scheduled / `/loop` / multi-agent
  contexts.

### Pick the mode

Use these signals in order; first match wins:

1. **User says "preview only"** / "don't post" / "show me first" /
   "dry run" → **skip both modes**. Emit only the chat report,
   stop. Don't ask "are you sure".
2. **User says "submit this review"** / "publish" / "send to PR" /
   "post as final" → **Mode B**.
3. **Calling context is autonomous** — `/loop`, `/schedule`,
   `$CI` env, agent-to-agent pipeline (e.g. invoked by a sibling
   agent who said something like "review and post") → **Mode B**.
4. **There's a `git` user attached to this session who matches
   the project owner** AND no autonomous signal in step 3 →
   **Mode A**.
5. **Otherwise** (genuinely ambiguous — first time running on a
   shared repo, mixed signals) → **Mode A**. Pending is reversible;
   submitted isn't. When in doubt, take the reversible action.

### Mode A — handoff to `github-pr-review-pending`

1. Prep: rewrite findings into `conventional-comments` shape per
   the dual-format mapping. Trim Gate 5 drive-by observations to
   the top three.
2. Invoke `github-pr-review-pending/SKILL.md` — it builds the
   JSON, POSTs to `/reviews` without `event`, and prints the
   "review is pending; click Submit in the UI" instruction. No
   per-POST confirmation gate inside that skill.
3. End.

### Mode B — handoff to `github-pr-review-submitted`

1. Same prep as Mode A (`conventional-comments` shape).
2. Compute the `event` value per the severity-to-event mapping in
   `github-pr-review-submitted/SKILL.md`:
   - Any ✗ finding → `event: "REQUEST_CHANGES"`
   - Only ⚠ / `note:` / `ℹ️` → `event: "COMMENT"`
   - All CLEAN → **do not post a review at all** (an agent must
     never auto-`APPROVE`). End the session, or
     `gh pr comment <N> --body "review agent: no findings, CLEAN"`
     if a status signal is needed for the dev agent to read.
3. Invoke `github-pr-review-submitted/SKILL.md` — it POSTs to
   `/reviews` with the chosen `event`, the review goes live in
   one call. No per-POST confirmation gate.
4. End. The dev agent (in a separate session) reads it back via
   `github-pr-review-fetch/SKILL.md`.

### When findings are sparse / non-thread-worthy

Skip the post-back entirely (both modes) when:

- The verdict is CLEAN (no findings) — there's nothing to thread.
  In Mode B context, optionally `gh pr comment` a one-liner.
- All findings are Gate 5 drive-by observations only — those go
  in the chat report but are too low-stakes to clutter the PR.
- The diff is doc-only / hooks-only / translation-only and Gate 1
  short-circuited to CLEAN at the start.

### Never

- **Auto-`APPROVE` from an agent.** Even if every gate passes
  clean. Approve is a trust signal that should come from a human.
  A `gh pr comment` works fine to record the CLEAN outcome.
- **Use `gh pr review --comment / --approve / --request-changes`** —
  those all submit immediately and bypass both mechanics above.
  The composition graph points at the `-pending` / `-submitted`
  skills for a reason: they pass `--input <file>` so multi-line
  thread bodies survive shell quoting.
- **Auto-merge after a clean review.** `gh pr merge` is not part
  of this skill or any of its sub-skills.

## Reading review feedback back into a session

When a dev agent resumes work on a PR after a review agent has
posted findings (Mode B above, or a human reviewer), the dev
agent needs to **read** the feedback into its session context.
That's the job of `github-pr-review-fetch/SKILL.md` — the
inbound counterpart to the two outbound posting skills.

Typical agent-driven loop:

```
Agent A (dev)   →   /pr-review (Mode B post-back)   →   PR has submitted review
                                                          │
Agent B (dev again, new session)   ←   github-pr-review-fetch   ←   reads reviews
   │
   addresses findings, pushes new commits
   │
   loop until verdict is CLEAN
```

Invoke `github-pr-review-fetch <PR-number>` (or no arg to use the
current branch's PR) at the start of a fix-and-iterate session.
The skill emits a structured chat report of all submitted
reviews, all line-anchored threads, and all issue-style PR
comments — ready for the agent to walk through as a to-do list.

## Why this exists

The file-touch gates (`docs_guard`, `qa_scenario_guard`) catch
*absence* — they fire when a behaviour file changed and no doc /
scenario was touched at all. They cannot catch:

- A features.md entry that was touched but with stale text.
- A qa scenario that exists for a file but doesn't drive the
  newly-added branch.
- A new conditional dialog added with no features.md section.
- An f-string SQL injection slipping in.
- A migration inserted mid-list.

This manager is the semantic-content layer. It runs as an LLM
prompt in the same Claude Code session that wrote the code — no
external API, no GitHub Action, no extra infrastructure — and
dispatches to specialised sub-skills so each gate's rubric stays
small, focused, and independently editable.
