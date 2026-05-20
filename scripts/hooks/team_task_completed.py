"""TaskCompleted hook: reject empty findings when a gate's trigger fired.

When a teammate marks a `/pr-review` gate task completed, the
expected output is a SendMessage (or task comment) carrying findings
in the conventional shape. If the gate's trigger condition fired on
the diff AND the completion carries empty findings, that's suspicious
— either the teammate skipped the work or the rubric returned a
silent false-CLEAN.

This hook checks the completed task's payload for a non-trivial
findings field. If empty AND the task subject indicates a triggered
gate, block the completion with an explanation. The teammate can then
either re-run the rubric, add an explicit "CLEAN: <reason>" comment,
or use the bypass token.

The hook is intentionally conservative on payload shape: the
Agent-Teams `TaskCompleted` event schema is not yet documented at
the time this hook ships, so the hook sniffs known key paths
(``task.comments``, ``tool_input.comment``) and **fails open** (exit 0)
when the payload doesn't match.

Bypass
------
Include the literal token ``[team-empty-ok: <reason>]`` in the
completion comment when an empty-findings completion is intentional
(e.g. the gate's trigger fired but the rubric explicitly returned
CLEAN with a documented reason).

Hook protocol
-------------
* stdin  — JSON, expected shape (subject to confirmation):
  ``{"event": "TaskCompleted", "task": {"subject": "...", "comments": [...], "status": "completed"}}`` OR
  ``{"tool_name": "TaskUpdate", "tool_input": {"status": "completed", "comment": "...", ...}}``
* exit 0 — allow the completion (default; fail-open on unknown shapes).
* exit 2 — BLOCK the completion. Stderr is shown to Claude.
"""
from __future__ import annotations

import json
import re
import sys

# A task subject matches "triggered gate" if it names a known
# pr-review gate — see scripts/hooks/team_task_created.py for the
# allowlist that gates the inbound side.
_TRIGGERED_GATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^Gate(s)? 2(\+3)?:.*features\.md.*qa scenario", re.IGNORECASE),
    re.compile(r"^docs[- ]reviewer:.*", re.IGNORECASE),
    re.compile(r"^Gate 7:.*app[- ]level security", re.IGNORECASE),
    re.compile(r"^app[- ]security[- ]reviewer:.*", re.IGNORECASE),
    re.compile(r"^Gate(s)? 8(\+9(\+10)?)?:.*(migration|perf|threading|test)", re.IGNORECASE),
    re.compile(r"^quality[- ]reviewer:.*", re.IGNORECASE),
)

_BYPASS_PATTERN = re.compile(r"\[team-empty-ok:[^\]]*\]")

# A "CLEAN with reason" completion is acceptable — it signals the
# teammate did look and found nothing worth flagging.
_EXPLICIT_CLEAN_PATTERN = re.compile(
    r"^\s*SUMMARY:\s*0 findings.*CLEAN", re.IGNORECASE | re.MULTILINE,
)

# A meaningful findings report has a SUMMARY line citing at least one
# severity icon, or has structured headings with content beneath.
_FINDINGS_PATTERN = re.compile(
    r"(✗|⚠|ℹ️|warn:|note:|##\s+\w)", re.MULTILINE,
)


def _extract_subject_and_text(payload: dict) -> tuple[str | None, str]:
    """Sniff a task subject and completion-comment text from known shapes.

    Returns (subject, text). subject is None when the payload doesn't
    look like a TaskCompleted event we recognise.
    """
    task = payload.get("task")
    if isinstance(task, dict):
        subject = task.get("subject") if isinstance(task.get("subject"), str) else None
        comments = task.get("comments") or []
        text = "\n".join(c.get("body", "") for c in comments if isinstance(c, dict))
        return subject, text

    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict) and payload.get("tool_name") == "TaskUpdate":
        if tool_input.get("status") != "completed":
            return None, ""  # not a completion — don't check
        # Fall back to subject from sibling fields if present.
        subject = (
            tool_input.get("subject")
            if isinstance(tool_input.get("subject"), str)
            else None
        )
        text = ""
        for k in ("comment", "body", "summary"):
            v = tool_input.get(k)
            if isinstance(v, str):
                text += v + "\n"
        return subject, text

    return None, ""


def check(subject: str, text: str) -> tuple[int, str]:
    """Return (exit_code, stderr_message) for a completion."""
    if _BYPASS_PATTERN.search(text):
        return 0, ""
    if not any(pat.search(subject) for pat in _TRIGGERED_GATE_PATTERNS):
        return 0, ""  # not a known gate — don't enforce
    if _EXPLICIT_CLEAN_PATTERN.search(text):
        return 0, ""  # explicit CLEAN is acceptable
    if _FINDINGS_PATTERN.search(text):
        return 0, ""  # has structured findings — accept

    msg = (
        "team-mode task-completed guard fired — blocking TaskUpdate.\n"
        "\n"
        f"  task subject: {subject!r}\n"
        "\n"
        "  This task names a triggered /pr-review gate, but the\n"
        "  completion carries no findings and no explicit CLEAN\n"
        "  signal. That suggests either the rubric was skipped or\n"
        "  the teammate emitted findings via a channel the LEAD\n"
        "  cannot read.\n"
        "\n"
        "  Expected one of:\n"
        "    * a structured findings block with severity icons\n"
        "      (`✗` / `⚠` / `ℹ️`) or `## ` section headers, OR\n"
        "    * an explicit `SUMMARY: 0 findings — CLEAN` line\n"
        "      stating why nothing fired.\n"
        "\n"
        "  To unblock:\n"
        "    a) Re-run the gate's rubric and emit findings via\n"
        "       SendMessage to LEAD, or add a comment to the task\n"
        "       with the findings block.\n"
        "    b) Include `[team-empty-ok: <reason>]` in the comment\n"
        "       for intentional empty completions (e.g. gate\n"
        "       trigger fired on the file list but the rubric\n"
        "       explicitly noops on this content).\n"
    )
    return 2, msg


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    subject, text = _extract_subject_and_text(payload)
    if subject is None:
        return 0  # not a recognised completion event
    rc, msg = check(subject, text)
    if msg:
        sys.stderr.write(msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
