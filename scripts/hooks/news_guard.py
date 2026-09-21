"""CI gate: require a ``news/<PR>.<type>`` changelog fragment on every PR.

``towncrier`` builds ``CHANGELOG.md`` from the one-line fragments in
``news/``; this gate is what keeps them arriving with the code. It runs
server-side only — there is no PreToolUse sibling, because the fragment's
filename needs the PR number, which doesn't exist until after
``gh pr create``. See ``.github/workflows/news-gate.yml``.

Bypass
------
Include the literal token ``[skip-news: <reason>]`` in the PR title or
body when a change genuinely has nothing worth a changelog line — a typo
in a code comment, a transitive dep bump with no behaviour change. The
reason is for the reviewer, so it must be real:

* ``[skip-news:]`` / ``[skip-news:   ]`` — blank reason, rejected.
* ``[skip-news: no closing bracket`` — never closes on its line, rejected.
  A later ``]`` anywhere below (a markdown link, a ``- [ ]`` checklist box)
  does not close it either: the reason must sit on one line with its
  bracket (#872), or the swallowed paragraphs become "the reason".
* ``[skip-news: <reason>]`` — the documented placeholder pasted verbatim,
  or the token quoted in prose (a docs PR citing ``news/README.md``),
  rejected.

Until #858 this decision was ``grep -Fq '[skip-news:'`` inline in the
workflow — a fixed-string PREFIX match that needed neither a reason nor a
closing bracket and fired on any body that merely *mentioned* the token.
That turned the only changelog gate into opt-in, silently: the job printed
"Bypass token present" and went green. The patterns below mirror the
``[docs-not-needed:]`` / ``[qa-not-needed:]`` shape landed for
``docs_guard.py`` / ``qa_scenario_guard.py`` in #857, so all three tokens
agree on what counts as a reason.

Invocation
----------
``python scripts/hooks/news_guard.py --ci``, the way ``pr-gates.yml``
calls ``docs_guard.py``. All input arrives through the environment — a PR
body routinely contains backticks, quotes and ``$(...)``, so it must never
travel as a shell argument:

* ``PR_TITLE`` / ``PR_BODY`` — searched for the bypass token.
* ``ADDED_FILES`` — newline-separated paths of the files this PR *adds*,
  as listed by the workflow's ``gh api .../pulls/<n>/files`` call. The
  API is used rather than ``git diff`` so the gate does not depend on
  checkout history depth.

Exit codes: 0 allows, 1 blocks with an explanation on stderr. (The exit-2
convention of the two PreToolUse guards does not apply here — this script
is never a hook, and in CI any non-zero exit fails the job.)
"""
from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterable, Sequence

# The five suffixes towncrier builds; the canonical table is in
# news/README.md. Anchored, so `docs/news/1.bugfix` is a different file.
_NEWS_FRAGMENT_PATTERN = re.compile(
    r"^news/[0-9]+\.(feature|bugfix|doc|removal|misc)$"
)

# A bypass needs a non-blank reason and a closing `]` ON THE SAME LINE:
# leading blanks are fine, then at least one non-space character, then
# anything up to the `]` — but never a line break (#872). Punctuation,
# unicode and `#refs` all still match; only `]` or the end of that line ends
# the token. `[^\S\r\n]` is "whitespace that is not a line break", used
# everywhere `\s` used to be so a token left unclosed on its own line cannot
# be closed by an unrelated `]` further down the body — a markdown link, a
# `- [ ]` checklist box, a bracketed reference — which would silently turn
# several paragraphs into "the reason". The lookahead rejects one specific
# reason, the documented placeholder itself (#858): `[skip-news: <reason>]`
# is how the token is *written down*, in news/README.md and in every brief
# that quotes it, so accepting it means any PR body that explains the
# convention disables the gate.
_BYPASS_PATTERN = re.compile(
    r"\[skip-news:(?![^\S\r\n]*<reason>[^\S\r\n]*\])"
    r"[^\S\r\n]*[^\]\s][^\]\r\n]*\]"
)

# The three rejected shapes, kept as separate patterns so the block message
# can name the actual problem instead of falling through to the generic
# "no fragment found" text — a developer who pasted a template needs to be
# told that, not told to write a fragment they may not need.
_PLACEHOLDER_BYPASS_PATTERN = re.compile(
    r"\[skip-news:[^\S\r\n]*<reason>[^\S\r\n]*\]"
)
_EMPTY_BYPASS_PATTERN = re.compile(r"\[skip-news:[^\S\r\n]*\]")
_TOKEN_PREFIX_PATTERN = re.compile(r"\[skip-news:")

