# News fragments

This directory holds one-line changelog snippets, one file per PR. At
release time, `towncrier build` consumes the fragments and prepends a
versioned section to [`../CHANGELOG.md`](../CHANGELOG.md), then deletes
the consumed files.

See the **Adding a news fragment for your PR** section of
[`../CONTRIBUTING.md`](../CONTRIBUTING.md) for the authoring rules.

## Naming

`<PR-number>.<type>` where `<type>` is one of:

| Type      | When                                                            |
|-----------|-----------------------------------------------------------------|
| `feature` | User-visible new behaviour (new menu item, dialog, scoring)     |
| `bugfix`  | Fixes a wrong behaviour a user could hit                        |
| `doc`     | Documentation only (README, CONTRIBUTING, docs/, comments)      |
| `removal` | Removed feature, removed dependency, breaking schema change     |
| `misc`    | Refactor, CI, tooling, anything else without user-visible diff  |

Example: `news/280.feature` — the filename is the PR number, not the
issue number.

## Content

One line, present-tense imperative ("Add foo", not "Added foo"). End
with `(#<issue>)` if the work closes a tracked issue — the link is
rendered automatically by towncrier's `issue_format`.

```text
Add Execute Action preview pane (#165).
```

## Bypass — and the rule shared by all three gate tokens

A PR with no diff worth a one-line record (e.g. fixing a typo in a
comment, bumping a transitive dep with no behaviour change) can skip
the fragment by including the literal token `[skip-news: <reason>]`
in the PR title or body. CI enforces this; reviewers see the reason
inline.

This is one of three bypass tokens, and they share one rule: **the
reason is the mechanism.** It is what a reviewer reads to judge the
call, so a token that carries no reason bypasses nothing.

| Token | Gate it bypasses | Enforced by |
|---|---|---|
| `[skip-news: <reason>]` | this changelog gate | `scripts/hooks/news_guard.py` — CI only (`news-gate.yml`), because the fragment's filename needs the PR number |
| `[docs-not-needed: <reason>]` | `docs/features.md` / doc staleness | `scripts/hooks/docs_guard.py` — PreToolUse hook **and** `pr-gates.yml` |
| `[qa-not-needed: <reason>]` | `qa/scenarios/sNN_*.py` coverage | `scripts/hooks/qa_scenario_guard.py` — PreToolUse hook **and** `pr-gates.yml` |

All three reject the following, each with a message naming the problem
rather than the generic "you forgot the thing" text:

- **A blank reason** — `[skip-news:]`, `[skip-news:   ]` (#857).
- **The placeholder itself** — `[skip-news: <reason>]` pasted from a
  template or quoted in prose (#858). That is what makes it safe for
  this table, and for a brief, to spell the tokens out: documenting a
  gate must never disable it.
- **A token whose `]` never arrives** — `[skip-news: no closing
  bracket` (#858). The `news-gate` check used to be a fixed-string
  *prefix* grep, so a dangling token passed; it now says so.

Anything else is a reason, punctuation and `#refs` included — only `]`
ends the token. Keep it specific (`[skip-news: comment typo]`, not
`[skip-news: trivial]`); it is visible in review and in the CI log.
