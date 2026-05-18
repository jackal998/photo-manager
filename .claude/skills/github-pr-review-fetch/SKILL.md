---
name: github-pr-review-fetch
description: Read submitted review feedback from a GitHub PR into the current session, structured for an agent to address each finding. Use when a dev agent resumes work on a PR after a separate review agent (or human reviewer) has posted findings — this skill fetches all submitted reviews, all line-anchored review threads, all issue-style PR comments, and presents them as a structured to-do list. Inbound counterpart to github-pr-review-pending / github-pr-review-submitted (which are outbound).
origin: local
---

# GitHub PR review — fetch incoming feedback

This is the **inbound** counterpart to the two outbound posting
skills (`github-pr-review-pending`, `github-pr-review-submitted`).
Use it when a dev agent resumes work on a PR and needs to
**ingest review feedback** that was posted by a separate review
agent (or by a human reviewer). The skill fetches:

1. All **submitted reviews** on the PR (state = `COMMENTED`,
   `CHANGES_REQUESTED`, `APPROVED`, or `DISMISSED`), with the
   review-level body and the `event` value.
2. All **line-anchored review threads** (path + line + body +
   resolution state), so the dev agent knows which file:line
   each finding refers to.
3. All **issue-style PR comments** (not tied to a line — `gh pr
   comment` style messages) for general handoff text.
4. Optionally, the **calling agent's own pending draft review**
   (if any) — so it can be aware of in-flight work.

Output is a structured chat report that the dev agent reads as
a to-do list: address each unresolved thread, push fixes, loop.

## When to invoke

Run from the dev agent's session after `git pull` or after a
review round has been requested. Typical scenarios:

- Dev agent finished work, pushed, and a review agent posted
  feedback. Dev agent restarts session, runs this skill.
- Long-running dev session — periodically poll for new review
  feedback during multi-round iteration.
- Human reviewer left feedback on a PR; dev agent (or you, the
  user) wants to see all findings at once instead of clicking
  through GitHub.
- After running `/pr-review <N>` on your own branch as a
  self-check, this skill reads back any submitted reviews from
  others.

Do NOT use this skill to fetch the *diff* — `gh pr diff` is the
right call for that. This skill is for **feedback on the diff**,
not the diff itself.

## Prerequisites

| Requirement | Check |
|---|---|
| `gh` installed and on PATH | `gh --version` |
| Authenticated for the right host | `gh auth status` — if it fails, stop and tell the user |
| Read access to the PR | implicit; works for public repos or repos the gh user can read |

## Invocation contract

```
github-pr-review-fetch <PR-number>
github-pr-review-fetch                          # current branch's PR, resolved via `gh pr view --json number`
github-pr-review-fetch <PR-number> --since <ISO-date>   # only feedback newer than this timestamp
github-pr-review-fetch <PR-number> --unresolved-only    # filter to threads where isResolved=false
```

`<PR-number>` is optional when invoked from a branch with an open
PR; resolved via `gh pr view --json number -q .number` from the
current branch.

## Phase 1 — Resolve PR number and identity

```powershell
# PR number (skip if user supplied one explicitly)
$prNumber = gh pr view --json number -q .number

# Current gh identity — used to filter "MY pending draft" vs "others' submitted reviews"
$me = gh api user -q .login
```

If `gh pr view` fails ("no pull request found for branch"), the
current branch doesn't have an open PR. Surface the error and
exit — there's nothing to fetch.

## Phase 2 — Parallel fetch (fire all four at once)

Run these four `gh` calls **in parallel** (single message,
multiple tool calls — they have no dependencies):

### A. Submitted reviews + thread state (GraphQL)

```bash
gh api graphql -f query='
query {
  repository(owner: "OWNER", name: "REPO") {
    pullRequest(number: <N>) {
      reviews(first: 50) {
        nodes {
          id
          databaseId
          state            # PENDING / COMMENTED / CHANGES_REQUESTED / APPROVED / DISMISSED
          author { login }
          body
          submittedAt
          commit { oid }
          comments(first: 100) {
            nodes {
              id
              databaseId
              path
              line
              originalLine
              body
              author { login }
              createdAt
            }
          }
        }
      }
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          comments(first: 50) {
            nodes { databaseId author { login } body createdAt }
          }
        }
      }
    }
  }
}'
```

`reviewThreads` and `reviews.comments` overlap — both describe
the same line-anchored comments, but `reviewThreads` includes
`isResolved` / `isOutdated` while `reviews` includes the
review-level `state` and `body`. Use `reviewThreads` for
resolution filtering, `reviews` for the review-level context.

### B. Issue-style PR comments (REST)

```bash
gh api --paginate "repos/OWNER/REPO/issues/<N>/comments"
```

