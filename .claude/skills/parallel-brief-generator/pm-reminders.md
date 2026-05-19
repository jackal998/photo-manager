# Photo-manager-specific reminders to bake into briefs

The orchestrator references this file when composing each brief in
Step 3 of [SKILL.md](SKILL.md). Not every reminder applies to every
brief — match each item's trigger to the in-scope file list before
including it. Reminders that are universally relevant (the
three-gate breakdown) belong in EVERY brief; the scanner-side
gotchas only matter when scope touches `scanner/`.

## Three PR-creation gates — name them precisely in every brief

The brief template's PR-workflow step (see
[brief-template.md](brief-template.md) step 8 / 8b) already names
all three; this section is the orchestrator-side reference for
*which* gate each task's scope will actually trigger.

1. **`qa_scenario_guard`** (PreToolUse on `gh pr create`) — fires on
   `app/views/{handlers,dialogs,components,workers}/` diffs without
   a `qa/scenarios/sNN_*.py` change. Bypass:
   `[qa-not-needed: <reason>]`.

2. **`docs_guard`** (PreToolUse on `gh pr create`) — has **two
   distinct triggers**, easy to confuse:
   - **new-file trigger**: NEW `.py` files under the structured
     dirs (`app/views/{dialogs,handlers,workers,components,widgets,layout,viewmodels}/`,
     `infrastructure/`, `scanner/`, `core/{models,services/}`,
     `tests/test_*.py`, `qa/scenarios/s*.py`). Satisfied by ANY
     edit to `README.md` / `docs/*.md` / `CLAUDE.md` /
     `pyproject.toml`.
   - **behavioural-modify trigger** (#262): MODIFIED files under
     `app/views/{dialogs,handlers}/` with ≥10 added+deleted lines
     OR a `def` signature change. **Strict: requires
     `docs/features.md` specifically.** Other doc touches
     (docs/testing.md, README, CLAUDE.md, pyproject) do NOT
     satisfy this trigger.

   Bypass for either: `[docs-not-needed: <reason>]`.

3. **`news-gate`** (CI workflow, not a local hook — fires after
   PR creation): requires `news/<PR#>.<type>` where type ∈
   `{feature, bugfix, doc, removal, misc}`. Bypass:
   `[skip-news: <reason>]`. See brief-template.md step 8b for the
   post-PR workflow that adds the fragment.

## Behavioural-modify pre-staging rule

When in-scope files trip the docs_guard **behavioural-modify
trigger** (MODIFIED `app/views/{dialogs,handlers}/*.py` over the
threshold), the brief MUST:

- **(a)** name the trigger and the docs/features.md requirement
  explicitly — don't bury it under generic "hooks may fire"; AND
- **(b)** pre-stage the recommended action in the PR-body
  template (the `<body>` placeholder in step 8 of
  brief-template.md), one of:
    - `"Add or update the <feature name> entry in
      docs/features.md"` when the change IS user-visible, OR
    - Pre-written `[docs-not-needed: <reason>]` token in the
      body, e.g.
      `[docs-not-needed: test-only refactor — helper extraction
      preserves behaviour byte-for-byte]` when the change is
      pure-refactor / behaviour-preserving.

The point: the cold session gets it right on first PR creation,
not after a failed CI run.

## Pattern-PR cross-check rule

Before saying "follow PR-X's pattern" in a brief, verify PR-X's
target files were under the **same gated subdir** as the new task's
targets. The gates' predicates are PATH-based, not pattern-based.

Example failure mode (#312): briefing #293 to "follow #283/#285/#289's
helper-extraction pattern" — those PRs touched
`app/views/main_window.py` and `app/views/widgets/*` (OUTSIDE the
behavioural-modify subdirs), so they never triggered docs_guard's
strict mode. #293 targeted `app/views/handlers/dialog_handler.py`
(INSIDE the strict subdir), which DID trigger, and the brief's
"docs/testing.md update satisfies it" was wrong as a result.

Run this check whenever citing prior-art PRs as templates.

## Scanner-side gotchas (only bake into scanner-touching briefs)

These apply when the bundle's scope touches `scanner/` — skip them
otherwise.

- **`read_result_rows` is broken on CI** — has a `y_min=600` filter
  that drops all rows on the smaller CI render. Use sqlite reads
  (pattern: s14, s32, s35) for tree-content assertions.
- **Trailing-period Windows paths**, **Live Photo pair-clusters**,
  **case-insensitive pathlib** — scanner-side correctness gotchas
  worth naming in any brief that walks files or hashes them.

## Destructive QA scenarios

Universal — bake into any brief that adds qa/scenarios coverage.

- `s13`, `s36` send real files to the recycle bin per run. Do NOT
  extend destructive coverage without explicit user agreement
  surfaced as a gated action.
