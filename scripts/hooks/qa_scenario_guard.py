"""PreToolUse hook: enforce QA-scenario coverage for user-facing PRs.

When ``gh pr create`` is about to run, scan the branch's diff vs.
``origin/master`` for user-facing file changes (the React client under
``frontend/src/`` and the FastAPI surface under ``app/web/``). If any are
present and no ``qa/web/scenarios/sNN_*.py`` change accompanies them,
block the PR creation with a clear stderr message naming the offenders.

Per CLAUDE.md, layer-3 ``qa/web/scenarios/sNN_*.py`` drivers are a hard
requirement for user-facing flows. Words alone in the docs were not
enough — see photo-manager#175 (Locked-state PR initially shipped
without a QA scenario; required a follow-up commit to comply).

Retargeted by #646 (Phase-4 cutover): the old triggers pointed at
``app/views/`` — the deleted Qt client — which would have left this gate
matching nothing and silently passing every PR.

Bypass
------
Include the literal token ``[qa-not-needed: <reason>]`` anywhere in the
``gh pr create`` command (typically in ``--title`` or ``--body``) when
a change genuinely doesn't need a QA scenario — e.g. an internal
refactor with zero user-visible effect, a translation-string update, a
docstring fix. The reason becomes part of the PR title/body so the
choice is visible in code review.

The reason must be **non-blank** (#857) and must not be the documented
placeholder ``<reason>`` itself (#858). ``[qa-not-needed:]``,
``[qa-not-needed:   ]`` and ``[qa-not-needed: <reason>]`` do not bypass
anything: they block with a message naming the problem. A pasted
template placeholder used to satisfy the old ``[^\\]]*`` pattern and
disabled this gate silently.

Hook protocol
-------------
* stdin  — JSON with shape ``{"tool_name": ..., "tool_input": {"command": ...}}``
* exit 0 — allow the tool call (default; not a ``gh pr create``, no
  user-facing changes, QA changes present, or bypass token found).
* exit 2 — BLOCK the tool call. Stderr is shown to Claude; tool input
  is rejected. Per Claude Code hook docs.
* any other non-zero exit — surfaced to the user but does NOT block.
  We deliberately use exit 2 for the enforcement path.

CI mode
-------
Invoke with ``--ci`` to run the same gate against a GitHub Actions
pull-request payload (see ``.github/workflows/pr-gates.yml``). Reads
``PR_TITLE`` + ``PR_BODY`` from the environment for bypass-token
detection, and ``DIFF_BASE`` (default ``origin/master``) for the
diff base. Same exit-2-on-block contract; same bypass token.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

USER_FACING_PATTERNS = (
    # The React client — components, hooks, store, api layer, styles.
    re.compile(r"^frontend/src/.*\.(ts|tsx|css)$"),
    # The FastAPI surface the client talks to (routes + their models).
    re.compile(r"^app/web/.*\.py$"),
)

# Files that live under the roots above but are not themselves a user
# surface: co-located vitest specs, the vitest bootstrap, and any Python
# test that ever lands inside app/web/. Excluded so a test-only PR does
# not demand a layer-3 driver (the false-positive half of this gate).
NOT_USER_FACING_PATTERNS = (
    re.compile(r"^frontend/src/test/"),
    re.compile(r"^frontend/src/.*\.test\.(ts|tsx)$"),
    re.compile(r"^app/web/(.*/)?test_[^/]+\.py$"),
    re.compile(r"^app/web/(.*/)?tests?/"),
)

QA_SCENARIO_PATTERN = re.compile(r"^qa/web/scenarios/s\d+.*\.py$")
# The reason must be non-blank: at least one non-space character after the
# colon (leading whitespace is fine), then anything up to the closing ``]``.
# Punctuation, unicode and ``#refs`` all still match — only ``]`` ends it.
# The lookahead rejects one specific reason, the documented placeholder
# itself (#858): ``[qa-not-needed: <reason>]`` is how the token is
# *written down* — in README.md and in every brief that quotes the
# convention — so accepting it means a PR body that merely explains the
# token disables the gate.
BYPASS_PATTERN = re.compile(
    r"\[qa-not-needed:(?!\s*<reason>\s*\])\s*[^\]\s][^\]]*\]"
)

# The empty form (#857): a token whose reason is blank. The old
# ``[^\]]*`` matched zero characters, so a pasted template — the literal
# ``[qa-not-needed:]`` — was a valid bypass and disabled this gate
# silently, in CI as well as in the local hook. It now blocks, and this
# pattern exists so the block message can name the real problem instead of
# falling through to the generic "no qa scenario" text.
EMPTY_BYPASS_PATTERN = re.compile(r"\[qa-not-needed:\s*\]")

_EMPTY_BYPASS_MSG_LINES = (
    "  bypass token seen but its reason is empty — write why.",
    "",
    "    A `[qa-not-needed:]` with no reason does not bypass this gate.",
    "    The reason IS the mechanism: it is what a reviewer reads to judge",
    "    the call, so a pasted placeholder must not silently disable the",
    "    check.",
    "",
    "    Write `[qa-not-needed: <reason>]` with a specific reason, or add",
    "    the driver described below.",
    "",
)

# The placeholder form (#858): a reason that is exactly the documented
# ``<reason>``. Same failure as the empty token one step later — a brief's
# template pasted verbatim, or the convention quoted in prose, silently
# disabling the gate.
PLACEHOLDER_BYPASS_PATTERN = re.compile(r"\[qa-not-needed:\s*<reason>\s*\]")

_PLACEHOLDER_BYPASS_MSG_LINES = (
    "  bypass token seen but its reason is the literal `<reason>`",
    "  placeholder — write a real one.",
    "",
    "    `[qa-not-needed: <reason>]` is how this token is *documented*.",
    "    Pasting it verbatim, or quoting it in prose, does not bypass the",
    "    gate — otherwise any PR that explains the convention would turn",
    "    it off.",
    "",
    "    Write a specific reason, or add the driver described below.",
    "",
)


def _rejected_bypass_lines(pr_text: str) -> tuple[str, ...]:
    """Explain a ``[qa-not-needed:`` token that did not bypass.

    Empty (#857) and placeholder (#858) reasons do not block on their own —
    if the gate has nothing to say, a vestigial token is harmless — but when
    the gate DOES fire, the message must lead with the real problem so the
    developer fixes the right thing instead of reading the generic text.
    """
    if PLACEHOLDER_BYPASS_PATTERN.search(pr_text):
        return _PLACEHOLDER_BYPASS_MSG_LINES
    if EMPTY_BYPASS_PATTERN.search(pr_text):
        return _EMPTY_BYPASS_MSG_LINES
    return ()


def _diff_base() -> str:
    """Resolve the base ref for the branch-diff. CI mode sets DIFF_BASE
    to ``origin/<github.event.pull_request.base.ref>`` so stacked PRs
    diff against their immediate parent, not always master."""
    return os.environ.get("DIFF_BASE", "origin/master")


def _changed_files() -> list[str]:
    """Return files changed on the current branch vs the diff base."""
    base = _diff_base()
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def check(pr_text: str) -> tuple[int, str]:
    """Run the gate against the current branch.

    ``pr_text`` is searched for the bypass token — pass the
    ``gh pr create`` command line in PreToolUse mode, or the PR
    title + body concatenated in CI mode.

    Returns ``(exit_code, stderr_message)``. ``exit_code`` is 0
    (allow) or 2 (block). When 0, ``stderr_message`` is empty.
    """
    if BYPASS_PATTERN.search(pr_text):
        return 0, ""

    rejected_bypass = _rejected_bypass_lines(pr_text)

    changed = _changed_files()
    if not changed:
        # No diff against the base — nothing to check (or no remote).
        # Don't block; CI / review will surface other issues if relevant.
        return 0, ""

    user_facing = [
        f for f in changed
        if any(p.match(f) for p in USER_FACING_PATTERNS)
        and not any(p.match(f) for p in NOT_USER_FACING_PATTERNS)
    ]
    qa_changes = [f for f in changed if QA_SCENARIO_PATTERN.match(f)]

    if user_facing and not qa_changes:
        msg_lines = ["QA-scenario guard fired — blocking `gh pr create`.", ""]
        msg_lines += list(rejected_bypass)
        msg_lines.append("  user-facing files changed:")
        for f in user_facing:
            msg_lines.append(f"    {f}")
        msg_lines += [
            "",
            "  no qa/web/scenarios/sNN_*.py changes in this PR.",
            "",
            "  Per CLAUDE.md, user-facing flows (button / dialog / menu /",
            "  status bar) require a layer-3 qa/web/scenarios/sNN_*.py driver.",
            "",
            "  To unblock:",
            "    a) Add or extend a qa/web/scenarios/sNN_*.py driver, OR",
            "    b) Include `[qa-not-needed: <reason>]` in the gh pr create",
            "       command (title or body) — the reason will be visible in",
            "       review so the choice is auditable. It must be non-blank:",
            "       an empty `[qa-not-needed:]` is rejected.",
        ]
        return 2, "\n".join(msg_lines) + "\n"

    return 0, ""


def _run_ci() -> int:
    """CI mode: read PR title + body from env vars; check the diff."""
    pr_text = (
        os.environ.get("PR_TITLE", "")
        + "\n"
        + os.environ.get("PR_BODY", "")
    )
    rc, msg = check(pr_text)
    if msg:
        sys.stderr.write(msg)
    return rc


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--ci":
        return _run_ci()

    try:
        payload = json.load(sys.stdin)
    except (ValueError, json.JSONDecodeError):
        return 0  # don't block on malformed input — fail open

    if payload.get("tool_name") != "Bash":
        return 0
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if "gh pr create" not in cmd:
        return 0

    rc, msg = check(cmd)
    if msg:
        sys.stderr.write(msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
