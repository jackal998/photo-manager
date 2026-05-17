"""Tests for :mod:`app.views.main_window_helpers`.

These tests cover the pure-logic helpers extracted from
``MainWindow`` (#185). The extraction keeps the load-bearing logic
unit-testable against a plain ``QStandardItemModel`` (or no Qt at
all) without cascade-importing the full MainWindow view stack — the
same pattern used for ``action_handlers.py`` (#182),
``empty_state.py`` (#137), and ``status_reporter_impl.py`` (#138,
#140).

Each test maps to a real, named failure mode. Tests whose only
purpose would be hitting defensive ``except: pass`` branches are
deliberately absent — see CLAUDE.md "Testing ground rules" and
``feedback_no_test_padding``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.views.constants import COL_GROUP, COL_NAME, NUM_COLUMNS, PATH_ROLE
from app.views.main_window_helpers import (
    count_isolated_rows,
    extract_first_selected_file_path,
    extract_keeper_paths,
    find_path_in_model,
    find_paths_in_model,
)


# ── shared model builder ──────────────────────────────────────────────────


def _build_two_group_model(qapp):
    """Build a real ``QStandardItemModel`` with the shape MainWindow
    populates: 2 group rows, each containing 2 file rows. The path is
    stored on the COL_NAME cell at ``PATH_ROLE``, matching the
    convention pinned by ``app.views.tree_model_builder``."""
    del qapp  # the fixture is here to guarantee QApplication exists
    from PySide6.QtGui import QStandardItem, QStandardItemModel

    model = QStandardItemModel()

    def _make_row(label: str, path: str | None) -> list[QStandardItem]:
        row = [QStandardItem("") for _ in range(NUM_COLUMNS)]
        row[COL_NAME].setText(label)
        if path is not None:
            row[COL_NAME].setData(path, PATH_ROLE)
        return row

    for group_n in (1, 2):
        group_row = [QStandardItem("") for _ in range(NUM_COLUMNS)]
        group_row[COL_GROUP].setText(f"Group {group_n}")
        model.appendRow(group_row)
        group_item = group_row[COL_GROUP]
        group_item.appendRow(_make_row(f"a{group_n}.jpg", f"/photos/g{group_n}/a.jpg"))
        group_item.appendRow(_make_row(f"b{group_n}.jpg", f"/photos/g{group_n}/b.jpg"))

    return model


# ── find_path_in_model ────────────────────────────────────────────────────


def test_find_path_returns_index_for_matching_file(qapp):
    """A path stored on COL_NAME at PATH_ROLE is found and the returned
    index points at the correct (column, row, parent) cell.

    Catches: a refactor that shifts COL_NAME / COL_GROUP / PATH_ROLE
    out of alignment — the existing s50 (#238) probe pins the columns
    that are exposed to the regex dialog, but nothing else verifies
    that ``find_path_in_model`` resolves THIS coordinate system.
    """
    model = _build_two_group_model(qapp)

    idx = find_path_in_model(model, "/photos/g2/b.jpg")

    assert idx is not None
    assert idx.isValid()
    assert idx.column() == COL_NAME
    assert idx.row() == 1
    assert idx.parent().isValid()
    assert idx.parent().row() == 1
    assert model.data(idx, PATH_ROLE) == "/photos/g2/b.jpg"


def test_find_path_returns_none_for_missing_file(qapp):
    """A path that isn't in the model returns ``None`` rather than
    raising or returning a stale index.

    Catches: stale ``selected_path`` left over from a previous
    manifest — would crash ``_apply_relocalize_state`` if the walk
    didn't gracefully report no-match. This is the exact failure mode
    the i18n round-trip after Open Manifest exposes.
    """
    model = _build_two_group_model(qapp)

    assert find_path_in_model(model, "/photos/nowhere/missing.jpg") is None


def test_find_path_returns_none_for_null_model():
    """A ``None`` model (e.g. called before the tree has been
    populated) returns ``None`` rather than raising."""
    assert find_path_in_model(None, "/anything.jpg") is None


# ── find_paths_in_model (multi-target — #239 auto-select highlight) ──────


def test_find_paths_returns_all_matching_rows_in_walk_order(qapp):
    """Multi-target walk returns one index per match, in top-down
    tree-walk order.

    Catches: the #239 failure mode where the auto-select-after-scan
    feature picks 3 KEEP rows but the helper only returns the first,
    leaving the other 2 unhighlighted — silent UX regression.
    """
    model = _build_two_group_model(qapp)

    matches = find_paths_in_model(
        model, {"/photos/g1/b.jpg", "/photos/g2/a.jpg"}
    )

    assert len(matches) == 2
    paths = [model.data(idx, PATH_ROLE) for idx in matches]
    # Walk-order: g1's children before g2's, so g1/b comes first.
    assert paths == ["/photos/g1/b.jpg", "/photos/g2/a.jpg"]


def test_find_paths_returns_empty_for_no_matches(qapp):
    """Targets that don't exist in the model produce an empty list —
    not None, not raise. Callers check truthiness, so the empty-list
    convention matters."""
    model = _build_two_group_model(qapp)

    assert find_paths_in_model(model, {"/nowhere/x.jpg", "/nowhere/y.jpg"}) == []


def test_find_paths_returns_empty_for_empty_target_set(qapp):
    """Empty target set returns ``[]`` without iterating the model —
    fast path for the common ``_load_manifest_after_scan`` case where
    the scan produces zero KEEP rows."""
    model = _build_two_group_model(qapp)

    assert find_paths_in_model(model, set()) == []


def test_find_paths_returns_empty_for_null_model():
    """``None`` model returns ``[]`` — same defensive contract as
    :func:`find_path_in_model`."""
    assert find_paths_in_model(None, {"/anything.jpg"}) == []


# ── extract_keeper_paths (#239 — scan-complete handler) ──────────────────


def test_extract_keeper_paths_returns_only_keep_action_paths():
    """Walk groups, return paths whose ``action`` is exactly ``"KEEP"``.
    Other actions (``"DELETE"``, ``""``) are ignored.

    Catches: a refactor that broadens the predicate (e.g. picks up
    ``"keep"`` lowercase or any truthy action) silently selects the
    wrong rows after scan — exactly the #239 class of bug.
    """
    g1 = SimpleNamespace(
        items=[
            SimpleNamespace(file_path="/a.jpg", action="KEEP"),
            SimpleNamespace(file_path="/b.jpg", action="DELETE"),
            SimpleNamespace(file_path="/c.jpg", action=""),
        ]
    )
    g2 = SimpleNamespace(
        items=[
            SimpleNamespace(file_path="/d.jpg", action="KEEP"),
            SimpleNamespace(file_path="/e.jpg", action="keep"),  # case sensitive
        ]
    )

    paths = extract_keeper_paths([g1, g2])

    assert paths == {"/a.jpg", "/d.jpg"}


def test_extract_keeper_paths_strips_empty_path():
    """A record with empty ``file_path`` (corrupt manifest row) is
    dropped. Catches: silently selecting a "no-path KEEP row" would
    crash downstream when the selection code tries to scroll to it.
    """
    g = SimpleNamespace(
        items=[
            SimpleNamespace(file_path="", action="KEEP"),
            SimpleNamespace(file_path="/real.jpg", action="KEEP"),
        ]
    )

    assert extract_keeper_paths([g]) == {"/real.jpg"}


def test_extract_keeper_paths_empty_groups_returns_empty_set():
    """No groups → no keepers. Catches the common no-scan-yet startup
    state."""
    assert extract_keeper_paths([]) == set()


# ── count_isolated_rows (#138/#140 — status-bar baseline) ────────────────


def _make_manifest_db(tmp_path: Path, row_count: int) -> Path:
    """Build a minimal SQLite manifest with ``row_count`` rows in the
    table ``count_isolated_rows`` queries. Schema is reduced to the
    one column the helper reads; production schema is set by
    ``infrastructure.manifest_repository`` and is broader."""
    db = tmp_path / "manifest.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE migration_manifest (file_path TEXT)")
        conn.executemany(
            "INSERT INTO migration_manifest (file_path) VALUES (?)",
            [(f"/p/{i}.jpg",) for i in range(row_count)],
        )
        conn.commit()
    return db


def test_count_isolated_rows_returns_difference(tmp_path):
    """10 total rows, 3 grouped → 7 isolated. The status bar's
    "X groups, Y isolated files" message depends on this arithmetic
    being correct (#138, #140)."""
    db = _make_manifest_db(tmp_path, row_count=10)

    assert count_isolated_rows(str(db), grouped_count=3) == 7


def test_count_isolated_rows_returns_zero_when_grouped_exceeds_total(tmp_path):
    """Defensive clamp: if ``grouped_count`` somehow exceeds total
    (impossible in practice, but stale state could expose it),
    return 0 rather than a negative number. Negative would break the
    plural form / status text downstream."""
    db = _make_manifest_db(tmp_path, row_count=5)

    assert count_isolated_rows(str(db), grouped_count=10) == 0


def test_count_isolated_rows_returns_zero_on_missing_db(tmp_path):
    """A missing manifest file should not crash the load flow — the
    isolated count just gets omitted from the status text. Catches:
    a partially-loaded or deleted manifest mid-startup."""
    nonexistent = tmp_path / "does_not_exist.sqlite"

    assert count_isolated_rows(str(nonexistent), grouped_count=0) == 0


# ── extract_first_selected_file_path (#22 — relocalize selection) ───────


def test_extract_first_selected_file_path_returns_first_file_row():
    """Mixed list of group + file rows: return the path of the first
    file row in iteration order, skipping group rows entirely.

    Catches: the relocalize snapshot picks the wrong entry (e.g.
    grabs a group row's path key, or the LAST file instead of the
    first). The user's pre-relocalize selection wouldn't survive.
    """
    items = [
        {"type": "group", "path": "/should-skip/"},
        {"type": "file", "path": "/photos/a.jpg"},
        {"type": "file", "path": "/photos/b.jpg"},
    ]

    assert extract_first_selected_file_path(items) == "/photos/a.jpg"


def test_extract_first_selected_file_path_returns_none_when_no_file_rows():
    """Only group rows → None. Defends against relocalize asking
    ``_reselect_by_path("")`` and crashing on the missing path."""
    items = [{"type": "group", "path": "/g1/"}, {"type": "group", "path": "/g2/"}]

    assert extract_first_selected_file_path(items) is None


def test_extract_first_selected_file_path_skips_empty_path():
    """A file row with empty ``path`` is skipped — corrupt selection
    state shouldn't make relocalize crash on a non-existent file."""
    items = [
        {"type": "file", "path": ""},
        {"type": "file", "path": "/real.jpg"},
    ]

    assert extract_first_selected_file_path(items) == "/real.jpg"


def test_extract_first_selected_file_path_handles_non_dict_items():
    """Tree controller's get_selected_items signature is loose
    (returns ``list[dict]`` but in practice may return objects from
    older code paths). The helper must not raise on non-dict items."""
    items = [None, "stray-string", {"type": "file", "path": "/ok.jpg"}]

    assert extract_first_selected_file_path(items) == "/ok.jpg"


def test_count_isolated_rows_returns_zero_on_missing_table(tmp_path):
    """A SQLite file without the expected schema returns 0 rather
    than raising — protects the status bar from a DB schema mismatch
    (e.g. someone points the loader at a non-manifest sqlite)."""
    db = tmp_path / "wrong_schema.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE something_else (x INTEGER)")
        conn.commit()

    assert count_isolated_rows(str(db), grouped_count=0) == 0
