# github-pr-review-pending — Reference

Detailed mechanics that don't belong in the SKILL.md main body.

## Phase 1 — Full GraphQL query (list reviews + comments)

Used to detect an existing PENDING review by the current `gh` user
and fetch the comment node ids needed for in-place edits.

```bash
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
          body
          submittedAt
          commit { oid }
          comments(first: 100) {
            nodes {
              id
              databaseId
              url
              body
              path
              line
              originalLine
              diffHunk
              author { login }
            }
          }
        }
      }
    }
  }
}'
```

### Result usage

| Need | Use |
|---|---|
| Pending reviews only | `reviews.nodes[] \| select(.state == "PENDING")` |
| Your pending draft threads | Also filter `author.login == <your gh user>` on the review |
| Numeric REST id (for `DELETE .../reviews/{id}`) | `databaseId` on the review |
| Numeric comment id (for REST `PATCH .../pulls/comments/{id}` — only works on REST-created comments) | `databaseId` on each `comments.nodes[]` |
| GraphQL mutation id (for `updatePullRequestReviewComment` — works for all comments incl. GraphQL-created) | `id` (global node id) on each `comments.nodes[]` |

Always request **both** `id` and `databaseId` in the same query. You
don't know in advance which mutation path you'll need, and a re-fetch
to get the other field is a wasted round-trip.

## Adding a new thread to an existing pending review

When Phase 1 finds an existing PENDING review by you, you cannot
"add to it" via `POST .../reviews` — that endpoint creates a new
review, and there can only be one pending review per user per PR.
Use GraphQL `addPullRequestReviewThread` instead:

```graphql
mutation {
  addPullRequestReviewThread(input: {
    pullRequestReviewId: "<review node id from Phase 1>"
    path: "app/views/dialogs/save_changes_dialog.py"
    line: 142
    side: RIGHT
    body: "**suggestion (non-blocking):** ..."
  }) {
    thread { id }
  }
}
```

Notes:

- **`pullRequestReviewId` is the GraphQL node `id`**, not
  `databaseId`. Easy to confuse — the GraphQL mutation will reject
  the numeric form.
- `addPullRequestReviewComment` is a different mutation that uses
  `position` (diff-offset integer) and has no `line` argument. **Do
  not use it** — it returns HTTP 422 when given line-anchored data.

## Editing an existing pending thread's text

Two paths, depending on how the thread was created:

| Thread created via | Edit via | Id to use |
|---|---|---|
| REST `POST .../reviews` (with `comments[]`) | REST `PATCH .../pulls/comments/{id}` | numeric `databaseId` |
| GraphQL `addPullRequestReviewThread` | GraphQL `updatePullRequestReviewComment` | global node `id` |

The cross-pattern fails with HTTP 404 — REST PATCH on a
GraphQL-created comment returns "Not Found" because the REST view of
that comment is incomplete while the review is still PENDING.

```bash
# REST path (thread originally created via REST)
gh api --method PATCH "repos/OWNER/REPO/pulls/comments/<databaseId>" \
  -f body='**suggestion (non-blocking):** revised text.'
```

```graphql
# GraphQL path (thread originally created via GraphQL)
mutation {
  updatePullRequestReviewComment(input: {
    pullRequestReviewCommentId: "<node id>"
    body: "**suggestion (non-blocking):** revised text."
  }) {
    pullRequestReviewComment { id }
  }
}
```

## Verified failures (do not retry the same way)

| Attempt | Result | Why it fails |
|---|---|---|
| `POST .../reviews` body has `"event": "PENDING"` | **HTTP 422** | `PENDING` is not a valid `event` enum value. Pending reviews are created by **omitting** the `event` key, not by sending a string. |
| `POST .../reviews` body has `"event": null` | **HTTP 422** | `null` is not a valid enum value either. Omit the key. |
| `POST .../reviews` body has `"event": "COMMENT"` | **Review is SUBMITTED, not pending** | Any non-null `event` (`COMMENT`, `APPROVE`, `REQUEST_CHANGES`) submits the review in the same call. This is the failure mode this skill exists to prevent. |
| `gh api ... -F comments='[{"path":...}]'` | **HTTP 422** — `comments` "is not an array" | `-F` sends the value as a string. The API expects a JSON array. Use `--input <file>` with a real JSON file. |
| `GET .../pulls/{n}/comments` returns `[]` while the PR shows a pending review with threads in the UI | **Expected** | Draft threads attached to a PENDING review do not appear in the REST comments list. Use the GraphQL query above instead. |
| REST `PATCH .../pulls/comments/<databaseId>` on a comment created via `addPullRequestReviewThread` | **HTTP 404** | The REST view of GraphQL-created comments is incomplete while pending. Use GraphQL `updatePullRequestReviewComment` with the node `id`. |
| `gh pr review --comment --body "..."` | **Review is SUBMITTED, not pending** | The `gh pr review` family always submits. Use `gh api --method POST .../reviews` with the JSON shape in SKILL.md Phase 2 instead. |

## Discard the whole pending review

If the user changes their mind after the POST:

```bash
gh api --method DELETE "repos/OWNER/REPO/pulls/<N>/reviews/<REVIEW_ID>"
```

This removes the pending review and all its draft threads in one
call. Notifications do not fire. Confirm with the user before
deleting — even though it's "just a draft", it represents your
findings being discarded.

## What this skill never does

- `POST .../pulls/<N>/reviews/<REVIEW_ID>/events` — submits the
  review. **Human only.**
- `gh pr review --approve / --request-changes / --comment` — all
  three submit immediately.
- Edit or delete another user's comments — even on a pending
  review of your own. Different ownership.

## Source

Adapted from publicly-documented GitHub REST and GraphQL behaviour
(see SKILL.md "See also") plus the verified-failures table assembled
from the same APIs during exploration.
