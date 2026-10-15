"""Generate the index table in docs/features.md from the per-Area entry files.

The feature inventory is split (#958): each entry lives in docs/features/<area>.md,
one file per Area value, and docs/features.md is the landing page holding the
generated index and the section schema. The index links every entry to its file
(``features/<area>.md#<slug>``).

Usage:
    python scripts/gen_features_index.py            # rewrite the index block in place
    python scripts/gen_features_index.py --check    # exit 1 if the committed block is stale
    python scripts/gen_features_index.py PATH [--check]

The area files are read from ``features/`` beside PATH (``docs/features/``).

Entries are the H3 headings outside code fences, except those under the
conventions H2 (CONVENTIONS_H2). Each entry must carry an `- **Area:** <value>`
line as the first non-blank line under its heading; <value> must be in
AREA_VOCABULARY and must match the file the entry lives in (see area_file_name).
The landing holds no entries. The index block sits between START_MARKER and
END_MARKER and is sorted case-insensitively by feature name (the heading text).

Exit codes: 0 ok, 1 index out of date (--check), 2 structural error (missing
markers, missing or unknown Area, an entry in the wrong area file, an unknown
area file, malformed label).
"""
from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
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
# Directory of the area files, relative to the landing docs/features.md.
FEATURES_SUBDIR = "features"
BLOCK_FALLBACK = (
    "index block differs from generated output (header, blank, duplicate or unparsed line); "
    "regenerate with scripts/gen_features_index.py"
)
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "docs" / "features.md"

_HEADING_RE = re.compile(r"^(#{1,6}) (.+?)\s*$")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
_AREA_RE = re.compile(r"^- \*\*Area:\*\* (.+?)\s*$")
_ROW_RE = re.compile(r"^\| \[(.*)\]\(([^)]*)\) \| (.*) \|$")


class StructureError(Exception):
    """A condition the generator cannot repair; the file must be fixed by hand."""


def area_file_name(area: str) -> str:
    """The area file for an Area value: lowercase, runs of non-alphanumerics become
    '-', e.g. 'Set Action dialog' -> 'set-action-dialog.md', 'i18n' -> 'i18n.md'.
    This function is the single source of the Area -> file mapping."""
    if area not in AREA_VOCABULARY:
        raise StructureError(f"Area '{area}' is not in AREA_VOCABULARY")
    return re.sub(r"[^a-z0-9]+", "-", area.casefold()).strip("-") + ".md"


def area_file_names() -> frozenset[str]:
    return frozenset(area_file_name(area) for area in AREA_VOCABULARY)


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
    file: str = ""


@dataclass(frozen=True)
class EntrySection:
    """An entry and the lines it owns: the heading through the line before the
    next heading of level 1-3. ``start`` and ``end`` are 1-based and inclusive."""

    label: str
    slug: str
    start: int
    end: int


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


def _is_entry(heading: Heading) -> bool:
    return heading.level == 3 and heading.h2 != CONVENTIONS_H2


def entry_sections(text: str) -> list[EntrySection]:
    """Every entry with the line span it owns, in document order (for the docs-impact gate)."""
    lines = _normalise(text).split("\n")
    headings = _headings(lines)
    slugs = slugify_all([h.text for h in headings])
    sections: list[EntrySection] = []
    for pos, (heading, slug) in enumerate(zip(headings, slugs)):
        if not _is_entry(heading):
            continue
        following = [h.line_idx for h in headings[pos + 1 :] if h.level <= 3]
        end_idx = following[0] if following else len(lines)
        sections.append(EntrySection(heading.text, slug, heading.line_idx + 1, end_idx))
    return sections


def parse_entries(text: str) -> list[Entry]:
    lines = _normalise(text).split("\n")
    headings = _headings(lines)
    slugs = slugify_all([h.text for h in headings])
    entries: list[Entry] = []
    for heading, slug in zip(headings, slugs):
        if not _is_entry(heading):
            continue
        if any(ch in heading.text for ch in "[]|"):
            raise StructureError(f"entry heading '{heading.text}' contains [ ] or | and cannot be a table label")
        area = _area_under(lines, heading)
        entries.append(Entry(heading.text, slug, area, heading.line_idx))
    return entries


