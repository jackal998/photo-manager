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
- **Before tagging a release.** A release tag (v0.2.0-rc2 or any later one) requires a
  reconcile entry in `docs/reconcile-log.md` whose `SHA` equals the tagged commit. No
  such entry means no tag: run this skill first.
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

3. **Rules at risk (sections c and d).** For each rule ID, dispatch a FRESH reviewer
   (`general-purpose`, `model: opus`, read-only). Give it the rule block from
   `docs/behaviour/rules.md`, its owning-code paths, its pins, and the commit range.
   Do not give it your reasoning. It judges the rule sentence against the code at HEAD,
   and greps for the rule's symbols outside the owning list when the verdict depends on
   it. Each rule gets one verdict with `file:line` evidence:
   `HOLDS` / `FALSE` (say what the code does) / `UNCLEAR` (say what is missing).
   A pin that no longer asserts the rule is a finding even when its name still resolves.

4. **Stale-candidate entries (section e).** Ask a fresh reviewer to read each listed
   entry against the cited source and return `still accurate` or `stale: <sentence>`.
   Section e is best effort: it sees only entries that cite source paths.

5. **Each PR in range (sections a and b).** Read its declared impact:
   `gh pr view <N> --json body --jq .body`, then the `Docs impact:` line.
   - A behaviour-bearing PR that declares `none`, or names entries its diff did not
     touch, is a finding.
   - A PR that names an entry the diff did not touch is a finding.
   - A PR merged before #970 has no such line. Record it as "no line (pre-#970)"; judge
     its changed behaviour files against the entries instead.

6. **File or fix each finding.**
   - A false rule is fixed in `docs/behaviour/rules.md`, the register, never in a copy.
     Then run `python scripts/check_rules_register.py`.
   - A stale entry is fixed in `docs/features.md`. If you change a heading or an
     `Area:` line, run `python scripts/gen_features_index.py --check`.
   - A finding not fixed in this pass is filed with `github-issue-create` (that skill's
     gate applies: explicit yes first). Deferred work is never silently dropped.
   - Fixes go through `github-pr-create` as their own PR with a real `Docs impact:` line.

7. **Append a log entry** to `docs/reconcile-log.md`, at the end, never editing an old one:

   ```
   ## YYYY-MM-DD — <short label>
   - SHA: <full SHA of --until, the commit reconciled>
   - Reviewer: <who ran it>
   - Result: <rules HOLDS/FALSE/UNCLEAR; stale entries; PRs with a mismatched Docs impact; issues filed; fix PR>
   ```

   The entry may be committed after the SHA it names. It must be on the master branch
   when the tag is cut, and the tag points at the SHA it names.

## Limits

- Sections c and d match owning paths, not behaviour. A rule whose behaviour lives in a
  file it does not list is missed by the helper; step 3's grep is the backstop.
- Section e judges nothing by itself. It only proposes candidates from cited paths.
- The helper runs the existing checks and reports them. It does not fix anything.

## Before committing this skill

Project skills are PII-audited before commit. Run the grep given under "PII audit" in
`.claude/rules/skills-and-setup.md` on this SKILL.md; any real match (a home path, an IP,
a credential-shaped literal) blocks the commit. Do not copy that pattern into this file.
