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

# Behavioural-modify trigger (#262): MODIFIED files under
# app/views/{dialogs,handlers}/ shift user-visible behaviour by
# definition — these are the dialog bodies and action handlers a
# user reaches. When the diff is non-trivial, require
# docs/features.md specifically rather than letting any doc touch
# satisfy the gate. Trivial edits (typo, single-line comment) stay
# under the diff-size / signature-change threshold and don't fire.
_DOC_BEHAVIOURAL_MODIFY_PATTERN = re.compile(
    r"^app/views/(dialogs|handlers)/[^/]+\.py$"
)
_BEHAVIOURAL_FEATURES_DOC = "docs/features.md"
_BEHAVIOURAL_TRIGGER_DIFF_THRESHOLD = 10

_BYPASS_PATTERN = re.compile(r"\[docs-not-needed:[^\]]*\]")


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


def _new_files() -> set[str]:
    """Return files ADDED on this branch vs the diff base.

    Renames / modifications of existing files don't trigger the guard
    on most patterns — we only want to flag NEW source modules and
    NEW tests. (Renames and modifications are caught by other patterns
    or are typically smaller-scope and don't warrant a doc update.)
    Falls back to the full changed list if git can't distinguish.
    """
    base = _diff_base()
    try:
        out = subprocess.check_output(
            [
                "git", "diff", "--name-status",
                "--diff-filter=A",  # added only
                f"{base}...HEAD",
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


def _behavioural_modify_qualifies(path: str) -> bool:
    """Decide whether a modify to ``path`` warrants firing the
    behavioural docs gate.

    Qualifies when EITHER the diff is at least
    :data:`_BEHAVIOURAL_TRIGGER_DIFF_THRESHOLD` added + deleted lines
    OR a function signature line appears in the diff (heuristic: a
    ``def`` or ``async def`` line on the + / - side of ``git diff
    -U0``). Trivial edits (a single-line copy tweak, a comment fix)
    fall under both bars and don't fire the gate.

    Used to keep this trigger from blocking on edits that genuinely
    don't shift user-visible behaviour.
    """
    base = _diff_base()
    try:
        numstat_out = subprocess.check_output(
            ["git", "diff", "--numstat", f"{base}...HEAD", "--", path],
            text=True, stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    total = 0
    for line in numstat_out.splitlines():
        # numstat: "added\tdeleted\tpath"; binary files print "-".
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            total += int(parts[0]) + int(parts[1])
    if total >= _BEHAVIOURAL_TRIGGER_DIFF_THRESHOLD:
        return True
    try:
        diff_out = subprocess.check_output(
            ["git", "diff", "-U0", f"{base}...HEAD", "--", path],
            text=True, stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return bool(re.search(r"^[+-]\s*(async\s+)?def\s", diff_out, re.MULTILINE))


def _doc_relevant(changed: list[str], added: set[str]) -> list[tuple[str, str]]:
    """Return ``[(path, suggested_doc), …]`` for changes that need docs.

    NEW files under doc-relevant directories always trigger. MODIFIED
    files trigger for:
      * qa scenarios — name / coverage table updates;
      * ``infrastructure/manifest_repository.py`` — migration list;
      * files under ``app/views/{dialogs,handlers}/`` that pass
        :func:`_behavioural_modify_qualifies` — these shift
        user-visible behaviour and require
        :data:`_BEHAVIOURAL_FEATURES_DOC` specifically (enforced in
        :func:`main`).
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
            # Behavioural-modify trigger (#262).
            if (
                _DOC_BEHAVIOURAL_MODIFY_PATTERN.match(f)
                and _behavioural_modify_qualifies(f)
            ):
                out.append(
                    (f, f"{_BEHAVIOURAL_FEATURES_DOC} (user-visible behaviour)")
                )
                break
    return out


def _docs_touched(changed: list[str]) -> list[str]:
    return [f for f in changed if any(p.match(f) for p in _DOC_FILE_PATTERNS)]


def check(pr_text: str) -> tuple[int, str]:
    """Run the gate against the current branch.

    ``pr_text`` is searched for the bypass token — pass the
    ``gh pr create`` command line in PreToolUse mode, or the PR
    title + body concatenated in CI mode.

    Returns ``(exit_code, stderr_message)``. ``exit_code`` is 0
    (allow) or 2 (block). When 0, ``stderr_message`` is empty.
    """
    if _BYPASS_PATTERN.search(pr_text):
        return 0, ""

    changed = _changed_files()
    if not changed:
        return 0, ""

    added = _new_files()
    relevant = _doc_relevant(changed, added)
    if not relevant:
        return 0, ""

    docs = _docs_touched(changed)

    # Behavioural-modify triggers (#262) are strict: they require
    # docs/features.md specifically, not just any doc touch. New files
    # keep the legacy coarse semantic ("did you think about docs at
    # all"). The bypass token below still works for both.
    behavioural = [
        f for f, _ in relevant
        if _DOC_BEHAVIOURAL_MODIFY_PATTERN.match(f) and f not in added
    ]
    if behavioural and _BEHAVIOURAL_FEATURES_DOC not in docs:
        msg_lines = [
            "docs guard fired — blocking `gh pr create`.",
            "",
            f"  user-visible behaviour change without a {_BEHAVIOURAL_FEATURES_DOC} update:",
        ]
        for f in behavioural:
            msg_lines.append(f"    {f}")
        msg_lines += [
            "",
            "  Behavioural changes under app/views/{dialogs,handlers}/",
            f"  must update {_BEHAVIOURAL_FEATURES_DOC} so the canonical",
            "  feature inventory stays in sync.",
            "",
            "  To unblock:",
            f"    a) Add or update the corresponding section in {_BEHAVIOURAL_FEATURES_DOC}.",
            "    b) Include `[docs-not-needed: <reason>]` in the gh pr create",
            "       command (title or body) when the change is genuinely not",
            "       user-visible (e.g. an internal refactor that preserves",
            "       behaviour byte-for-byte).",
        ]
        return 2, "\n".join(msg_lines) + "\n"

    if docs:
        # At least one doc file was touched — accept the PR; we're
        # checking "did you think about docs at all," not "did you
        # update the exactly-correct line." The qa-scenario-guard
        # applies the same coarse rule. (Behavioural-modify changes
        # take the strict path above.)
        return 0, ""

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
    return 2, "\n".join(msg_lines) + "\n"


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
        return 0

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
