---
name: docs-features-drift
description: Detect drift between behaviour-bearing source files in a diff and entries in docs/features.md. Use when /pr-review's Gate 1 has classified files as behaviour-bearing — this skill judges whether each one has a covering entry in features.md, whether the entry's wording matches what the diff actually does, or whether a new entry is missing entirely.
origin: local
---

# docs-features-drift — Gate 2 rubric

This skill is invoked by `/pr-review` Gate 2 when the diff has
behaviour-bearing files (per Gate 1's classifier). It compares the
diff against `docs/features.md` — the canonical feature inventory
for photo-manager — and emits ✓ / ⚠ / ✗ per matched entry.

The file-touch gates (`scripts/hooks/docs_guard.py`) catch the
*absence* case — features.md wasn't touched at all when behaviour
files changed. This skill catches the *content* case — features.md
was touched but with stale or incomplete wording.

## When to invoke

`/pr-review` invokes this skill via the Skill tool when Gate 1
classifies any of the following in the diff:

- `app/views/dialogs/**.py`, `app/views/handlers/**.py`,
  `app/views/workers/**.py`, `app/views/main_window.py`,
  `app/views/window_state.py` (non-trivial diff)
- `core/services/**.py` or `core/models.py` with return/signature
  changes that flow to UI
- `qa/scenarios/sNN_*.py` add/rename (signals a new flow)
- `settings.json` user-visible key add/remove
- `translations/*.yml` keys also touched in Python

## How to apply

For each behaviour-bearing source file in the diff, search the
**current working-tree `docs/features.md`** for entries that
reference it. Two ways to match:

- **File path match.** The entry's `Entry point:` or `Related:`
  line names the file path (with or without line number).
- **PR-number match.** The entry's `Related:` field names the PR
  number being reviewed (e.g., `[PR #260]`, `pull/260`). This
  catches retroactive backfill — see `pr-review/SKILL.md` Gate 4
  for the historical-drift caveat.

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

## What NOT to flag

- Pure refactor: same behaviour, different code shape (renamed
  private helper, extracted method, moved constant).
- Bugfix that restores the documented behaviour (the diff makes
  the code match what features.md already says).
- Internal docstring or comment edits.

## Output format

Emit findings under the `## docs/features.md coverage` section in
`/pr-review`'s chat report, one line per file:

```
✓ <feature-name>: <one-line summary of why it's covered>
⚠ <feature-name>: <one-line drift description> — see <file:line>
✗ <touched-file>: no features.md entry — appears user-visible
    suggested entry name: "<area> — <behaviour>"
```

## See also

- `pr-review/SKILL.md` — the manager that invokes this skill.
- `qa-scenario-drift/SKILL.md` — chains after this skill; uses
  the `Related:` field of entries matched here to find scenarios.
- `docs/features.md` — the canonical inventory this skill reads.
- `update-docs/SKILL.md` — the write-side companion that fixes
  drift after the fact (not invoked from pr-review; user runs
  it separately).
