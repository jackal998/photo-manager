## Pre-merge checklist

- [ ] User-visible behaviour added/changed → updated `docs/features.md`
- [ ] New file under `app/views/{dialogs,handlers,components,workers}/` → corresponding `qa/scenarios/sNN_*.py` driver added
- [ ] Tests added (unit + qa scenario if user-facing)
- [ ] No `--no-verify` used
- [ ] Pre-PR hooks (`docs_guard`, `qa_scenario_guard`) passed locally

For any item that legitimately doesn't apply, replace `[ ]` with `[N/A]` so reviewers can see at a glance that you considered it and made a deliberate call. See [`CONTRIBUTING.md`](../CONTRIBUTING.md#pr-template-and-pre-merge-checklist) for details.

## Summary

<1-3 bullets on what changed and why>

## Test plan

- [ ] `pytest` full suite passes
- [ ] Per-file coverage floor (70%) clears
- [ ] qa-batch scenarios pass (if user-facing)
- [ ] Manual smoke if the change is hard to assert via tests

Closes #N (where applicable)
