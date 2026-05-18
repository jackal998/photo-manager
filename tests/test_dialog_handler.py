"""Layer-1 tests for :class:`app.views.handlers.dialog_handler.DialogHandler`
(#293).

Closes the dialog_handler portion of #293 (cascade-omit follow-up
to #185). Mirrors the pattern proved out by #283 (main_window) and
#285 (group_media_controller): pure-logic extraction into a sibling
helper module + fake-self (``SimpleNamespace``) thin-proxy tests
for every dispatch method.

The pure-logic helpers themselves are tested in
``test_dialog_handler_helpers.py`` (sibling module).

## Not covered here (by design)

* The real ``QFileDialog`` / ``ActionDialog`` ``exec()`` round-trip —
  layer 3 via s12 (manifest save), s14/s29/s30 (regex), s17 (scan
  sources), s38 (path-field validation).
* The ``ImportError``-fallback ``QMessageBox.critical`` branch in
  ``show_action_dialog`` — ``ActionDialog`` is an in-project hard
  import that can't realistically fail at import time. Testing it
  would require monkeypatching ``app.views.dialogs.select_dialog``
  to raise on import, which is the "mock-the-world to bump
  coverage" pattern CLAUDE.md rejects. The branch is a defensive
  fallback for a failure mode that production cannot produce.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

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
from app.views.handlers.dialog_handler import DialogHandler
from app.views.handlers.dialog_handler_helpers import (
    default_action_dialog_fields,
)


# ── fake QModelIndex / QStandardItemModel for the model-walk tests ──────


class _FakeIdx:
    """Minimal QModelIndex-shaped object."""

    def __init__(self, row: int, parent: "_FakeIdx | _FakeInvalidIdx | None" = None):
        self._row = row
        self._parent = parent if parent is not None else _FakeInvalidIdx()

    def row(self) -> int:
        return self._row

    def parent(self):
        return self._parent

    def isValid(self) -> bool:
        return True


class _FakeInvalidIdx:
    """An invalid index — what ``idx.parent()`` returns for top-level
    rows. Maps to QModelIndex()."""

    def isValid(self) -> bool:
        return False

    def row(self) -> int:
        return -1

    def parent(self):
        return _FakeInvalidIdx()


class _FakeModel:
    """Minimal model fake: ``index(row, col, parent)`` tags an
    address; ``data(tag)`` looks it up in a dict.

    Top-level rows are addressed as ``("top", row, col)``. Child
    rows are ``("child", row, col, parent_row)``. The grandparent
    of a child is always invalid in this test (single-level group
    hierarchy)."""

    def __init__(self, cells: dict | None = None):
        self._cells = cells or {}

    def index(self, row: int, col: int, parent=None):
        if parent is None or not parent.isValid():
            return ("top", row, col)
        return ("child", row, col, parent.row())

    def data(self, tag):
        return self._cells.get(tag, "")


def _make_child_idx(child_row: int, parent_row: int) -> _FakeIdx:
    """Helper: build a child index whose parent is at ``parent_row``."""
    parent = _FakeIdx(parent_row)
    return _FakeIdx(child_row, parent=parent)


# ── __init__ ─────────────────────────────────────────────────────────────


class TestInit:
    """The constructor — stores its inputs verbatim with no
    surprise side effects."""

    def test_minimal_construction_stores_inputs(self):
        """Default kwargs: optional handlers stay None."""
        parent = object()
        provider = object()
        handler = DialogHandler(parent_widget=parent, tree_data_provider=provider)
        assert handler.parent is parent
        assert handler.tree_provider is provider
        assert handler.action_handler is None
        assert handler.records_provider is None
        assert handler.settings is None

    def test_all_inputs_stored(self):
        """Every constructor kwarg lands on the instance with no
        wrapping. Failure mode: a refactor that wraps
        ``records_provider`` in another callable would break the
        #237 contract (the wrapping layer could swallow the
        provider's return)."""
        parent = object()
        provider = object()
        action = MagicMock()
        records = MagicMock()
        settings = object()
        handler = DialogHandler(
            parent_widget=parent,
            tree_data_provider=provider,
            action_handler=action,
            records_provider=records,
            settings=settings,
        )
        assert handler.action_handler is action
        assert handler.records_provider is records
        assert handler.settings is settings


# ── show_action_dialog ───────────────────────────────────────────────────


