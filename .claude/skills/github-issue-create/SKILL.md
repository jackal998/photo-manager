---
name: github-issue-create
description: Standardise new GitHub issue filing — team-prefixed title (`[QA]` / `[FE]` / `[BE]` / `[CI]` / `[DX]` / `[DOCS]`), mandatory **What / Why / How** body sections, label allocation from the existing repo set, and explicit gate before posting. Use whenever an in-session finding is decided to live in the tracker rather than be acted on now: `/work` "out of scope — file as follow-up issue" path, `/pr-review` drive-by observations promoted to issues, deferred audit items, paper-trail observations, layer-3 qa follow-ups, debt the user said "track this for later" on. Sibling to `github-pr-review-{pending,submitted,fetch}` (which only handle PR reviews, not issues).
---

# github-issue-create — one shape for every new issue

The repo's recent issue stream has three competing title styles
(`[BUG] xxx` / `qa: xxx` / `[QA] xxx`) and inconsistent body
structure — some issues open with a Symptom block, some with a
bullet list, some with a paragraph. This skill replaces the
improvisation with one shape so a future reader (human or agent)
can skim the tracker without re-deriving "what kind of thing is
this issue" every time.

## Title format — `[TEAM]` or `[TEAM:scope]` + descriptive title

The bracket prefix names the **team / area** that owns the work,
not the **type** of work (type is what labels are for).

```
[TEAM] one-line descriptive title in sentence case, no trailing period
[TEAM:scope] one-line descriptive title …
```

### Team prefixes (this repo)

| Prefix | Scope | Typical paths |
|---|---|---|
| `[QA]` | qa scenarios, probes, qa-batch CI flakes | `qa/scenarios/`, `qa/probes/`, qa-batch workflows |
| `[FE]` | Qt UI, dialogs, handlers, workers, translations | `app/views/`, `translations/` |
| `[BE]` | scanner, core models, infrastructure, manifest, scoring | `scanner/`, `core/`, `infrastructure/` |
| `[CI]` | GitHub Actions workflows, pre-commit hooks, news-gate | `.github/workflows/`, `scripts/hooks/`, `news/` |
| `[DX]` | Claude harness, agents, skills, MCPs, settings | `.claude/`, `CLAUDE.md`, MCP configs |
| `[DOCS]` | features.md, README, audits, news fragments (content) | `docs/`, `README.md`, `news/` (when about content not gate) |

### Optional sub-scope

When the team prefix is too broad, add a sub-scope after a colon —
helps with skim-find in the issue list:

- `[QA:s31] …`
- `[FE:action-dialog] …`
- `[BE:scanner] …`
- `[CI:qa-batch] …`
- `[DX:skill:work] …`
- `[DOCS:features] …`

### Multi-team issues

A bug that genuinely spans two teams: pick the team where the
**fix** is most likely to live, then add the other team's keyword
in the title body or a `Related teams:` line at the end of **How**.
Don't double-bracket (`[FE][BE]`) — too noisy in the issue list.

### Title — examples

Recent issues re-titled to this convention:

| Old | New |
|---|---|
| `[BUG] ActionDialog (Set Action by Regex): functional bugs — data loss, lying preview, broken example` | `[FE:action-dialog] functional bugs — data loss, lying preview, broken example` |
| `[CHORE] Status emission on skip-locked branch in ExecuteActionDialog._handle_execute_request_with_lock_check` | `[FE:execute-dialog] status emission missing on skip-locked branch` |
| `ci(qa-batch): qa (2) shard fails on docs-only PR #368 — likely runner flake` | `[CI:qa-batch] qa (2) shard fails on docs-only PR #368 — likely runner flake` |
| `qa: extend s31 with Wave 10 layer-3 coverage (D3 confirm modal + D4 test-against label)` | `[QA:s31] extend with Wave 10 layer-3 coverage (D3 confirm modal + D4 test-against)` |
| `regex-dialog: verify Recent ▾ menu off-screen clamp on multi-monitor disconnect (audit D7)` | `[FE:action-dialog] verify Recent ▾ menu off-screen clamp on multi-monitor disconnect (audit D7)` |

The label set (`bug` / `chore` / `enhancement` / `ux` / `documentation`
+ `priority: critical|high|medium|low`) carries the type & priority
axes — the title no longer needs to.

## Body — mandatory `What` / `Why` / `How` + optional sections

The three mandatory sections answer the three questions every
reader has, in the order they ask them: **What** is the observation,
**Why** does it matter, **How** would we approach it. Without any
of the three the issue rots (no shared understanding) or gets
re-opened later because the original reporter's intent was lost.

