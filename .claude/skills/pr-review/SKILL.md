---
name: pr-review
description: Use after `git push` (or against an existing PR number) to catch semantic drift between code, docs/features.md, and qa/scenarios/sNN_*.py that the file-touch hooks (docs_guard, qa_scenario_guard) cannot see. Reads the diff and the relevant docs/scenarios; reports findings in chat; optionally posts to the PR after explicit user approval.
origin: local
---

# PR review — semantic doc/test drift check

The file-touch gates (`scripts/hooks/docs_guard.py`,
`scripts/hooks/qa_scenario_guard.py`) check **presence**: did the PR
touch `docs/features.md` / a qa scenario when it touched a behaviour
file? They cannot check **content**:

- features.md entry was touched but with stale wording
- qa scenario exists but doesn't exercise the new branch
- new conditional dialog with no trigger-explaining docstring

This skill layers semantic-content checking on top, run manually after
`git push` (or against an open PR number) by Claude in the same
session that wrote the code.

## When to invoke

- After `git push -u origin <branch>` and BEFORE `gh pr create`, on
  the current branch. Default invocation.
- After opening a PR, to spot-check via `/pr-review <PR-number>`.
- During code review of someone else's PR — `/pr-review <PR-number>`
  pulls the diff via `gh pr diff <N>` and applies the same rubric.

Do NOT auto-invoke. Do NOT post to the PR without explicit user
"yes" — the optional post-back step has its own gate (see "Optional
post-back" below).

## Invocation contract

```
/pr-review                   # current branch vs origin/master
/pr-review <PR-number>       # `gh pr diff <N>`
```

The skill reads:

1. **The diff.**
   - No args: `git diff origin/master...HEAD --stat` and
     `git diff origin/master...HEAD` (full hunks).
   - With PR number: `gh pr diff <N>` and
     `gh pr view <N> --json title,body,headRefOid,baseRefOid`.
2. **`docs/features.md`** at the working tree's HEAD (the canonical
   feature inventory).
3. **`qa/scenarios/sNN_*.py`** matching each touched `app/views/`
   file (look up `Related:` lines in the feature entries identified
   in step 2).
4. **`README.md § Usage — GUI § Step 1-4`** ONLY if the diff touches
   the documented happy-path surfaces (scan dialog, save flow,
   execute flow). Skip otherwise — the inventory in features.md is
   the canonical answer.

Side effects: **none by default**. The skill produces a report in
chat. The user decides whether to act on it. The optional post-back
to the PR routes through `github-pr-review-pending/SKILL.md` (creates
a pending draft review only — human submits in the GitHub UI) and is
a separate, explicitly-gated step.

## Review rubric

Apply the rubric in the order below. Stop at the first gate that
short-circuits to CLEAN — don't keep digging.

### Gate 0 — task alignment

Fires when the PR body has `Fixes #N`, `Closes #N`, or `Resolves #N`,
OR when the user supplied an issue number directly with `/pr-review`.
Skip entirely if no issue link.

**What it does:**

