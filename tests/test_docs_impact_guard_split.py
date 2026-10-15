"""docs_impact_guard after the #958 split: entries live in docs/features/<area>.md.

The failure these prevent: the gate read only docs/features.md, so an entry edited
in an area file was invisible to it and any Docs impact claim passed. Each case
uses a real area-file path and an ordinary claim, so a pass is not produced by
refusing everything.
"""
from __future__ import annotations

from scripts.hooks.docs_impact_guard import Inputs, check, is_features_path
from tests.test_docs_impact_guard import ALPHA, AREA, BETA, DOC, _doc, _run_files

_AREA_HEAD = "# Features — Scan\n\n"


def _area(*entries: str) -> str:
    return _AREA_HEAD + "".join(entries)


def _landing_empty() -> str:
    return _doc()


def test_area_file_entry_edit_named_passes() -> None:
    base = {DOC: _landing_empty(), AREA: _area(ALPHA, BETA)}
    head = {DOC: _landing_empty(), AREA: _area(ALPHA.replace("Line two.", "Line two, edited."), BETA)}
    rc, msg = _run_files("Docs impact: Alpha feature", base, head)
    assert rc == 0, msg


def test_area_file_entry_edit_not_named_is_blocked_none() -> None:
    base = {DOC: _landing_empty(), AREA: _area(ALPHA, BETA)}
    head = {DOC: _landing_empty(), AREA: _area(ALPHA.replace("Line two.", "Line two, edited."), BETA)}
    rc, msg = _run_files("Docs impact: none — tidy wording", base, head)
    assert rc == 1
    assert "    Alpha feature" in msg


def test_area_file_entry_touched_but_other_entry_named_is_blocked() -> None:
    base = {DOC: _landing_empty(), AREA: _area(ALPHA, BETA)}
    head = {DOC: _landing_empty(), AREA: _area(ALPHA, BETA.replace("Beta text.", "Beta text, edited."))}
    rc, msg = _run_files("Docs impact: Alpha feature", base, head)
    assert rc == 1
    assert "touched but not named: Beta feature, with comma" in msg


def test_entry_moved_from_landing_to_area_file_counts_as_touched() -> None:
    base = {DOC: _doc(ALPHA)}
    head = {DOC: _landing_empty(), AREA: _area(ALPHA)}
    rc, msg = _run_files("Docs impact: none — no entry changed", base, head)
    assert rc == 1
    assert "    Alpha feature" in msg
    rc, msg = _run_files("Docs impact: Alpha feature", base, head)
    assert rc == 0, msg


def test_index_only_landing_edit_touches_no_entry() -> None:
    base = {DOC: _landing_empty(), AREA: _area(ALPHA)}
    head = {DOC: _doc(index_row="| [Alpha feature](features/scan.md#alpha-feature) | Scan |\n"), AREA: _area(ALPHA)}
    rc, msg = _run_files("Docs impact: none — index refresh only", base, head)
    assert rc == 0, msg


def test_unchanged_area_file_with_untouched_entry_is_not_a_touch() -> None:
    files = {DOC: _landing_empty(), AREA: _area(ALPHA)}
    rc, msg = _run_files("Docs impact: none — tooling only", files, files)
    assert rc == 0, msg


def test_named_entry_resolves_by_slug_in_its_area_file() -> None:
    base = {DOC: _landing_empty(), AREA: _area(ALPHA, BETA)}
    head = {DOC: _landing_empty(), AREA: _area(ALPHA.replace("Line two.", "Line two, edited."), BETA)}
    rc, msg = _run_files("Docs impact: #alpha-feature", base, head)
    assert rc == 0, msg


def test_landing_path_matches_only_the_landing_and_area_files() -> None:
    assert is_features_path("docs/features.md")
    assert is_features_path("docs/features/scan.md")
    assert not is_features_path("docs/features/nested/scan.md")
    assert not is_features_path("docs/features/scan.txt")
    assert not is_features_path("docs/behaviour/rules.md")


def test_check_accepts_a_real_inputs_object() -> None:
    files = {DOC: _landing_empty(), AREA: _area(ALPHA)}
    rc, _ = check("Docs impact: restructure — split test", lambda: Inputs(files, files, ""))
    assert rc == 0
