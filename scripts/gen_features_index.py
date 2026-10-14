"""Generate the index table in docs/features.md from its entry headings.

Usage:
    python scripts/gen_features_index.py            # rewrite the index block in place
    python scripts/gen_features_index.py --check    # exit 1 if the committed block is stale
    python scripts/gen_features_index.py PATH [--check]

Entries are the H3 headings outside code fences, except those under the
conventions H2 (CONVENTIONS_H2). Each entry must carry an `- **Area:** <value>`
line as the first non-blank line under its heading; <value> must be in
AREA_VOCABULARY. The index block sits between START_MARKER and END_MARKER and is
sorted case-insensitively by feature name (the heading text).

Exit codes: 0 ok, 1 index out of date (--check), 2 structural error (missing
markers, missing or unknown Area, malformed label).
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

START_MARKER = "<!-- features-index:start -->"
END_MARKER = "<!-- features-index:end -->"
CONVENTIONS_H2 = "How to update this file"
# Frozen snapshot of the Area values already used by the entries. Adding an area
# is a deliberate one-line edit here, never a side effect of editing an entry.
AREA_VOCABULARY = (
    "Bulk operations",
    "Context menu",
    "Execute Action",
    "File operations",
    "Main window",
    "Menus",
    "Preview pane",
    "Review",
    "Scan",
    "Set Action dialog",
    "Web UI",
    "i18n",
)
BLOCK_FALLBACK = (
    "index block differs from generated output (header, blank, duplicate or unparsed line); "
    "regenerate with scripts/gen_features_index.py"
)
DEFAULT_PATH =Path(__file__).resolve().parent.parent / "docs" / "features.md"

_HEADING_RE = re.compile(r"^(#{1,6}) (.+?)\s*$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
_AREA_RE = re.compile(r"^- \*\*Area:\*\* (.+?)\s*$")
_ROW_RE = re.compile(r"^\| \[(.*)\]\(#([^)]*)\) \| (.*) \|$")


class StructureError(Exception):
    """A condition the generator cannot repair; the file must be fixed by hand."""


@dataclass(frozen=True)
class Heading:
    line_idx: int
    level: int
    text: str
    h2: str | None


@dataclass(frozen=True)
class Entry:
    label: str
    slug: str
    area: str
    line_idx: int


def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n")


def _base_slug(text: str) -> str:
    """GitHub heading slug before de-duplication."""
    kept = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return kept.replace(" ", "-")


def slugify_all(texts: list[str]) -> list[str]:
    """GitHub anchors for headings in document order: duplicates get -1, -2, ..."""
    used: set[str] = set()
    slugs: list[str] = []
    for text in texts:
        base = _base_slug(text)
        slug, n = base, 0
        while slug in used:
            n += 1
            slug = f"{base}-{n}"
        used.add(slug)
        slugs.append(slug)
    return slugs


def _headings(lines: list[str]) -> list[Heading]:
    """All ATX headings outside fenced code blocks, tagged with their H2 section."""
    found: list[Heading] = []
    fence: str | None = None
    h2: str | None = None
    for idx, line in enumerate(lines):
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        match = _HEADING_RE.match(line) if fence is None else None
        if not match:
            continue
        level, text = len(match.group(1)), match.group(2)
        if level == 2:
            h2 = text
        found.append(Heading(idx, level, text, h2))
    return found


def _area_under(lines: list[str], heading: Heading) -> str:
    for line in lines[heading.line_idx + 1 :]:
        if not line.strip():
            continue
        match = _AREA_RE.match(line)
        if not match:
            break
        area = match.group(1)
        if area not in AREA_VOCABULARY:
            raise StructureError(
                f"entry '{heading.text}': Area '{area}' is not in the vocabulary "
                f"{list(AREA_VOCABULARY)} (see AREA_VOCABULARY in gen_features_index.py)"
            )
        return area
    raise StructureError(
        f"entry '{heading.text}' (line {heading.line_idx + 1}) has no "
        "'- **Area:** <value>' line directly under its heading"
    )


def parse_entries(text: str) -> list[Entry]:
    lines = _normalise(text).split("\n")
    headings = _headings(lines)
    slugs = slugify_all([h.text for h in headings])
    entries: list[Entry] = []
    for heading, slug in zip(headings, slugs):
        if heading.level != 3 or heading.h2 == CONVENTIONS_H2:
            continue
        if any(ch in heading.text for ch in "[]|"):
            raise StructureError(f"entry heading '{heading.text}' contains [ ] or | and cannot be a table label")
        area = _area_under(lines, heading)
        entries.append(Entry(heading.text, slug, area, heading.line_idx))
    return entries


def _sort_key(entry: Entry) -> tuple[str, str]:
    return (entry.label.casefold(), entry.slug)


def render_block(entries: list[Entry]) -> list[str]:
    rows = [f"| [{e.label}](#{e.slug}) | {e.area} |" for e in sorted(entries, key=_sort_key)]
    return [START_MARKER, "| Feature | Area |", "|---|---|", *rows, END_MARKER]


def _block_span(lines: list[str]) -> tuple[int, int]:
    starts = [i for i, ln in enumerate(lines) if ln == START_MARKER]
    ends = [i for i, ln in enumerate(lines) if ln == END_MARKER]
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise StructureError(f"expected exactly one '{START_MARKER}' before one '{END_MARKER}'")
    return starts[0], ends[0]


def _committed_rows(block: list[str]) -> list[tuple[str, str, str]]:
    rows = []
    for line in block:
        match = _ROW_RE.match(line)
        if match:
            rows.append((match.group(1), match.group(2), match.group(3)))
    return rows


def _row_problems(expected: list[str], committed: list[tuple[str, str, str]]) -> list[str]:
    exp_rows = [m.groups() for m in (_ROW_RE.match(r) for r in expected) if m]
    exp_labels = [label for label, _, _ in exp_rows]
    com_labels = [label for label, _, _ in committed]
    problems = [f"missing from index: {lab}" for lab in exp_labels if lab not in com_labels]
    problems += [f"extra in index (no such entry): {lab}" for lab in com_labels if lab not in exp_labels]
    if problems:
        return problems
    if com_labels != exp_labels:
        for i, (exp, com) in enumerate(zip(exp_labels, com_labels)):
            if exp != com:
                return [f"misordered: row {i + 1} should be '{exp}' but is '{com}' (sorted by feature name)"]
    exp_by_label = {label: (anchor, area) for label, anchor, area in exp_rows}
    com_by_label = {label: (anchor, area) for label, anchor, area in committed}
    return [
        f"stale row (anchor or Area differs): {label}"
        for label in exp_labels
        if exp_by_label[label] != com_by_label[label]
    ]


def check_text(text: str) -> list[str]:
    """Return a list of problems; an empty list means the committed block is current."""
    text = _normalise(text)
    expected = render_block(parse_entries(text))
    lines = text.split("\n")
    start, end = _block_span(lines)
    committed = lines[start : end + 1]
    if committed == expected:
        return []
    return _row_problems(expected[3:-1], _committed_rows(committed)) or [BLOCK_FALLBACK]


def regenerate_text(text: str) -> str:
    text = _normalise(text)
    block = render_block(parse_entries(text))
    lines = text.split("\n")
    start, end = _block_span(lines)
    return "\n".join([*lines[:start], *block, *lines[end + 1 :]])


def _read(path: Path) -> str:
    return _normalise(path.read_bytes().decode("utf-8"))


def _write_lf(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main(argv: list[str]) -> int:
    check = "--check" in argv
    positional = [a for a in argv if a != "--check"]
    path = Path(positional[0]) if positional else DEFAULT_PATH
    try:
        text = _read(path)
        if check:
            problems = check_text(text)
            if problems:
                print(f"features index out of date in {path}:")
                for problem in problems:
                    print(f"  - {problem}")
                print("fix: python scripts/gen_features_index.py")
                return 1
            print(f"features index up to date ({len(parse_entries(text))} entries)")
            return 0
        new_text = regenerate_text(text)
        _write_lf(path, new_text)
        print(f"wrote features index ({len(parse_entries(new_text))} entries) to {path}")
        return 0
    except (StructureError, OSError, UnicodeDecodeError) as exc:
        print(f"gen_features_index: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
