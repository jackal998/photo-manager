"""Tests for scripts/gen_features_index.py (the generated index of docs/features.md).

Each case exercises a failure a reviewer would otherwise miss by hand: a dropped
entry, a mis-sorted index, a CRLF checkout, structural entry detection, the
GitHub heading-slug rule, and the fail-closed Area checks.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import scripts.gen_features_index as gfi

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "gen_features_index.py"


def _raw_doc(entries: list[tuple[str, str]]) -> str:
    """A features.md-shaped document with an EMPTY index block (not yet generated)."""
    body = "".join(
        f"### {heading}\n\n- **Area:** {area}\n\n- **Behaviour:** text\n\n"
        for heading, area in entries
    )
    return (
        "# Feature Inventory\n\n## Index\n\n"
        f"{gfi.START_MARKER}\n{gfi.END_MARKER}\n\n---\n\n"
        f"{body}"
        f"## {gfi.CONVENTIONS_H2}\n\n"
        "### Section schema\n\n"
        "```markdown\n### <Feature name>\n\n- **Area:** <value>\n```\n"
    )


def _doc(entries: list[tuple[str, str]]) -> str:
    """The same document with its index block generated."""
    return gfi.regenerate_text(_raw_doc(entries))


_ENTRIES = [
    ("Scan dialog — folder list (no priority arrows)", "Scan"),
    ("Web — settings dialog", "Web UI"),
    ("Similarity column", "Review"),
]


def test_missing_entry_is_detected() -> None:
    text = _doc(_ENTRIES)
    lines = [ln for ln in text.split("\n") if "Similarity column" not in ln or "](#" not in ln]
    problems = gfi.check_text("\n".join(lines))
    assert any("missing" in p and "Similarity column" in p for p in problems), problems


def test_misordered_index_is_detected() -> None:
    text = _doc(_ENTRIES)
    rows = [ln for ln in text.split("\n") if ln.startswith("| [")]
    swapped = text.replace(rows[0], "@@A@@").replace(rows[1], rows[0]).replace("@@A@@", rows[1])
    problems = gfi.check_text(swapped)
    assert problems and any("misordered" in p for p in problems), problems


def test_crlf_copy_of_correct_file_passes() -> None:
    text = _doc(_ENTRIES)
    assert gfi.check_text(text.replace("\n", "\r\n")) == []


def test_real_features_md_passes_check_cli() -> None:
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=_REPO,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("heading", "slug"),
    [
        ("Web — overlay window geometry (movable / resizable / persisted)",
         "web--overlay-window-geometry-movable--resizable--persisted"),
        ("Execute Action — filter by action type (#502)",
         "execute-action--filter-by-action-type-502"),
        ("Web — app shell never scrolls; the tree does (#894)",
         "web--app-shell-never-scrolls-the-tree-does-894"),
    ],
)
def test_heading_slugs_follow_github_rule(heading: str, slug: str) -> None:
    assert gfi.slugify_all([heading]) == [slug]


def test_duplicate_headings_get_numeric_suffixes() -> None:
    assert gfi.slugify_all(["Similarity column", "Similarity column"]) == [
        "similarity-column",
        "similarity-column-1",
    ]


def test_schema_and_fenced_template_are_not_entries() -> None:
    labels = [e.label for e in gfi.parse_entries(_doc(_ENTRIES))]
    assert labels == [label for label, _ in _ENTRIES]


def test_entry_without_area_fails_closed() -> None:
    text = _doc(_ENTRIES).replace("- **Area:** Review\n\n", "", 1)
    with pytest.raises(gfi.StructureError, match="Similarity column"):
        gfi.check_text(text)


def test_area_outside_vocabulary_fails_closed() -> None:
    text = _raw_doc([("Similarity column", "Reviewz")])
    with pytest.raises(gfi.StructureError, match="Reviewz"):
        gfi.check_text(text)
