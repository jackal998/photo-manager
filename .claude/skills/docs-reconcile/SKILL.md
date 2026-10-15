---
name: docs-reconcile
description: Reconcile the docs source of truth (docs/behaviour/rules.md and docs/features.md) against code merged since the last reconcile. Use after a merge batch, before tagging a release (v0.2.0-rc2 or later), or on "/docs-reconcile". Runs scripts/docs_reconcile.py for the mechanical part, has fresh reviewers re-verify each rule and entry at risk against HEAD, compares each PR's Docs impact claim with its diff, files or fixes findings, and appends a log entry with the reconciled SHA.
---

# docs-reconcile — catch behaviour that changed without a docs touch

PR-time gates check each PR's own claim: the `Docs impact:` line, `docs_guard`,
`docs_impact_guard`. A code-only PR whose author never touched docs changes
behaviour unseen. This skill reconciles after the fact, against the code at HEAD.

## When

- After each merge batch.
- **Before tagging a release.** A release tag (v0.2.0-rc2 or any later one) is allowed
  only when BOTH hold: a reconcile entry in `docs/reconcile-log.md` has `SHA` equal to the
  tagged commit, AND that entry's `Unresolved:` line says `none`. Any fix PR merged after
  a reconcile moves HEAD, so a new reconcile entry is needed at the new SHA before tagging.
- On `/docs-reconcile`.

## Procedure

1. **Use a clean checkout of the target commit.** The mechanical checks read the working
   tree. Fetch with `git fetch --prune origin`, then check out the master tip or the tag
   candidate in a clean worktree. Do not reconcile a dirty tree.

2. **Run the helper** and save its output to the scratchpad under a name you own:
   `python scripts/docs_reconcile.py > <scratch>/<you>_reconcile.md`.
   The default `--since` is the SHA on the last `- SHA:` line of `docs/reconcile-log.md`.
   Override with `--since <sha>`; `--until <rev>` defaults to HEAD. Exit 1 means a
   mechanical check in section (f) failed: fix that first, then rerun.

3. **Rules (sections c, d and d2).** Every rule listed in (c), (d) or (d2) gets a FRESH
   reviewer verdict: `general-purpose`, `model: opus`, read-only. Section (d2), the
   unpinned rules, is always included, flagged or not, because nothing else would notice
   them going stale. Batch by area if you like, but each rule gets its own verdict. Give
   the reviewer the rule block from `docs/behaviour/rules.md`, its owning-code paths, its
   pins, and the commit range. Do not give it your reasoning. It judges the rule sentence
   against the code at HEAD, and greps for the rule's symbols outside the owning list
   when the verdict depends on it. Each rule gets one verdict with `file:line` evidence:
   `HOLDS` / `FALSE` (say what the code does) / `UNCLEAR` (say what is missing).
   A pin that no longer asserts the rule is a finding even when its name still resolves.

4. **Stale-candidate entries (section e).** Ask a fresh reviewer to read each listed
   entry against the cited source and return `still accurate` or `stale: <sentence>`.
   Section e is best effort: it sees only entries that cite source paths.

5. **Each PR in range (sections a and b).** Read its declared impact:
   `gh pr view <N> --json body --jq .body`, then the `Docs impact:` line.
   - A PR that declares `none`, or names entries its diff did not touch, is a finding.
     A PR that names an entry the diff did not touch is a finding too.
   - A PR merged before #970 has no such line. Record it as "no line (pre-#970)".
   - **Every `[behaviour-bearing]` PR** (section b marks it: it touches `app/`, `core/`,
     `infrastructure/`, `scanner/` or `frontend/src/`) also gets a fresh reviewer against
     the register, not only against features.md. The checklist for that reviewer:
     1. Which behaviour does this PR change that a register rule describes? Judge each
        such rule as in step 3, even if the rule is absent from sections (c) and (d).
     2. Which features entries describe the changed surface? Read each one.
     3. Does the PR's `Docs impact:` line name exactly those entries? `none` is correct
        only when neither 1 nor 2 applies.
     4. Is there user-visible behaviour with no register rule and no entry? That is a
        finding: add the rule or the entry.
     The reviewer judges from the diff and the register and features text, not from the
     author's reasoning.

6. **File or fix each finding.**
   - A false rule is fixed in `docs/behaviour/rules.md`, the register, never in a copy.
     Then run `python scripts/check_rules_register.py`.
   - A stale entry is fixed in its area file `docs/features/<area>.md`. If you change a heading or an
     `Area:` line, run `python scripts/gen_features_index.py --check`.
   - A finding not fixed in this pass is filed with `github-issue-create` (that skill's
     gate applies: explicit yes first). Deferred work is never silently dropped. Record
     each such issue number in the log entry's `Unresolved:` line.
   - Fixes go through `github-pr-create` as their own PR with a real `Docs impact:` line.

7. **Append a log entry** to `docs/reconcile-log.md`, at the end, never editing an old one:

   ```
   ## YYYY-MM-DD — <short label>
   - SHA: <full SHA of --until, the commit reconciled>
   - Reviewer: <who ran it>
   - Result: <rules HOLDS/FALSE/UNCLEAR counts; stale entries; PRs with a mismatched Docs impact; issues filed; fix PRs>
   - Unresolved: <none, or each open FALSE/UNCLEAR finding with its issue number>
   ```

   The entry may be committed after the SHA it names. It must be on the master branch
   when the tag is cut, and the tag points at the SHA it names. A tag is cut only when
   that entry's `Unresolved:` line is `none`.

## Limits

- **What the helper cannot see.** Sections (c) and (d) match owning paths, not behaviour.
  A rule whose behaviour comes from a file it does not list is in neither section. Example:
  R-LOCK-1's 409 mapping lives in `app/web/routes/action.py`, which is not one of its owning
  paths, so a change there does not appear in (c) or (d).
- **Nets that remain.** A pinned rule usually has a net: when its behaviour changes, its
  pin breaks in CI, and a changed pin file shows in (d). That net holds only as far as the
  pin asserts the behaviour. An unpinned rule has no net at all, which is why step 3 always
  re-verifies every (d2) rule. Step 5 is the only check that reads PR diffs for behaviour
  the register does not cover.
- Section (e) judges nothing by itself. It only proposes candidates from cited paths.
- The helper runs the existing checks and reports them. It does not fix anything.

## Before committing this skill

Project skills are PII-audited before commit. Run the grep given under "PII audit" in
`.claude/rules/skills-and-setup.md` on this SKILL.md; any real match (a home path, an IP,
a credential-shaped literal) blocks the commit. Do not copy that pattern into this file.
