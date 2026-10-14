"""CI gate: every PR body declares its ``docs/features.md`` impact, and the
declaration is checked against the PR's diff.

The existing docs guards (``docs_guard.py``) only ask whether the file was
TOUCHED. Here the author names the entries the PR affects, and the gate checks
that claim against the diff in both directions: every named entry must be
touched, and every touched entry must be named.

The body line (case-insensitive key, one line, CRLF tolerated)::

    Docs impact: <value>

``<value>`` is one of three forms:

* a comma-separated list of entries, each the exact ``###`` heading text or
  ``#<slug>``. Heading text may contain commas;
* ``none — <reason>`` (``—``, ``--`` or ``-`` as the separator), valid only
  when the diff touches NO entry section;
* ``restructure — <reason>``, valid whatever the diff touches.

Entry sections come from ``scripts/gen_features_index.py`` (``entry_sections``),
so "entry" means the same thing to this gate as to the generated index. The
index block sits under the ``## Index`` heading, outside every entry, so an
index-only edit touches no entry.

Exemptions: none. The existing PR-body gates (``docs_guard``, ``news_guard``,
``qa_scenario_guard``) have no author or bot exemption either, so this gate
applies to every PR. A ``[docs-not-needed: …]`` token does NOT satisfy it.

Invocation (CI: ``.github/workflows/pr-gates.yml``, from the repo root)::

    python -m scripts.hooks.docs_impact_guard

Input is environment only, as in the other PR-body gates: ``PR_BODY`` and
``DIFF_BASE`` (default ``origin/master``). Exit 0 passes, 1 blocks.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from scripts.gen_features_index import EntrySection, entry_sections

DOC_PATH = "docs/features.md"

_KEY_LINE = re.compile(
    r"^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?docs impact(?::\*\*|\*\*[ \t]*:|[ \t]*:)(.*)$",
    re.IGNORECASE,
)
_KEYWORD_FORM = re.compile(r"(none|restructure)[ \t]*(?:—|--|-)[ \t]*(.*)", re.IGNORECASE)
# An angle-bracketed token is a template placeholder: `<reason>`, `<entry>`.
_PLACEHOLDER = re.compile(r"<[^<>]*>")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_COMMA_SPACING = re.compile(r"\s*,\s*")

FORMS_HINT = (
    "  The PR body needs ONE line `Docs impact: <value>`. <value> is one of:",
    "    a) a comma-separated list of docs/features.md entries, each the exact",
    "       `###` heading text or `#<slug>`:",
    "         Docs impact: Example entry, #example-slug",
    "    b) none — <reason>   (only when the diff touches NO entry section)",
    "         Docs impact: none — CI gate and tooling only",
    "    c) restructure — <reason>   (for PRs that move or split many entries)",
    "         Docs impact: restructure — split the Scan entries into two pages",
)


@dataclass(frozen=True)
class Inputs:
    """The three texts the check needs; ``diff`` is ``git diff --unified=0
    <base>...HEAD -- docs/features.md``, whose hunks index the two texts."""

    base_text: str
    head_text: str
    diff: str


@dataclass(frozen=True)
class _DiffState:
    base_sections: list[EntrySection]
    head_sections: list[EntrySection]
    touched: set[str]
    labels: dict[str, str]


def _fail(lines: list[str]) -> tuple[int, str]:
    body = [
        "docs-impact: BLOCKED — the Docs impact line does not match the diff.",
        "",
        *lines,
        "",
        *FORMS_HINT,
    ]
    return 1, "\n".join(body) + "\n"


def _declarations(body: str) -> list[str]:
    values: list[str] = []
    for line in body.splitlines():
        match = _KEY_LINE.match(line)
        if match:
            values.append(match.group(1).strip())
    return values


def _normalise_label(text: str) -> str:
    return _COMMA_SPACING.sub(",", text.strip())


def _hunk_ranges(diff: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Return (removed base ranges, added head ranges), 1-based inclusive."""
    removed: list[tuple[int, int]] = []
    added: list[tuple[int, int]] = []
    for line in diff.splitlines():
        match = _HUNK.match(line)
        if match is None:
            continue
        old_start, old_len = int(match.group(1)), _count(match.group(2))
        new_start, new_len = int(match.group(3)), _count(match.group(4))
        if old_len:
            removed.append((old_start, old_start + old_len - 1))
        if new_len:
            added.append((new_start, new_start + new_len - 1))
    return removed, added


def _count(text: str | None) -> int:
    """A hunk length; git omits it when it is 1."""
    return 1 if text is None else int(text)


def _slugs_overlapping(
    sections: list[EntrySection], ranges: list[tuple[int, int]]
) -> set[str]:
    return {
        section.slug
        for section in sections
        for lo, hi in ranges
        if section.start <= hi and lo <= section.end
    }