@pytest.fixture
def _patched_action_dialog(monkeypatch):
    """Patch the ``ActionDialog`` class at its import location so
    ``show_action_dialog`` builds a fake dialog (no Qt event loop)."""
    fake_dialog = MagicMock()
    fake_cls = MagicMock(return_value=fake_dialog)
    monkeypatch.setattr(
        "app.views.dialogs.select_dialog.ActionDialog", fake_cls
    )
    return fake_cls, fake_dialog


class TestShowActionDialog:
    """The main dialog-open dispatch. Covers field-list assembly,
    initial-field resolution, records-provider plumbing, and
    signal-wiring."""

    def test_builds_dialog_with_default_fields(self, _patched_action_dialog):
        """The dropdown gets the canonical 11-field list. Failure
        mode: a refactor that drops or reorders the fields would
        present the wrong dropdown to the user — silent UX
        regression."""
        fake_cls, _ = _patched_action_dialog
        fake_self = SimpleNamespace(
            parent=None,
            tree_provider=MagicMock(),
            action_handler=None,
            records_provider=None,
            settings=None,
            _get_highlighted_row_values=MagicMock(return_value={}),
        )

        DialogHandler.show_action_dialog(fake_self)

        assert fake_cls.call_args.kwargs["fields"] == list(
            default_action_dialog_fields()
        )

    def test_clicked_col_resolves_to_initial_field(self, _patched_action_dialog):
        """Right-click on the Action column → ``initial_field="Action"``."""
        fake_cls, _ = _patched_action_dialog
        fake_self = SimpleNamespace(
            parent=None,
            tree_provider=MagicMock(),
            action_handler=None,
            records_provider=None,
            settings=None,
            _get_highlighted_row_values=MagicMock(return_value={}),
        )

        DialogHandler.show_action_dialog(fake_self, clicked_col=COL_ACTION)

        assert fake_cls.call_args.kwargs["initial_field"] == "Action"

    def test_no_clicked_col_means_no_initial_field(self, _patched_action_dialog):
        """Menu route (no column clicked) → ``initial_field=None``,
        dialog defaults to its first dropdown entry."""
        fake_cls, _ = _patched_action_dialog
        fake_self = SimpleNamespace(
            parent=None,
            tree_provider=MagicMock(),
            action_handler=None,
            records_provider=None,
            settings=None,
            _get_highlighted_row_values=MagicMock(return_value={}),
        )

        DialogHandler.show_action_dialog(fake_self)

        assert fake_cls.call_args.kwargs["initial_field"] is None

    def test_row_values_from_highlighted_method(self, _patched_action_dialog):
        """Whatever ``_get_highlighted_row_values`` returns is
        forwarded as ``row_values=``. Failure mode: a refactor that
        accidentally rebuilt the values dict here would diverge
        from the model-walk and produce wrong pre-fills."""
        fake_cls, _ = _patched_action_dialog
        prefill = {"Action": "delete", "File Name": "p.jpg"}
        fake_self = SimpleNamespace(
            parent=None,
            tree_provider=MagicMock(),
            action_handler=None,
            records_provider=None,
            settings=None,
            _get_highlighted_row_values=MagicMock(return_value=prefill),
        )

        DialogHandler.show_action_dialog(fake_self)

        assert fake_cls.call_args.kwargs["row_values"] == prefill

    def test_no_records_provider_means_no_match_fn_no_groups(
        self, _patched_action_dialog
    ):
        """Without records → ``groups=[]``, ``match_fn=None``. The
        regex panel is the only thing reachable — that's expected
        on a fresh empty-state."""
        fake_cls, _ = _patched_action_dialog
        fake_self = SimpleNamespace(
            parent=None,
            tree_provider=MagicMock(),
            action_handler=None,
            records_provider=None,
            settings=None,
            _get_highlighted_row_values=MagicMock(return_value={}),
        )

        DialogHandler.show_action_dialog(fake_self)

        kwargs = fake_cls.call_args.kwargs
        assert kwargs["groups"] == []
        assert kwargs["match_fn"] is None

    def test_records_provider_builds_match_fn_and_passes_groups(
        self, _patched_action_dialog, monkeypatch
    ):
        """#237's load-bearing contract: when groups exist,
        ``match_fn`` is built from them AND ``groups`` is passed
        through. Without ``groups``, the dialog's numeric-condition
        panel silently stays hidden."""
        fake_cls, _ = _patched_action_dialog
        fake_match_fn = MagicMock(name="match_fn")
        fake_build = MagicMock(return_value=fake_match_fn)
        monkeypatch.setattr(
            "app.views.handlers.file_operations.build_match_fn", fake_build
        )
        groups = [{"sig": "abc"}, {"sig": "def"}]
        fake_self = SimpleNamespace(
            parent=None,
            tree_provider=MagicMock(),
            action_handler=None,
            records_provider=lambda: groups,
            settings=None,
            _get_highlighted_row_values=MagicMock(return_value={}),
        )

        DialogHandler.show_action_dialog(fake_self)

        fake_build.assert_called_once_with(groups)
        kwargs = fake_cls.call_args.kwargs
        assert kwargs["groups"] == groups
        assert kwargs["match_fn"] is fake_match_fn

    def test_records_provider_error_does_not_crash_dialog_open(
        self, _patched_action_dialog
    ):
        """The named #237-class failure mode: a concurrent scan
        rebuilds records and the provider raises. The dialog must
        still open (without preview) — otherwise the menu click
        silently dead-ends and the user can't tell their click
        registered."""
        fake_cls, _ = _patched_action_dialog

        def _boom():
            raise RuntimeError("records being rebuilt")

        fake_self = SimpleNamespace(
            parent=None,
            tree_provider=MagicMock(),
            action_handler=None,
            records_provider=_boom,
            settings=None,
            _get_highlighted_row_values=MagicMock(return_value={}),
        )

        DialogHandler.show_action_dialog(fake_self)

        kwargs = fake_cls.call_args.kwargs
        assert kwargs["groups"] == []
        assert kwargs["match_fn"] is None
        fake_cls.return_value.exec.assert_called_once()

    def test_action_handler_signal_connected(self, _patched_action_dialog):
        """When ``action_handler`` is wired, the dialog's
        ``setActionRequested`` signal is connected to it. Failure
        mode (the #175-class hole #185 is closing): if a refactor
        forgot the ``.connect()`` call, the user's Apply click in
        the dialog would silently do nothing."""
        fake_cls, fake_dialog = _patched_action_dialog
        action = MagicMock()
        fake_self = SimpleNamespace(
            parent=None,
            tree_provider=MagicMock(),
            action_handler=action,
            records_provider=None,
            settings=None,
            _get_highlighted_row_values=MagicMock(return_value={}),
        )

        DialogHandler.show_action_dialog(fake_self)

        fake_dialog.setActionRequested.connect.assert_called_once_with(action)

    def test_no_action_handler_means_no_signal_connect(
        self, _patched_action_dialog
    ):
        """When ``action_handler=None``, no signal connect happens —
        otherwise we'd connect to ``None`` and crash on emit."""
        fake_cls, fake_dialog = _patched_action_dialog
        fake_self = SimpleNamespace(
            parent=None,
            tree_provider=MagicMock(),
            action_handler=None,
            records_provider=None,
            settings=None,
            _get_highlighted_row_values=MagicMock(return_value={}),
        )

        DialogHandler.show_action_dialog(fake_self)

        fake_dialog.setActionRequested.connect.assert_not_called()

    def test_settings_forwarded_to_dialog(self, _patched_action_dialog):
        """The ``settings`` handle (recent-patterns + mode pref) is
        forwarded as a kwarg. Failure mode: a refactor that dropped
        the kwarg would reset the user's recent-patterns history
        every open."""
        fake_cls, _ = _patched_action_dialog
        settings = object()
        fake_self = SimpleNamespace(
            parent=None,
            tree_provider=MagicMock(),
            action_handler=None,
            records_provider=None,
            settings=settings,
            _get_highlighted_row_values=MagicMock(return_value={}),
        )

        DialogHandler.show_action_dialog(fake_self)

        assert fake_cls.call_args.kwargs["settings"] is settings

    def test_exec_is_called(self, _patched_action_dialog):
        """The dialog runs its event loop via ``.exec()``. The
        ``setActionRequested.connect`` happens BEFORE ``.exec()``
        so callers don't miss the emit."""
        _, fake_dialog = _patched_action_dialog
        fake_self = SimpleNamespace(
            parent=None,
            tree_provider=MagicMock(),
            action_handler=None,
            records_provider=None,
            settings=None,
            _get_highlighted_row_values=MagicMock(return_value={}),
        )

        DialogHandler.show_action_dialog(fake_self)

        fake_dialog.exec.assert_called_once()


