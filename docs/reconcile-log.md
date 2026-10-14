# Docs reconcile log

Each entry records one `/docs-reconcile` run (`.claude/skills/docs-reconcile/SKILL.md`):
the commit it reconciled up to, the date, the reviewer, and the result.

- `scripts/docs_reconcile.py` takes the SHA on the LAST `- SHA:` line here as its
  default `--since`, so the next run starts where this one stopped.
- A release tag (v0.2.0-rc2 or later) requires an entry whose `SHA` equals the tagged
  commit. Reconcile first, then tag.

Append new entries at the end. Never edit an entry that is already recorded.

## 2026-10-14 — baseline

- SHA: ec77b6fb5f7931a7b485475990ccea0ddbf3a316
- Reviewer: orchestrator
- Result: baseline — register seeded by #971
