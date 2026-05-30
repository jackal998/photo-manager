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

## Bypass

A PR with no diff worth a one-line record (e.g. fixing a typo in a
comment, bumping a transitive dep with no behaviour change) can skip
the fragment by including the literal token `[skip-news: <reason>]`
in the PR title or body. CI enforces this; reviewers see the reason
inline.

<!-- ci-probe: A/B test for #487 — confirms test_select_dialog flake on master HEAD. Revert after diagnosis. -->