### The mandatory three (in this order)

```markdown
## What
{The concrete observation. State it once, plainly. For a bug, the
buggy behaviour; for a feature, the missing capability; for a chore,
the debt; for a doc gap, the missing/stale content. Include file
paths + line numbers if applicable.}

## Why
{Why this matters. Who hits it, what breaks (or what value is
unlocked), what the cost is of not doing this. If the answer is
"because it would be nice", question whether the issue should exist.}

## How
{Proposed approach. If genuinely unknown, write "TBD — needs
investigation" with one or two starting points. If known, list the
steps as a plan or sketch the diff shape. This is the section that
turns an observation into actionable work.}
```

### Optional sections (use the ones that apply)

Order them after the mandatory three. Section names listed below
in the order they typically appear:

| Section | Use when |
|---|---|
| `## Symptom` / `## Reproduction` | Bugs — concrete steps to reproduce, error messages, stack traces. Goes between What and Why for bugs. |
| `## Acceptance criteria` | Features / enhancements — bulleted list of "done when …" so a future implementor knows when to stop. |
| `## Out of scope` | Anything close to the issue that the reporter explicitly does NOT want this issue to grow into. Prevents scope creep at planning time. |
| `## Related` | Cross-links to other issues (`#N`), PRs (`#N`), audits, source lines (`select_dialog.py:1218`). Use `Supersedes #N` / `Superseded by #N` / `Follow-up to #N` / `Blocks #N` / `Blocked by #N` shapes when the relationship is structural. |
| `## Context` | Background that made this filable — "found during /work on #X", "drive-by from /pr-review on PR #N", "audit item E10". Useful for "why was this filed *now*" questions later. |
| `## Trace` | For audit-derived issues — the source line or commit that prompted the filing, so a future reader can re-derive the observation. |

### Body — full example

```markdown
## What
`connect_main` in `qa/scenarios/_uia.py:351` has a 5-second default
timeout for the UIA-connect step. On GitHub Actions Windows runners
this is often insufficient, leading to intermittent
`ElementNotFoundError: '.*Photo Manager.*'` flakes that surface as
random qa-shard failures.

## Symptom
Run captured at https://github.com/jackal998/photo-manager/actions/runs/26301844529/attempts/1 —
qa (2) shard fails 6m56s in, with two scenarios (`s02_empty_folder`,
`s12_save_manifest`) both raising `pywinauto.findwindows.ElementNotFoundError`
for `.*Photo Manager.*` after the 8s window-appear warner fired.

## Why
The flake erodes CI signal — docs-only PRs cannot physically introduce
qa regressions, so a failed qa shard on such a PR is noise that costs
maintainer attention and slows merge cadence. Recurring noise also
trains reviewers to ignore qa failures, which masks real regressions.

## How
Raise the `connect_main` default from `5` to `20` to match the
precedent already established in `qa/probes/_runtime.py:150`. Total
launch-to-UIA-ready budget becomes ~28s (8s waiter + 20s connect)
— generous enough for slow runners; local runs unaffected (finish in
<2s, ceiling never hit).

## Out of scope
- Migrating scenarios off pywinauto (separate, larger discussion)
- Raising the 8s `_wait_for_main_window` default (non-fatal warner only)

## Related
- Follow-up to flake observation in #373
- Precedent: `qa/probes/_runtime.py:150` `_uia.connect_main(timeout=20)`

## Context
Surfaced during `/work #373` when Path C (fix-now) was chosen over
Path A (close-as-flake-confirmed). Filed for paper trail in case
future runs reveal cases the 20s ceiling still doesn't absorb.
```

## Label allocation

Pull labels from the existing repo set only — don't invent new ones
in the issue-create call. To list the current set:

```bash
gh label list --limit 50
```

This repo's current labels (as of 2026-05-24):

| Axis | Labels | When to apply |
|---|---|---|
| **Type** (pick one) | `bug` `enhancement` `chore` `documentation` `ux` | Mandatory. Match the body — a "What" describing broken behaviour → `bug`; missing capability → `enhancement`; debt/cleanup → `chore`; doc gap → `documentation`; UI surface concern → `ux` (often combined with another) |
| **Priority** (pick one) | `priority: critical` `priority: high` `priority: medium` `priority: low` | Mandatory. `critical` = blocks release or causes data loss; `high` = important, near-term; `medium` = normal; `low` = nice to have. When unsure, default to `medium` and let the user adjust. |
| **Meta** (rare) | `duplicate` `good first issue` `help wanted` `invalid` `question` `wontfix` | Apply when applicable — usually added by triage, not by filer. |

If a label seems missing (e.g. a `tech-debt` distinct from `chore`,
or a per-team label like `team:qa`), flag it in chat and ask before
creating. **Do not create new labels via `gh label create` without
explicit user "yes"** — labels are a shared taxonomy and changes are
hard to roll back.

## When to use

Trigger phrases that should activate this skill:

- "file an issue for X"
- "open an issue for that"
- "track this as an issue"
- "make a github issue"
- "file for visibility / for trace / for the record"
- "file this as a follow-up"
- "create a tracking issue"

Skill should also be invoked by other skills:

- **`/work`** — when the researcher-agent's brief surfaces unrelated
  findings, /work's "out of scope — file as follow-up issue" path
  loads this skill to actually file the issues. Pre-existing memory
  ([Capture full design space](../../../../.claude/projects/C--Users-J-repository-photo-manager/memory/feedback_capture_full_design_space.md))
  says deferred work must always be filed — this skill is the
  mechanic that closes that gap.
- **`/pr-review`** — Gate 5 drive-by observations that exceed the
  3-finding inline budget OR that the reviewer wants tracked beyond
  the current PR's lifecycle.
- **Audit clusters** — when an audit (regex-dialog #347–#351 shape)
  produces N grouped findings, file each cluster as one issue with
  sub-item codes in the body.

## When NOT to use

Skip the skill and just respond in chat when:

- The observation is **ephemeral** — debugging a specific run that
  won't recur, or noting something the user is about to act on in
  the same session.
- The user said "**don't file**" / "**just note it**" / "**preview only**".
- The observation is **already an open issue** — search first with
  `gh issue list --search "<keyword>"`. Linking is better than
  duplicate-filing.
- The work is **so small it'll be done in the same conversation** —
  file only what survives the session.

## Workflow

### Step 1 — Draft the issue in chat

Before any `gh` call, draft the **title + labels + body** as plain
text and show it to the user. This gives the user a single place to
edit and approve.

Output shape:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Proposed issue
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Title:  [TEAM:scope] descriptive title
Labels: type-label, priority: X

Body:
─────
## What
…

## Why
…

## How
…

## {optional sections as applicable}
…
─────
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Say "yes" to file, or edit any field first.
```

