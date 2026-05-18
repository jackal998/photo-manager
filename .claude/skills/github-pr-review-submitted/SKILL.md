---
name: github-pr-review-submitted
description: Post pr-review findings to a GitHub PR as a **submitted** review in one call — created with `gh api` against the `/reviews` endpoint WITH `event` set (COMMENT / REQUEST_CHANGES). Use in agent-driven workflows where there's no human to click Submit — the review goes live immediately, fires notifications, and is visible to other agents reading the PR. Sibling of `github-pr-review-pending` (which leaves the review pending for a human to submit).
origin: local
---

# GitHub PR review — submitted (mechanics)

photo-manager's `/pr-review` skill emits findings in chat. When the
user (or a calling agent) asks to publish those findings to the PR
**in agent-driven mode** — i.e. there is no human in the loop to
click "Submit review" in the GitHub UI — this skill is the
mechanic. It posts a **submitted** review in one call:
notifications fire, the review is immediately visible to anyone
with PR read access (including a downstream dev agent reading
their own PR), and there's no separate Submit step.

This is the sibling of `github-pr-review-pending` (which leaves
the review pending for a human to submit in the UI). Use this one
in agent-to-agent workflows; use the pending sibling when a human
is in the loop.

## When to invoke vs. the pending sibling

| Scenario | Use this skill (-submitted) | Use the pending sibling (-pending) |
|---|---|---|
| Solo human runs `/pr-review` and wants to read before publishing | — | ✓ |
| Dev agent pushes PR, separate review agent posts feedback for the dev agent to read back | ✓ | — |
| Scheduled / cron review agent | ✓ | — |
| Human reviewing on someone else's repo | — | ✓ (safer default) |
| Bot reviewing on its own org repo where notifications are expected | ✓ | — |

The `/pr-review` Optional post-back step picks which sibling to
invoke based on the user's framing ("submit this review" vs "post
as pending for me to submit") or based on an explicit mode hint.

## Severity → event mapping

This skill maps `/pr-review`'s verdict / per-gate findings to the
GitHub review `event` value:

| /pr-review state | `event` value | Reasoning |
|---|---|---|
| Any ✗ in findings (severe — blocking) | **`REQUEST_CHANGES`** | Tells the dev agent "address these before merge" |
| Only ⚠ / `note:` / `ℹ️` | **`COMMENT`** | Non-blocking feedback |
| All gates CLEAN, no findings | **DO NOT post a review** | An agent should never auto-`APPROVE` — leave the PR un-reviewed and end the session |

**Never use `event: APPROVE` from an agent.** Approvals are a
trust signal that should come from a human, even in agent-driven
workflows. If `/pr-review` returns CLEAN and the calling agent
wants to record that signal somewhere, write it to a regular PR
comment ("review agent: no findings, CLEAN") via
`gh pr comment <N> --body "..."`, not as an APPROVE review.

## Prerequisites

| Requirement | Check |
|---|---|
| `gh` installed and on PATH | `gh --version` |
| Authenticated for the right host | `gh auth status` — if it fails, stop and surface to the calling agent; do not fall back to `curl` |
| Repo accessible to current `gh` user | implicit in the `gh pr view` step pr-review already ran |
| **Distinct `gh` identity is OK** | Unlike pending reviews (which are visible only to the author), submitted reviews are visible to anyone with PR read access — so the dev agent and review agent can use different gh identities |

## Phase 1 — Re-fetch head SHA

`/pr-review` already has the diff and findings. One extra read
before posting:

```powershell
gh pr view <N> --json headRefOid -q .headRefOid
```

**Unlike the pending sibling, do NOT check for existing pending
reviews by the current user.** Submitted reviews don't have the
"one pending per user" limitation — each submitted review is
independent. If the review agent has already submitted a previous
round of feedback, a new round goes in as a separate review.

## Phase 2 — Build the submitted-review JSON

For each `/pr-review` finding worth threading on a specific line:

- Resolve the **path** from the file the finding names.
- Resolve the **line** — the new-file line number at `headRefOid`.
- Format the **body** per `conventional-comments/SKILL.md`. **Apply
  the dual-format rule** — even though this is the agent-to-agent
  path, use the full label format (not chat icons), since the body
  is destined for a PR thread.
- Set **side** to `"RIGHT"` (comment on the new version of the
  file).

