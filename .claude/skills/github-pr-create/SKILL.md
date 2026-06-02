---
name: github-pr-create
description: Single source of truth for opening a GitHub PR that goes green — owns the full lifecycle so nothing is missed regardless of who opens the PR (manual, /work, a cold session). Pre-flight (branch guard, features.md / qa drift token decisions), `gh pr create`, the post-create tail that is easy to forget (news fragment, CI watch, one auto-iteration on red), and the merge-ready handoff. Use whenever you are about to open a PR — trigger phrases "open a PR", "create a pull request", "push and open a PR", "ship this", "raise a PR for X" — and as the delegated PR step inside `/work` Phase 4→5. Composes with `/pr-review` (semantic review, runs before create), the `pr-gates` / `news-gate` CI workflows, and the `docs_guard` / `qa_scenario_guard` PreToolUse hooks (it supplies the inputs those gates check; it does not re-implement them). Sibling of `github-issue-create` and `github-pr-review-{pending,submitted,fetch}`.
---

# github-pr-create — one shape for every PR you open

Opening a PR in this repo has more moving parts than `gh pr create`
suggests: three independent CI gates (`pr-gates`, `news-gate`, plus
`tests` / `qa-batch`), two of which need an **input the human/agent
must supply at create time** (a bypass token in the body, a
`news/<PR>.<type>` file keyed by the not-yet-existing PR number). That
responsibility used to live scattered across `/work` Phase 5, the
PreToolUse hooks, the CI workflows, and — when a PR was opened any
other way — improvised follow-up commits. The scatter is why things
got missed (the recurring `require-news-fragment` red is the canonical
symptom).

This skill is the **one place** that knows the whole lifecycle. Every
PR-opening context routes through it so the checklist can't be
half-remembered. It **orchestrates and supplies inputs**; it does not
duplicate the enforcement that already lives in hooks and CI — those
stay the source of truth for *whether* a gate passes; this skill is the
source of truth for *doing the steps that make them pass*.

## The gate map — what every PR must satisfy, and who enforces it

| Requirement | Enforced by | What this skill does |
|---|---|---|
| On a feature branch, not `master` | `branch-guard` hook (commit-time) | Pre-flight check before any commit/create |
| `docs/features.md` updated when user-visible behaviour changed | `docs_guard.py` — PreToolUse hook **and** `pr-gates.yml` `gates` job | Decide: update features.md (via `/update-docs`) **or** put `[docs-not-needed: <reason>]` in the PR body |
| `qa/scenarios/sNN_*.py` added/extended for user-facing flows | `qa_scenario_guard.py` — PreToolUse hook **and** `pr-gates.yml` `gates` job | Decide: add a driver **or** put `[qa-not-needed: <reason>]` in the PR body |
| `news/<PR>.<type>` changelog fragment | `news-gate.yml` `require-news-fragment` (server-only — no client hook, because the filename needs the PR number) | Write it **after** create, or put `[skip-news: <reason>]` in the body |
| Unit + coverage (70% file / 80% global) | `tests.yml` `pytest` | Run locally before push (`/work` Phase 4 already does) |
| qa scenario batch | `qa-batch.yml` `qa (1..5)` | Server-side; watch in the tail |
| Semantic drift (code ↔ features.md ↔ qa) | `/pr-review` (advisory, in-session) | Run before create when the diff qualifies |

The asymmetry that bites: `docs_guard` / `qa_scenario_guard` run **both**
client-side (block `gh pr create` locally) and server-side, so they're
hard to forget. `news-gate` is **server-only** — it can't be a
PreToolUse blocker because the fragment is keyed by the PR number, which
doesn't exist until after create. That post-create step is the one this
skill exists to never drop.

## Workflow

### Step 0 — Pre-flight (before create)

