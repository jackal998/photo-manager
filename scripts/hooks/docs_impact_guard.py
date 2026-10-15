"""CI gate: every PR body declares its feature-inventory impact, and the
declaration is checked against the PR's diff.

The feature inventory is split (#958): ``docs/features.md`` is the landing and
index, and each entry lives in ``docs/features/<area>.md``. The gate reads every
one of those files, at the merge base and at HEAD.

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
landing holds no entries, so an index-only edit touches no entry.

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
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from scripts.gen_features_index import EntrySection, entry_sections

DOC_PATH = "docs/features.md"
DOC_DIR = "docs/features"
_FEATURES_FILE = re.compile(r"^docs/features(?:\.md|/[^/]+\.md)$")

_KEY_LINE = re.compile(
    r"^[ \t]*(?:[-*][ \t]+)?(?:\*\*)?docs impact(?::\*\*|\*\*[ \t]*:|[ \t]*:)(.*)$",
    re.IGNORECASE,
)
_KEYWORD_FORM = re.compile(r"(none|restructure)[ \t]*(?:—|--|-)[ \t]*(.*)", re.IGNORECASE)
# An angle-bracketed token is a template placeholder: `<reason>`, `<entry>`.
_PLACEHOLDER = re.compile(r"<[^<>]*>")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_FILE_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
_COMMA_SPACING = re.compile(r"\s*,\s*")

FORMS_HINT = (
    "  The PR body needs ONE line `Docs impact: <value>`. <value> is one of:",
    "    a) a comma-separated list of docs/features entries, each the exact",
    "       `###` heading text or `#<slug>`:",
    "         Docs impact: Example entry, #example-slug",
    "    b) none — <reason>   (only when the diff touches NO entry section)",
    "         Docs impact: none — CI gate and tooling only",
    "    c) restructure — <reason>   (for PRs that move or split many entries)",
    "         Docs impact: restructure — split the Scan entries into two pages",
)

Ranges = list[tuple[int, int]]


def is_features_path(path: str) -> bool:
    """True for the landing docs/features.md and each docs/features/<area>.md."""
    return _FEATURES_FILE.match(path) is not None


@dataclass(frozen=True)
class Inputs:
    """The texts the check needs. ``base_texts`` and ``head_texts`` map a features
    path to its text at the merge base and at HEAD (a path absent on a side is
    simply missing from that mapping). ``diff`` is ``git diff --unified=0
    <base>...HEAD`` over the features paths, with its ``diff --git`` headers, so
    each hunk can be attributed to its file."""

    base_texts: Mapping[str, str]
    head_texts: Mapping[str, str]
    diff: str


@dataclass(frozen=True)
class _Entry:
    path: str
    key: str  # "<path>#<slug>": the identity of an entry across both sides
    label: str
    slug: str
    section: EntrySection


@dataclass(frozen=True)
class _DiffState:
    base_entries: list[_Entry]
    head_entries: list[_Entry]
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


def _count(text: str | None) -> int:
    """A hunk length; git omits it when it is 1."""
    return 1 if text is None else int(text)


def _hunks_by_path(diff: str) -> dict[str, tuple[Ranges, Ranges]]:
    """Per file: (removed base ranges, added head ranges), 1-based inclusive."""
    out: dict[str, tuple[Ranges, Ranges]] = {}
    current: str | None = None
    for line in diff.splitlines():
        header = _FILE_HEADER.match(line)
        if header is not None:
            current = header.group(2)
            out.setdefault(current, ([], []))
            continue
        match = _HUNK.match(line)
        if match is None or current is None:
            continue
        removed, added = out[current]
        old_start, old_len = int(match.group(1)), _count(match.group(2))
        new_start, new_len = int(match.group(3)), _count(match.group(4))
        if old_len:
            removed.append((old_start, old_start + old_len - 1))
        if new_len:
            added.append((new_start, new_start + new_len - 1))
    return out


def _overlaps(section: EntrySection, ranges: Ranges) -> bool:
    return any(section.start <= hi and lo <= section.end for lo, hi in ranges)


def _entries(texts: Mapping[str, str]) -> list[_Entry]:
    found: list[_Entry] = []
    for path in sorted(texts):
        for section in entry_sections(texts[path]):
            found.append(_Entry(path, f"{path}#{section.slug}", section.label, section.slug, section))
    return found


def _diff_state(inputs: Inputs) -> _DiffState:
    base_entries = _entries(inputs.base_texts)
    head_entries = _entries(inputs.head_texts)
    hunks = _hunks_by_path(inputs.diff)
    base_keys = {e.key for e in base_entries}
    head_keys = {e.key for e in head_entries}
    touched = (
        (head_keys - base_keys)  # added entries
        | (base_keys - head_keys)  # deleted entries
    )
    for entry in head_entries:  # additions and edits inside an entry
        touched |= {entry.key} if _overlaps(entry.section, hunks.get(entry.path, ([], []))[1]) else set()
    for entry in base_entries:  # pure deletions inside an entry
        touched |= {entry.key} if _overlaps(entry.section, hunks.get(entry.path, ([], []))[0]) else set()
    labels = {e.key: e.label for e in [*base_entries, *head_entries]}
    return _DiffState(base_entries, head_entries, touched, labels)


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


def _resolve(name: str, entries: list[_Entry]) -> set[str]:
    """Entry keys a written name denotes across both versions; empty when unknown."""
    if name.startswith("#"):
        return {e.key for e in entries if e.slug == name[1:]}
    wanted = _normalise_label(name)
    return {e.key for e in entries if _normalise_label(e.label) == wanted}


def _check_keyword(form: str, reason: str, load: Callable[[], Inputs]) -> tuple[int, str]:
    if not reason:
        return _fail([f"  `{form}` has a blank reason — write why."])
    if form == "restructure":
        return 0, f"docs-impact: restructure — {reason}\n"
    state = _diff_state(load())
    if state.touched:
        listed = [f"    {state.labels[key]}" for key in sorted(state.touched)]
        return _fail(
            ["  `none` but the diff touches these docs/features entries:", *listed,
             "  Name them in a list instead, or use restructure."]
        )
    return 0, f"docs-impact: none — {reason}; no entry section touched.\n"


def _check_list(value: str, load: Callable[[], Inputs]) -> tuple[int, str]:
    state = _diff_state(load())
    all_entries = [*state.head_entries, *state.base_entries]
    names = _split_list(value, [e.label for e in all_entries])
    if names is None:
        return _fail(["  the list has an empty item (stray comma)."])
    problems: list[str] = []
    named: set[str] = set()
    for name in names:
        keys = _resolve(name, all_entries)
        if not keys:
            problems.append(f"  unknown entry: {name}")
        elif len({key.rsplit("#", 1)[1] for key in keys}) > 1:
            # keys sharing one slug are the same entry on both sides of a move
            problems.append(f"  ambiguous (several headings share this text): {name} — write its #slug")
        else:
            named |= keys
    for key in sorted(named - state.touched):
        problems.append(f"  named but not touched by the diff: {state.labels[key]}")
    for key in sorted(state.touched - named):
        problems.append(f"  touched but not named: {state.labels[key]}")
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


def _features_paths(rev: str) -> list[str]:
    """The features files present at ``rev``: the landing and every area file."""
    listing = _git("ls-tree", "-r", "--name-only", rev, "--", DOC_PATH, DOC_DIR)
    return [path for path in listing.splitlines() if is_features_path(path)]


def _git_inputs(base_ref: str) -> Inputs:
    merge_base = _git("merge-base", base_ref, "HEAD").strip()
    return Inputs(
        base_texts={p: _git("show", f"{merge_base}:{p}") for p in _features_paths(merge_base)},
        head_texts={p: _git("show", f"HEAD:{p}") for p in _features_paths("HEAD")},
        diff=_git("diff", "--unified=0", f"{base_ref}...HEAD", "--", DOC_PATH, DOC_DIR),
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
