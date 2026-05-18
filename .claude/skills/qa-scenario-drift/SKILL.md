---
name: qa-scenario-drift
description: Detect whether qa/scenarios/sNN_*.py drivers exercise the new branch added by a PR. Use when /pr-review's Gate 2 (docs-features-drift) has matched features.md entries — this skill reads the scenarios named in each entry's Related field and checks whether the new behaviour is actually covered by the driver.
origin: local
---

# qa-scenario-drift — Gate 3 rubric

Invoked by `/pr-review` Gate 3 immediately after `docs-features-drift`
(Gate 2). For each behaviour-bearing source file with a matched
`docs/features.md` entry, this skill checks whether the
`qa/scenarios/sNN_*.py` driver named in the entry's `Related:`
field actually exercises the new branch the diff added.

The file-touch gate (`scripts/hooks/qa_scenario_guard.py`) catches
the *absence* case — no scenario touched when a UI surface changed.
This skill catches the *content* case — scenario exists but its
assertions cover branch A while the PR adds branch B.

## When to invoke

`/pr-review` invokes this skill via the Skill tool when:

- Gate 2 (`docs-features-drift`) has matched a features.md entry for
  a behaviour-bearing source file, AND
- That entry's `Related:` field names a `qa/scenarios/sNN_*.py`
  driver.

If a behaviour-bearing file has no matching entry, Gate 2 emits ✗
and Gate 3 doesn't run for that file (no scenario to check). If
the matching entry doesn't name a scenario, Gate 3 emits the
"no scenario" ⚠ directly (see Outcomes below) without reading a
driver file.

## How to apply

For each matched entry from Gate 2 that names a scenario:

1. Read the named `qa/scenarios/sNN_*.py` driver file.
2. Read the diff's hunks for the behaviour-bearing file in full.
3. Check whether the scenario exercises the NEW branch the diff
   added. Concrete signals:
   - Assertions that name a button text, menu item, or dialog
     title added in the diff.
   - Steps that traverse the new condition (e.g., "click Save
     when …" matching a new conditional path).
   - Expected post-action state that matches the new behaviour.

Outcomes:

- ✓ scenario exists AND covers the new branch.
- ⚠ scenario exists BUT doesn't exercise the new branch — flag
  with the scenario name and a one-line "extend scenario to
  cover X" suggestion.
- ⚠ no scenario named in the entry AND the behaviour is
  user-visible — flag suggesting "add or extend qa/scenarios/
  driver to cover X". Lower severity than missing features.md
  entry (which is Gate 2's ✗).

## What NOT to flag

- Translation-only changes (no scenario needed).
- Pure boundary fixes that are intentionally covered by unit
  tests instead of qa scenarios (see CLAUDE.md "Testing ground
  rules" — layer 1 vs layer 3 split, and the per-module table
  in `docs/testing.md`).
- Refactors that don't change observable behaviour.
- Internal docstring/comment edits to a scenario driver.

## Output format

Emit findings under the `## qa/scenarios/ coverage` section in
`/pr-review`'s chat report, one line per matched-but-drifted entry:

```
✓ sNN: <one-line summary>
⚠ sNN: exists but doesn't exercise <new-branch> at <file:line>
⚠ no scenario: <touched-file> — consider extending sNN or adding new
```

## See also

- `pr-review/SKILL.md` — the manager that invokes this skill.
- `docs-features-drift/SKILL.md` — Gate 2; provides the matched
  entries this skill iterates.
- `qa/scenarios/sNN_*.py` — the driver files this skill reads.
- `docs/testing.md` — three-layer model; layer 3 is what these
  scenarios cover.
