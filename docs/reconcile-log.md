# Docs reconcile log

Each entry records one `/docs-reconcile` run (`.claude/skills/docs-reconcile/SKILL.md`):
the commit it reconciled up to, the date, the reviewer, and the result.

- `scripts/docs_reconcile.py` takes the SHA on the LAST `- SHA:` line here as its
  default `--since`, so the next run starts where this one stopped.
- A release tag (v0.2.0-rc2 or later) requires an entry whose `SHA` equals the tagged
  commit AND whose `Unresolved:` line is `none`. A fix PR merged after a reconcile moves
  HEAD, so reconcile again at the new SHA before tagging.

Each entry carries: `SHA`, `Reviewer`, `Result` (counts and PRs), and `Unresolved`
(`none`, or each open FALSE/UNCLEAR finding with its issue number).

Append new entries at the end. Never edit an entry that is already recorded.

## 2026-10-14 — baseline

- SHA: ec77b6fb5f7931a7b485475990ccea0ddbf3a316
- Reviewer: orchestrator
- Result: baseline — register seeded by #971
- Unresolved: none

## 2026-10-16 — after #973–#980

- SHA: e28f963d108173c3c4136c2049d764d8b2e4453b
- Reviewer: orchestrator (fresh opus reviewer per rule and per behaviour-bearing PR)
- Result: rules 7 HOLDS / 1 FALSE (R-LOCK-2: an Execute clears the lock in its own write before it deletes or skips; register corrected, Execute unlock pin added) / 0 UNCLEAR; stale entries 2 (Execute Action — filter by action type; Execute Action — lock-confirm dialog, s34 bullet), both fixed; Docs impact mismatches 1 (#979 did not name "Execute Action — filter by action type (#502)"); issues filed: none; fix PR: #PRNUM
- Unresolved: none (the code still unlocks before it applies; that order is tracked as #985)