# ── show_select_dialog (backward-compat alias) ───────────────────────────


class TestShowSelectDialog:
    """The deprecated alias kept for callers that still use the
    pre-#100-era name."""

    def test_alias_forwards_to_show_action_dialog(self):
        """The alias must remain a one-line forward — not a copy
        of the body. Drift would mean the old-name route shows a
        different dialog from the new-name route."""
        fake_self = SimpleNamespace(show_action_dialog=MagicMock())

        DialogHandler.show_select_dialog(fake_self, clicked_col=COL_NAME)

        fake_self.show_action_dialog.assert_called_once_with(clicked_col=COL_NAME)

    def test_alias_default_clicked_col_is_none(self):
        """Default kwarg keeps parity with the new-name signature."""
        fake_self = SimpleNamespace(show_action_dialog=MagicMock())

        DialogHandler.show_select_dialog(fake_self)

        fake_self.show_action_dialog.assert_called_once_with(clicked_col=None)


# ── _get_highlighted_row_values ──────────────────────────────────────────


class TestGetHighlightedRowValues:
    """The tree-model walk that pre-populates the dialog. Six
    branches: no selection model, no selected rows, proxy mapping,
    no proxy, child-row pull, top-row pull. All six are real
    user-reachable paths."""

    def test_no_selection_model_returns_empty(self):
        """Empty-state with no selection model → empty dict, dialog
        opens with no pre-fill."""
        provider = MagicMock()
        provider.get_selection_model.return_value = None
        fake_self = SimpleNamespace(tree_provider=provider)

        assert DialogHandler._get_highlighted_row_values(fake_self) == {}

    def test_no_selected_rows_returns_empty(self):
        """User has a selection model but hasn't clicked anything →
        empty dict. The right-click route does select a row before
        opening; the menu route may not."""
        sel = MagicMock()
        sel.selectedRows.return_value = []
        provider = MagicMock()
        provider.get_selection_model.return_value = sel
        fake_self = SimpleNamespace(tree_provider=provider)

        assert DialogHandler._get_highlighted_row_values(fake_self) == {}

    def test_proxy_with_mapToSource_routes_through_src_model(self):
        """When a sort/filter proxy is wired, ``mapToSource(idx)``
        gives the index into the source model — that's where the
        actual data lives. Failure mode (the #175-class hole): a
        refactor that read from the proxy model directly would see
        post-sort indices and pull the wrong row's values."""
        src_idx = _make_child_idx(child_row=2, parent_row=5)
        src_model = _FakeModel({
            ("child", 2, COL_NAME, 5):     "kept_photo.jpg",
            ("child", 2, COL_ACTION, 5):   "keep",
            ("child", 2, COL_FOLDER, 5):   "C:/photos",
            ("child", 2, COL_SIZE_BYTES, 5):   "12345",
            ("child", 2, COL_CREATION_DATE, 5): "2026-01-01",
            ("child", 2, COL_SHOT_DATE, 5):     "2026-01-02",
            ("top", 5, COL_GROUP):       "98%",
            ("top", 5, COL_GROUP_COUNT): "3",
        })

        proxy_idx = object()  # marker — proxy gets mapped
        proxy = MagicMock()
        proxy.mapToSource.return_value = src_idx

        sel = MagicMock()
        sel.selectedRows.return_value = [proxy_idx]
        provider = MagicMock()
        provider.get_selection_model.return_value = sel
        provider.get_view_model.return_value = MagicMock(name="view_model")  # NOT used
        provider.get_source_model.return_value = src_model
        provider.get_proxy_model.return_value = proxy
        fake_self = SimpleNamespace(tree_provider=provider)

        result = DialogHandler._get_highlighted_row_values(fake_self)

        proxy.mapToSource.assert_called_once_with(proxy_idx)
        assert result["File Name"] == "kept_photo.jpg"
        assert result["Action"] == "keep"
        assert result["Similarity"] == "98%"
        assert result["Group Count"] == "3"

    def test_no_proxy_uses_view_model_directly(self):
        """When the proxy is None (or doesn't have ``mapToSource``),
        the view model is the source of truth."""
        idx = _make_child_idx(child_row=0, parent_row=0)
        view_model = _FakeModel({
            ("child", 0, COL_NAME, 0):   "a.jpg",
            ("child", 0, COL_ACTION, 0): "delete",
            ("top", 0, COL_GROUP):       "100%",
            ("top", 0, COL_GROUP_COUNT): "2",
        })

        sel = MagicMock()
        sel.selectedRows.return_value = [idx]
        provider = MagicMock()
        provider.get_selection_model.return_value = sel
        provider.get_view_model.return_value = view_model
        provider.get_source_model.return_value = MagicMock(name="src")  # NOT used
        provider.get_proxy_model.return_value = None
        fake_self = SimpleNamespace(tree_provider=provider)

        result = DialogHandler._get_highlighted_row_values(fake_self)

        assert result["File Name"] == "a.jpg"
        assert result["Action"] == "delete"

    def test_proxy_without_mapToSource_falls_back_to_view_model(self):
        """A "proxy" that doesn't have ``mapToSource`` (some QObject
        in the slot but not a QSortFilterProxyModel) → falls through
        to view_model. Defensive guard against a wiring mistake."""
        idx = _make_child_idx(child_row=0, parent_row=0)
        view_model = _FakeModel({("child", 0, COL_NAME, 0): "from_view.jpg"})
        bad_proxy = SimpleNamespace()  # no mapToSource attribute

        sel = MagicMock()
        sel.selectedRows.return_value = [idx]
        provider = MagicMock()
        provider.get_selection_model.return_value = sel
        provider.get_view_model.return_value = view_model
        provider.get_source_model.return_value = MagicMock(name="src")
        provider.get_proxy_model.return_value = bad_proxy
        fake_self = SimpleNamespace(tree_provider=provider)

        result = DialogHandler._get_highlighted_row_values(fake_self)

        assert result["File Name"] == "from_view.jpg"

    def test_child_row_pulls_child_and_group_fields(self):
        """The full child-row contract: 6 fields from the child row +
        2 fields from the parent group, all 8 in the result.

        Failure mode (subtle): a refactor that swapped the order of
        ``dict_from_pairs(GROUP_ROW_FIELDS, ...)`` and
        ``dict_from_pairs(CHILD_ROW_FIELDS, ...)`` would not matter
        today (labels are disjoint by design) — but if the disjoint
        invariant ever drifts, this assertion + the disjoint-table
        test would both fire."""
        idx = _make_child_idx(child_row=3, parent_row=7)
        model = _FakeModel({
            # Child fields
            ("child", 3, COL_ACTION, 7):         "delete",
            ("child", 3, COL_NAME, 7):           "dup.jpg",
            ("child", 3, COL_FOLDER, 7):         "C:/dups",
            ("child", 3, COL_SIZE_BYTES, 7):     "9999",
            ("child", 3, COL_CREATION_DATE, 7):  "2026-03-01",
            ("child", 3, COL_SHOT_DATE, 7):      "2026-03-02",
            # Parent group fields
            ("top", 7, COL_GROUP):       "97%",
            ("top", 7, COL_GROUP_COUNT): "4",
        })

        sel = MagicMock()
        sel.selectedRows.return_value = [idx]
        provider = MagicMock()
        provider.get_selection_model.return_value = sel
        provider.get_view_model.return_value = model
        provider.get_source_model.return_value = model
        provider.get_proxy_model.return_value = None
        fake_self = SimpleNamespace(tree_provider=provider)

        result = DialogHandler._get_highlighted_row_values(fake_self)

        assert result == {
            "Action":        "delete",
            "File Name":     "dup.jpg",
            "Folder":        "C:/dups",
            "Size (Bytes)":  "9999",
            "Creation Date": "2026-03-01",
            "Shot Date":     "2026-03-02",
            "Similarity":    "97%",
            "Group Count":   "4",
        }

    def test_top_level_row_pulls_only_group_fields(self):
        """When the user has selected the group header row (no
        valid parent), only group-level fields are returned.
        The child-row fields would have no meaning here."""
        # Top-level idx: parent is invalid
        idx = _FakeIdx(row=2, parent=_FakeInvalidIdx())
        model = _FakeModel({
            ("top", 2, COL_GROUP):       "95%",
            ("top", 2, COL_GROUP_COUNT): "5",
        })

        sel = MagicMock()
        sel.selectedRows.return_value = [idx]
        provider = MagicMock()
        provider.get_selection_model.return_value = sel
        provider.get_view_model.return_value = model
        provider.get_source_model.return_value = model
        provider.get_proxy_model.return_value = None
        fake_self = SimpleNamespace(tree_provider=provider)

        result = DialogHandler._get_highlighted_row_values(fake_self)

        assert result == {"Similarity": "95%", "Group Count": "5"}
        # Child fields explicitly absent
        assert "File Name" not in result
        assert "Action" not in result
