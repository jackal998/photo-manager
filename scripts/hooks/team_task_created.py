"""TaskCreated hook: allowlist task titles when team mode is active.

When a `/pr-review` team-mode session calls TaskCreate to enqueue a
gate for a teammate, the task subject should follow a known pattern.
This hook rejects task subjects that don't match — preventing
free-form task creation that would expand the team's scope beyond
the gates the user actually opted into.

Allowlisted task subject patterns are listed in :data:`_ALLOWED_PATTERNS`
below. To add a new team-mode gate or refactor an existing one, edit
that list — additions are deliberate and reviewable.

The hook is intentionally conservative on payload shape: the
Agent-Teams `TaskCreated` event schema is not yet documented at the
time this hook ships, so the hook sniffs known key paths
(``task.subject``, ``tool_input.subject``) and **fails open** (exit 0)
when the payload doesn't match. This keeps the hook safe to wire
in advance — it can't block a future Claude Code release that
restructures the event payload.

Bypass
------
Include the literal token ``[team-task-freeform: <reason>]`` in the
task subject when a one-off task genuinely doesn't fit the allowlist
(e.g. a manual instruction from LEAD to a teammate during debugging).
The reason becomes part of the subject so the choice is visible in
the team's task list.

Hook protocol
-------------
* stdin  — JSON, expected shape (subject to confirmation as Agent
  Teams matures):
  ``{"event": "TaskCreated", "task": {"subject": "...", ...}}`` OR
  ``{"tool_name": "TaskCreate", "tool_input": {"subject": "...", ...}}``
* exit 0 — allow the task creation (default; fail-open on unknown
  payload shapes).
* exit 2 — BLOCK the task creation. Stderr is shown to Claude.
* any other non-zero exit — surfaced to the user but does NOT block.
"""
from __future__ import annotations

import json
import re
import sys

# Conventional team-mode task subjects. Each pattern matches the
# subject string the LEAD session creates when spawning a teammate for
# that gate. Edits here are deliberate and should be reviewed.
_ALLOWED_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Gates 2+3 — docs-reviewer teammate
    re.compile(r"^Gate(s)? 2(\+3)?:.*features\.md.*qa scenario", re.IGNORECASE),
    re.compile(r"^docs[- ]reviewer:.*", re.IGNORECASE),
    # Gate 7 — app-security-reviewer teammate
    re.compile(r"^Gate 7:.*app[- ]level security", re.IGNORECASE),
    re.compile(r"^app[- ]security[- ]reviewer:.*", re.IGNORECASE),
    # Gates 8+9+10 — quality-reviewer teammate
    re.compile(r"^Gate(s)? 8(\+9(\+10)?)?:.*(migration|perf|threading|test)", re.IGNORECASE),
    re.compile(r"^quality[- ]reviewer:.*", re.IGNORECASE),
)

_BYPASS_PATTERN = re.compile(r"\[team-task-freeform:[^\]]*\]")


def _extract_subject(payload: dict) -> str | None:
    """Sniff a subject string from one of the known payload shapes.

    Returns None if the payload doesn't look like a TaskCreated event
    we recognise — caller treats None as fail-open.
    """
    task = payload.get("task")
    if isinstance(task, dict):
        subject = task.get("subject")
        if isinstance(subject, str):
            return subject
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict) and payload.get("tool_name") == "TaskCreate":
        subject = tool_input.get("subject")
        if isinstance(subject, str):
            return subject
    return None


def check(subject: str) -> tuple[int, str]:
    """Return (exit_code, stderr_message) for a given task subject."""
    if _BYPASS_PATTERN.search(subject):
        return 0, ""
    if any(pat.search(subject) for pat in _ALLOWED_PATTERNS):
        return 0, ""
    msg = (
        "team-mode task-created guard fired — blocking TaskCreate.\n"
        "\n"
        f"  task subject: {subject!r}\n"
        "\n"
        "  This subject does not match any allowlisted team-mode task\n"
        "  pattern. Team-mode tasks should follow the gate-naming\n"
        "  convention so the team's scope stays bounded.\n"
        "\n"
        "  To unblock:\n"
        "    a) Rephrase the subject to match a known gate (see\n"
        "       _ALLOWED_PATTERNS in scripts/hooks/team_task_created.py).\n"
        "    b) Include `[team-task-freeform: <reason>]` in the subject\n"
        "       for one-off tasks (e.g. LEAD-issued debugging instructions).\n"
    )
    return 2, msg


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, json.JSONDecodeError):
        return 0  # fail open — don't block on malformed input
    if not isinstance(payload, dict):
        return 0
    subject = _extract_subject(payload)
    if subject is None:
        return 0  # fail open — unknown payload shape
    rc, msg = check(subject)
    if msg:
        sys.stderr.write(msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