### Step 2 — Search for duplicates before filing

Run `gh issue list --search "<3-5 key words from the title>" --state all`
to catch likely duplicates. Surface any matches in the chat output
above. If a strong duplicate exists:

- Stop. Recommend commenting on the existing issue instead.
- If the user confirms the new issue is distinct (different scope,
  different cause), proceed but add a `Related: #N` line to the
  body's `## Related` section.

### Step 3 — Surface the gate

`gh issue create` is a remote write — visible to anyone with repo
read access, sends notifications to label/area subscribers. Per
CLAUDE.md security gates, surface explicitly before posting:

```
> Gated action — `gh issue create`
> - What: file new issue with title "<title>"
> - Where: github.com/<owner>/<repo>/issues
> - Risk: remote write — visible & notifying. Reversible via
>   `gh issue close` + edit-to-blank, but not invisible.
> - Verdict: safe.
```

### Step 4 — Create the issue

Use `gh issue create` with `--body-file -` reading from stdin (works
on Windows + POSIX; avoids `--body` shell-quoting fragility on `##` /
backticks / nested markdown):

```bash
gh issue create \
  --title "[TEAM:scope] …" \
  --label "bug,priority: high" \
  --body-file - <<'EOF'
## What
…

## Why
…

## How
…
EOF
```

Capture stdout — it's the issue URL. Parse the trailing integer for
the `#N` to surface back to the user.

For repeated filings (audit clusters of N issues), loop step 1–4
per issue. Do **not** batch into one `gh issue create` call —
GitHub's CLI is one-issue-per-call, and surfacing one gate per
issue keeps the user in control.

### Step 5 — Report back

One line per filed issue:

```
Filed #N — [TEAM:scope] title (<labels>) → <url>
```

If filing was part of a `/work` "out of scope" sweep or a `/pr-review`
drive-by promotion, also one line of cross-context:

```
Linked from PR #M's Gate 5 drive-by → #N
```

## Composition

This skill composes naturally with:

