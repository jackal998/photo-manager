# Claude Code skills & one-time setup — full detail

> Read when adding or editing a Claude Code skill for this repo, or when
> bootstrapping a fresh checkout (settings.json). Core rule: project skills are
> tracked + PII-clean; anything with machine-specific paths / NAS IPs /
> credentials goes in `personal/` or `~/.claude/skills/`. PII-audit before
> committing any project skill.

## Claude Code skills

Skills live in two homes, split by trust level:

- **Project skills** — `.claude/skills/<name>/` — tracked in git,
  shared across all contributors. Generic to the codebase: workflow,
  conventions, test scaffolding, QA drivers. Today this includes
  `agentic-engineering/`, `app-security-patterns/`, `conventional-comments/`,
  `docs-features-drift/`, `github-issue-create/`,
  `github-pr-create/`,
  `github-pr-review-fetch/`, `github-pr-review-pending/`,
  `github-pr-review-submitted/`, `impact-map/`,
  `parallel-brief-generator/`, `pr-review/`,
  `qa-explore/`, `qa-scenario-drift/`, `scanner-perf-patterns/`,
  `skill-pii-audit/`, `sqlite-migration-safety/`,
  `test-padding-patterns/`, `update-docs/`, `work/`. New project skills
  land here.

  `/pr-review` runs the semantic-content review the file-touch
  gates (`docs_guard.py`, `qa_scenario_guard.py`) cannot do — it
  reads the branch diff and compares it against `docs/features.md`
  entries and `qa/scenarios/sNN_*.py` drivers, reporting drift in
  chat. **Acts as a manager** that dispatches to per-gate
  sub-skills (`docs-features-drift`, `qa-scenario-drift`,
  `app-security-patterns`, `sqlite-migration-safety`,
  `scanner-perf-patterns`, `test-padding-patterns`,
  `skill-pii-audit`) plus the global `/security-scan` (harness
  audit) — see the Composition graph in `pr-review/SKILL.md`.
  Each sub-skill owns one gate's rubric and is invoked only when
  the diff matches its trigger condition. Invoke manually after
  `git push` and before `gh pr create`; pass an optional PR
  number to spot-check an existing PR. The skill never posts to
  GitHub without an explicit follow-up confirmation.

  `conventional-comments/` defines the uniform label + decoration
  + subject shape (`**suggestion (non-blocking):** …`) and the
  **dual-format rule**: `/pr-review`'s chat output uses the
  scan-fast icons (`✗` / `⚠` / `ℹ️`); the full label format kicks
  in only when findings get posted as PR thread bodies via
  `github-pr-review-pending/`. The icon → label mapping in
  `conventional-comments/SKILL.md` is what bridges the two
  formats.

  `github-pr-review-pending/` is the optional post-back mechanic
  invoked from `/pr-review` in **human-in-loop mode** — it creates
  a **pending (draft)** GitHub review via `gh api` (no `event`
  key, so nothing is submitted) and stops, leaving the human to
  click "Submit review" in the GitHub UI.

  `github-pr-review-submitted/` is the sibling mechanic for
  **agent-driven mode** — when the review is being posted by an
  agent (scheduled, peer agent in a multi-agent pipeline) with no
  human to click Submit. It POSTs with `event` set to `COMMENT`
  (or `REQUEST_CHANGES` if findings are blocking) so the review
  goes live in one call. Agents never use `APPROVE` — that's a
  human-only trust signal.

  `github-pr-review-fetch/` is the **inbound** counterpart to the
  two outbound siblings. When a dev agent resumes work on a PR
  after a separate review agent (or human reviewer) posted
  findings, this skill fetches all submitted reviews, line-anchored
  threads, and issue-style PR comments via `gh api` + GraphQL,
  then emits a structured chat report ready for the dev agent to
  walk through as a to-do list. Inbound + outbound + manager
  together form the agent-to-agent review loop:
  dev → push → review agent (`/pr-review` + `-submitted`) → PR has feedback →
  dev agent (`-fetch` to ingest) → fix + push → loop.

  `github-issue-create/` standardises new GitHub *issue* filing —
  team-prefixed title (`[QA]` / `[FE]` / `[BE]` / `[CI]` / `[DX]`
  / `[DOCS]`), mandatory `## What` / `## Why` / `## How` body
  sections, label allocation from the existing repo set, and an
  explicit gate per issue. Sibling to the three `github-pr-review-*`
  skills but distinct surface: those handle PR *reviews*; this one
  handles issue *creation*. Invoked from `/work`'s "out of scope —
  file as follow-up" path and from `/pr-review`'s Gate 5 drive-by
  promotions; also fires on direct trigger phrases like "file an
  issue for X" / "track this for later". Closes the "deferred
  work must always be filed" gap captured by the
  [Capture full design space](#) memory rule.

  `github-pr-create/` is the single source of truth for **opening a
  PR that goes green**. It owns the whole lifecycle — pre-flight
  (branch guard, the docs / qa / news token decisions), `gh pr
  create`, the post-create tail that is easy to forget (the
  `news/<PR>.<type>` fragment keyed by the new PR number, the
  `gh pr checks --watch` with a 20-min timeout, one auto-iteration on
  red), and the "ready for your merge" handoff. It **orchestrates and
  supplies inputs**; it does not re-implement the enforcement that
  lives in the `pr-gates` / `news-gate` CI workflows or the
  `docs_guard` / `qa_scenario_guard` PreToolUse hooks. `/work` Phase 5
  delegates wholesale to it rather than inlining the steps — the
  inline scatter is what kept dropping the news fragment. Fires on
  trigger phrases like "open a PR" / "create a pull request" / "ship
  this", and is the delegated PR step inside `/work`.
- **Personal skills** — `.claude/skills/personal/<name>/` (gitignored)
  or `~/.claude/skills/<name>/` (user-level, never in any repo). For
  ad-hoc skills with machine-specific paths, Synology IPs, NAS
  hostnames, credentials, or anything else you wouldn't paste into a
  PR. Use the `personal/` subdirectory when the skill is repo-scoped
  but private; use `~/.claude/skills/` when the skill applies across
  every project.

**PII audit before committing a project skill** — run this on the
SKILL.md and any sibling files; expected to be zero matches:

```
grep -i -E "C:\\\\Users|/Users/|/home/|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+|password|secret|token|key=" <file>
```

The patterns cover: Windows / macOS / Linux home paths, IPv4
addresses (Synology / NAS), and credential-like strings. False
positives (e.g. `key=value` in a log-format example) are fine to wave
through — surface them in chat before committing, don't silently
include them. If any match is real (an actual path or IP), move the
skill into `personal/` or `~/.claude/skills/` instead of committing
it.

## Setup (one-time, per machine)

`.claude/settings.json` is gitignored because it contains a machine-specific
home path. To enable the security gates above on a fresh checkout:

1. Copy `.claude/settings.json.example` to `.claude/settings.json`
2. Replace `<USER_HOME>` with your actual home directory
   (e.g. `C:/Users/J` on Windows, `/home/you` on Linux, `/Users/you` on macOS)
3. Restart your Claude Code session, then run `/permissions` to confirm the
   `ask` rules are loaded

When `.claude/settings.json.example` changes (new `ask` / `deny`
entries, new hooks), your local `.claude/settings.json` does NOT
auto-update — it's gitignored. Diff the example against your local
copy after pulling and port over any new entries by hand. Watch for
PRs that touch the example file (e.g. #288, #291).