1. `git branch --show-current` — confirm a feature branch, not
   `master`. (Branch confusion has landed commits on the wrong branch
   before — confirm, don't assume.)
2. Confirm the branch is pushed (`git push -u origin <branch>` if it's
   the first push — this is a `git push`, surface it per CLAUDE.md if
   your harness prompts).
3. If the diff has user-visible behaviour or user-facing files, run
   `/pr-review` first and resolve its findings — it's cheaper to fix
   drift before the PR than after.

### Step 1 — Decide the three token questions

For each of the three gateable axes, decide **content-or-token** and
record the answer — these become inputs to Steps 2 and 4:

- **docs:** did user-visible behaviour change? → update `features.md`
  (use `/update-docs`), else `[docs-not-needed: <reason>]`.
- **qa:** did a user-facing flow change? → add/extend a `sNN` driver,
  else `[qa-not-needed: <reason>]`.
- **news:** is there a changelog-worthy line? → plan the fragment
  type (Step 4 table), else `[skip-news: <reason>]`.

Tokens must be **honest** — they're visible in review and in CI logs.
Use `[qa-not-needed]` when a layer-3 driver would be padding (e.g.
asserting a value only a flaky UIA read can observe), not to dodge real
coverage — the project's no-test-padding rule (CLAUDE.md "Testing
ground rules") applies to the token decision too.

### Step 2 — Compose title + body

- **Title:** Conventional Commits shape — `type(scope): description`,
  imperative, ≤72 chars, describe WHY not WHAT.
- **Body:** `## What` / `## Why` / `## How`, plus any bypass tokens from
  Step 1 placed on their own line so the CI grep finds them. Tokens may
  go in the title or body; body is cleaner.

### Step 3 — Create the PR

Use `--body-file -` from stdin (avoids `--body` shell-quoting fragility
on `##` / backticks / nested markdown — same reason as
`github-issue-create`):

```bash
gh pr create --base master --head <branch> \
  --title "type(scope): …" \
  --body-file - <<'EOF'
## What
…

## Why
…

## How
…

[qa-not-needed: <reason if applicable>]
EOF
```

`gh pr create` stdout is the PR URL. Parse the trailing integer:
`https://github.com/<owner>/<repo>/pull/<N>` → `N`. Store as `$PR`.

> **Stacked PRs:** if the branch targets another feature branch, pass
> `--base <that-branch>`. `pr-gates` diffs against the PR's actual base,
> so don't leave `--base master` on a stacked PR or earlier-stage files
> surface as "changed".

### Step 4 — News fragment (the easy-to-forget step)

If Step 1 chose `[skip-news:]`, this step is a no-op — skip to Step 5.

Otherwise write `news/<PR>.<type>`, one line, present-tense imperative,
ending `(#<PR-or-issue>)`. Map the head commit's Conventional-Commits
type to the fragment suffix (canonical table in
[`news/README.md`](../../../news/README.md)):

| cc type | fragment suffix |
|---|---|
| `feat` | `.feature` |
| `fix` | `.bugfix` |
| `docs` | `.doc` |
| `chore` `refactor` `test` `perf` `ci` `build` | `.misc` |
| `revert` | `.misc` |
| removed feature / breaking schema (judge by content, not prefix) | `.removal` |

```bash
git add news/<PR>.<type>
git commit -m "docs(news): add fragment for #<PR>"
```

Then `git push` (surface the push gate if your harness prompts —
single-line additive text file, no secrets, safe). The push's
`synchronize` event re-runs `news-gate` green. **If Step 1 also chose a
bypass token, edit the PR body BEFORE this push** — the gates read the
live PR body on the `synchronize` event.

### Step 5 — Watch CI to a verdict

```bash
gh pr checks <PR> --watch --interval 30
```

Wrap with `timeout: 1200000` (20 min). The `--watch` listener can stall
silently; on timeout (or a manual "stop"), re-fetch one-shot with
`gh pr checks <PR>` and surface current state instead of looping.

- **Green** → Step 6.
- **Red** → enumerate failures (`gh pr checks <PR>`), classify (lint /
  unit / `require-news-fragment` / `gates` / qa), fetch the failing log
  (`gh run view <run-id> --log-failed`), apply **one** auto-iteration
  fix, commit, push, and loop back to Step 5 **once**. If the second
  watch is red again, STOP and surface — don't keep guessing. One CI
  iteration is one hypothesis; after the first auto-fix fails, a human
  read beats a second blind guess.

### Step 6 — Hand off, don't merge

On green, emit one line:

```
PR #<N> CI green — ready for your merge. → <url>
```

Never run `gh pr merge` — the user merges every PR themselves in the
GitHub UI. After surfacing, do not autopoll the merge — schedule at
most one long wakeup if a follow-up depends on the merge.

## Composition

- **`/work`** — Phase 4 ends at "ready to open the PR"; it delegates the
  entire create-and-drive-green tail to this skill instead of inlining
  Steps 3–6. `/work` keeps Phases 1–4 (research / dev / qa / merge /
  `/pr-review`).
- **`/pr-review`** — runs in Step 0/pre-flight, before create. It judges
  semantic drift; this skill consumes that judgment when deciding the
  `[docs-not-needed]` / `[qa-not-needed]` tokens.
- **`/update-docs`** — invoked from Step 1 when behaviour changed and
  `features.md` needs the edit (the alternative to `[docs-not-needed]`).
- **`github-issue-create`** — when a drive-by finding surfaces while
  opening the PR, file it through that skill rather than bloating this
  PR — deferred work always gets filed, never silently dropped.
- **CI workflows** (`pr-gates`, `news-gate`, `tests`, `qa-batch`) and
  **PreToolUse hooks** (`docs_guard`, `qa_scenario_guard`) are the
  enforcers; this skill supplies their inputs and watches their result.
  Do not re-implement their checks here.

## When NOT to use

- You are not actually opening or driving a PR (just committing locally,
  or pushing to a branch with an existing open PR — for that, the tail
  is the same but Step 3 is skipped; jump to Step 4/5).
- The PR already exists and you're addressing review feedback — use
  `github-pr-review-fetch` to ingest, then push fixes and re-enter at
  Step 5.

## Anti-patterns — do not do these

- **`gh pr create` then walking away.** The PR isn't done until CI is
  green (Step 5/6). A bare create leaves `require-news-fragment` red and
  the PR un-mergeable — the exact gap this skill closes.
- **Guessing the news type from the commit prefix when content
  disagrees.** A `fix(scanner):` that removes a user-facing feature is
  `.removal`, not `.bugfix`. Judge by what the change does to users.
- **Dishonest bypass tokens.** `[qa-not-needed: covered]` with no actual
  coverage is worse than a red gate — it launders a gap past review.
- **Editing the PR body for a token AFTER the synchronize push.** The
  gate already read the old body; the token won't be seen until the next
  event. Order: body first, then push.
- **Looping the CI watch past one auto-iteration.** Two reds in a row →
  surface, don't keep guessing.
- **Merging.** Not yours to do — hand off at Step 6.

## Minimal flow

1. Pre-flight: branch ≠ master, pushed, `/pr-review` if it qualifies.
2. Decide docs / qa / news → content or honest token.
3. Compose CC title + What/Why/How body (+ tokens).
4. `gh pr create --body-file -` → parse `$PR`.
5. Write `news/<PR>.<type>` (or skip-news) → commit → push.
6. `gh pr checks <PR> --watch` → green: hand off; red: one fix, one
   re-watch, else surface.
7. "PR #<N> CI green — ready for your merge."