- **`conventional-comments/`** — for sub-item formatting inside an
  issue body when filing audit-cluster-style issues that enumerate
  N findings as labelled bullets. (Not for the issue title — title
  uses the `[TEAM]` prefix above, not the conventional-comments
  label format.)
- **`/work`** — invoked by /work's "out of scope" path; receives a
  pre-digested finding and emits an issue.
- **`/pr-review`** — invoked by /pr-review when Gate 5 drive-by
  observations should be tracked beyond the chat report.

Does NOT compose with:

- `github-pr-review-{pending,submitted,fetch}` — those handle PR
  *reviews*. Issues and PR reviews share `gh` but are distinct
  surfaces.

## Anti-patterns — do not do these

- **Filing without `Why`.** "Filed for visibility" with no impact
  argument means the issue becomes triage debt and either rots in
  the backlog or gets closed as `wontfix` by future-you. If you
  can't write the Why, ask the user — the Why often comes from
  context that's in the conversation but hasn't been said out loud.
- **Filing without `How`.** "TBD — needs investigation" is a valid
  How for genuinely exploratory items, but bare "this is broken" is
  not. The How is what makes the issue actionable; without it the
  issue is a Slack message in a tracker.
- **Type-prefixed titles** (`[BUG] …` / `[CHORE] …`). Use labels for
  type; use the team bracket for area. The repo's older issues use
  type-prefix style — they're inconsistent precedent, not the
  standard going forward.
- **Inventing labels mid-filing**. If you think a label is missing,
  surface that as a separate question to the user before filing —
  don't `gh label create` autonomously.
- **Skipping duplicate search.** Half the friction in issue trackers
  comes from N issues describing the same thing from different
  angles. Step 2 is cheap; do it.
- **Batching the gate.** N issues = N `gh issue create` calls =
  N gates surfaced. "Let's go" approves only the next one, not the
  whole sweep — same per-gated-action rule as elsewhere in CLAUDE.md.
- **Dumping research briefs / chat scrollback into the body.** The
  body should stand alone. Link to the source (`Related: PR #N`,
  `Context: filed during /work #X`) instead of pasting the full
  origin material.
- **Filing ephemeral observations.** If you wouldn't pay the cost of
  re-reading it in 6 months, don't file it. Chat is fine for
  one-shot debug notes.
- **Auto-`gh label create` for "team:" labels.** Bracket prefix is
  the team marker; a parallel `team: qa` label would be redundant.
  Do not create per-team labels without explicit user "yes".

## Edge cases

- **Cluster issues (audit-style, like #347–#351).** File one issue
  per cluster, each with a numbered sub-item list in the body
  (A1, A2, B1, …). Future PRs reference the issue + sub-item code
  (`Addresses A1, A11 from #347`). The `What` section enumerates
  sub-items; the `How` section can be "see per-item How: blocks
  in the list".

- **Layer-3 qa follow-ups (the `[QA:sNN] extend with …` shape).**
  These are the canonical chore-issue template in this repo. They
  always:
  - Reference a parent PR/wave in `Related`
  - List the specific items needing runtime coverage in `What`
  - Default to `priority: low` (chore label, low priority) unless
    the parent surface is on a critical-path UX flow

- **Paper-trail-only issues (the #373 shape).** Sometimes the issue
  exists to document an observation, not to track work. Make this
  explicit in `How`: "Close protocol: re-run X, observe Y, close
  with comment naming both runs." This signals to future readers
  that "no PR" is the expected resolution. See also the
  [flake-close-vs-fix](../../../../.claude/projects/C--Users-J-repository-photo-manager/memory/feedback_flake_close_vs_fix.md)
  memory: even a paper-trail issue can warrant promotion to a fix
  if the underlying cause is small and well-precedented.

- **Spawned mid-`/work`.** When `/work`'s researcher-agent surfaces
  an unrelated finding, file the issue *before* completing the
  primary work — that way the cross-reference (`Filed from PR #M
  drive-by`) is accurate when the primary PR opens. Do not bundle
  the deferred work into the primary PR.

## Output template — chat report

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Issues filed (N)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

#A — [TEAM:scope] title (label, priority) → url
#B — [TEAM:scope] title (label, priority) → url
…

Skipped (duplicates / preview-only):
  • would-have-been-title — already covered by #X
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Minimal flow

1. User (or calling skill): trigger phrase + the observation.
2. Draft title + labels + body in chat per the template.
3. Search for duplicates (`gh issue list --search`).
4. Surface the per-issue gate; wait for "yes".
5. `gh issue create --title … --label … --body-file …`.
6. One-line report-back per issue.
