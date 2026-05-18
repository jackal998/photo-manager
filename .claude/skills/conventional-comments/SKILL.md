---
name: conventional-comments
description: Uniform format for review feedback in chat output and PR review threads — label + optional decorations + subject, with a documented set of labels (suggestion, issue, question, praise, etc.) and `<details>` folding for long quoted blocks. Use whenever pr-review emits findings or whenever you post comments to a GitHub PR via github-pr-review-pending.
origin: local
---

# Conventional Comments (project format)

Adapted from <https://conventionalcomments.org/>. The goal: make review
feedback **skimmable** (label tells the reader the kind of finding before
the prose does) and **tool-friendly** (the labels parse uniformly for
later filtering / counting).

This skill is the **single source of truth** for finding-output shape in
photo-manager. Other skills (notably `pr-review` and
`github-pr-review-pending`) reference this file rather than repeating
the spec.

## Shape

```
**<label> [(decorations)]:** <subject>

<optional reasoning, reference, or next step>
```

- **Label** — exactly one, from the table below.
- **Decorations (optional)** — comma-separated qualifiers in
  parentheses: `(blocking)`, `(non-blocking)`, `(security)`,
  `(if-minor)`.
- **Subject** — the main point in one line when possible.
- **Discussion** — optional; blank line after the subject; can include
  links, quoted code, or a recommended action.

## Labels

| Label | Use for |
|---|---|
| `praise:` | A genuine positive — only when you mean it. Pads if used reflexively. |
| `suggestion:` | A concrete improvement. Pair with `(blocking)` or `(non-blocking)` when it matters. |
| `issue:` | A real problem. Prefer pairing with a `suggestion:` or `question:` follow-up. |
| `question:` | Uncertainty; need author input. |
| `thought:` | Non-blocking idea or angle worth recording. |
| `nitpick:` / `note:` | Small preference or FYI; usually `(non-blocking)`. |
| `chore:` | Process item before merge (e.g. "rebase on master"). |
| `todo:` | A small must-do before acceptance. |

When the project's `pr-review` skill emits its existing severity icons
(`⚠`, `✗`, `ℹ️`), map them to conventional labels in the body so the
finding is unambiguous at a glance:

| pr-review icon | Conventional label |
|---|---|
| `✗` (severe — missing entry, breaks contract) | `issue (blocking):` |
| `⚠` (drift — stale text, missing branch coverage) | `suggestion:` (add `(blocking)` only if merge should wait) |
| `ℹ️` (informational — historical caveat, routing pointer) | `note:` |

## Example — photo-manager flavoured

```markdown
**suggestion (non-blocking):** Move the conditional dialog trigger
into a named helper.

The branch in `app/views/dialogs/save_changes_dialog.py:142` is the
third place this exact "files changed but none in-scope" check
appears. A `_should_show_save_dialog(...)` helper keeps the rules
in one spot and lets `docs/features.md` describe the trigger in
words. Non-blocking — the duplication is small.
```

```markdown
**issue (blocking):** `_MIGRATIONS` entry inserted at index 4
instead of appended.

`infrastructure/manifest_repository.py:88` adds the new
`ALTER TABLE` between existing entries 3 and 5. Migrations run
in list order on first launch; insertion shifts every later
migration's version number, so any user who has already run up to
the original entry 5 will skip the new ALTER. Per pr-review Gate 8,
migrations must be **append-only**. Move to the tail of the list.
```

## Folding long quotes (`<details>` / `<summary>`)

For threads that need to quote more than a screenful of code, logs,
stack traces, or test output, wrap the quoted block in `<details>` so
the label + subject + reasoning stay scannable in the GitHub UI:

```markdown
**suggestion:** Tighten the exiftool batch call.

The diff drops `-stay_open` for HEIC files; that's the
per-file-cost trap from `photo-scanner-patterns`. Re-enable batching.

<details>
<summary>Current call (before/after)</summary>

```python
# before — batched
process = subprocess.Popen([exiftool_bin, "-stay_open", "True", ...])
# after — per-file (this PR)
subprocess.run([exiftool_bin, "-j", path], ...)
```

</details>
```

**Fold:** quoted code > 10 lines, full stack traces, large diff
hunks, before/after dumps.

**Don't fold:** the label + subject + core reasoning. Those must be
visible without expanding.

**GitHub renderer caveat:** blank lines are required immediately
before AND after the inner code fence inside `<details>` — without
them GitHub silently drops the fenced block.

## When NOT to use this format

- Chat-only summary lines that fit in one sentence ("Verdict: CLEAN").
  Reserve the label format for actual findings, not section headers.
- The end-of-review **Verdict** line in `pr-review` output — that
  stays as the existing one-liner (`CLEAN / N⚠ / M✗`).

## See also

- `pr-review/SKILL.md` — applies this format inside its Gate 1–11
  output blocks.
- `github-pr-review-pending/SKILL.md` — posts these bodies as
  `comments[].body` on a pending GitHub review.
