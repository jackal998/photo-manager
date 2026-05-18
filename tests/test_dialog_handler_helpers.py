"""Tests for :mod:`app.views.handlers.dialog_handler_helpers`.

Covers the pure-logic helpers extracted from
:class:`app.views.handlers.dialog_handler.DialogHandler` (#293). The
extraction keeps the load-bearing decision logic unit-testable
against plain Python without cascade-importing the Qt dialog stack —
same pattern as ``main_window_helpers.py`` (#185 / #283),
``group_media_controller_helpers.py`` (#185 / #285), and
``preview_pane_helpers.py`` (#185 / #289).

Each test maps to a real, named failure mode.
"""

from __future__ import annotations

import pytest

from app.views.constants import (
    COL_ACTION,
    COL_CREATION_DATE,
    COL_FOLDER,
    COL_GROUP,
    COL_GROUP_COUNT,
    COL_NAME,
    COL_SHOT_DATE,
    COL_SIZE_BYTES,
)
from app.views.handlers.dialog_handler_helpers import (
    CHILD_ROW_FIELDS,
    COL_TO_FIELD,
    GROUP_ROW_FIELDS,
    TOP_ROW_FIELDS,
    default_action_dialog_fields,
    dict_from_pairs,
    resolve_initial_field,
    safe_call_records_provider,
)


# ── COL_TO_FIELD and resolve_initial_field ───────────────────────────────


class TestResolveInitialField:
    """Tree column index → dialog field name."""

    def test_none_clicked_col_returns_none(self):
        """Menu-route open (no specific column) → no preselected field.

        Failure mode: a refactor that drops the ``None`` guard would
        raise ``KeyError``/``TypeError`` on the menu click and the
        dialog wouldn't open at all.
        """
        assert resolve_initial_field(None) is None

    def test_known_column_returns_field(self):
        """Right-click on the Action column → "Action" preselected."""
        assert resolve_initial_field(COL_ACTION) == "Action"

    def test_unknown_column_returns_none(self):
        """A column not in the mapping (e.g. SCORE if added later) →
        falls back to ``None``. Caller treats that as "no preselect"."""
        assert resolve_initial_field(999) is None

    @pytest.mark.parametrize(
        "col,expected",
        [
            (COL_GROUP,         "Similarity"),
            (COL_ACTION,        "Action"),
            (COL_NAME,          "File Name"),
            (COL_FOLDER,        "Folder"),
            (COL_SIZE_BYTES,    "Size (Bytes)"),
            (COL_GROUP_COUNT,   "Group Count"),
            (COL_CREATION_DATE, "Creation Date"),
            (COL_SHOT_DATE,     "Shot Date"),
        ],
    )
    def test_every_mapped_column_resolves(self, col, expected):
        """Every entry in ``COL_TO_FIELD`` is reachable and returns
        the canonical field name. Pins the cross-source coupling
        between tree columns and dialog fields."""
        assert resolve_initial_field(col) == expected

    def test_field_names_match_default_dialog_fields(self):
        """Every value in ``COL_TO_FIELD`` is also one of the
        default dialog fields. Drift between the two would mean
        the right-click route preselects a field that doesn't
        exist in the dropdown.

        Failure mode: rename a field in ``default_action_dialog_fields``
        without updating ``COL_TO_FIELD`` → the dropdown would
        silently fall through to its first entry on right-click.
        """
        valid_fields = set(default_action_dialog_fields())
        assert set(COL_TO_FIELD.values()) <= valid_fields


# ── default_action_dialog_fields ─────────────────────────────────────────


class TestDefaultActionDialogFields:
    """The canonical dropdown field list."""

    def test_returns_tuple_so_mutation_is_impossible(self):
        """Tuple — callers should ``list(...)`` for ``ActionDialog``
        which takes a mutable list. Returning a tuple here prevents
        a shared-state bug where one caller mutates the list and
        the next dialog open sees a shorter list."""
        assert isinstance(default_action_dialog_fields(), tuple)

    def test_contains_eleven_canonical_fields(self):
        """The 11-field surface (drift here means a new field was
        added but not paired with its tree column, or removed
        without updating the result-tree)."""
        assert len(default_action_dialog_fields()) == 11

    def test_first_field_is_similarity(self):
        """Order matters: Similarity is the leftmost tree column and
        the topmost dropdown entry. The probe
        ``test_probe_select_dialog_exposes_every_filterable_tree_column``
        relies on this ordering invariant."""
        assert default_action_dialog_fields()[0] == "Similarity"

    def test_contains_lock_and_resolution(self):
        """Lock (#164) and Resolution (#238) — both added later;
        regression-guards their continued presence."""
        fields = default_action_dialog_fields()
        assert "Lock" in fields
        assert "Resolution" in fields


# ── dict_from_pairs ──────────────────────────────────────────────────────