def collect_entries(area_texts: Mapping[str, str]) -> list[Entry]:
    """Entries of every area file (name -> text), each tagged with its file. Fails
    closed on an unknown file, an empty area file, or an entry whose Area does not
    match the file it lives in."""
    known = area_file_names()
    entries: list[Entry] = []
    file_of_label: dict[str, str] = {}
    for fname in sorted(area_texts):
        if fname not in known:
            raise StructureError(
                f"docs/features/{fname} is not an area file (expected one of {sorted(known)})"
            )
        found = parse_entries(area_texts[fname])
        if not found:
            raise StructureError(f"docs/features/{fname} holds no entries; delete the file")
        for entry in found:
            expected = area_file_name(entry.area)
            if expected != fname:
                raise StructureError(
                    f"entry '{entry.label}' has Area '{entry.area}' but lives in "
                    f"docs/features/{fname}; it belongs in docs/features/{expected}"
                )
            other = file_of_label.get(entry.label)
            if other is not None and other != fname:
                raise StructureError(
                    f"entry '{entry.label}' is defined in docs/features/{other} and "
                    f"docs/features/{fname}; a feature name must name one entry"
                )
            file_of_label[entry.label] = fname
        entries.extend(replace(entry, file=fname) for entry in found)
    return entries


def _sort_key(entry: Entry) -> tuple[str, str]:
    return (entry.label.casefold(), entry.slug)


def render_block(entries: list[Entry]) -> list[str]:
    rows = [
        f"| [{e.label}]({FEATURES_SUBDIR}/{e.file}#{e.slug}) | {e.area} |"
        for e in sorted(entries, key=_sort_key)
    ]
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


def _no_entries_in_landing(text: str) -> None:
    if parse_entries(text):
        raise StructureError(
            "the landing docs/features.md holds entries; each entry belongs in "
            "docs/features/<area>.md"
        )


def check_text(text: str, area_texts: Mapping[str, str] | None = None) -> list[str]:
    """Return a list of problems; an empty list means the committed block is current."""
    text = _normalise(text)
    _no_entries_in_landing(text)
    expected = render_block(collect_entries(area_texts or {}))
    lines = text.split("\n")
    start, end = _block_span(lines)
    committed = lines[start : end + 1]
    if committed == expected:
        return []
    return _row_problems(expected[3:-1], _committed_rows(committed)) or [BLOCK_FALLBACK]


def regenerate_text(text: str, area_texts: Mapping[str, str] | None = None) -> str:
    text = _normalise(text)
    _no_entries_in_landing(text)
    block = render_block(collect_entries(area_texts or {}))
    lines = text.split("\n")
    start, end = _block_span(lines)
    return "\n".join([*lines[:start], *block, *lines[end + 1 :]])


def _read(path: Path) -> str:
    return _normalise(path.read_bytes().decode("utf-8"))


def _write_lf(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _area_texts(landing: Path) -> dict[str, str]:
    """Every docs/features/*.md beside the landing, by file name; empty when absent."""
    folder = landing.parent / FEATURES_SUBDIR
    if not folder.is_dir():
        return {}
    return {p.name: _read(p) for p in sorted(folder.glob("*.md"))}


def main(argv: list[str]) -> int:
    check = "--check" in argv
    positional = [a for a in argv if a != "--check"]
    path = Path(positional[0]) if positional else DEFAULT_PATH
    try:
        text = _read(path)
        areas = _area_texts(path)
        if check:
            problems = check_text(text, areas)
            if problems:
                print(f"features index out of date in {path}:")
                for problem in problems:
                    print(f"  - {problem}")
                print("fix: python scripts/gen_features_index.py")
                return 1
            print(f"features index up to date ({len(collect_entries(areas))} entries in {len(areas)} area files)")
            return 0
        new_text = regenerate_text(text, areas)
        _write_lf(path, new_text)
        print(f"wrote features index ({len(collect_entries(areas))} entries) to {path}")
        return 0
    except (StructureError, OSError, UnicodeDecodeError) as exc:
        print(f"gen_features_index: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
