"""Layer-1 tests for :class:`app.views.main_window.MainWindow` (#185).

Background: ``main_window.py`` had no layer-1 unit tests for ~6
months; the #175 bridge-pattern hole lived undetected the whole time
because nothing imported MainWindow at unit-test time. The
structural probes (#243 / #246 / #247) catch static invariants; this
file pins the *call-time* delegation contracts.

## Layer & cost

These are **fast** layer-1 tests. The slow path (constructing a real
``MainWindow`` and exercising it through real Qt) would duplicate
what qa-explore (layer 3) already covers — s22 (relocalize), s28
(close-event), s39/s47 (geometry / column persistence), s41 (empty
state), s49 (auto-select-after-scan), and every single scenario
constructs MainWindow. Re-running that work in pytest with a
function-scoped Qt fixture costs ~25s per test for zero new bug-
catching power, which is the "metric gaming" pattern CLAUDE.md
explicitly rejects.

Two patterns are used:

1. **Unbound-method-on-fake-self** — for thin proxy methods. The
   method body is short and depends only on a few attrs; we construct
   a ``SimpleNamespace`` carrying just those attrs and call
   ``MainWindow.method_name(fake_self, ...)``. This skips all Qt
   construction (instant) while still executing the actual method
   body in ``main_window.py``, so coverage counts.

2. **One real-construction test** — pins the ``__init__`` /
   ``_setup_components`` / ``_setup_ui`` assembly contract. Catches
   future refactors that reorder construction such that a later step
   touches an un-set attribute. Costs ~25s once.

## Not covered here (covered elsewhere, by design)

* Window-state persistence (geometry / splitter / column layout) —
  layer 3 via s39 / s47 / s48; mocking ``QSettings`` to bump coverage
  is metric gaming per ``docs/testing.md`` and the omit-list for
  the cascade GUI files.
* ``_capture_relocalize_state`` / ``_apply_relocalize_state`` —
  layer 3 via s22 (live language switch). The pure model-walk piece
  is unit-tested in ``test_main_window_helpers.py``.
* Bridge proxies (``set_locked_state``, ``set_decision_with_lock_check``,
  ``remove_items_from_list``) — already pinned in
  ``test_context_menu.py::TestActionHandlersImplBridge``.
* ``set_decision_by_regex`` partition + lock-confirm logic — already
  pinned in ``test_file_operations.py::TestSetDecisionByRegexLockConfirm``.
* Log-directory open helpers — uniform ``os.startfile`` delegation;
  testing would be metric gaming.
* Tree selection-changed body — needs a real preview pane to assert
  on; layer 3 covers the user-visible result.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.viewmodels.main_vm import MainVM
from app.views.main_window import MainWindow
from core.models import PhotoRecord


def _rec(path: str, group: int = 1, action: str = "") -> PhotoRecord:
    """Minimum PhotoRecord for VM-construction tests."""
    return PhotoRecord(
        group_number=group,
        is_mark=False,
        is_locked=False,
        folder_path="",
        file_path=path,
        capture_date=None,
        modified_date=None,
        file_size_bytes=0,
        action=action,
    )


# ── one real-construction test (catches __init__ assembly bugs) ──────────


def test_main_window_constructs_with_minimal_vm(qapp):
    """``MainWindow(MainVM())`` returns without raising and every
    component the rest of the app relies on is attached.

    Failure mode: a refactor reorders ``_setup_components`` so a
    later step references an un-set attribute (e.g.
    ``ContextMenuHandler`` constructed before ``ActionHandlersImpl``).
    The bug surfaces as "MainWindow won't open" — invisible to layer
    3 because every scenario hits it identically and so they all
    fail together rather than narrowing the cause.

    This is the only test in this file that constructs a real
    MainWindow. Cost: ~25s for the Qt cold-start. Everything else
    uses the fake-self pattern.
    """
    vm = MainVM()
    win = MainWindow(vm, image_service=None, settings=None)
    try:
        assert win.tree is not None
        assert win.tree_controller is not None
        assert win.menu_controller is not None
        assert win.layout_manager is not None
        assert win.file_operations is not None
        assert win.action_handlers is not None
        assert win.context_menu_handler is not None
        assert win.dialog_handler is not None
        assert win._preview is not None
        assert win._empty_state_widget is not None
        # The bridge wired in #182 — context menu reaches file_operations
        # through this exact attribute chain.
        assert win.action_handlers.file_ops is win.file_operations
    finally:
        win.close()
        win.deleteLater()


# ── menu-action delegations (fake-self; same failure mode class as #175) ─


@pytest.mark.parametrize(
    "method_name, target_attr, target_method",
    [
        ("on_open_manifest", "file_operations", "import_manifest"),
        ("on_save_manifest", "file_operations", "save_manifest_decisions"),
        ("on_execute_action", "file_operations", "execute_action"),
        ("on_open_action_dialog", "dialog_handler", "show_action_dialog"),
    ],
)
def test_menu_action_delegates_to_handler(method_name, target_attr, target_method):
    """Each File-menu callback routes to the right handler method
    (file_operations.* or dialog_handler.*). A rename or signal-rewire
    that breaks any of these manifests as a silently dead menu item —
    same class as the #175 bridge-pattern hole this PR exists to
    catch."""
    fake_handler = MagicMock()
    fake_self = SimpleNamespace(**{target_attr: fake_handler})

    getattr(MainWindow, method_name)(fake_self)

    getattr(fake_handler, target_method).assert_called_once_with()


def test_clear_preview_calls_preview_pane_clear():
    """#431 — MainWindow.clear_preview is the UIUpdateCallback
    impl that FileOperationsHandler._on_manifest_loaded calls before
    refresh_tree. Catches the regression where preview content from
    a prior manifest survives an Open Manifest… call."""
    fake_preview = MagicMock()
    fake_self = SimpleNamespace(_preview=fake_preview)

    MainWindow.clear_preview(fake_self)

    fake_preview.clear.assert_called_once_with()


def test_clear_preview_safe_when_preview_unset():
    """Defensive — during early teardown the _preview attribute can
    be detached. clear_preview must no-op without raising."""
    fake_self = SimpleNamespace()

    # Should not raise.
    MainWindow.clear_preview(fake_self)


def test_on_execute_action_selected_only_delegates_with_kwarg():
    """#410 — ``on_execute_action_selected_only`` must call
    ``file_operations.execute_action(selected_only=True)``. The kwarg
    is the explicit scope signal; passing positional would break the
    sibling ``on_execute_action`` (which calls with no args).
    Bridge-pattern silent-no-op risk: a refactor that drops the kwarg
    surfaces as the new menu entry behaving identically to the old
    one — no visible error.
    """
    fake_ops = MagicMock()
    fake_self = SimpleNamespace(file_operations=fake_ops)

    MainWindow.on_execute_action_selected_only(fake_self)

    fake_ops.execute_action.assert_called_once_with(selected_only=True)


def test_refresh_execute_selected_only_enabled_requires_manifest_and_selection():
    """#410 — the (only selected) menu entry must be enabled only when
    BOTH (a) a manifest is loaded (sibling ``execute_action`` entry is
    enabled) AND (b) at least one file row is selected in the tree.
    Either condition false → disabled. Catches a regression where the
    AND becomes an OR (would enable the entry pre-manifest if a stale
    selection survived, or post-manifest with no selection)."""
    matrix = [
        # (manifest_loaded, has_file_selection, expected_enabled)
        (False, False, False),
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ]
    for manifest_loaded, has_file_selection, expected in matrix:
        execute_action = MagicMock()
        execute_action.isEnabled.return_value = manifest_loaded
        selected_only = MagicMock()
        actions = {
            "execute_action": execute_action,
            "execute_action_selected_only": selected_only,
        }
        menu_controller = SimpleNamespace(actions=actions)
        items = [{"type": "file", "path": "/a.jpg"}] if has_file_selection else []
        tree_controller = MagicMock()
        tree_controller.get_selected_items.return_value = items
        fake_self = SimpleNamespace(
            menu_controller=menu_controller,
            tree_controller=tree_controller,
        )

        MainWindow._refresh_execute_selected_only_enabled(fake_self)

        selected_only.setEnabled.assert_called_with(expected)


def test_apply_action_by_regex_delegates_to_file_operations():
    """``_apply_action_by_regex`` is the action_handler the regex
    dialog hands its parsed (field, pattern, value) tuple to. Must
    route to ``file_operations.set_decision_by_regex``.

    Failure mode: a rename of ``set_decision_by_regex`` on
    FileOperationsHandler that forgets the MainWindow call site —
    bridge-pattern hole.
    """
    fake_ops = MagicMock()
    fake_self = SimpleNamespace(file_operations=fake_ops)

    MainWindow._apply_action_by_regex(fake_self, "name", r".*\.jpg", "delete")

    fake_ops.set_decision_by_regex.assert_called_once_with(
        "name", r".*\.jpg", "delete"
    )


def test_on_image_loaded_delegates_to_preview():
    """The ``imageLoaded`` signal slot forwards (token, path, image)
    straight to ``PreviewPane.on_image_loaded``.

    Failure mode: a refactor that breaks the slot wiring causes the
    preview pane to stay blank after thumbnail decode — same class
    of silent-no-op as #175.
    """
    fake_preview = MagicMock()
    fake_self = SimpleNamespace(_preview=fake_preview)
    sentinel = object()

    MainWindow._on_image_loaded(fake_self, "tok-1", "/p.jpg", sentinel)

    fake_preview.on_image_loaded.assert_called_once_with("tok-1", "/p.jpg", sentinel)


def test_remove_from_list_toolbar_routes_selection_to_file_operations():
    """The toolbar's Remove-from-List button reads the tree
    controller's highlighted items and hands them to
    ``remove_from_list_toolbar``.

    Failure mode: a refactor changes the selection-extraction API on
    tree_controller and the toolbar starts passing the wrong shape (or
    an empty list) — silent no-op the user spots only when files they
    expect to vanish stay put.
    """
    sentinel_items = [{"type": "file", "path": "/x.jpg"}]
    fake_tc = MagicMock()
    fake_tc.get_selected_items.return_value = sentinel_items
    fake_ops = MagicMock()
    fake_self = SimpleNamespace(tree_controller=fake_tc, file_operations=fake_ops)

    MainWindow._remove_from_list_toolbar(fake_self)

    fake_ops.remove_from_list_toolbar.assert_called_once_with(sentinel_items)


# ── #239 scan-complete sequencing — load + auto-select ──────────────────


def test_load_manifest_after_scan_selects_keeper_paths():
    """After a scan, ``_load_manifest_after_scan`` must (1) load the
    manifest and (2) ask the window to highlight every row whose
    ``action == "KEEP"`` — the visible half of the #239 auto-select
    feature.

    Failure mode: the helper extracts the keepers but the call to
    ``_select_rows_by_paths`` is dropped or wired to the wrong list
    — silent regression where the worker decided keepers but nothing
    appears selected in the UI. (Layer 3 s49 covers the full UIA
    round-trip; this test pins the dispatch contract so a unit-level
    refactor can't silently break it.)
    """
    vm = SimpleNamespace(
        groups=[
            SimpleNamespace(
                items=[
                    SimpleNamespace(file_path="/a.jpg", action="KEEP"),
                    SimpleNamespace(file_path="/b.jpg", action="DELETE"),
                ]
            ),
            SimpleNamespace(
                items=[
                    SimpleNamespace(file_path="/c.jpg", action="KEEP"),
                ]
            ),
        ]
    )
    captured: dict = {}

    def fake_load(self, path):  # signature mirrors the real method
        captured["loaded_path"] = path

    def fake_select(self, paths):
        captured["selected"] = set(paths)

    fake_self = SimpleNamespace(
        _vm=vm,
        _load_manifest_from_path=lambda path: fake_load(fake_self, path),
        _select_rows_by_paths=lambda paths: fake_select(fake_self, paths),
    )

    MainWindow._load_manifest_after_scan(fake_self, "/m.sqlite")

    assert captured["loaded_path"] == "/m.sqlite"
    assert captured["selected"] == {"/a.jpg", "/c.jpg"}


def test_load_manifest_after_scan_skips_select_when_no_keepers():
    """No KEEP rows → don't call ``_select_rows_by_paths`` at all
    (avoids clearing the user's existing selection on an Open Manifest
    of a manifest that has no auto-selections)."""
    vm = SimpleNamespace(
        groups=[
            SimpleNamespace(items=[SimpleNamespace(file_path="/a.jpg", action="")])
        ]
    )
    select_was_called = {"yes": False}

    def fake_select(self, paths):
        select_was_called["yes"] = True

    fake_self = SimpleNamespace(
        _vm=vm,
        _load_manifest_from_path=lambda path: None,
        _select_rows_by_paths=lambda paths: fake_select(fake_self, paths),
    )

    MainWindow._load_manifest_after_scan(fake_self, "/m.sqlite")

    assert select_was_called["yes"] is False


# ── _reselect_by_path / _select_rows_by_paths body (calls into helper) ──


def test_reselect_by_path_calls_helper_then_selects_returned_index():
    """``_reselect_by_path`` looks up the path via the helper, then
    asks the tree to scroll-and-select.

    Failure mode: a refactor swaps the helper call for an in-line walk
    that no longer matches the same coordinate system as the tree
    selection — the user sees the wrong row highlighted, or none at
    all (the post-#239 selection-loss class).
    """
    sentinel_idx = object()
    fake_model = MagicMock()
    fake_tree = MagicMock()
    fake_tree.model.return_value = fake_model
    fake_sel = MagicMock()
    fake_tree.selectionModel.return_value = fake_sel
    fake_self = SimpleNamespace(tree=fake_tree)

    import app.views.main_window as mw_mod

    def fake_finder(model, target):
        assert model is fake_model
        assert target == "/photos/x.jpg"
        return sentinel_idx

    # Patch the helper at its import site inside _reselect_by_path.
    # The function does ``from app.views.main_window_helpers import
    # find_path_in_model`` at call time; we replace the symbol on the
    # helpers module so the inner import resolves to our fake.
    import app.views.main_window_helpers as helpers
    real = helpers.find_path_in_model
    helpers.find_path_in_model = fake_finder
    try:
        mw_mod.MainWindow._reselect_by_path(fake_self, "/photos/x.jpg")
    finally:
        helpers.find_path_in_model = real

    fake_tree.scrollTo.assert_called_once_with(sentinel_idx)
    fake_sel.select.assert_called_once()
    selected_idx, _flags = fake_sel.select.call_args[0]
    assert selected_idx is sentinel_idx


def test_reselect_by_path_does_nothing_when_helper_returns_none():
    """Helper returns ``None`` (missing path) → no scroll, no select.
    Catches the stale-selected-path crash class."""
    fake_tree = MagicMock()
    fake_tree.model.return_value = MagicMock()
    fake_self = SimpleNamespace(tree=fake_tree)

    import app.views.main_window_helpers as helpers
    real = helpers.find_path_in_model
    helpers.find_path_in_model = lambda m, p: None
    try:
        MainWindow._reselect_by_path(fake_self, "/missing.jpg")
    finally:
        helpers.find_path_in_model = real

    fake_tree.scrollTo.assert_not_called()
    fake_tree.selectionModel.assert_not_called()


def test_select_rows_by_paths_applies_selection_in_one_call():
    """Multi-target version: build a single QItemSelection, apply it
    in one ``select(...)`` call with ClearAndSelect | Rows, then
    ``scrollTo`` the first match.

    Failure mode: regressing to a per-index loop over ``sel_model.select(idx, ...)``
    emits ``selectionChanged`` once per match. The wired handler
    (``on_tree_selection_changed`` → ``_preview.show_single``) is a
    heavy image/video load on the UI thread — N=hundreds of keepers
    freezes the window after Close & Load (see #344). Test pins the
    contract: select() must be called exactly once regardless of N.
    """
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtGui import QStandardItem, QStandardItemModel

    # Real QModelIndex objects — QItemSelection.select is type-checked
    # PySide6 code and rejects bare object() mocks.
    real_model = QStandardItemModel()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        real_model.appendRow(QStandardItem(name))
    idx_a = real_model.index(0, 0)
    idx_b = real_model.index(1, 0)
    idx_c = real_model.index(2, 0)

    fake_tree = MagicMock()
    fake_tree.model.return_value = real_model
    fake_sel = MagicMock()
    fake_tree.selectionModel.return_value = fake_sel
    fake_self = SimpleNamespace(tree=fake_tree)

    import app.views.main_window_helpers as helpers
    real = helpers.find_paths_in_model
    helpers.find_paths_in_model = lambda model, targets: [idx_a, idx_b, idx_c]
    try:
        MainWindow._select_rows_by_paths(fake_self, {"/a.jpg", "/b.jpg", "/c.jpg"})
    finally:
        helpers.find_paths_in_model = real

    # The freeze regression check: exactly one .select() call no matter
    # how many keepers the auto-select produces.
    assert fake_sel.select.call_count == 1, (
        f"sel_model.select() called {fake_sel.select.call_count}x — "
        f"regressed to per-index loop; will refreeze on real scans (see #344)"
    )

    # Verify the single call uses ClearAndSelect | Rows (replaces the
    # previous clearSelection() + Select-each pair atomically).
    args, _ = fake_sel.select.call_args
    flags = args[1]
    assert flags & QItemSelectionModel.ClearAndSelect, (
        "select() flags must include ClearAndSelect — otherwise stale "
        "selection from before the scan is kept"
    )
    assert flags & QItemSelectionModel.Rows, (
        "select() flags must include Rows — single-cell selection "
        "doesn't highlight the full row"
    )

    fake_tree.scrollTo.assert_called_once_with(idx_a)


def test_select_rows_by_paths_noop_when_no_matches():
    """No matches → no clear, no select, no scroll. Avoids wiping
    the user's selection when the auto-select set is empty."""
    fake_tree = MagicMock()
    fake_tree.model.return_value = MagicMock()
    fake_sel = MagicMock()
    fake_tree.selectionModel.return_value = fake_sel
    fake_self = SimpleNamespace(tree=fake_tree)

    import app.views.main_window_helpers as helpers
    real = helpers.find_paths_in_model
    helpers.find_paths_in_model = lambda model, targets: []
    try:
        MainWindow._select_rows_by_paths(fake_self, set())
    finally:
        helpers.find_paths_in_model = real

    fake_sel.clearSelection.assert_not_called()
    fake_sel.select.assert_not_called()
    fake_tree.scrollTo.assert_not_called()


# ── refresh_tree empty-state toggle (#137 / #239 territory) ──────────────


def test_refresh_tree_first_load_hides_empty_state_and_shows_tree():
    """First call with the empty-state visible: hides empty-state,
    shows tree, refreshes the model.

    Failure mode (#137 / #239 territory): the toggle drifts and after
    a scan the empty-state placeholder stays visible while the tree
    sits invisible underneath — silent UX failure where the user
    stares at an empty page after a successful scan.
    """
    empty_state = MagicMock()
    empty_state.isVisible.return_value = True
    tree = MagicMock()
    tc = MagicMock()
    lm = MagicMock()
    fake_self = SimpleNamespace(
        _empty_state_widget=empty_state,
        tree=tree,
        tree_controller=tc,
        layout_manager=lm,
        _window_state_qsettings=lambda: MagicMock(),
        QSETTINGS_KEY_COLUMN_STATE="x",
        on_tree_selection_changed=lambda *a: None,
    )

    MainWindow.refresh_tree(fake_self, ["groupA"])

    empty_state.setVisible.assert_called_once_with(False)
    tree.setVisible.assert_called_once_with(True)
    tc.refresh_model.assert_called_once_with(["groupA"])
    tc.reconnect_selection_handler.assert_called_once_with(
        fake_self.on_tree_selection_changed
    )


def test_refresh_tree_does_not_retoggle_when_empty_state_already_hidden():
    """Second + subsequent calls: don't re-toggle visibility
    (idempotent). Subtle catch — a wrong refactor that always toggles
    would flicker the tree off-then-on on every manifest reload.
    """
    empty_state = MagicMock()
    empty_state.isVisible.return_value = False
    tree = MagicMock()
    tc = MagicMock()
    fake_self = SimpleNamespace(
        _empty_state_widget=empty_state,
        tree=tree,
        tree_controller=tc,
        layout_manager=MagicMock(),
        _window_state_qsettings=lambda: MagicMock(),
        QSETTINGS_KEY_COLUMN_STATE="x",
        on_tree_selection_changed=lambda *a: None,
    )

    MainWindow.refresh_tree(fake_self, ["groupB"])

    empty_state.setVisible.assert_not_called()
    tree.setVisible.assert_not_called()
    tc.refresh_model.assert_called_once_with(["groupB"])


# ── #142 — re-scan with pending decisions ────────────────────────────────


def test_confirm_no_pending_decisions_fast_path_when_zero():
    """No pending → return True without prompting. #142 fast path."""
    fake_self = SimpleNamespace(_vm=SimpleNamespace(pending_decision_count=0))

    assert MainWindow._confirm_no_pending_decisions(fake_self) is True


def test_confirm_no_pending_decisions_returns_true_when_user_clicks_yes(monkeypatch):
    """Pending exists, user clicks Yes → True (proceed with re-scan)."""
    from PySide6.QtWidgets import QMessageBox

    fake_self = SimpleNamespace(_vm=SimpleNamespace(pending_decision_count=3))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)

    assert MainWindow._confirm_no_pending_decisions(fake_self) is True


def test_confirm_no_pending_decisions_returns_false_when_user_clicks_no(monkeypatch):
    """Pending exists, user clicks No → False (cancel re-scan). This
    is the whole point of the #142 protective dialog — if this branch
    flips, the user's review work is silently lost on every re-scan.
    """
    from PySide6.QtWidgets import QMessageBox

    fake_self = SimpleNamespace(_vm=SimpleNamespace(pending_decision_count=5))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.No)

    assert MainWindow._confirm_no_pending_decisions(fake_self) is False


# ── _load_manifest_from_path orchestration ───────────────────────────────


def test_load_manifest_from_path_loads_refreshes_and_sets_status(monkeypatch):
    """Happy path: load the manifest, set ``_manifest_path``, refresh
    tree, enable manifest-dependent menu actions, set baseline status.

    Failure mode: any link in this chain dropped silently breaks
    Open Manifest. The user opens a manifest but the tree stays
    empty (refresh_tree dropped), or the Execute Action menu stays
    greyed (set_manifest_actions(True) dropped — #244's gating
    contract).
    """
    import app.views.main_window as mw_mod

    fake_vm = MagicMock()
    fake_vm.groups = []
    fake_vm.group_count = 0
    fake_file_ops = MagicMock()
    fake_menu = MagicMock()

    fake_self = SimpleNamespace(
        _vm=fake_vm,
        file_operations=fake_file_ops,
        show_groups_summary=lambda groups: None,
        refresh_tree=MagicMock(),
        menu_controller=fake_menu,
        set_status_baseline=MagicMock(),
    )

    # Stub the SQLite-touching helper so we don't need a real DB.
    monkeypatch.setattr(
        "app.views.main_window_helpers.count_isolated_rows",
        lambda path, grouped: 0,
    )
    # Stub the manifest repository import inside the method.
    fake_repo_instance = MagicMock()
    monkeypatch.setattr(
        "infrastructure.manifest_repository.ManifestRepository",
        lambda: fake_repo_instance,
    )

    mw_mod.MainWindow._load_manifest_from_path(fake_self, "/m.sqlite")

    fake_vm.load_from_repo.assert_called_once_with(fake_repo_instance, "/m.sqlite")
    assert fake_file_ops._manifest_path == "/m.sqlite"
    fake_self.refresh_tree.assert_called_once_with([])
    fake_menu.set_manifest_actions.assert_called_once_with(True)
    fake_self.set_status_baseline.assert_called_once()


# ── _on_header_clicked: sort tracking (#121 territory) ───────────────────


def test_on_header_clicked_updates_sort_state():
    """Clicking a column header forwards the column index + order to
    ``tree_controller.update_sort_state``.

    Failure mode (#121 territory): the click handler stops calling
    update_sort_state and the sort choice gets lost on the next
    refresh_model rebuild.
    """
    fake_tc = MagicMock()
    fake_header = MagicMock()
    fake_header.sortIndicatorOrder.return_value = 0  # Qt.AscendingOrder
    fake_tree = MagicMock()
    fake_tree.header.return_value = fake_header
    fake_self = SimpleNamespace(tree=fake_tree, tree_controller=fake_tc)

    MainWindow._on_header_clicked(fake_self, 2)

    fake_tc.update_sort_state.assert_called_once_with(2, 0)


# ── UIUpdaterImpl + TreeDataProviderImpl thin wrappers ───────────────────


class TestUIUpdaterImpl:
    """``UIUpdaterImpl`` exposes the UIUpdateCallback protocol on
    MainWindow. Each method is a one-line delegation — the failure
    mode is identical to the #175 bridge-pattern hole: a renamed
    MainWindow method silently drops the corresponding call here.
    """

    def test_refresh_tree_delegates(self):
        from app.views.main_window import UIUpdaterImpl

        win = MagicMock()
        impl = UIUpdaterImpl(win)
        impl.refresh_tree(["g1", "g2"])

        win.refresh_tree.assert_called_once_with(["g1", "g2"])

    def test_show_group_counts_delegates(self):
        from app.views.main_window import UIUpdaterImpl

        win = MagicMock()
        impl = UIUpdaterImpl(win)
        impl.show_group_counts(7)

        win.show_group_counts.assert_called_once_with(7)

    def test_show_groups_summary_delegates(self):
        from app.views.main_window import UIUpdaterImpl

        win = MagicMock()
        impl = UIUpdaterImpl(win)
        impl.show_groups_summary(["g"])

        win.show_groups_summary.assert_called_once_with(["g"])


class TestTreeDataProviderImpl:
    """``TreeDataProviderImpl`` adapts ``QTreeView`` +
    ``TreeController`` to the protocol the dialogs consume. Each
    accessor is a one-line lookup; same bridge-hole failure mode."""

    def test_get_selection_model_returns_tree_selection_model(self):
        from app.views.main_window import TreeDataProviderImpl

        tree = MagicMock()
        tc = MagicMock()
        provider = TreeDataProviderImpl(tree, tc)

        result = provider.get_selection_model()

        assert result is tree.selectionModel.return_value

    def test_get_view_model_returns_tree_model(self):
        from app.views.main_window import TreeDataProviderImpl

        tree = MagicMock()
        tc = MagicMock()
        provider = TreeDataProviderImpl(tree, tc)

        result = provider.get_view_model()

        assert result is tree.model.return_value

    def test_get_source_model_returns_controller_model(self):
        from app.views.main_window import TreeDataProviderImpl

        tree = MagicMock()
        tc = MagicMock()
        tc.model = "source"
        provider = TreeDataProviderImpl(tree, tc)

        assert provider.get_source_model() == "source"

    def test_get_proxy_model_returns_controller_proxy(self):
        from app.views.main_window import TreeDataProviderImpl

        tree = MagicMock()
        tc = MagicMock()
        tc.proxy = "proxy"
        provider = TreeDataProviderImpl(tree, tc)

        assert provider.get_proxy_model() == "proxy"


# ── set_status_baseline (#138 / #140 — transient vs persistent messages) ─


def test_set_status_baseline_updates_label_and_clears_temp_message():
    """``set_status_baseline`` updates the persistent label AND
    clears any active temp message so the new baseline surfaces now.

    Failure mode (#138, #140): a worker leaves a persistent
    ``showMessage(text, 0)`` on the status bar; without the explicit
    ``clearMessage()`` here, the new baseline stays hidden behind
    that temp message indefinitely.
    """
    fake_label = MagicMock()
    fake_status_bar = MagicMock()
    fake_self = SimpleNamespace(
        _status_baseline=fake_label,
        statusBar=lambda: fake_status_bar,
    )

    MainWindow.set_status_baseline(fake_self, "Loaded 3 groups.")

    fake_label.setText.assert_called_once_with("Loaded 3 groups.")
    fake_status_bar.clearMessage.assert_called_once_with()


# ── on_scan_sources — open ScanDialog with the right callbacks ────────────


def test_on_scan_sources_opens_scan_dialog_with_callbacks(monkeypatch):
    """``on_scan_sources`` instantiates ScanDialog with the load
    callback + dirty-check predicate.

    Failure mode: a renamed callback (e.g. ``_load_manifest_after_scan``
    → ``_handle_scan_result``) is not picked up here, and the scan
    finishes successfully but no manifest loads in the UI. Same
    bridge-pattern class as #175.
    """
    captured: dict = {}

    class FakeScanDialog:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            # #468 — MainWindow.on_scan_sources connects to these signals;
            # MagicMock supplies a .connect() that no-ops cleanly so the
            # test doesn't have to care about Qt signal mechanics.
            self.scan_started = MagicMock()
            self.scan_finished = MagicMock()

        def exec(self):
            captured["exec_called"] = True

    monkeypatch.setattr("app.views.dialogs.scan_dialog.ScanDialog", FakeScanDialog)

    fake_self = SimpleNamespace(
        _settings={"x": 1},
        _load_manifest_after_scan=lambda p: None,
        _confirm_no_pending_decisions=lambda: True,
    )

    MainWindow.on_scan_sources(fake_self)

    assert captured["exec_called"] is True
    assert captured["on_scan_complete"] is fake_self._load_manifest_after_scan
    assert captured["should_proceed"] is fake_self._confirm_no_pending_decisions
    assert captured["settings"] == {"x": 1}


# ── on_tree_selection_changed (#239 — preview-pane dispatch) ─────────────


def _make_index_with_data(model, row, col, parent_idx, data_map):
    """Return a MagicMock index that responds to .row(), .column(),
    .parent(), .isValid() consistently. ``data_map`` maps role -> value
    for ``model.data(idx, role)`` lookups."""
    idx = MagicMock()
    idx.row.return_value = row
    idx.column.return_value = col
    idx.parent.return_value = parent_idx
    idx.isValid.return_value = True
    return idx


def test_on_tree_selection_changed_file_row_calls_show_single():
    """File-row branch: extract path + metadata via model.data() and
    invoke ``preview.show_single(path, info_dict)``.

    Failure mode (#239 territory): proxy→source mapping breaks and
    the user sees the WRONG file previewed after sorting — the bug
    survives all column-sort tests because they don't assert on the
    preview content.
    """
    # Build a fake model whose `.data(idx, role)` lookup returns
    # deterministic values keyed on (idx.row(), col, role).
    fake_model = MagicMock()

    valid_parent = MagicMock()
    valid_parent.isValid.return_value = True
    file_idx = MagicMock()
    file_idx.row.return_value = 0
    file_idx.column.return_value = 4  # COL_NAME
    file_idx.parent.return_value = valid_parent
    file_idx.isValid.return_value = True

    # Six sub-indexes for the model.index() calls inside the method.
    def model_index_side_effect(row, col, parent=None):
        sub = MagicMock()
        sub._row = row
        sub._col = col
        return sub

    fake_model.index.side_effect = model_index_side_effect

    def model_data_side_effect(idx, role=None):
        if role == 32:  # PATH_ROLE
            return "/photos/file.jpg"
        # text data for COL_NAME / COL_FOLDER / COL_SIZE_BYTES etc.
        return {
            4: "file.jpg",      # COL_NAME
            5: "/photos",       # COL_FOLDER
            6: "1024",          # COL_SIZE_BYTES
            8: "2024-01-01",    # COL_CREATION_DATE
            9: "2024-01-01",    # COL_SHOT_DATE
        }.get(idx._col, "")

    fake_model.data.side_effect = model_data_side_effect

    fake_sel_model = MagicMock()
    fake_sel_model.selectedRows.return_value = [file_idx]
    fake_tree = MagicMock()
    fake_tree.model.return_value = fake_model
    fake_tree.selectionModel.return_value = fake_sel_model

    fake_tc = MagicMock()
    fake_tc.model = fake_model
    fake_tc.proxy = None  # no proxy, use view model directly

    fake_preview = MagicMock()
    fake_self = SimpleNamespace(
        tree=fake_tree,
        tree_controller=fake_tc,
        _preview=fake_preview,
    )

    MainWindow.on_tree_selection_changed(fake_self)

    fake_preview.show_single.assert_called_once()
    path_arg, info_arg = fake_preview.show_single.call_args[0]
    assert path_arg == "/photos/file.jpg"
    assert info_arg["name"] == "file.jpg"


def test_on_tree_selection_changed_group_row_calls_show_grid():
    """Group-row branch: walk parent_item.child(r, ...) for each row
    and invoke ``preview.show_grid(items)`` with one tuple per file.

    Failure mode: group selection broken → user clicks a group header
    expecting thumbnails, gets nothing. Layer-3 covers the visual
    result; this pins the dispatch + extraction.
    """
    fake_model = MagicMock()

    # group_idx has invalid parent (top-level row)
    invalid_parent = MagicMock()
    invalid_parent.isValid.return_value = False
    group_idx = MagicMock()
    group_idx.row.return_value = 0
    group_idx.column.return_value = 0
    group_idx.parent.return_value = invalid_parent
    group_idx.isValid.return_value = True

    fake_model.index.return_value = MagicMock()

    # parent_item with 2 children (files in the group)
    parent_item = MagicMock()
    parent_item.rowCount.return_value = 2

    def _child_cell(label_text: str, path: str | None = None):
        cell = MagicMock()
        cell.text.return_value = label_text
        if path is not None:
            cell.data.return_value = path
        else:
            cell.data.return_value = None
        return cell

    def parent_child_side_effect(row, col):
        # Return a non-None mock cell for every cell lookup.
        return _child_cell(f"r{row}c{col}",
                           path=f"/photos/r{row}.jpg" if col == 4 else None)

    parent_item.child.side_effect = parent_child_side_effect

    fake_model.itemFromIndex.return_value = parent_item

    # Override itemFromIndex for child cells to return cells with .text()
    real_item_from_index = fake_model.itemFromIndex

    def item_from_index_side_effect(idx):
        # First call returns parent_item; subsequent return cells
        # with text. We just always return a mock with .text() = "x".
        m = MagicMock()
        m.text.return_value = "x"
        return m if real_item_from_index.call_count > 1 else parent_item

    fake_model.itemFromIndex.side_effect = item_from_index_side_effect

    fake_sel_model = MagicMock()
    fake_sel_model.selectedRows.return_value = [group_idx]
    fake_tree = MagicMock()
    fake_tree.model.return_value = fake_model
    fake_tree.selectionModel.return_value = fake_sel_model

    fake_tc = MagicMock()
    fake_tc.model = fake_model
    fake_tc.proxy = None

    fake_preview = MagicMock()
    fake_self = SimpleNamespace(
        tree=fake_tree,
        tree_controller=fake_tc,
        _preview=fake_preview,
    )

    MainWindow.on_tree_selection_changed(fake_self)

    fake_preview.show_grid.assert_called_once()
    grid_items = fake_preview.show_grid.call_args[0][0]
    assert len(grid_items) == 2


def test_on_tree_selection_changed_empty_selection_returns_early():
    """No selection → immediate return, no preview call. Defends
    against a refactor that fires on empty selection (would crash on
    indexes[0])."""
    fake_sel_model = MagicMock()
    fake_sel_model.selectedRows.return_value = []
    fake_tree = MagicMock()
    fake_tree.selectionModel.return_value = fake_sel_model
    fake_preview = MagicMock()
    fake_self = SimpleNamespace(
        tree=fake_tree,
        tree_controller=MagicMock(),
        _preview=fake_preview,
    )

    MainWindow.on_tree_selection_changed(fake_self)

    fake_preview.show_single.assert_not_called()
    fake_preview.show_grid.assert_not_called()


def test_on_tree_selection_changed_falls_back_to_folder_name_when_path_role_missing():
    """If PATH_ROLE is empty/None on the COL_NAME cell, fall back to
    ``Path(folder) / name``.

    Real failure mode: a tree-model rebuild that drops PATH_ROLE on
    some rows (e.g. a partial refresh) would crash the preview if
    the fallback weren't honored — instead, the user gets the
    composed path string, which is still correct.
    """
    fake_model = MagicMock()
    fake_model.index.return_value = MagicMock()

    def data_side(idx, role=None):
        if role == 32:  # PATH_ROLE
            return None  # missing — triggers fallback
        # name + folder + sizes via implicit columns
        col = getattr(idx, "_col", None)
        return {4: "img.jpg", 5: "/photos"}.get(col, "")

    # We need idx to carry _col; rebuild side_effect to attach it.
    def index_side(row, col, parent=None):
        sub = MagicMock()
        sub._col = col
        return sub

    fake_model.index.side_effect = index_side
    fake_model.data.side_effect = data_side

    valid_parent = MagicMock()
    valid_parent.isValid.return_value = True
    file_idx = MagicMock()
    file_idx.row.return_value = 0
    file_idx.parent.return_value = valid_parent
    file_idx.isValid.return_value = True

    fake_sel_model = MagicMock()
    fake_sel_model.selectedRows.return_value = [file_idx]
    fake_tree = MagicMock()
    fake_tree.model.return_value = fake_model
    fake_tree.selectionModel.return_value = fake_sel_model

    fake_tc = MagicMock()
    fake_tc.model = fake_model
    fake_tc.proxy = None

    fake_preview = MagicMock()
    fake_self = SimpleNamespace(
        tree=fake_tree,
        tree_controller=fake_tc,
        _preview=fake_preview,
    )

    MainWindow.on_tree_selection_changed(fake_self)

    fake_preview.show_single.assert_called_once()
    path_arg = fake_preview.show_single.call_args[0][0]
    # str(Path('/photos') / 'img.jpg') — platform-specific separator;
    # check it contains both pieces in order.
    assert "photos" in path_arg
    assert path_arg.endswith("img.jpg")


def test_on_tree_selection_changed_returns_when_path_and_folder_both_empty():
    """If PATH_ROLE is empty AND folder OR name is empty, abort with
    no preview call. Defends against a malformed row crashing the
    preview pane.
    """
    fake_model = MagicMock()

    def data_side(idx, role=None):
        # PATH_ROLE empty, name empty, folder empty
        return None if role == 32 else ""

    def index_side(row, col, parent=None):
        sub = MagicMock()
        sub._col = col
        return sub

    fake_model.index.side_effect = index_side
    fake_model.data.side_effect = data_side

    valid_parent = MagicMock()
    valid_parent.isValid.return_value = True
    file_idx = MagicMock()
    file_idx.row.return_value = 0
    file_idx.parent.return_value = valid_parent
    file_idx.isValid.return_value = True

    fake_sel_model = MagicMock()
    fake_sel_model.selectedRows.return_value = [file_idx]
    fake_tree = MagicMock()
    fake_tree.model.return_value = fake_model
    fake_tree.selectionModel.return_value = fake_sel_model

    fake_tc = MagicMock()
    fake_tc.model = fake_model
    fake_tc.proxy = None

    fake_preview = MagicMock()
    fake_self = SimpleNamespace(
        tree=fake_tree,
        tree_controller=fake_tc,
        _preview=fake_preview,
    )

    MainWindow.on_tree_selection_changed(fake_self)

    fake_preview.show_single.assert_not_called()


# ── window_state shims — back-compat with old callers ───────────────────


def test_qsettings_path_shim_delegates_to_module():
    """``MainWindow._qsettings_path`` is a back-compat shim around
    ``app.views.window_state.qsettings_path``. Catches: a refactor
    that removes the module-level function and breaks the shim
    silently — old callers using ``MainWindow._qsettings_path``
    would fail at runtime.
    """
    from app.views.window_state import qsettings_path

    assert MainWindow._qsettings_path() == qsettings_path()


# ── relocalize selected-path round-trip (#22 selection survival) ─────────


def test_capture_relocalize_state_captures_first_selected_file_path():
    """``_capture_relocalize_state`` must include the
    first-selected file path so the post-language-switch reconstruct
    can restore it.

    Failure mode (#22 territory): refactor drops the
    ``extract_first_selected_file_path`` call → snapshot has no
    selected_path → user's row is silently lost on every language
    switch. Note: this test pins the *business logic* (selected path
    flows through), NOT the Qt plumbing (saveGeometry / splitter
    state), which stays at layer 3 per ``docs/testing.md:151``.
    """
    fake_layout = MagicMock()
    fake_layout.get_splitter.return_value = None  # skip splitter branch
    fake_tc = MagicMock()
    fake_tc.get_selected_items.return_value = [
        {"type": "group", "path": "/g/"},
        {"type": "file", "path": "/sel.jpg"},
    ]
    fake_self = SimpleNamespace(
        saveGeometry=lambda: b"",
        layout_manager=fake_layout,
        tree_controller=fake_tc,
        _thumb_size=512,
        # #428 — capture now reads file_operations._manifest_path too.
        # An unloaded handler (no _manifest_path attr) is the
        # relevant baseline for the selected-path contract.
        file_operations=SimpleNamespace(),
    )

    state = MainWindow._capture_relocalize_state(fake_self)

    assert state["selected_path"] == "/sel.jpg"
    assert state["thumb_size"] == 512


def test_apply_relocalize_state_reselects_when_path_in_state():
    """``_apply_relocalize_state`` with a non-empty ``selected_path``
    must call ``_reselect_by_path``. Catches: a refactor that renames
    the state-dict key and silently drops the re-selection — the
    user's row goes missing every time they switch language."""
    fake_self = SimpleNamespace(
        restoreGeometry=lambda b: None,
        layout_manager=MagicMock(),
        _reselect_by_path=MagicMock(),
    )
    fake_self.layout_manager.get_splitter.return_value = None

    MainWindow._apply_relocalize_state(
        fake_self,
        {
            "geometry": None,
            "splitter_state": None,
            "selected_path": "/photos/sel.jpg",
            "thumb_size": 512,
        },
    )

    fake_self._reselect_by_path.assert_called_once_with("/photos/sel.jpg")


def test_apply_relocalize_state_skips_reselect_when_no_path():
    """Empty/None selected_path → no reselect call (avoids calling
    ``_reselect_by_path("")`` which would silently no-op anyway but
    pins the contract)."""
    fake_self = SimpleNamespace(
        restoreGeometry=lambda b: None,
        layout_manager=MagicMock(),
        _reselect_by_path=MagicMock(),
    )
    fake_self.layout_manager.get_splitter.return_value = None

    MainWindow._apply_relocalize_state(
        fake_self,
        {"geometry": None, "splitter_state": None, "selected_path": None, "thumb_size": 0},
    )

    fake_self._reselect_by_path.assert_not_called()


# ── relocalize manifest_path round-trip (#428) ───────────────────────────


def test_capture_relocalize_state_captures_manifest_path():
    """``_capture_relocalize_state`` must include the loaded manifest
    path so the post-switch MainWindow can re-load it (#428).

    Failure mode (the original #428 bug, restated as a test): without
    this key the new MainWindow shows the empty-state hint after a
    language switch even though ``language.confirm_body`` told the
    user "your loaded manifest and decisions stay intact". A
    regression would silently strip the key from the snapshot — the
    new window would have nothing to call ``_load_manifest_from_path``
    on and the tree would render empty.
    """
    fake_layout = MagicMock()
    fake_layout.get_splitter.return_value = None
    fake_tc = MagicMock()
    fake_tc.get_selected_items.return_value = []
    fake_self = SimpleNamespace(
        saveGeometry=lambda: b"",
        layout_manager=fake_layout,
        tree_controller=fake_tc,
        _thumb_size=256,
        file_operations=SimpleNamespace(_manifest_path="/tmp/m.sqlite"),
    )

    state = MainWindow._capture_relocalize_state(fake_self)

    assert state["manifest_path"] == "/tmp/m.sqlite"


def test_capture_relocalize_state_manifest_path_none_when_unloaded():
    """When no manifest is loaded, ``_manifest_path`` may be unset on
    file_operations. The capture must fall back to ``None`` rather
    than raising — relocalize is reachable from the menu before any
    scan / open has populated the handler attr."""
    fake_layout = MagicMock()
    fake_layout.get_splitter.return_value = None
    fake_tc = MagicMock()
    fake_tc.get_selected_items.return_value = []
    fake_self = SimpleNamespace(
        saveGeometry=lambda: b"",
        layout_manager=fake_layout,
        tree_controller=fake_tc,
        _thumb_size=256,
        # Handler with no _manifest_path attribute at all — mirrors the
        # pre-first-scan state of the real FileOperationsHandler.
        file_operations=SimpleNamespace(),
    )

    state = MainWindow._capture_relocalize_state(fake_self)

    assert state["manifest_path"] is None


def test_apply_relocalize_state_reloads_manifest_when_path_in_state():
    """``_apply_relocalize_state`` with a non-None ``manifest_path``
    must call ``_load_manifest_from_path``. This is the user-visible
    half of the #428 fix — the freshly-built MainWindow has no tree
    rows until something repopulates them, and the SQLite re-load
    rebuilds tree + status bar + menu-action gating in one call.
    Reload happens BEFORE the reselect call so the row walk has
    something to find.
    """
    call_order: list[str] = []
    fake_empty = MagicMock()
    fake_tree = MagicMock()
    fake_self = SimpleNamespace(
        restoreGeometry=lambda b: None,
        layout_manager=MagicMock(),
        _load_manifest_from_path=MagicMock(
            side_effect=lambda _p: call_order.append("load")
        ),
        _reselect_by_path=MagicMock(
            side_effect=lambda _p: call_order.append("select")
        ),
        _empty_state_widget=fake_empty,
        tree=fake_tree,
    )
    fake_self.layout_manager.get_splitter.return_value = None

    MainWindow._apply_relocalize_state(
        fake_self,
        {
            "geometry": None,
            "splitter_state": None,
            "selected_path": "/photos/sel.jpg",
            "thumb_size": 256,
            "manifest_path": "/tmp/m.sqlite",
        },
    )

    fake_self._load_manifest_from_path.assert_called_once_with("/tmp/m.sqlite")
    # Reload must precede reselect — without rows the reselect walk
    # finds nothing and the previous-row recovery silently no-ops.
    assert call_order == ["load", "select"]
    # Pin the explicit visibility flip — refresh_tree's isVisible()
    # guard misses pre-show, so without this flip the empty-state
    # widget stays visible and the tree stays hidden after show()
    # even though the model has rows.
    fake_empty.setVisible.assert_called_once_with(False)
    fake_tree.setVisible.assert_called_once_with(True)


def test_apply_relocalize_state_skips_reload_when_no_manifest_path():
    """A relocalize with no manifest in flight (user switched language
    on the empty-state window) must not call _load_manifest_from_path
    — there's nothing to load, and the existing tree-empty state is
    already the desired post-switch state."""
    fake_self = SimpleNamespace(
        restoreGeometry=lambda b: None,
        layout_manager=MagicMock(),
        _load_manifest_from_path=MagicMock(),
        _reselect_by_path=MagicMock(),
    )
    fake_self.layout_manager.get_splitter.return_value = None

    MainWindow._apply_relocalize_state(
        fake_self,
        {
            "geometry": None,
            "splitter_state": None,
            "selected_path": None,
            "thumb_size": 0,
            "manifest_path": None,
        },
    )

    fake_self._load_manifest_from_path.assert_not_called()


def test_window_state_qsettings_shim_delegates_to_module():
    """Companion shim — same back-compat contract for the QSettings
    factory. Returns a QSettings instance pointing at the same path
    every time."""
    from app.views.window_state import window_state_qsettings

    s1 = MainWindow._window_state_qsettings()
    s2 = window_state_qsettings()
    assert s1.fileName() == s2.fileName()


def test_on_tree_selection_changed_uses_proxy_mapping_when_proxy_present():
    """If ``tree_controller.proxy`` exists with ``mapToSource``, the
    method MUST go through it before reading data — otherwise the
    sorted-tree view returns the WRONG source row (#239 root cause
    class).
    """
    fake_model = MagicMock()
    fake_model.data.return_value = "/p.jpg"
    fake_model.index.return_value = MagicMock()

    src_idx = MagicMock()
    src_idx.row.return_value = 7
    src_idx.column.return_value = 0
    parent = MagicMock()
    parent.isValid.return_value = True
    src_idx.parent.return_value = parent
    src_idx.isValid.return_value = True

    view_idx = MagicMock()
    view_idx.row.return_value = 0  # different from src_idx.row — proves mapping was used
    view_idx.parent.return_value = parent
    view_idx.isValid.return_value = True

    fake_proxy = MagicMock()
    fake_proxy.mapToSource.return_value = src_idx

    fake_view_model = MagicMock()
    fake_sel_model = MagicMock()
    fake_sel_model.selectedRows.return_value = [view_idx]
    fake_tree = MagicMock()
    fake_tree.model.return_value = fake_view_model
    fake_tree.selectionModel.return_value = fake_sel_model

    fake_tc = MagicMock()
    fake_tc.model = fake_model  # source model
    fake_tc.proxy = fake_proxy

    fake_preview = MagicMock()
    fake_self = SimpleNamespace(
        tree=fake_tree,
        tree_controller=fake_tc,
        _preview=fake_preview,
    )

    MainWindow.on_tree_selection_changed(fake_self)

    fake_proxy.mapToSource.assert_called_once_with(view_idx)
    fake_preview.show_single.assert_called_once()


class TestExitDialogButtonsConstant:
    """Pin the ``EXIT_DIALOG_BUTTONS`` spec used by ``MainWindow.closeEvent``.

    The qa batch runner (``qa/scenarios/_close_window_helper.py``)
    looks up the "Leave" button by its display text. If a future PR
    reorders / renames / removes entries in ``EXIT_DIALOG_BUTTONS``
    the runner's label-based click can break silently — the symptom
    is qa scenarios printing "app did not exit cleanly, terminating"
    again, exactly the regression #325 fixed.

    These tests cost ~0ms — they import a tuple and check its shape,
    no Qt event loop, no QMessageBox instance. The metric-gaming
    rejection in CLAUDE.md applies to *defensive branches* not to
    real-bug probes; this one catches a documented past regression
    class, so it earns its keep.
    """

    def test_constant_lists_exactly_three_buttons_in_dialog_order(self) -> None:
        """save → leave → back is the order ``MainWindow.closeEvent``
        adds buttons and the order Qt renders them left-to-right. The
        qa helper's positional ``fallback_tab_enter`` (Tab Tab Enter
        from default-focused Back) only works if Leave sits at index 1.
        """
        from app.views.main_window import EXIT_DIALOG_BUTTONS

        names = [name for name, _key, _role in EXIT_DIALOG_BUTTONS]
        assert names == ["save", "leave", "back"], (
            f"EXIT_DIALOG_BUTTONS order changed: got {names!r} — update "
            "qa/scenarios/_close_window_helper.py::fallback_tab_enter "
            "to match, then update this test."
        )

    def test_leave_button_carries_destructive_role(self) -> None:
        """``DestructiveRole`` is what tells QMessageBox to render the
        button with the "irreversible action" affordance (different
        styling on some platforms). The qa helper doesn't read role
        directly, but a reviewer eyeballing the dialog should see
        save=accept, leave=destructive, back=reject — losing
        DestructiveRole on Leave would change the visible UI without
        any other test catching it.
        """
        from PySide6.QtWidgets import QMessageBox

        from app.views.main_window import EXIT_DIALOG_BUTTONS

        leave_entry = next(
            entry for entry in EXIT_DIALOG_BUTTONS if entry[0] == "leave"
        )
        assert leave_entry[1] == "exit.button_leave"
        assert leave_entry[2] == QMessageBox.DestructiveRole

    def test_translation_keys_resolve_to_non_empty_strings_in_both_locales(self) -> None:
        """Every translation key in the spec must resolve to a non-
        empty display string in both ``en`` and ``zh_TW`` — otherwise
        the qa runner would look up an empty-string button label and
        match nothing. This is the regression class #325 is *prevent-
        ing*: silent label drift between the constant and translations.
        """
        from pathlib import Path

        from app.views.main_window import EXIT_DIALOG_BUTTONS
        from infrastructure.i18n import Translator

        translations_dir = Path(__file__).resolve().parents[1] / "translations"
        for locale in ("en", "zh_TW"):
            translator = Translator(locale, translations_dir)
            for name, key, _role in EXIT_DIALOG_BUTTONS:
                resolved = translator.t(key)
                assert resolved and resolved != key, (
                    f"{key!r} (button {name!r}) didn't resolve in {locale!r}: "
                    f"got {resolved!r} — translations/{locale}.yml may be "
                    "missing the key."
                )

    def test_close_event_body_uses_the_constant_not_inline_addbutton_calls(self) -> None:
        """The whole point of extracting ``EXIT_DIALOG_BUTTONS`` is
        that ``closeEvent`` reads from it. If a future PR inlines
        ``box.addButton(t("exit.button_leave"), ...)`` again, the
        constant becomes a lie. Catch that by parsing the source.

        This is a source-level check, not a behaviour check, but the
        behaviour version (real QMessageBox, real exec()) would cost
        ~25s per run and need a qapp fixture — far above the value of
        what's being asserted.
        """
        import inspect

        from app.views.main_window import MainWindow

        source = inspect.getsource(MainWindow.closeEvent)
        # The pre-#325 body called addButton(t("exit.button_leave"), ...)
        # inline three times. Forbid that exact pattern as a regression
        # tripwire — closeEvent should iterate over the constant.
        assert "EXIT_DIALOG_BUTTONS" in source, (
            "closeEvent no longer references EXIT_DIALOG_BUTTONS — if you "
            "removed the constant on purpose, also remove this test and "
            "update qa/scenarios/_close_window_helper.py's docstring."
        )
        # An explicit inline 't(\"exit.button_' call inside closeEvent
        # (beyond the dialog title/body) would mean someone added a
        # button outside the constant — that's the drift we want to
        # block.
        for forbidden_key in ("exit.button_save_leave", "exit.button_leave", "exit.button_back"):
            assert forbidden_key not in source, (
                f"closeEvent contains a hard-coded reference to "
                f"{forbidden_key!r}. Move it into EXIT_DIALOG_BUTTONS so "
                "the qa helper's label-based selector stays in sync."
            )
