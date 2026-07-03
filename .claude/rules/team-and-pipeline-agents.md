# Team mode & pipeline agents — full detail

> Read when running `/pr-review` in team mode, or `/work` that spawns the
> researcher / developer / qa pipeline agents. Core rule: teammates are
> evidence-producers, not decision-makers; only LEAD writes to remotes and
> LEAD still owns every security gate (see the root `CLAUDE.md` gated-actions
> list, which applies unchanged here).

## Team mode discipline

Anthropic's experimental Agent Teams feature is opt-in per
`/pr-review` invocation. When team mode is enabled the LEAD session
spawns up to three teammates from `.claude/agents/` to apply the
pr-review gates in parallel:

- `docs-reviewer` — Gates 2+3 (features.md drift, qa scenario coverage)
- `app-security-reviewer` — Gate 7 (app-level security patterns)
- `quality-reviewer` — Gates 8+9+10 (migrations, scanner perf, test padding)

The discipline below applies whenever team mode is active.

### Security gates still belong to LEAD

A teammate's recommendation does **not** satisfy the per-action "yes"
gate. The Security gates list applies unchanged: even if all
three teammates report CLEAN, LEAD must still surface and get
explicit "yes" before any install, history-rewriting `git` op, or
PR/issue close/merge. Teammates are evidence-producers, not
decision-makers.

### Only LEAD writes to remotes

Each teammate's permission constraints block remote-write and
install commands. If a teammate suggests a change that would require
one of these actions, it describes the action in findings — LEAD
decides whether to surface the gate and ask. Teammates never:

- run `git push` / `git reset --hard` / `git rebase` / anything that
  writes to a remote
- run `gh pr *` / `gh issue *` / `gh api .../reviews` (with or
  without `event`) — including the `-pending` and `-submitted` review
  posting that's auto-approved for LEAD
- run `pip install` / `npm install` / `git clone <url>`
- modify source files, tests, hooks, settings, or docs in-place

### Team mode is opt-in per invocation

The default `/pr-review` mode is single-session. Team mode is
explicit (user types something like "/pr-review team" or the calling
context enables it). Token cost is roughly 4× single-session for a
three-teammate run, so team mode should decline on small PRs (≤5
behaviour-bearing files OR ≤300 diff lines) and on PRs whose Gate 1
classifier short-circuits to CLEAN.

### Project agents shadow user-level — use distinct names

Project `.claude/agents/<name>.md` definitions shadow user-level
`~/.claude/agents/<name>.md` of the same name. To avoid silent
shadow, the project's security teammate is named
`app-security-reviewer` (not `security-reviewer`) — the user-level
generic-OWASP `security-reviewer` remains unshadowed and reachable
for ad-hoc invocations.

### Pipeline agents (spawned by /work)

Three additional agents live in `.claude/agents/` and are invoked by the
`/work` skill only — not by `/pr-review` team mode.

| File | Role | Spawned by |
|---|---|---|
| `researcher-agent.md` | 3-angle read-only investigator | `/work` Phase 1 |
| `developer-agent.md` | Worktree-isolated implementation | `/work` Phase 4 (complex) |
| `qa-agent.md` | Post-implementation validator | `/work` Phase 4 (complex) |

These agents never commit, push, or open PRs — LEAD owns all git operations.

### Hook wiring (one-time, per machine)

`.claude/settings.json` (gitignored) is where team-event hooks are
wired. The three scripts ship in
`scripts/hooks/team_task_{created,completed}.py` and
`scripts/hooks/team_teammate_idle.py`. The `TaskCreate` and
`TaskUpdate` matchers are now wired in `.claude/settings.json.example`
as PreToolUse hooks — copy the example to bootstrap a new machine.
`team_teammate_idle.py` requires a `TeammateIdle` event type whose
payload schema is not yet documented upstream; wire it separately once
Claude Code's team-event dispatch is confirmed. All three scripts sniff
known key paths and fail open on unrecognised shapes — safe to have
active before the schema stabilises.
