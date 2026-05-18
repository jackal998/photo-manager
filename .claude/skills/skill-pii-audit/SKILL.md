---
name: skill-pii-audit
description: Audit project skill files for PII and credential-shaped literals. Use when /pr-review's diff adds or modifies files under .claude/skills/<name>/ — this skill scans for absolute home paths (Windows / macOS / Linux), IPv4 addresses (NAS / Synology / VPN), and credential-shaped literals (GitHub / AWS / JWT / generic high-entropy near key=/token=/password=). Honors the critical pattern-description-vs-literal filter rule.
origin: local
---

# skill-pii-audit — Gate 11 rubric

Invoked by `/pr-review` Gate 11 when the diff adds or modifies
files under `.claude/skills/<name>/` (NOT `.claude/skills/personal/<name>/`
— that path is gitignored by design). Enforces what CLAUDE.md's
"PII audit before committing a project skill" rule asks the
author to do manually.

## When to invoke

`/pr-review` invokes this skill when the diff adds or modifies
files matching:

- `.claude/skills/<name>/**` where `<name>` is NOT `personal`

Skip entirely otherwise. The `personal/` subdirectory is
gitignored — Gate 11 doesn't reach into it.

## Patterns to flag

For each added/modified line in those files, look for:

### Absolute home paths

`C:\Users\<name>\…`, `/Users/<name>/…`, `/home/<name>/…`. A real
path leaks the author's username. Placeholders (`<USER>`, `~/`,
`$HOME`, `%USERPROFILE%`) are fine.

### IPv4 addresses

`\d+\.\d+\.\d+\.\d+`. Most often NAS, Synology, or VPN
endpoints. Filter out:

- Software version numbers (`1.0.0.0`, `pyqt6 6.6.1`)
- RFC 5737 documentation IPs (`192.0.2.0/24`, `198.51.100.0/24`,
  `203.0.113.0/24`)
- RFC 1918 ranges *in commented examples* (`192.168.0.0/24` in
  "block this subnet" context — fine; bare `192.168.1.42` as a
  config target — flag)

### Credential-shaped literals

Tokens with provider-specific prefixes — `ghp_…` (36+ chars),
`AKIA[0-9A-Z]{16}`, JWT (three dot-separated base64 segments
≥10 chars each), generic ≥32-char high-entropy strings next to
`key=` / `token=` / `password=` / `secret=`.

## Critical filtering rule — pattern descriptions are NOT literal values

A skill that documents its own scan (like `app-security-patterns`'
pattern list, or a regex example in a README) will contain text
like `password\s*=\s*["'][^"']+["']` — that's the REGEX, not a
hardcoded password. **DO NOT flag pattern descriptions.**

Specifically, do NOT flag:

- Pattern strings inside backticks / code blocks that describe
  what to LOOK for (regex literals, glob patterns, CLI
  invocations).
- Variable names containing the trigger word: `LOCK_KEY`,
  `auth_token_param`, `password_field`, `api_key_setting`.
- Test-fixture placeholders: `"test-key"`, `"dummy-token"`,
  `"changeme"`, `"…"`, `"<your-token-here>"`.
- Comments referencing the concept: `# Don't commit secrets`,
  `// API key goes in env var`.
- Strings whose value is obviously a category label, not a
  credential: `"key=value"` (a format-string description),
  `"password"` (a UI label).

When in doubt, surface the match in chat and ASK the user: "line
N matches pattern X — placeholder or real?" rather than silently
flagging or silently dismissing. This is one of the rare gates
where false-positive-aversion AND false-negative-aversion BOTH
matter (an unflagged real token is catastrophic; a noisy false
flag erodes trust).

## Severity escalation

- ✗ for a confirmed-real GitHub / AWS / Slack token. Recommend
  rotate-then-force-push-to-scrub. Don't ship.
- ⚠ for likely-real home path / IP / generic credential shape.
- ℹ️ for "matches pattern but probably FP — confirm please".

## Output format

Emit findings under the `## PII audit (Gate 11)` section of
pr-review's chat report:

```
⚠ <file:line> — <category>: <evidence>
ℹ️ <file:line> — possible <category>: <evidence> — confirm placeholder vs real
✗ <file:line> — confirmed credential: rotate immediately
```

## See also

- `pr-review/SKILL.md` — the manager that invokes this skill.
- `CLAUDE.md` "PII audit before committing a project skill" —
  the authoritative source for the rule this skill enforces.
- `security-scan` (global, AgentShield) — Gate 6 of pr-review;
  scans `.claude/settings.json`, hooks, MCP servers. This
  skill (Gate 11) is the complementary scan on `.claude/skills/`.