1. `gh issue view <N> --json title,body,labels` (run in parallel with
   step 1's other fetches — see "How to apply" step 1).
2. Compare:
   - Issue title vs PR title.
   - Issue body's "what we want" / acceptance criteria vs the diff's
     visible behaviour changes.
3. Emit one of:
   - ✓ aligned: PR addresses what the issue describes. No output
     unless something's noteworthy.
   - ⚠ scope: PR delivers more than the issue asks, or different.
     One-line summary: `⚠ scope: PR adds X; issue #N asks for Y`.
     Rule: **issue wins** — recommend aligning the PR or updating
     the issue.
   - ⚠ unclear-issue: issue body is empty / vague / contradicts
     itself. One-line `note:` and continue.
4. If no issue link in PR body and no number supplied → emit one
   `note: no linked issue` and continue. Don't block.

**What it does NOT do:**

- Doesn't read closed/related issues from other PRs.
- Doesn't try to second-guess the issue's intent — just compare
  what's said against what's done.
- Doesn't gate later gates. A scope mismatch is a finding, not a
  CLEAN short-circuit.

### Gate 1 — is the diff behaviour-bearing?

A PR is **behaviour-bearing** when it could change what a user sees,
clicks, or what happens when they act. Concretely:

- Touches `app/views/dialogs/**.py`, `app/views/handlers/**.py`,
  `app/views/workers/**.py`, `app/views/main_window.py`, or
  `app/views/window_state.py` with non-trivial diff (>10 added+deleted
  lines OR a signature change OR a new conditional branch OR a new
  string literal that surfaces in the UI).
- Touches `core/services/**.py` or `core/models.py` in a way that
  changes a return shape, raised exception, or side-effect signature
  that flows to a UI surface.
- Adds or renames a `qa/scenarios/sNN_*.py` driver (signals a new
  user-visible flow worth recording in features.md).
- Adds or removes a `settings.json` key visible to the user.
- Adds, renames, or removes a translation key referenced from
  `app/views/`.

A PR is **NOT behaviour-bearing** (→ CLEAN, stop) when it ONLY
touches:

- `docs/*.md`, `README.md`, `CONTRIBUTING.md`, `CLAUDE.md` —
  documentation only.
- `scripts/hooks/*.py`, `.github/workflows/*.yml`,
  `pyproject.toml`, `.gitignore`, `Makefile` — tooling/CI/build.
- `tests/test_*.py` or `tests/integration/test_*.py` only
  (no source files alongside) — test-only changes.
- `translations/*.yml` only (no Python touched) — i18n catalogue
  refresh. Note: if the translation keys are also added/renamed in
  Python at the same time, that's behaviour-bearing — see above.
- `.claude/skills/**`, `.claude/agents/**`, `news/**` —
  meta/tooling files.

If the diff is a mix, treat any behaviour-bearing file as a
behaviour-bearing PR — but in the output's CLEAN summary explicitly
identify the meta-only files as "not subject to features.md scope".

### Gate 2 — does features.md cover the behaviour?

For each behaviour-bearing source file in the diff, search the
**current working-tree `docs/features.md`** for entries that
reference it. Two ways to match:

- **File path match.** The entry's `Entry point:` or `Related:`
  line names the file path (with or without line number).
- **PR-number match.** The entry's `Related:` field names the PR
  number being reviewed (e.g., `[PR #260]`, `pull/260`). This
  catches retroactive backfill — see Gate 4 for the historical
  caveat.

Outcomes per file:

- ✓ entry exists AND covers the behaviour added by the diff.
- ⚠ entry exists BUT the diff appears to add/modify a behaviour
  the entry does not mention (drift). Common drift signals:
  - New conditional dialog/branch not described in
    Conditions / variants.
  - New keyboard shortcut, button label, or menu item whose text
    string in the diff doesn't appear verbatim in the entry's
    Behaviour or Conditions sections.
  - Changed scope of an existing action (e.g., "applies to all"
    → "applies to highlighted only") with no entry update.
  - Renamed handler/dialog where the entry still references the
    old name.
- ✗ no entry exists AND the file appears to introduce
  user-visible behaviour. Most severe — features.md needs a new
  section.

When deciding ⚠ vs ✓ for an existing entry: read the entry's
Behaviour / Conditions / Variants sections in full. If the diff
adds something a user would care about (a label, a confirm
dialog, a scope rule, a new key/shortcut, a new condition that
gates a flow) and it's not mentioned, that's ⚠ drift.

Do NOT flag ⚠ for:

- Pure refactor: same behaviour, different code shape (renamed
  private helper, extracted method, moved constant).
- Bugfix that restores the documented behaviour (the diff makes
  the code match what features.md already says).
- Internal docstring or comment edits.

### Gate 3 — does the qa scenario cover the new branch?

For each behaviour-bearing source file, look up the qa scenarios
named in the matched features.md entry's `Related:` field. If
the entry names `qa/scenarios/sNN_*.py`:

- Read the scenario file.
- Check whether the scenario exercises the NEW branch added by
  the diff (look for assertions, button text, or step that
  matches the diff's new path).

Outcomes:

- ✓ scenario exists AND covers the new branch.
- ⚠ scenario exists BUT doesn't exercise the new branch — flag
  with the scenario name and a one-line "extend scenario to
  cover X" suggestion.
- ⚠ no scenario named in the entry AND the behaviour is
  user-visible — flag suggesting "add or extend qa/scenarios/
  driver to cover X". Lower severity than missing features.md
  entry.

Do NOT flag for:

- Translation-only changes (no scenario needed).
- Pure boundary fixes that are intentionally covered by unit
  tests instead of qa scenarios (see CLAUDE.md "Testing ground
  rules" — layer 1 vs layer 3 split).
- Refactors that don't change observable behaviour.

### Gate 4 — historical-drift caveat (retroactive runs)

When invoked with a past PR number (`/pr-review <N>`), it may turn
out the PR pre-dates `docs/features.md` itself (introduced in
[PR #263](https://github.com/jackal998/photo-manager/pull/263), full
backfill in [PR #267](https://github.com/jackal998/photo-manager/pull/267)).
Check this explicitly:

```
git show <pr-head-sha>:docs/features.md 2>/dev/null
```

(`pr-head-sha` from `gh pr view <N> --json headRefOid -q
.headRefOid`.) If the command fails (file didn't exist at PR
head), the PR is pre-features.md.

Pre-features.md PRs are treated as follows:

- If the current features.md HAS an entry referencing this PR
  number → the entry was added retroactively in the backfill.
  Emit ℹ️ informational note: "This PR predates the features.md
  inventory; entry was added in a later backfill PR. Current
  entry [name] covers this behaviour." **Do NOT count as ⚠ or ✗
  — informational only.** This avoids flagging every pre-#263
  PR as a false positive.
- If the current features.md has NO entry referencing this PR
  number AND the diff is behaviour-bearing → emit ✗ "no
  features.md entry exists for the new behaviour introduced by
  this PR" naming the most prominent user-visible change in the
  diff (look at the PR title and the new strings/dialogs in the
  diff). This catches genuinely undocumented behaviour even
  retrospectively.

### Gate 5 — drive-by observations

After the structured findings, list any incidental observations
worth surfacing — these are advisory, not gating:

- New conditional dialog/branch with no docstring or comment
  explaining the trigger (the "why does this fire?" smell).
- README.md § Usage — GUI § Step N text that contradicts the
  diff (cross-check ONLY if the diff touches a Step-1-4 surface).
- Settings-key changes not documented in
  `README.md § Configuration`.

Limit to 3 observations. If you have more, pick the highest-impact
three and say "+N more available on request".

### Gate 6 — harness-security cross-promote

Independent of Gates 1–5 (does not gate them). If the diff touches
any of the following paths, emit an ℹ️ pointer to `/security-scan`
in the output's "Harness security" section:

- `.claude/**` (settings, agents, skills, hooks config)
- `scripts/hooks/**` (project-side PreToolUse / Stop hooks)
- `settings.json` / `settings.local.json` / `.mcp.json`
- `CLAUDE.md` *only* when the touched lines change agent
  permissions, install instructions, or hook directives (skip
  for prose/section edits)

Reason: `/pr-review` checks app-level drift (features.md, qa
scenarios). It does NOT check harness-level risks — prompt
injection in `CLAUDE.md`, command injection in hooks,
over-permissive `Bash(*)` allow lists, MCP supply chain. Those
are what `/security-scan` (AgentShield) catches. When a PR
touches the harness, route the reviewer to the right tool.

The pointer is **informational only**. It never flags ⚠ or ✗,
never blocks, and never affects the Verdict line. It is a
routing reminder, not a finding.

If the diff does NOT touch any harness path, skip this gate
entirely (do not emit an empty "Harness security" section).
### Gates 7-11 — application-level pattern scans

Five gates that scan the diff for concrete dangerous, buggy, or
sloppy patterns. Each has narrow trigger conditions and its own
rubric. The **full rubrics** (patterns, anti-patterns, severity
tiers, output format per gate) live in
[`app-gates.md`](app-gates.md) — read that file's section for
each gate that fires on this diff.

| Gate | Fires when | What it catches |
|---|---|---|
| 7 | Diff is behaviour-bearing | SQL injection (f-string `execute`), hardcoded secrets, unsafe deserialisation (`pickle.load`, `yaml.load`), shell injection (`subprocess shell=True` with interpolation), `eval`/`exec` on diff content, path traversal |
| 8 | `_MIGRATIONS` (in `infrastructure/manifest_repository.py`) or `CREATE TABLE migration_manifest` (in `scanner/manifest.py`) touched | Non-additive migrations, mid-list insertion, missing companion edits to `ManifestRow` + schema SQL, missing README schema-table update |
| 9 | `scanner/**.py`, `app/views/workers/**.py`, or new `QThread` / `QRunnable` / `ThreadPoolExecutor` added | Per-row I/O in a loop, nested O(N²) over filesystem paths, subprocess-in-loop without `-stay_open` batching, `QThread.run()` without progress/cancel, subprocess without timeout. **Also consult `photo-scanner-patterns` (global skill) before judging — it covers known boundary failure modes (exiftool batching, SMB latency, pHash collision on flat images, sidecar matching) that drive most of this gate's findings.** |
| 10 | `tests/test_*.py` or `tests/integration/test_*.py` added/modified | Monkeypatch-to-cover-defensive (`QStandardItem.setData` raising etc.), forced feature-flag fallback, undocumented `@pytest.mark.skip`, `pytest.skip()` in body, stub-AttributeError, branch-reached-only assertions, generic test names for what should be regression tests (`test_pr_NNN_<symptom>`) |
| 11 | `.claude/skills/<name>/` files added/modified (NOT `.claude/skills/personal/`) | Absolute home paths, IPv4 addresses, credential-shaped literals — with the **critical filter rule** that pattern descriptions inside code blocks are NOT literal values |

For each gate whose trigger fires:
1. Read the corresponding section in [`app-gates.md`](app-gates.md).
2. Apply the patterns + anti-patterns described there.
3. Emit findings in the gate's output section (see Output template
   below). Omit the section if zero findings on that gate.

If a gate's trigger does NOT fire, skip its rubric entirely — don't
read its section, don't emit an empty header.

## Output template

Emit exactly this structure in chat. Use `## CLEAN` (no findings) or
the per-section pattern below.

```
PR review — <branch-name> (<commit-count> commits, <file-count> files touched)
Diff: origin/master...HEAD   |   Files in scope: <N behaviour-bearing> / <total>

## Task alignment (Gate 0)
✓ PR matches issue #N
⚠ scope: PR adds X; issue #N asks for Y — ticket wins
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

## App-level security (Gate 7)
⚠ <file:line> — <pattern>: <evidence quote>

## SQLite migration safety (Gate 8)
⚠ <line> — <issue>: <evidence>

## Performance / threading (Gate 9)
⚠ <file:line> — <pattern>: <evidence>

## Test quality (Gate 10)
⚠ <file:line> — <anti-pattern>: <evidence>

## PII audit (Gate 11)
⚠ <file:line> — <category>: <evidence>
ℹ️ <file:line> — possible <category>: <evidence> — confirm placeholder vs real

## Harness security (Gate 6)
ℹ️ This PR touches harness/config: <files>
   Run `/security-scan` before merging — `/pr-review`'s rubric
   does not check harness risks (prompt injection in CLAUDE.md,
   hook command injection, over-permissive Bash allow-list,
   MCP supply-chain).

## Verdict
<one-line summary: CLEAN / N⚠ / M✗ / N ⚠ + M ✗>
```

(Omit the **Harness security** section entirely when Gate 6
finds no harness/config files touched — don't emit an empty
header.)

For a clean PR:

```
PR review — <branch-name> (<commit-count> commits, <file-count> files touched)
Diff: origin/master...HEAD   |   Files in scope: 0 / <total> behaviour-bearing

## CLEAN
No behaviour-bearing changes — diff touches only <docs / tests / hooks / translations / etc>.
No features.md or qa scenario coverage to verify.
```

(Omit any of the Gate 7-10 sections that produced zero findings —
don't emit empty headers. Same rule as Gate 6.)

## How to apply the rubric (step-by-step)

When the user runs `/pr-review` (with or without a PR number):

1. **Resolve the diff.**
   - No args: run `git diff origin/master...HEAD --stat` then
     `git diff origin/master...HEAD`.
   - With number: run `gh pr diff <N>` and
     `gh pr view <N> --json title,body,baseRefName,headRefName,headRefOid,url,state,files,additions,deletions,author,closingIssuesReferences`
     (the extended field list — `closingIssuesReferences` and `body`
     drive Gate 0, `headRefOid` is needed if Optional post-back fires).
   - **Run these in parallel** when invoked with a PR number: the
     `gh pr diff`, `gh pr view --json`, and (if Gate 0 fires)
     `gh issue view` calls have no dependencies on each other. Fire
     all of them in a single message with multiple tool calls and
     synthesise after all return — don't serialise.

2. **Check task alignment** (Gate 0). See Gate 0 in §Review rubric.

3. **List behaviour-bearing files** (Gate 1). State explicitly:
   "behaviour-bearing: [list]. Out of scope: [list]." If empty,
   short-circuit to the CLEAN output.

4. **Per behaviour-bearing file, search features.md** for matching
   entries — by file path AND by PR number (Gate 2).
   - Read the matched entries in full.
   - Read the diff's hunks for that file in full.
   - Decide ✓ / ⚠ / ✗ per the Gate 2 criteria.

5. **Per matched entry, check the qa scenario named in `Related:`**
   (Gate 3).
   - Read the scenario file.
   - Decide ✓ / ⚠ per Gate 3 criteria.

6. **If invoked with a PR number, check historical context** (Gate
   4). Run `git show <head-sha>:docs/features.md` to detect
   pre-features.md PRs and apply the caveat.

7. **Scan for drive-by observations** (Gate 5). Limit to 3.

8. **Check harness-config touch** (Gate 6). If any file in the
   diff matches `.claude/**`, `scripts/hooks/**`,
   `settings.json` / `settings.local.json` / `.mcp.json`, or
   a permissions/install line in `CLAUDE.md` — include the
   "Harness security" section pointing at `/security-scan`.
   Otherwise omit the section.

9. **Run application-level gates (7-11)** per
   [`app-gates.md`](app-gates.md). For each gate whose trigger
   fires (see trigger table in §Review rubric above), read that
   gate's section in `app-gates.md` and apply its rubric. Emit
   findings in the corresponding section of the output template.
   **Omit any gate's section if zero findings**, the same as
   Gate 6. If no gate's trigger fires on this diff, skip reading
   `app-gates.md` entirely.

10. **Emit the report** in the output-template structure. End with
    the Verdict line.

11. **Stop.** Do NOT post anything to the PR. Wait for the user.

## Anti-patterns — what NOT to flag

Misuses that erode trust in the skill (a noisy skill gets ignored):

- ✗ Don't flag a refactor. If the diff changes signatures internally
  but every user-visible string/condition is unchanged, it's a
  refactor. Features.md is about user-visible behaviour, not code
  shape.
- ✗ Don't flag missing features.md on a doc-only PR. Gate 1 catches
  this; if it gets past Gate 1, your gate is too loose.
- ✗ Don't flag missing features.md on a hooks/CI/build-only PR
  (e.g. scripts/hooks/, .github/workflows/, pyproject.toml,
  Makefile). Same as above.
- ✗ Don't flag missing features.md on a test-only PR. Tests
  don't introduce user-visible behaviour.
- ✗ Don't flag missing features.md on a translation-only PR.
  Translation keys come and go; their existence is governed by
  the catalogue, not features.md.
- ✗ Don't flag entries you "would have written differently". Drift
  is about behaviour the entry **does not describe**, not about
  prose style.
- ✗ Don't open new findings on README.md unless the diff touches a
  Step-1-4 happy-path surface AND the README copy directly
  contradicts the diff. Per-feature documentation lives in
  features.md; README is the walkthrough only.
- ✗ Don't flag pre-features.md PRs as ✗ when current features.md
  has an entry referencing the PR (Gate 4). Use ℹ️ informational
  instead.
- ✗ Don't recommend running any other skill. /pr-review is the
  end of the line for semantic review.

## Optional post-back to the PR (explicitly gated)

After the user has read the chat report, they may want to publish
findings as PR review comments. This is a **separate, explicit
step** — and it routes through the `github-pr-review-pending`
skill, NOT through `gh pr review --comment` (which would submit
immediately and bypass the project's "never publish without a yes"
gate).

When the user says "post that to the PR" or similar:

1. Confirm the PR number explicitly. If the skill was invoked
   without a number (current branch), ask for the PR number first.
2. Show the exact thread bodies that will be posted, one per
   line-anchored finding, formatted per
   `conventional-comments/SKILL.md`. Trim Gate 5 drive-by
   observations to the top three.
3. Hand off to `github-pr-review-pending/SKILL.md` Phase 2 (build
   the JSON) and Phase 3 (POST). That skill creates a **pending
   (draft) review** — no notifications fire, the user submits in
   the GitHub UI when ready.
4. Per `github-pr-review-pending` Phase 4, tell the user the
   review is pending and they need to click **Submit review** (or
   **Discard pending review**) in the GitHub UI.

Do NOT auto-post under any circumstances. Do NOT use
`gh pr review --comment / --approve / --request-changes` — those
all submit immediately. The skill's job is to surface findings;
the user decides what reaches the PR and when.

## Why this exists

The file-touch gates (`docs_guard`, `qa_scenario_guard`) catch
*absence* — they fire when a behaviour file changed and no doc /
scenario was touched at all. They cannot catch:

- A features.md entry that was touched but with stale text (the
  developer updated the wrong section, or copy-pasted from a
  similar feature without re-reading).
- A qa scenario that exists for a file but doesn't drive the
  newly-added branch (the scenario covers branch A; the PR adds
  branch B; the scenario file appears on the diff list — but
  only the import line changed).
- A new conditional dialog/menu item added with no features.md
  section at all, when features.md was touched for another
  unrelated reason in the same PR.

This skill is the semantic-content layer. It runs as an LLM
prompt in the same Claude Code session that wrote the code — no
external API, no GitHub Action, no extra infrastructure.