def _diff_state(inputs: Inputs) -> _DiffState:
    base_sections = entry_sections(inputs.base_text)
    head_sections = entry_sections(inputs.head_text)
    removed, added = _hunk_ranges(inputs.diff)
    base_slugs = {s.slug for s in base_sections}
    head_slugs = {s.slug for s in head_sections}
    touched = (
        (head_slugs - base_slugs)  # added entries
        | (base_slugs - head_slugs)  # deleted entries
        | _slugs_overlapping(head_sections, added)
        | _slugs_overlapping(base_sections, removed)  # pure deletions inside an entry
    )
    labels = {s.slug: s.label for s in base_sections}
    labels.update({s.slug: s.label for s in head_sections})
    return _DiffState(base_sections, head_sections, touched, labels)


def _split_list(value: str, labels: list[str]) -> list[str] | None:
    """Split on commas, re-joining pieces that form one known heading text
    (several headings contain commas). None when an item is empty."""
    pieces = [piece.strip() for piece in value.split(",")]
    if not all(pieces):
        return None
    known = {_normalise_label(label) for label in labels}
    names: list[str] = []
    i = 0
    while i < len(pieces):
        for end in range(len(pieces), i, -1):
            candidate = ", ".join(pieces[i:end])
            if _normalise_label(candidate) in known:
                names.append(candidate)
                i = end
                break
        else:
            names.append(pieces[i])
            i += 1
    return names


def _resolve(name: str, sections: list[EntrySection]) -> set[str]:
    """Slugs a written name denotes across both versions; empty when unknown."""
    if name.startswith("#"):
        return {s.slug for s in sections if s.slug == name[1:]}
    wanted = _normalise_label(name)
    return {s.slug for s in sections if _normalise_label(s.label) == wanted}


def _check_keyword(form: str, reason: str, load: Callable[[], Inputs]) -> tuple[int, str]:
    if not reason:
        return _fail([f"  `{form}` has a blank reason — write why."])
    if form == "restructure":
        return 0, f"docs-impact: restructure — {reason}\n"
    state = _diff_state(load())
    if state.touched:
        listed = [f"    {state.labels[slug]}" for slug in sorted(state.touched)]
        return _fail(
            ["  `none` but the diff touches these docs/features.md entries:", *listed,
             "  Name them in a list instead, or use restructure."]
        )
    return 0, f"docs-impact: none — {reason}; no entry section touched.\n"


def _check_list(value: str, load: Callable[[], Inputs]) -> tuple[int, str]:
    state = _diff_state(load())
    all_sections = [*state.head_sections, *state.base_sections]
    names = _split_list(value, [s.label for s in all_sections])
    if names is None:
        return _fail(["  the list has an empty item (stray comma)."])
    problems: list[str] = []
    named: set[str] = set()
    for name in names:
        slugs = _resolve(name, all_sections)
        if not slugs:
            problems.append(f"  unknown entry: {name}")
        elif len(slugs) > 1:
            problems.append(f"  ambiguous (several headings share this text): {name} — write its #slug")
        else:
            named |= slugs
    for slug in sorted(named - state.touched):
        problems.append(f"  named but not touched by the diff: {state.labels[slug]}")
    for slug in sorted(state.touched - named):
        problems.append(f"  touched but not named: {state.labels[slug]}")
    if problems:
        return _fail(problems)
    return 0, f"docs-impact: {len(named)} entr{'y' if len(named) == 1 else 'ies'} named and checked against the diff.\n"


def check(body: str, load: Callable[[], Inputs]) -> tuple[int, str]:
    """Decide the gate for a PR body. ``load`` supplies the diff inputs and is
    called only when the declaration needs them (a ``restructure`` line does not)."""
    values = _declarations(body)
    if not values:
        return _fail(["  no `Docs impact:` line in the PR body."])
    if len(values) > 1:
        return _fail(["  more than one `Docs impact:` line — keep exactly one."])
    value = values[0]
    if not value:
        return _fail(["  `Docs impact:` has no value."])
    if _PLACEHOLDER.search(value):
        return _fail(["  the value still holds a template placeholder (`<...>`) — write the real one."])
    keyword = _KEYWORD_FORM.fullmatch(value)
    if keyword is not None:
        return _check_keyword(keyword.group(1).lower(), keyword.group(2).strip(), load)
    if value.lower() in ("none", "restructure"):
        return _fail([f"  `{value.lower()}` needs a reason: `{value.lower()} — <reason>`."])
    return _check_list(value, load)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def _blob(rev: str) -> str:
    """The doc at ``rev``; empty when the file does not exist there."""
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{rev}:{DOC_PATH}"], capture_output=True
    )
    if probe.returncode != 0:
        return ""
    return _git("show", f"{rev}:{DOC_PATH}")


def _git_inputs(base_ref: str) -> Inputs:
    merge_base = _git("merge-base", base_ref, "HEAD").strip()
    return Inputs(
        base_text=_blob(merge_base),
        head_text=_blob("HEAD"),
        diff=_git("diff", "--unified=0", f"{base_ref}...HEAD", "--", DOC_PATH),
    )


def main() -> int:
    base_ref = os.environ.get("DIFF_BASE", "origin/master")
    try:
        rc, msg = check(os.environ.get("PR_BODY", ""), lambda: _git_inputs(base_ref))
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(f"docs-impact: BLOCKED — git failed: {exc.stderr or exc}\n")
        return 1
    (sys.stderr if rc else sys.stdout).write(msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
