"""Tests for scripts/gen_features_index.py (the generated index of docs/features.md).

The inventory is split (#958): docs/features.md is the landing and generated
index; each entry lives in docs/features/<area>.md. Each case exercises a failure
a reviewer would otherwise miss by hand: a dropped entry, a mis-sorted index, an
entry filed under the wrong Area, a CRLF checkout, the GitHub heading-slug rule,
and the fail-closed structure checks.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import scripts.gen_features_index as gfi

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "gen_features_index.py"


def _entry_block(heading: str, area: str) -> str:
    return f"### {heading}\n\n- **Area:** {area}\n\n- **Behaviour:** text\n\n"


def _landing(extra: str = "") -> str:
    """The landing with an EMPTY index block (not yet generated) and the schema."""
    return (
        "# Feature Inventory\n\n## Index\n\n"
        f"{gfi.START_MARKER}\n{gfi.END_MARKER}\n\n---\n\n"
        f"{extra}"
        f"## {gfi.CONVENTIONS_H2}\n\n"
        "### Section schema\n\n"
        "```markdown\n### <Feature name>\n\n- **Area:** <value>\n```\n"
    )


def _areas(entries: list[tuple[str, str]]) -> dict[str, str]:
    """Area files (file name -> text) holding the entries, each filed by its Area."""
    files: dict[str, str] = {}
    for heading, area in entries:
        name = gfi.area_file_name(area)
        files.setdefault(name, f"# Features — {area}\n\n")
        files[name] += _entry_block(heading, area)
    return files


def _generated(entries: list[tuple[str, str]]) -> tuple[str, dict[str, str]]:
    """The landing with its index generated from ``entries``, and the area files."""
    areas = _areas(entries)
    return gfi.regenerate_text(_landing(), areas), areas


_ENTRIES = [
    ("Scan dialog — folder list (no priority arrows)", "Scan"),
    ("Web — settings dialog", "Web UI"),
    ("Similarity column", "Review"),
]


def test_generated_landing_passes_check() -> None:
    text, areas = _generated(_ENTRIES)
    assert gfi.check_text(text, areas) == []


def test_index_rows_link_into_the_area_file() -> None:
    text, _ = _generated(_ENTRIES)
    assert "| [Similarity column](features/review.md#similarity-column) | Review |" in text


def test_missing_entry_is_detected() -> None:
    text, _ = _generated(_ENTRIES)
    areas = _areas(_ENTRIES[:2])  # the Similarity column file is gone, its row is not
    problems = gfi.check_text(text, areas)
    assert any("extra in index" in p and "Similarity column" in p for p in problems), problems


def test_missing_row_is_detected() -> None:
    text, areas = _generated(_ENTRIES)
    lines = [ln for ln in text.split("\n") if "Similarity column" not in ln or not ln.startswith("| [")]
    problems = gfi.check_text("\n".join(lines), areas)
    assert any("missing" in p and "Similarity column" in p for p in problems), problems


def test_misordered_index_is_detected() -> None:
    text, areas = _generated(_ENTRIES)
    rows = [ln for ln in text.split("\n") if ln.startswith("| [")]
    swapped = text.replace(rows[0], "@@A@@").replace(rows[1], rows[0]).replace("@@A@@", rows[1])
    problems = gfi.check_text(swapped, areas)
    assert problems and any("misordered" in p for p in problems), problems


def test_single_stale_row_names_only_that_label() -> None:
    text, areas = _generated(_ENTRIES)
    row = "| [Similarity column](features/review.md#similarity-column) | Review |"
    stale = row.replace("| Review |", "| Menus |")
    problems = gfi.check_text(text.replace(row, stale), areas)
    assert problems == ["stale row (anchor or Area differs): Similarity column"], problems


def _dup_last_row(text: str) -> str:
    rows = [ln for ln in text.split("\n") if ln.startswith("| [")]
    return text.replace(rows[-1], rows[-1] + "\n" + rows[-1], 1)


def _blank_after_separator(text: str) -> str:
    return text.replace("|---|---|\n", "|---|---|\n\n", 1)


def _rename_header(text: str) -> str:
    return text.replace("| Feature | Area |", "| Name | Area |", 1)


def _stray_line(text: str) -> str:
    rows = [ln for ln in text.split("\n") if ln.startswith("| [")]
    return text.replace(rows[0], rows[0] + "\nstray text", 1)


@pytest.mark.parametrize(
    "probe",
    [_dup_last_row, _blank_after_separator, _rename_header, _stray_line],
    ids=["duplicate-row", "blank-line", "header", "stray-text"],
)
def test_block_difference_with_matching_rows_still_fails(probe, tmp_path: Path) -> None:
    text, areas = _generated(_ENTRIES)
    text = probe(text)
    problems = gfi.check_text(text, areas)
    assert problems, "a differing block must never pass"
    path = tmp_path / "features.md"
    path.write_bytes(text.encode("utf-8"))
    (tmp_path / "features").mkdir()
    for name, body in areas.items():
        (tmp_path / "features" / name).write_bytes(body.encode("utf-8"))
    assert gfi.main(["--check", str(path)]) == 1


def test_crlf_copy_of_correct_tree_passes() -> None:
    text, areas = _generated(_ENTRIES)
    crlf_areas = {name: body.replace("\n", "\r\n") for name, body in areas.items()}
    assert gfi.check_text(text.replace("\n", "\r\n"), crlf_areas) == []


def test_real_features_tree_passes_check_cli() -> None:
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=_REPO,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_misfiled_entry_fails_closed_and_names_the_right_file() -> None:
    areas = _areas(_ENTRIES)
    areas["bulk-operations.md"] = areas.pop("review.md")
    with pytest.raises(gfi.StructureError, match=r"Similarity column.*docs/features/review\.md"):
        gfi.collect_entries(areas)


def test_misfiled_entry_check_cli_exits_two(tmp_path: Path) -> None:
    text, areas = _generated(_ENTRIES)
    (tmp_path / "features").mkdir()
    areas_wrong = dict(areas)
    areas_wrong["bulk-operations.md"] = areas_wrong.pop("review.md")
    (tmp_path / "features.md").write_bytes(text.encode("utf-8"))
    for name, body in areas_wrong.items():
        (tmp_path / "features" / name).write_bytes(body.encode("utf-8"))
    assert gfi.main(["--check", str(tmp_path / "features.md")]) == 2


def test_area_file_name_maps_every_vocabulary_value_to_a_distinct_file() -> None:
    names = [gfi.area_file_name(area) for area in gfi.AREA_VOCABULARY]
    assert len(set(names)) == len(gfi.AREA_VOCABULARY)
    assert gfi.area_file_name("Set Action dialog") == "set-action-dialog.md"
    assert gfi.area_file_name("i18n") == "i18n.md"
    assert gfi.area_file_name("Web UI") == "web-ui.md"


def test_area_file_name_rejects_a_value_outside_the_vocabulary() -> None:
    with pytest.raises(gfi.StructureError, match="Reviewz"):
        gfi.area_file_name("Reviewz")


def test_unknown_area_file_fails_closed() -> None:
    with pytest.raises(gfi.StructureError, match="notes.md"):
        gfi.collect_entries({"notes.md": _entry_block("Similarity column", "Review")})


def test_empty_area_file_fails_closed() -> None:
    with pytest.raises(gfi.StructureError, match="holds no entries"):
        gfi.collect_entries({"review.md": "# Features — Review\n\n"})


def test_same_feature_name_in_two_area_files_fails_closed() -> None:
    areas = {
        "review.md": _entry_block("Similarity column", "Review"),
        "scan.md": _entry_block("Similarity column", "Scan"),
    }
    with pytest.raises(gfi.StructureError, match="defined in"):
        gfi.collect_entries(areas)


def test_entry_in_landing_fails_closed() -> None:
    landing = _landing(_entry_block("Similarity column", "Review"))
    with pytest.raises(gfi.StructureError, match="landing"):
        gfi.check_text(landing, {})


def test_schema_and_fenced_template_are_not_entries() -> None:
    areas = _areas(_ENTRIES)
    labels = [e.label for e in gfi.collect_entries(areas)]
    assert sorted(labels) == sorted(label for label, _ in _ENTRIES)
    assert gfi.parse_entries(_landing()) == []


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


def test_entry_without_area_fails_closed() -> None:
    areas = _areas(_ENTRIES)
    areas["review.md"] = areas["review.md"].replace("- **Area:** Review\n\n", "", 1)
    with pytest.raises(gfi.StructureError, match="Similarity column"):
        gfi.collect_entries(areas)


def test_area_outside_vocabulary_fails_closed() -> None:
    with pytest.raises(gfi.StructureError, match="Reviewz"):
        gfi.collect_entries({"review.md": _entry_block("Similarity column", "Reviewz")})
