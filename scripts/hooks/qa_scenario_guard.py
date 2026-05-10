"""PreToolUse hook: enforce QA-scenario coverage for user-facing PRs.

When ``gh pr create`` is about to run, scan the branch's diff vs.
``origin/master`` for user-facing file changes (handlers / dialogs /
components / workers under ``app/views/``). If any are present and no
``qa/scenarios/sNN_*.py`` change accompanies them, block the PR creation
with a clear stderr message naming the offenders.

Per CLAUDE.md, layer-3 ``qa/scenarios/sNN_*.py`` drivers are a hard
requirement for user-facing flows. Words alone in the docs were not
enough — see photo-manager#175 (Locked-state PR initially shipped
without a QA scenario; required a follow-up commit to comply).

Bypass
------
Include the literal token ``[qa-not-needed: <reason>]`` anywhere in the
``gh pr create`` command (typically in ``--title`` or ``--body``) when
a change genuinely doesn't need a QA scenario — e.g. an internal
refactor with zero user-visible effect, a translation-string update, a
docstring fix. The reason becomes part of the PR title/body so the
choice is visible in code review.

Hook protocol
-------------
* stdin  — JSON with shape ``{"tool_name": ..., "tool_input": {"command": ...}}``
* exit 0 — allow the tool call (default; not a ``gh pr create``, no
  user-facing changes, QA changes present, or bypass token found).
* exit 2 — BLOCK the tool call. Stderr is shown to Claude; tool input
  is rejected. Per Claude Code hook docs.
* any other non-zero exit — surfaced to the user but does NOT block.
  We deliberately use exit 2 for the enforcement path.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

USER_FACING_PATTERNS = (
    re.compile(r"^app/views/handlers/.*\.py$"),
    re.compile(r"^app/views/dialogs/.*\.py$"),
    re.compile(r"^app/views/components/.*\.py$"),
    re.compile(r"^app/views/workers/.*\.py$"),
)
QA_SCENARIO_PATTERN = re.compile(r"^qa/scenarios/s\d+.*\.py$")
BYPASS_PATTERN = re.compile(r"\[qa-not-needed:[^\]]*\]")


def _changed_files() -> list[str]:
    """Return files changed on the current branch vs origin/master."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", "origin/master...HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, json.JSONDecodeError):
        return 0  # don't block on malformed input — fail open

    if payload.get("tool_name") != "Bash":
        return 0
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if "gh pr create" not in cmd:
        return 0

    if BYPASS_PATTERN.search(cmd):
        return 0

    changed = _changed_files()
    if not changed:
        # No diff against origin/master — nothing to check (or no remote).
        # Don't block; CI / review will surface other issues if relevant.
        return 0

    user_facing = [
        f for f in changed if any(p.match(f) for p in USER_FACING_PATTERNS)
    ]
    qa_changes = [f for f in changed if QA_SCENARIO_PATTERN.match(f)]

    if user_facing and not qa_changes:
        msg_lines = [
            "QA-scenario guard fired — blocking `gh pr create`.",
            "",
            "  user-facing files changed:",
        ]
        for f in user_facing:
            msg_lines.append(f"    {f}")
        msg_lines += [
            "",
            "  no qa/scenarios/sNN_*.py changes in this PR.",
            "",
            "  Per CLAUDE.md, user-facing flows (button / dialog / menu /",
            "  status bar) require a layer-3 qa/scenarios/sNN_*.py driver.",
            "",
            "  To unblock:",
            "    a) Add or extend a qa/scenarios/sNN_*.py driver, OR",
            "    b) Include `[qa-not-needed: <reason>]` in the gh pr create",
            "       command (title or body) — the reason will be visible in",
            "       review so the choice is auditable.",
        ]
        sys.stderr.write("\n".join(msg_lines) + "\n")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