Determine the **event** value from the severity-to-event mapping
above (any ✗ → `REQUEST_CHANGES`, else `COMMENT`).

Write the body to a JSON file:

```json
{
  "commit_id": "<headRefOid from Phase 1>",
  "event": "COMMENT",
  "body": "Review summary: 3 ⚠, 0 ✗. Verdict: ship-able after Gate 9 fix.",
  "comments": [
    {
      "path": "app/views/dialogs/save_changes_dialog.py",
      "line": 142,
      "side": "RIGHT",
      "body": "**suggestion (non-blocking):** Extract `_should_show_save_dialog(...)` ..."
    }
  ]
}
```

**Critical:** `event` MUST be present and set to one of
`"COMMENT"` / `"REQUEST_CHANGES"` (never `"APPROVE"` from an
agent, never `"PENDING"` — that's not a valid enum value). If
`event` is omitted, the API creates a PENDING review (which is
what `github-pr-review-pending` is for) — that's the wrong
mechanic for agent-to-agent.

## Phase 3 — POST the submitted review

```powershell
gh api --method POST `
  repos/OWNER/REPO/pulls/<N>/reviews `
  --input submitted-review.json
```

Save the `id` from the response as `REVIEW_ID` (numeric). Save the
`html_url` so the calling agent can include it in any handoff
message.

**This call is irreversible.** Once submitted, the review fires
notifications and cannot be "un-submitted". You can:

- Edit individual thread bodies via REST PATCH (works for REST-created
  comments) or GraphQL `updatePullRequestReviewComment` (works for
  all) — see `github-pr-review-pending/references/REFERENCE.md` for
  the path/id rules.
- Add a **dismiss** message via `PUT .../reviews/{id}/dismissals`,
  but only an org admin can dismiss reviews on protected branches.
- Add follow-up comments to existing threads.
- Reply with another submitted review (new round of feedback).

You **cannot** delete a submitted review. The pending sibling has
a `DELETE .../reviews/{id}` for un-submitted drafts; submitted
reviews don't.

## Phase 4 — Tell the calling agent / user what's next

After the POST succeeds, output verbatim:

> Submitted review posted on PR #N (review id: <REVIEW_ID>, event: <COMMENT|REQUEST_CHANGES>).
>
> Notifications have fired. The review is now visible to anyone
> with PR read access — the dev agent can read it via
> `github-pr-review-fetch/SKILL.md` (run from a separate session
> on the PR branch).
>
> URL: <html_url>
>
> This review cannot be un-submitted. To follow up: either reply
> with another submitted review (new round), or edit individual
> thread bodies via `updatePullRequestReviewComment`.

Then stop. Do NOT try to APPROVE the PR later (per the severity
mapping rule). Do NOT call `gh pr merge` — merging is the human's
decision even in agent-driven flows.

## What this skill does NOT do

- **Never** use `event: "APPROVE"` from an agent. Use a regular
  `gh pr comment <N>` with text like "review agent: no findings,
  CLEAN" if you need to signal a clean outcome.
- **Never** call `gh pr merge` — merging is not a review action.
- **Never** dismiss someone else's submitted review.
- **Never** include secrets or PII in the review body or thread
  text (per CLAUDE.md "Never log, echo, or commit secrets"). The
  review body and thread text are visible to anyone with PR read
  access.

## Platform note — PowerShell vs zsh/bash

Same as `github-pr-review-pending`: PowerShell on Windows doesn't
need the `?` glob-escape that zsh does. For HEREDOC-style JSON
bodies, prefer the `--input <file>` pattern over inline `-f` or
`-F`.

## See also

- `pr-review/SKILL.md` — emits the findings this skill posts; its
  Optional post-back step picks between this skill and
  `github-pr-review-pending` based on whether a human is in the
  loop.
- `github-pr-review-pending/SKILL.md` — sibling for the
  human-in-the-loop case (creates pending draft, human submits).
- `github-pr-review-fetch/SKILL.md` — the **incoming** side:
  used by the dev agent to read back this skill's submitted
  reviews.
- `conventional-comments/SKILL.md` — body shape for each
  `comments[].body` and for the review-level `body`.
- GitHub REST — [Create a review for a pull request](https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request)
- GitHub REST — [Pull request reviews](https://docs.github.com/en/rest/pulls/reviews)
