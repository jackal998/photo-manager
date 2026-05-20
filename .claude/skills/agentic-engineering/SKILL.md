---
name: agentic-engineering
description: Reference for multi-agent coordination in this project — when to use Agent Teams vs subagents, the verified teammate lifecycle on Windows CLI, /pr-review team-mode integration, hook wiring, and known limits as of 2026-05-20.
origin: local
---

# Agentic engineering — Agent Teams vs subagents

Two coordination primitives are available in Claude Code:

1. **Subagents** — the `Agent` tool, delegate tasks within the calling
   session's context window. Lighter, faster, no extra cost multiplier.
2. **Agent Teams** — experimental, CLI-only. Independent Claude Code
   processes coordinated by a lead session via shared task list and
   direct-message mailbox. True parallelism, ~4× cost for a
   3-teammate run.

## Decision heuristic

| Signal | Use |
|---|---|
| Work fits in one context window | Subagents |
| Delegate needs full Claude Code tool access per lane | Agent Teams |
| Each lane invokes its own sub-skills and takes > ~1 min | Agent Teams |
| Short research / targeted file read | Subagents |
| Cost is a concern on a small PR | Subagents (auto-decline in /pr-review team) |

For this project:

| Workflow | Choice | Reason |
|---|---|---|
| `/pr-review` gates 2+3, 7, 8+9+10 | **Agent Teams** (opt-in `team` flag) | Each lane runs a sub-skill rubric; sequential wall-clock ~3× longer |
| `/qa-explore` scenarios | **Deferred** | Blocked on per-teammate `PHOTO_MANAGER_HOME` isolation |
| Research / grep during a task | **Subagents** (Explore, general-purpose) | Single-pass, within-session |
| Ad-hoc security review | **Subagents** (security-reviewer) | Sequential, fits in LEAD's session |

## Agent Teams — verified mechanics (Windows CLI, 2026-05-20)

### Team lifecycle

```
TeamCreate(name="<team-name>")

# Spawn teammates in one message (parallel start):
Agent(team_name=..., name="docs-reviewer",        subagent_type="docs-reviewer",       prompt=...)
Agent(team_name=..., name="app-security-reviewer", subagent_type="app-security-reviewer", prompt=...)
Agent(team_name=..., name="quality-reviewer",      subagent_type="quality-reviewer",    prompt=...)

# Assign work:
TaskCreate(team_name=..., assignee="docs-reviewer",       subject="Gates 2+3: ...")
TaskCreate(team_name=..., assignee="app-security-reviewer", subject="Gate 7: ...")
TaskCreate(team_name=..., assignee="quality-reviewer",    subject="Gates 8+9+10: ...")

# Collect findings via <teammate-message> turns (no polling needed)

# Graceful shutdown — one teammate at a time:
SendMessage(to="docs-reviewer",       message={"type": "shutdown_request"})
# wait for shutdown_approved + teammate_terminated
SendMessage(to="app-security-reviewer", message={"type": "shutdown_request"})
# wait …
SendMessage(to="quality-reviewer",    message={"type": "shutdown_request"})
# wait …

TeamDelete(name="<team-name>")   # only after all terminations confirmed
```

### Communication

- Teammate → LEAD: `SendMessage(to="LEAD", message={...})` — arrives as
  a `<teammate-message>` turn in LEAD's conversation (no inbox poll needed)
- LEAD → teammate: `SendMessage(to="<name>", message={...})`
- Shutdown: send `{"type": "shutdown_request"}`, wait for both
  `shutdown_approved` (from teammate) and `teammate_terminated` (system
  event) before calling `TeamDelete`

### Windows in-process backend

- `backendType: "in-process"`, `tmuxPaneId: "in-process"` — no tmux
- Team config: `~/.claude/teams/<name>/config.json`
- Task list: `~/.claude/tasks/<name>/`
- `TeamDelete` cleans both; **fails if any member is still active**
- Permission prompts from teammates surface to the LEAD terminal — a
  stalled prompt can freeze the pipeline; `TeammateIdle` hook logs > 180 s
  idle to `.claude/team_idle.log`

## Teammate definitions for this project

Project agent definitions in `.claude/agents/<name>.md` shadow
user-level `~/.claude/agents/<name>.md` of the same name.

| File | Teammate | Gates |
|---|---|---|
| `.claude/agents/docs-reviewer.md` | `docs-reviewer` | /pr-review Gates 2+3 |
| `.claude/agents/app-security-reviewer.md` | `app-security-reviewer` | /pr-review Gate 7 |
| `.claude/agents/quality-reviewer.md` | `quality-reviewer` | /pr-review Gates 8+9+10 |

**Naming:** `app-security-reviewer` (not `security-reviewer`) avoids
silently shadowing the user-level generic-OWASP agent. See
`CLAUDE.md § Team mode discipline`.

## Hook wiring

Three scripts enforce team-mode discipline on Claude's tool calls:

| Script | Trigger | What it guards |
|---|---|---|
| `scripts/hooks/team_task_created.py` | TaskCreate | Subject must match a known gate pattern or carry `[team-task-freeform: <reason>]` |
| `scripts/hooks/team_task_completed.py` | TaskUpdate (status=completed) | Gate tasks must include structured findings or explicit CLEAN; blocks silent empty completions |
| `scripts/hooks/team_teammate_idle.py` | TeammateIdle | Writes one log line to `.claude/team_idle.log` when idle > 180 s; never blocks |

Wire in `.claude/settings.json` (gitignored). Template: `.claude/settings.json.example`.

## /pr-review team mode integration

Invocation: `/pr-review team` or `/pr-review <N> team`.

Auto-declines (falls back to single-session) when Gate 1 classifies
≤ 5 behaviour-bearing files or ≤ 300 diff lines. Full protocol in
`.claude/skills/pr-review/SKILL.md § Team mode (opt-in)`.

## Known limits (2026-05-20)

- **Event schema unverified.** `TeammateIdle`, `TaskCreated`,
  `TaskCompleted` payload shapes are not yet documented by Anthropic.
  Hook scripts sniff multiple known key paths and fail-open on
  unrecognised shapes — safe to ship, validate later.
- **Agent definition hot-load.** Whether `.claude/agents/` picks up
  new definitions without a CLI restart is unverified. If a spawn fails
  with an unknown-agent error, restart the Claude Code session.
- **`/qa-explore` isolation deferred.** Each teammate inherits LEAD's
  working directory; parallel teammates would race on `qa/window_state.ini`,
  `qa/settings.json`, `qa/run-manifest.sqlite`. Track in a follow-up
  issue — do not attempt `/qa-explore` team mode until isolation lands.
