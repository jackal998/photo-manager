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
chat. The user decides whether to act on it. The `gh pr review
--comment` post-back is a separate, explicitly-gated step.

## Review rubric

Apply the rubric in the order below. Stop at the first gate that
short-circuits to CLEAN — don't keep digging.

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

## Output template

Emit exactly this structure in chat. Use `## CLEAN` (no findings) or
the per-section pattern below.

```
PR review — <branch-name> (<commit-count> commits, <file-count> files touched)
Diff: origin/master...HEAD   |   Files in scope: <N behaviour-bearing> / <total>

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

## Verdict
<one-line summary: CLEAN / N⚠ / M✗ / N ⚠ + M ✗>
```

For a clean PR:

```
PR review — <branch-name> (<commit-count> commits, <file-count> files touched)
Diff: origin/master...HEAD   |   Files in scope: 0 / <total> behaviour-bearing

## CLEAN
No behaviour-bearing changes — diff touches only <docs / tests / hooks / translations / etc>.
No features.md or qa scenario coverage to verify.
```

## How to apply the rubric (step-by-step)

When the user runs `/pr-review` (with or without a PR number):

1. **Resolve the diff.**
   - No args: run `git diff origin/master...HEAD --stat` then
     `git diff origin/master...HEAD`.
   - With number: run `gh pr diff <N>` and
     `gh pr view <N> --json title,body,headRefOid,baseRefOid,files`.

2. **List behaviour-bearing files** (Gate 1). State explicitly:
   "behaviour-bearing: [list]. Out of scope: [list]." If empty,
   short-circuit to the CLEAN output.

3. **Per behaviour-bearing file, search features.md** for matching
   entries — by file path AND by PR number (Gate 2).
   - Read the matched entries in full.
   - Read the diff's hunks for that file in full.
   - Decide ✓ / ⚠ / ✗ per the Gate 2 criteria.

4. **Per matched entry, check the qa scenario named in `Related:`**
   (Gate 3).
   - Read the scenario file.
   - Decide ✓ / ⚠ per Gate 3 criteria.

5. **If invoked with a PR number, check historical context** (Gate
   4). Run `git show <head-sha>:docs/features.md` to detect
   pre-features.md PRs and apply the caveat.

6. **Scan for drive-by observations** (Gate 5). Limit to 3.

7. **Emit the report** in the output-template structure. End with
   the Verdict line.

8. **Stop.** Do NOT post anything to the PR. Wait for the user.

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
findings as a PR review comment. This is a **separate, explicit
step**.

When the user says "post that to the PR" or similar:

1. Confirm the PR number explicitly. If the skill was invoked
   without a number (current branch), ask for the PR number first.
2. Show the exact text that will be posted (the chat report,
   reformatted as markdown). Trim Gate 5 drive-by observations to
   the top three.
3. Ask: "Post this as `gh pr review <N> --comment --body ...`?
   (yes/no)"
4. Only after explicit "yes": run the command.

Do NOT auto-post under any circumstances. The skill's job is to
surface findings; the user decides what reaches the PR.

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
