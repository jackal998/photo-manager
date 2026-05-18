---
name: github-pr-review-pending
description: Post pr-review findings to a GitHub PR as a **pending (draft) review** — created with `gh api` against the `/reviews` endpoint with `event` omitted, leaving the human to click "Submit review" in the GitHub UI. Use only when invoked by pr-review's "Optional post-back" step after explicit user yes. The pending-draft POST itself is gated under CLAUDE.md "Opening PRs or pushing to a remote".
origin: local
---

# GitHub PR review — pending draft (mechanics)

photo-manager's `pr-review` skill emits findings in chat. When the user
asks to publish those findings to the PR, this skill is the **mechanic**
— it posts the findings as a **pending (draft)** GitHub review, never
submitted, so the human can read them in the GitHub UI and click
**Submit review** themselves (or discard).

This is the right behaviour for photo-manager because CLAUDE.md
explicitly gates **"Opening PRs or pushing to a remote"** — submitting
a review is a remote mutation that fires notifications. Creating a
pending draft leaves the user in control of when (or whether) it goes
out.

## Gate first — surface before POST

The pending-draft POST is itself a gated action under CLAUDE.md.
Before calling `gh api --method POST .../reviews`:

1. Confirm the PR number explicitly. If pr-review was invoked without a
   number (current branch), ask for it now.
2. Show the exact JSON body that will be POSTed (the `comments[]`
   array built from pr-review's chat findings).
3. Ask: "Create pending draft review on PR #N? You will submit it
   yourself in the GitHub UI. (yes/no)"
4. Only after explicit "yes": run the command.

If the user says no, stop. Don't propose a different mechanic — the
chat report is the deliverable, the PR post-back is an optional extra.

## Prerequisites

| Requirement | Check |
|---|---|
| `gh` installed and on PATH | `gh --version` |
| Authenticated for the right host | `gh auth status` — if it fails, stop and surface to the user; do not fall back to raw `curl` |
| Repo accessible to current `gh` user | implicit in the `gh pr view` step pr-review already ran |

## Phase 1 — Re-fetch head SHA and existing pending review

`pr-review` already has the diff and findings. Two extra reads before
posting:

```powershell
# Head SHA — required as commit_id on the POST
gh pr view <N> --json headRefOid -q .headRefOid
```

```powershell
# Existing pending review by current user?
# (Each user can have at most one PENDING review per PR at a time.)
gh api graphql -f query='
query {
  repository(owner: "OWNER", name: "REPO") {
    pullRequest(number: <N>) {
      reviews(first: 30) {
        nodes {
          id
          databaseId
          state
          author { login }
          comments(first: 100) {
            nodes { id databaseId path line body }
          }
        }
      }
    }
  }
}'
```

Filter the result for `state == "PENDING"` and `author.login == <your gh user>`.

- **No existing pending review** → continue to Phase 2 (create one).
- **Existing pending review** → add new threads to it via
  `addPullRequestReviewThread` GraphQL mutation, or update existing
  thread text via `updatePullRequestReviewComment` GraphQL mutation
  (use the node `id`, not `databaseId` — see REFERENCE.md).
  **Before adding**, scan the existing comments for any with the same
  `path` + `line` you're about to post; if one exists, update the text
  rather than duplicate the thread.

## Phase 2 — Build the pending-review JSON

For each pr-review finding worth threading on a specific line:

- Resolve the **path** from the file the finding names.
- Resolve the **line** — the new-file line number at `headRefOid`.
  The simplest path is to use the line `pr-review` already cited
  (`file:line` in the finding). If `pr-review` cited only a file,
  pick the most relevant changed line from `gh pr diff <N>`.
- Format the **body** per `conventional-comments/SKILL.md`.
- Set **side** to `"RIGHT"` (comment on the new version of the file).

Write the body to a JSON file — do **not** try to pass it inline as
`-F` or `-f`, which break on shell quoting of multi-line markdown.

```json
{
  "commit_id": "<headRefOid from Phase 1>",
  "body": "Optional draft summary — visible to humans before submit.",
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

**Critical:** `event` MUST be omitted entirely. Do **not** send
`"event": null`, `"event": ""`, or `"event": "PENDING"` — all three
either submit the review or return HTTP 422 (see REFERENCE.md
"Verified failures"). Omitting the key is what makes the review
PENDING.

## Phase 3 — POST the pending review

```powershell
gh api --method POST `
  repos/OWNER/REPO/pulls/<N>/reviews `
  --input pending-review.json
```

Save the `id` from the response as `REVIEW_ID` (numeric). If you
later need to discard the whole pending review:

```powershell
gh api --method DELETE repos/OWNER/REPO/pulls/<N>/reviews/<REVIEW_ID>
```

## Phase 4 — Tell the user what's next

After the POST succeeds, output verbatim:

> Pending review created on PR #N (review id: <REVIEW_ID>).
>
> It is **not submitted yet** — no notifications have fired. Open the
> PR's **Files changed** tab in the browser; you'll see a "Finish
> your review" button at the top. Click it to either **Submit
> review** (publishes; notifies reviewers) or **Discard pending
> review** (deletes the draft).
>
> I will **not** submit this for you. If you want to discard it from
> here, ask me to delete the pending review.

Then stop. Do NOT call `POST .../reviews/<REVIEW_ID>/events`. That
endpoint submits the review — it is the user's click in the UI, not
mine.

## What this skill does NOT do

- **Never** call `POST .../reviews/<REVIEW_ID>/events`. That endpoint
  publishes the review and fires notifications. It is the user's
  click in the GitHub UI.
- **Never** include `event` in the create-review POST body. See
  REFERENCE.md "Verified failures" for the three ways this goes
  wrong (submit-by-accident, HTTP 422 on `"PENDING"`, HTTP 422 on
  `null`).
- **Never** edit or delete someone else's comments. If you need to
  contradict a teammate's existing thread, create your own thread
  citing theirs — never edit theirs.
- **Never** call `gh pr review --approve` / `--request-changes` /
  `--comment` — these all submit immediately and bypass the
  pending-draft flow. Use `gh api` against `/reviews` instead.

## Platform note — PowerShell vs zsh/bash

The bundle this skill was adapted from warns that **zsh** treats `?`
as a glob, so `?ref=<sha>` URLs must be quoted. On the **Windows
PowerShell** environment photo-manager runs in, that quoting rule
does not apply — but double-quote URLs anyway for clarity and
cross-shell portability.

For HEREDOC-style JSON bodies, prefer the `--input <file>` pattern in
the examples above over inline `-f query='...'`, regardless of
shell. PowerShell here-strings are reliable but the per-shell
quirks are not worth the cost of cross-platform skill drift.

## See also

- `pr-review/SKILL.md` — emits the findings this skill posts.
- `conventional-comments/SKILL.md` — body shape for each
  `comments[].body`.
- `references/REFERENCE.md` — full GraphQL query for Phase 1,
  verified-failures table, and the `addPullRequestReviewThread`
  mutation shape for extending an existing pending review.
- GitHub REST — [Create a review for a pull request](https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request)
- GitHub REST — [Submit a review for a pull request](https://docs.github.com/en/rest/pulls/reviews#submit-a-review-for-a-pull-request)
  (human-only — never called by this skill)
- GitHub GraphQL — [Mutations](https://docs.github.com/en/graphql/reference/mutations)
  (`addPullRequestReviewThread`, `updatePullRequestReviewComment`)
