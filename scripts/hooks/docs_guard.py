"""PreToolUse hook: enforce documentation coverage for doc-relevant PRs.

When ``gh pr create`` is about to run, scan the branch's diff vs.
``origin/master`` for "doc-relevant" code changes: new ``.py`` files
under structured directories (``app/views/``, ``infrastructure/``,
``scanner/``, ``core/services/``, ``tests/``), new or renamed QA
scenarios, schema migration list changes, etc. If any are present and
NO doc file (``README.md`` / ``docs/testing.md`` / ``CLAUDE.md`` /
``pyproject.toml``'s omit list) was touched, block the PR creation
with a clear stderr message naming the offenders.

Mirror of ``qa_scenario_guard.py`` (#176), which enforces QA-scenario
coverage. This guard catches the symmetric class of drift: code lands
and the project tree / per-module testing map / setup docs go stale.
The #182 lock-redesign branch itself hit this twice — see the
``docs(#182):`` follow-up commit on that branch.

Bypass
------
Include the literal token ``[docs-not-needed: <reason>]`` anywhere in
the ``gh pr create`` command (typically in ``--title`` or ``--body``)
when a change genuinely doesn't need a doc edit — e.g. a one-line bug
fix, an internal refactor with zero structural impact. The reason
becomes part of the PR title/body so the choice is visible in code
review.

Hook protocol
-------------
* stdin  — JSON ``{"tool_name": "Bash", "tool_input": {"command": "gh pr create …"}}``
* exit 0 — allow the tool call.
* exit 2 — BLOCK the tool call. Stderr is shown to Claude; tool input
  is rejected. Per Claude Code hook docs.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

# Code changes that require some documentation touch. Each pattern is
# accompanied by the doc file(s) that would most naturally cover it,
# surfaced in the failure message so the developer knows where to look.
_DOC_RELEVANT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^app/views/(dialogs|handlers|workers|components|widgets|layout|viewmodels)/[^/]+\.py$"),
        "README.md project tree (under app/views/...)",
    ),
    (
        re.compile(r"^infrastructure/[^/]+\.py$"),
        "README.md project tree (infrastructure/)",
    ),
    (
        re.compile(r"^scanner/[^/]+\.py$"),
        "README.md project tree (scanner/) + docs/testing.md",
    ),
    (
        re.compile(r"^core/(models|services/[^/]+)\.py$"),
        "README.md project tree (core/)",
    ),
    (
        re.compile(r"^tests/test_[^/]+\.py$"),
        "README.md tests list (and docs/testing.md if it shifts a layer)",
    ),
    (
        re.compile(r"^qa/scenarios/s\d+.*\.py$"),
        "docs/testing.md per-module table (which scenario covers what)",
    ),
)

_DOC_FILE_PATTERNS = (
    re.compile(r"^README\.md$"),
    re.compile(r"^docs/.*\.md$"),
    re.compile(r"^CLAUDE\.md$"),
    re.compile(r"^pyproject\.toml$"),  # covers omit list edits
    re.compile(r"^translations/README\.md$"),
)

_BYPASS_PATTERN = re.compile(r"\[docs-not-needed:[^\]]*\]")


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


def _new_files() -> set[str]:
    """Return files ADDED on this branch vs origin/master.

    Renames / modifications of existing files don't trigger the guard
    on most patterns — we only want to flag NEW source modules and
    NEW tests. (Renames and modifications are caught by other patterns
    or are typically smaller-scope and don't warrant a doc update.)
    Falls back to the full changed list if git can't distinguish.
    """
    try:
        out = subprocess.check_output(
            [
                "git", "diff", "--name-status",
                "--diff-filter=A",  # added only
                "origin/master...HEAD",
            ],
            text=True, stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    added: set[str] = set()
    for line in out.splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 2:
            added.add(parts[-1])  # last column is the path
    return added


def _doc_relevant(changed: list[str], added: set[str]) -> list[tuple[str, str]]:
    """Return ``[(path, suggested_doc), …]`` for changes that need docs.

    NEW files under doc-relevant directories always trigger. MODIFIED
    files only trigger for a narrow subset where the modification
    typically shifts public-facing structure (qa scenarios — name /
    coverage table; tests — count + tree; manifest_repository —
    migration list).
    """
    out: list[tuple[str, str]] = []
    for f in changed:
        for pattern, suggested in _DOC_RELEVANT_PATTERNS:
            if not pattern.match(f):
                continue
            # NEW files always trigger.
            if f in added:
                out.append((f, suggested))
                break
            # MODIFIED — narrow trigger set.
            if f.startswith("qa/scenarios/s"):
                out.append((f, suggested))
                break
            if f == "infrastructure/manifest_repository.py":
                out.append((f, "README.md schema table (if _MIGRATIONS changed)"))
                break
    return out


def _docs_touched(changed: list[str]) -> list[str]:
    return [f for f in changed if any(p.match(f) for p in _DOC_FILE_PATTERNS)]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, json.JSONDecodeError):
        return 0

    if payload.get("tool_name") != "Bash":
        return 0
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if "gh pr create" not in cmd:
        return 0

    if _BYPASS_PATTERN.search(cmd):
        return 0

    changed = _changed_files()
    if not changed:
        return 0

    added = _new_files()
    relevant = _doc_relevant(changed, added)
    if not relevant:
        return 0

    docs = _docs_touched(changed)
    if docs:
        # At least one doc file was touched — accept the PR; we're
        # checking "did you think about docs at all," not "did you
        # update the exactly-correct line." The qa-scenario-guard
        # applies the same coarse rule.
        return 0

    msg_lines = [
        "docs guard fired — blocking `gh pr create`.",
        "",
        "  doc-relevant changes on this branch:",
    ]
    seen: set[str] = set()
    for f, suggested in relevant:
        if f in seen:
            continue
        seen.add(f)
        msg_lines.append(f"    {f}")
        msg_lines.append(f"        → consider updating: {suggested}")
    msg_lines += [
        "",
        "  no README.md / docs/*.md / CLAUDE.md / pyproject.toml changes",
        "  in this PR.",
        "",
        "  To unblock:",
        "    a) Surgically update the relevant doc section(s) — README.md",
        "       project tree, README.md tests list, docs/testing.md",
        "       per-module table, pyproject.toml omit list comment, etc.",
        "    b) Include `[docs-not-needed: <reason>]` in the gh pr create",
        "       command (title or body) — the reason will be visible in",
        "       review so the choice is auditable.",
    ]
    sys.stderr.write("\n".join(msg_lines) + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