These are the `gh pr comment <N> --body ...` messages — NOT
line-anchored, attached to the PR conversation tab. Useful for
handoff messages between agents ("review agent: round 2 complete,
3 ⚠ remaining"; "dev agent: addressed thread #4, please re-review").

### C. PR metadata + head SHA

```powershell
gh pr view <N> --json title,body,state,isDraft,mergeable,headRefOid,baseRefName,headRefName,reviewDecision,closingIssuesReferences
```

`reviewDecision` is `APPROVED` / `REVIEW_REQUIRED` /
`CHANGES_REQUESTED` — useful as a one-line status. `headRefOid`
lets the dev agent compare what was reviewed vs what's currently
on the branch (have any commits landed since the last review?).

### D. Recent check / CI runs (optional, GraphQL)

Skip unless the user explicitly wants CI signal alongside review
findings. When invoked:

```bash
gh pr checks <N>
```

## Phase 3 — Filter and structure

Apply filters in this order:

1. **Drop pending reviews authored by OTHER users.** Per GitHub's
   visibility model, a pending review by user X is only visible to
   X. If the GraphQL query returns pending reviews by users other
   than `$me`, that's a GitHub bug or a permission anomaly — skip
   them and log a `note:`.

2. **Keep the current user's own pending review** (if any) as a
   separate `## My in-flight pending draft` section — surface it
   so the dev agent doesn't forget about its own un-submitted
   feedback (rare in agent-driven flows, but possible).

3. **Apply `--since <ISO-date>` filter** if supplied: drop
   reviews and threads with `submittedAt` / `createdAt` older
   than the cutoff.

4. **Apply `--unresolved-only` filter** if supplied: drop
   threads where `isResolved == true`.

5. **Sort threads** by file path, then line number. Sort reviews
   by `submittedAt` descending (newest first).

## Phase 4 — Emit structured report

The output is **the entire point** of this skill — it must be
structured so the dev agent can iterate over findings without
re-querying. Use this template:

```
PR review feedback — #<N> (<PR title>)
Status: <reviewDecision>   |   Reviewers: <comma-separated unique authors>
Head: <headRefOid>   |   Last review: <submittedAt of newest>

## Review-level summary

### <reviewer-login> — <state>, submitted <submittedAt>
> <review.body, quoted, first 5 lines>

### <reviewer-login-2> — <state>, submitted <submittedAt>
> <review.body, quoted, first 5 lines>

## Unresolved threads (<count>)

### 1. <path>:<line> — by <author>, <createdAt>
**State:** unresolved <isOutdated ? "(outdated)" : "">
**Body:**
> <body, quoted in full>

**Replies (<N>):**
- <author>, <createdAt>: <body summary>

### 2. <path>:<line> — by <author>, <createdAt>
...

## Resolved threads (<count>)

(Listed in same format but compressed — one line per thread:
`<path>:<line> — <author>: <body first 80 chars> [resolved]`)

## Issue-style PR comments (<count>)

- <author>, <createdAt>: <body summary>
- <author>, <createdAt>: <body summary>

## My in-flight pending draft (if any)

(If `$me` has a pending review, name it here so the agent knows
it has un-submitted work.)

## Suggested next steps

For each unresolved thread, the dev agent should:
1. Read the cited file:line in full.
2. Decide: address (commit a fix) / disagree (reply with rationale) / defer (mark for follow-up).
3. After commit, mark the thread resolved via GraphQL `resolveReviewThread`.

For `CHANGES_REQUESTED` reviews: address every unresolved thread before requesting re-review.
For `COMMENTED` reviews: dev agent's call — non-blocking findings can be deferred or noted.
```

**Omit empty sections** (same convention as `pr-review/SKILL.md`).

## What this skill does NOT do

- **Does not address** the findings. It only fetches and
  structures. The dev agent reads the output and decides what to
  fix.
- **Does not resolve threads.** Resolution is a separate action —
  after the dev agent addresses a thread, it (or the calling
  user) calls GraphQL `resolveReviewThread` with the thread's
  node `id`. This skill surfaces the IDs but doesn't mutate.
- **Does not reply** to threads or post any comment. Replying is
  a write action; this skill is read-only.
- **Does not re-request review.** That's `gh pr review --request
  <user>` — separate action, user's call.
- **Does not fetch the diff.** Use `gh pr diff <N>` for that. This
  skill is for **feedback on the diff**.

## Resolving threads after fixing (related — separate action)

When the dev agent has addressed a thread, mark it resolved:

```bash
gh api graphql -f query='
mutation {
  resolveReviewThread(input: { threadId: "<thread node id from Phase 2 A>" }) {
    thread { id isResolved }
  }
}'
```

This call is the responsibility of the dev agent's *fix-and-push*
flow, not this skill. Mention the thread `id`s in the report
output so the calling agent has them ready.

## See also

- `github-pr-review-pending/SKILL.md` — outbound, human-in-loop case.
- `github-pr-review-submitted/SKILL.md` — outbound, agent-to-agent case;
  what this skill reads back when the upstream review agent used
  it to post.
- `pr-review/SKILL.md` — produces the findings the upstream agent
  posts.
- `conventional-comments/SKILL.md` — the format the bodies are
  written in; this skill parses them but doesn't re-format.
- GitHub GraphQL — [PullRequest review APIs](https://docs.github.com/en/graphql/reference/objects#pullrequest)
- GitHub REST — [Pull request review comments](https://docs.github.com/en/rest/pulls/comments)
