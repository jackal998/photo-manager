"""Tests for scripts/hooks/docs_impact_guard.py — the CI gate that makes every
PR body declare its ``docs/features.md`` impact and checks that claim against
the diff.

Failure mode these tests prevent: every existing docs guard only checks that
``docs/features.md`` was TOUCHED. A PR can touch the file and still leave the
wrong entries stale, because nothing compares which entries the PR claims to
affect with the entries its diff actually changed.

Diffs are built from small fixture strings with ``difflib`` (``n=0``, the same
hunk convention as ``git diff --unified=0``). No git calls, no network.
"""
from __future__ import annotations

import difflib

import pytest

from scripts.hooks.docs_impact_guard import Inputs, check

_PREAMBLE = (
    "# Feature Inventory\n\n## Index\n\n"
    "<!-- features-index:start -->\n"
    "| Feature | Area |\n|---|---|\n"
    "| [Alpha feature](#alpha-feature) | Scan |\n"
    "<!-- features-index:end -->\n\n---\n\n"
)
_CONVENTIONS = "## How to update this file\n\n### Section schema\n\nSchema text.\n\n"


def _entry(heading: str, body: str) -> str:
    return f"### {heading}\n\n- **Area:** Scan\n\n{body}\n\n"


def _doc(*entries: str, index_row: str | None = None) -> str:
    preamble = _PREAMBLE
    if index_row is not None:
        preamble = preamble.replace("| [Alpha feature](#alpha-feature) | Scan |\n", index_row)
    return preamble + "".join(entries) + _CONVENTIONS


ALPHA = _entry("Alpha feature", "Line one.\nLine two.")
BETA = _entry("Beta feature, with comma", "Beta text.")
GAMMA = _entry("Gamma feature", "Gamma text.")
BASE = _doc(ALPHA, BETA)


def _diff(base: str, head: str) -> str:
    return "".join(
        difflib.unified_diff(
            base.splitlines(keepends=True), head.splitlines(keepends=True), n=0
        )
    )


def _run(body: str, base: str = BASE, head: str = BASE) -> tuple[int, str]:
    return check(body, lambda: Inputs(base_text=base, head_text=head, diff=_diff(base, head)))


def _touch_alpha() -> str:
    return _doc(_entry("Alpha feature", "Line one.\nLine two, edited."), BETA)


def _touch_beta() -> str:
    return _doc(ALPHA, _entry("Beta feature, with comma", "Beta text, edited."))


# --- form (a): a list of entries -------------------------------------------

def test_list_by_heading_text_passes_when_named_entry_is_touched() -> None:
    rc, msg = _run("Docs impact: Alpha feature", head=_touch_alpha())
    assert rc == 0, msg


def test_list_by_slug_passes_when_named_entry_is_touched() -> None:
    rc, msg = _run("Docs impact: #alpha-feature", head=_touch_alpha())
    assert rc == 0, msg


def test_heading_text_containing_a_comma_is_one_entry() -> None:
    rc, msg = _run("Docs impact: Beta feature, with comma", head=_touch_beta())
    assert rc == 0, msg


def test_named_entry_not_touched_fails_and_names_it() -> None:
    rc, msg = _run("Docs impact: Alpha feature", head=_touch_beta())
    assert rc == 1
    assert "Alpha feature" in msg


def test_touched_entry_not_named_fails_and_names_it() -> None:
    rc, msg = _run("Docs impact: Alpha feature", head=_touch_beta())
    assert rc == 1
    assert "Beta feature, with comma" in msg


def test_unknown_entry_fails_and_names_it() -> None:
    rc, msg = _run("Docs impact: Gamma feature", head=_touch_alpha())
    assert rc == 1
    assert "Gamma feature" in msg


def test_template_placeholder_left_in_list_fails() -> None:
    rc, msg = _run("Docs impact: <entry heading or #slug>", head=_touch_alpha())
    assert rc == 1
    assert "placeholder" in msg


def test_pure_deletion_inside_entry_counts_as_touch() -> None:
    head = _doc(_entry("Alpha feature", "Line one."), BETA)
    rc, msg = _run("Docs impact: Alpha feature", head=head)
    assert rc == 0, msg


def test_deleted_entry_named_passes() -> None:
    base = _doc(ALPHA, BETA, GAMMA)
    head = _doc(ALPHA, BETA)
    rc, msg = _run("Docs impact: Gamma feature", base=base, head=head)
    assert rc == 0, msg


# --- forms (b) and (c): none / restructure ---------------------------------

@pytest.mark.parametrize("sep", ["—", "--", "-"])
def test_none_accepts_each_separator(sep: str) -> None:
    rc, msg = _run(f"Docs impact: none {sep} CI gate and tooling only")
    assert rc == 0, msg


def test_none_with_an_entry_touched_fails_and_names_it() -> None:
    rc, msg = _run("Docs impact: none — tidy wording", head=_touch_alpha())
    assert rc == 1
    assert "Alpha feature" in msg


def test_none_with_blank_reason_fails() -> None:
    rc, msg = _run("Docs impact: none —")
    assert rc == 1
    assert "reason" in msg


def test_none_with_placeholder_reason_fails() -> None:
    rc, msg = _run("Docs impact: none — <reason>")
    assert rc == 1
    assert "placeholder" in msg


def test_restructure_passes_with_many_entries_touched() -> None:
    base = _doc(ALPHA, BETA, GAMMA)
    head = _doc(
        _entry("Alpha feature", "Rewritten."),
        _entry("Beta feature, with comma", "Rewritten."),
        _entry("Gamma feature", "Rewritten."),
    )
    rc, msg = _run("Docs impact: restructure — split the Scan entries", base=base, head=head)
    assert rc == 0, msg


def test_restructure_with_blank_reason_fails() -> None:
    rc, msg = _run("Docs impact: restructure —", head=_touch_alpha())
    assert rc == 1


def test_index_only_edit_touches_no_entry() -> None:
    head = _doc(ALPHA, BETA, index_row="| [Alpha feature](#alpha-feature) | Review |\n")
    rc, msg = _run("Docs impact: none — index refresh only", head=head)
    assert rc == 0, msg


# --- declaration line -------------------------------------------------------

def test_missing_line_fails_and_lists_the_three_forms() -> None:
    rc, msg = _run("## What\n\nno declaration here")
    assert rc == 1
    assert "Docs impact:" in msg
    assert "restructure" in msg


def test_empty_value_fails() -> None:
    rc, msg = _run("Docs impact:   ")
    assert rc == 1


def test_two_declaration_lines_fail() -> None:
    rc, msg = _run("Docs impact: none — tooling\nDocs impact: Alpha feature")
    assert rc == 1


def test_crlf_body_parses() -> None:
    rc, msg = _run("## What\r\n\r\nDocs impact: Alpha feature\r\n\r\nWhy.\r\n", head=_touch_alpha())
    assert rc == 0, msg
