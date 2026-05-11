"""PreToolUse hook: warn about stale Photo Manager / pytest processes
before a QA-related commit.

When ``git commit`` is about to run and the staged diff touches files
that commonly spawn Photo Manager subprocesses (``qa/scenarios/*.py``,
``tests/test_*dialog*.py``, ``tests/test_*scenario*.py``), scan for
python processes still running this repo's ``main.py`` or
``qa.scenarios.*`` modules. Print PIDs + a one-line cleanup command
to stderr so the developer notices before the commit lands.

Rationale: this session's run of #182 produced 15 zombie pytest
processes from auto-backgrounded commands whose stdout/stderr never
reached the harness. They consumed memory, held QApplication state,
and once even left a "Locked Rows Affected" modal on screen. The
zombies aren't caused by the commit itself, but a commit boundary is
a natural checkpoint to flag them — better than discovering them on
the next test run.

Hook protocol
-------------
* stdin  — JSON ``{"tool_name": "Bash", "tool_input": {"command": "git commit …"}}``
* exit 0 — allow the tool call. The hook is intentionally non-blocking:
  stale processes don't make a commit incorrect; they just deserve a
  loud reminder. Stderr output is shown to Claude / the user.
* This is a Windows-only hook (``tasklist``/``wmic``). It bails cleanly
  on other platforms.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

# Pattern for files whose changes suggest the developer was running
# Photo Manager subprocesses recently (and may have left zombies).
_QA_RELEVANT_PATTERNS = (
    re.compile(r"^qa/scenarios/.*\.py$"),
    re.compile(r"^tests/test_.*dialog.*\.py$"),
    re.compile(r"^tests/test_qa_.*\.py$"),
    # The main GUI entry point. Touching it suggests local GUI
    # smoke-testing during development.
    re.compile(r"^main\.py$"),
)


def _staged_files() -> list[str]:
    """Return files staged for the pending commit."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _qa_relevant(files: list[str]) -> list[str]:
    return [f for f in files if any(p.match(f) for p in _QA_RELEVANT_PATTERNS)]


def _list_photo_manager_processes() -> list[tuple[int, str]]:
    """Return ``[(pid, command_snippet), …]`` for python.exe processes
    running this repo's main.py or any ``qa.scenarios`` module.

    Implementation: ``wmic process where Name='python.exe' get …``
    returns CSV. wmic is deprecated on Windows 11 but the older API
    still works; if it disappears in a future release we can switch
    to ``Get-CimInstance Win32_Process`` via PowerShell. Returns an
    empty list on non-Windows or if the command fails.
    """
    if os.name != "nt":
        return []
    try:
        # /format:csv yields "Node,CommandLine,ProcessId" rows with
        # commas inside CommandLine. We parse defensively — the
        # ProcessId is always the last field, command line is the
        # join of everything between Node and ProcessId.
        out = subprocess.check_output(
            [
                "wmic", "process", "where", "Name='python.exe'",
                "get", "CommandLine,ProcessId", "/format:csv",
            ],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return []

    results: list[tuple[int, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("Node,"):
            continue
        # Split from the right: trailing field is PID; everything
        # before the last comma (minus leading Node,) is the command.
        try:
            head, pid_str = line.rsplit(",", 1)
            pid = int(pid_str.strip())
        except (ValueError, AttributeError):
            continue
        # Strip the leading Node value (hostname) — first field.
        try:
            _node, cmd = head.split(",", 1)
        except ValueError:
            continue
        cmd = cmd.strip()
        if not cmd:
            continue
        # Match either main.py from this repo or any qa.scenarios import.
        if (
            "photo-manager" in cmd and ("main.py" in cmd or "qa.scenarios" in cmd)
        ):
            results.append((pid, cmd))
    return results


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, json.JSONDecodeError):
        return 0

    if payload.get("tool_name") != "Bash":
        return 0
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if not _is_git_commit(cmd):
        return 0

    staged = _staged_files()
    relevant = _qa_relevant(staged)
    if not relevant:
        return 0

    zombies = _list_photo_manager_processes()
    if not zombies:
        return 0

    pids = [str(pid) for pid, _ in zombies]
    msg_lines = [
        "zombie-check: found {} Photo Manager / QA python process(es) "
        "still running.".format(len(zombies)),
        "",
        "  QA-relevant files staged for this commit:",
    ]
    for f in relevant:
        msg_lines.append(f"    {f}")
    msg_lines += [
        "",
        "  Lingering PIDs (likely zombies from earlier test / scenario runs):",
    ]
    for pid, cmd_snip in zombies[:10]:
        snip = cmd_snip if len(cmd_snip) <= 100 else cmd_snip[:97] + "…"
        msg_lines.append(f"    {pid}  {snip}")
    if len(zombies) > 10:
        msg_lines.append(f"    …and {len(zombies) - 10} more")
    msg_lines += [
        "",
        "  Not blocking the commit — these are stale state, not a problem with",
        "  the commit itself. Clean up when convenient (any of these works):",
        "    taskkill /F " + " ".join(f"/PID {p}" for p in pids[:5]) + (
            "  # …(repeat for remaining)" if len(pids) > 5 else ""
        ),
        "",
        "  Background: see gotcha #10 in the photo-manager handover.",
    ]
    sys.stderr.write("\n".join(msg_lines) + "\n")
    return 0


def _is_git_commit(cmd: str) -> bool:
    """Return True if ``cmd`` is a ``git commit`` invocation.

    We match the literal ``git commit`` token followed by either a
    space or end-of-string so ``git committed`` (typo) or
    ``git commit-tree`` (a different plumbing command we don't care
    about) don't fire the hook.
    """
    return bool(re.search(r"\bgit\s+commit(\s|$)", cmd))


if __name__ == "__main__":
    sys.exit(main())
