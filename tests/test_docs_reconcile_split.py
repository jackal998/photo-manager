"""docs_reconcile across the #958 split: a range whose diff moves entries from the
landing into docs/features/<area>.md must still judge each entry by its own text.

The failure these prevent: the touched set was keyed by "<path>#<slug>", so a
moved entry looked like a deletion plus an addition, every entry counted as
touched, and section (e) "Possibly stale" came back empty without a word.
"""
from __future__ import annotations

import difflib

import scripts.docs_reconcile as dr

LANDING_BASE = (
    "## Index\n\n"
    "### Alpha\n\n- **Area:** Scan\n\nShows [x](../core/x.py) once.\n\n"
    "### Beta\n\n- **Area:** Scan\n\nPlain text.\n"
)
# The same entries after the move: the link destination gains the extra ../ of the new depth.
AREA_UNCHANGED = (
    "# Features — Scan\n\n"
    "### Alpha\n\n- **Area:** Scan\n\nShows [x](../../core/x.py) once.\n\n"
    "### Beta\n\n- **Area:** Scan\n\nPlain text.\n"
)
AREA_EDITED = AREA_UNCHANGED.replace("once.", "twice.")


def _file_diff(path: str, base: str, head: str) -> str:
    body = "".join(
        difflib.unified_diff(
            base.splitlines(keepends=True), head.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}", n=0,
        )
    )
    return f"diff --git a/{path} b/{path}\n{body}" if body else ""


def test_entry_moved_across_the_split_unchanged_is_still_a_stale_candidate() -> None:
    area = "docs/features/scan.md"
    diff = _file_diff("docs/features.md", LANDING_BASE, "") + _file_diff(area, "", AREA_UNCHANGED)
    touched, _, entries = dr.touched_entries(
        {"docs/features.md": LANDING_BASE}, {area: AREA_UNCHANGED}, diff
    )
    assert "alpha" not in touched, touched
    stale = dr.stale_candidates(entries, touched, changed={"core/x.py"})
    assert stale == [("Alpha", ["core/x.py"])], stale


def test_entry_whose_text_changed_during_the_move_is_touched() -> None:
    area = "docs/features/scan.md"
    diff = _file_diff("docs/features.md", LANDING_BASE, "") + _file_diff(area, "", AREA_EDITED)
    touched, labels, _ = dr.touched_entries(
        {"docs/features.md": LANDING_BASE}, {area: AREA_EDITED}, diff
    )
    assert touched == {"alpha"}, touched
    assert labels["alpha"] == "Alpha"