class TestDictFromPairs:
    """The pure dict-comprehension that assembles the values dict."""

    def test_empty_pairs_returns_empty_dict(self):
        """No pairs → empty dict. Edge case; the helper must not
        call ``data_getter`` at all."""
        called = []

        def _spy(col):
            called.append(col)
            return ""

        result = dict_from_pairs((), _spy)
        assert result == {}
        assert called == []

    def test_pairs_become_dict_entries(self):
        """One pair per dict entry, key=label, value=getter(col)."""
        pairs = (("A", 1), ("B", 2), ("C", 3))
        result = dict_from_pairs(pairs, lambda col: f"v{col}")
        assert result == {"A": "v1", "B": "v2", "C": "v3"}

    def test_data_getter_called_with_column_int(self):
        """The getter receives the column number, not the label.

        Failure mode: a refactor that swapped ``label`` and ``col``
        in the comprehension would feed labels into the getter
        (which expects a column index) — either silent empty
        strings or a downstream ``TypeError``."""
        seen = []
        dict_from_pairs((("X", 42), ("Y", 7)), lambda c: seen.append(c) or "")
        assert seen == [42, 7]

    def test_getter_value_is_used_verbatim(self):
        """Whatever the getter returns is the dict value (caller
        is responsible for ``or ""`` falsy-coalesce; helper does
        not second-guess)."""
        result = dict_from_pairs((("k", 0),), lambda _c: None)
        assert result == {"k": None}


# ── CHILD_ROW_FIELDS / GROUP_ROW_FIELDS / TOP_ROW_FIELDS ─────────────────


class TestRowFieldTables:
    """The three (label, col) tables that drive the values dict."""

    def test_child_row_fields_are_all_per_child_columns(self):
        """Child fields pull from the child's own row — not the
        group. So COL_GROUP and COL_GROUP_COUNT must NOT appear here."""
        cols = {col for _, col in CHILD_ROW_FIELDS}
        assert COL_GROUP not in cols
        assert COL_GROUP_COUNT not in cols

    def test_group_row_fields_are_all_per_group_columns(self):
        """Group fields describe the parent group — only Similarity
        and Group Count belong here."""
        labels = {label for label, _ in GROUP_ROW_FIELDS}
        assert labels == {"Similarity", "Group Count"}

    def test_top_row_fields_match_group_fields_for_top_level(self):
        """When the user selects a top-level (group) row, only
        group-level fields are populated. Same labels as
        ``GROUP_ROW_FIELDS`` because semantically they describe
        the same data, just sourced from a different model
        traversal."""
        top_labels = {label for label, _ in TOP_ROW_FIELDS}
        group_labels = {label for label, _ in GROUP_ROW_FIELDS}
        assert top_labels == group_labels

    def test_no_label_collision_across_child_and_group(self):
        """Child + group field labels must not overlap, else the
        ``values.update`` calls in ``_get_highlighted_row_values``
        would silently overwrite one with the other.

        Failure mode: adding "File Name" to ``GROUP_ROW_FIELDS``
        would let the group's blank "File Name" overwrite the
        child's actual filename — invisible bug, dialog shows
        empty pre-fill."""
        child_labels = {label for label, _ in CHILD_ROW_FIELDS}
        group_labels = {label for label, _ in GROUP_ROW_FIELDS}
        assert child_labels.isdisjoint(group_labels)

    def test_every_label_is_in_default_fields(self):
        """Every label in the three tables must be a real dialog
        field. Drift here means we'd pre-populate a field that
        doesn't exist."""
        all_labels = (
            {label for label, _ in CHILD_ROW_FIELDS}
            | {label for label, _ in GROUP_ROW_FIELDS}
            | {label for label, _ in TOP_ROW_FIELDS}
        )
        valid_fields = set(default_action_dialog_fields())
        assert all_labels <= valid_fields


# ── safe_call_records_provider ───────────────────────────────────────────


class TestSafeCallRecordsProvider:
    """The records-provider invocation wrapper. #237's load-bearing
    contract: any provider error must not crash the dialog open."""

    def test_none_provider_returns_empty_list(self):
        """No provider wired → no live preview, dialog still opens
        on the regex panel. This is the common state for test
        callers and for the initial empty-state."""
        assert safe_call_records_provider(None) == []

    def test_provider_returns_groups_passthrough(self):
        """Happy path: provider returns a list → that list is
        forwarded verbatim."""
        groups = [{"a": 1}, {"b": 2}]
        assert safe_call_records_provider(lambda: groups) is groups

    def test_provider_returns_none_becomes_empty_list(self):
        """Provider returning ``None`` (e.g. manifest not loaded
        yet) → empty list. Caller's ``if groups:`` guard then
        skips the match-fn construction."""
        assert safe_call_records_provider(lambda: None) == []

    def test_provider_raising_returns_empty_list(self):
        """The named failure mode: provider raises mid-call
        (concurrent scan rebuilding records) — dialog must still
        open, just without live preview.

        Failure mode: a refactor that lets the exception propagate
        would crash the menu click. The user sees nothing happen —
        worse than an empty dialog because they don't know whether
        the click registered."""
        def _boom():
            raise RuntimeError("records being rebuilt")

        assert safe_call_records_provider(_boom) == []

    def test_provider_returning_empty_list_is_falsy_for_caller(self):
        """An empty list still goes through unchanged — caller's
        ``if groups:`` check then skips match-fn (no preview).
        The wrapper does not synthesize a non-empty list."""
        assert safe_call_records_provider(lambda: []) == []