_EMPTY_REASON_LINES = (
    "  bypass token seen but its reason is empty — write why.",
    "",
    "    A `[skip-news:]` with no reason does not bypass this gate. The",
    "    reason IS the mechanism: it is what a reviewer reads to judge the",
    "    call, so a pasted placeholder must not silently disable the check.",
    "",
)

_PLACEHOLDER_REASON_LINES = (
    "  bypass token seen but its reason is the literal `<reason>`",
    "  placeholder — write a real one.",
    "",
    "    `[skip-news: <reason>]` is how this token is *documented* (see",
    "    news/README.md). Pasting it verbatim, or quoting it in prose,",
    "    does not bypass the gate — otherwise any PR that explains the",
    "    convention would turn it off.",
    "",
)

_UNCLOSED_LINES = (
    "  bypass token seen but its closing `]` does not arrive on the same",
    "  line — the token is the whole bracketed phrase, on one line.",
    "",
    "    Write `[skip-news: <a real reason>]` on one line, brackets and",
    "    all. A dangling `[skip-news:` is not a bypass, and a later `]`",
    "    further down the body — a markdown link, a `- [ ]` checklist box",
    "    — does not close it (#872).",
    "",
)

_GUIDANCE_LINES = (
    "  Add one of:",
    "    - news/<this-PR-number>.feature   (user-visible new behaviour)",
    "    - news/<this-PR-number>.bugfix    (fixes a user-hitting bug)",
    "    - news/<this-PR-number>.doc       (documentation only)",
    "    - news/<this-PR-number>.removal   (removed feature / breaking change)",
    "    - news/<this-PR-number>.misc      (refactor, CI, tooling, no user diff)",
    "",
    "  File contents: one line, present-tense imperative.",
    "    Example:  Add Execute Action preview pane (#165).",
    "",
    "  If this PR genuinely has no entry worth recording, include the",
    "  literal token `[skip-news: <a real reason>]` in the PR title or",
    "  body. The reason must be specific and non-blank.",
    "",
    "  See CONTRIBUTING.md § Adding a news fragment for your PR.",
    "",
)


def _rejected_bypass_lines(pr_text: str) -> tuple[str, ...]:
    """Explain a ``[skip-news:`` occurrence that did not bypass.

    Returns the message lines for the most specific problem found, or an
    empty tuple when the PR text carries no token at all (the ordinary
    "you forgot the fragment" case).
    """
    if _PLACEHOLDER_BYPASS_PATTERN.search(pr_text):
        return _PLACEHOLDER_REASON_LINES
    if _EMPTY_BYPASS_PATTERN.search(pr_text):
        return _EMPTY_REASON_LINES
    if _TOKEN_PREFIX_PATTERN.search(pr_text):
        return _UNCLOSED_LINES
    return ()


def check(pr_text: str, added_files: Iterable[str]) -> tuple[int, str]:
    """Decide the gate. Returns ``(exit_code, message)`` — the message goes
    to stdout when the gate passes and to stderr when it blocks, so a green
    run still says on which of the two grounds it passed."""
    if _BYPASS_PATTERN.search(pr_text):
        return 0, "news-gate: bypass token with a reason present — gate passes.\n"

    fragments = [f for f in added_files if _NEWS_FRAGMENT_PATTERN.match(f)]
    if fragments:
        # A fragment satisfies the gate outright. A malformed leftover token
        # alongside it is vestigial, not a reason to block: what the gate
        # wants has already been delivered.
        found = "".join(f"  {f}\n" for f in fragments)
        return 0, f"news-gate: found news fragment(s):\n{found}"

    msg_lines = [
        "",
        "  ✗ news-gate: this PR adds no news/<PR>.<type> fragment.",
        "",
    ]
    msg_lines += list(_rejected_bypass_lines(pr_text))
    msg_lines += list(_GUIDANCE_LINES)
    return 1, "\n".join(msg_lines) + "\n"


def _pr_text() -> str:
    return os.environ.get("PR_TITLE", "") + "\n" + os.environ.get("PR_BODY", "")


def _added_files() -> Sequence[str]:
    """Paths the PR adds, newline-separated in ``ADDED_FILES``. Blank lines
    are dropped: an unset or empty variable means "nothing added", never one
    empty filename."""
    raw = os.environ.get("ADDED_FILES", "")
    return [line.strip() for line in raw.splitlines() if line.strip()]


def main() -> int:
    rc, msg = check(_pr_text(), _added_files())
    (sys.stderr if rc else sys.stdout).write(msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
