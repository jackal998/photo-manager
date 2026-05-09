"""Tests for MenuController.set_manifest_actions and the MANIFEST_ACTIONS list.

The previous code maintained two divergent lists of "manifest-gated actions"
in main_window and file_operations. Centralizing them in MANIFEST_ACTIONS
fixed a latent bug where set_action_hl_delete/keep were referenced but never
registered. These tests guard against re-introducing that drift.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.views.components.menu_controller import MANIFEST_ACTIONS, MenuController


def _make_controller_with_actions() -> MenuController:
    """Build a MenuController whose actions dict is pre-populated with mocks
    for every name in MANIFEST_ACTIONS (no real Qt window required)."""
    mc = MenuController(MagicMock())
    mc.actions = {name: MagicMock() for name in MANIFEST_ACTIONS}
    return mc


def test_manifest_actions_lists_real_action_names():
    """Every name in MANIFEST_ACTIONS must match an action key registered by
    setup_menus(). If someone adds an entry that menu_controller doesn't
    register, set_manifest_actions silently skips it — that's the bug we're
    guarding against.
    """
    expected_at_minimum = {"save_manifest", "execute_action", "remove_from_list"}
    assert expected_at_minimum.issubset(set(MANIFEST_ACTIONS))


def test_set_manifest_actions_true_flips_every_action_to_enabled():
    mc = _make_controller_with_actions()
    mc.set_manifest_actions(True)
    for name in MANIFEST_ACTIONS:
        mc.actions[name].setEnabled.assert_called_once_with(True)


def test_set_manifest_actions_false_flips_every_action_to_disabled():
    mc = _make_controller_with_actions()
    mc.set_manifest_actions(False)
    for name in MANIFEST_ACTIONS:
        mc.actions[name].setEnabled.assert_called_once_with(False)


def test_set_manifest_actions_skips_unregistered_actions():
    """If a name in MANIFEST_ACTIONS isn't in self.actions (a future-typo
    safeguard), enable_action's existing guard makes it a no-op — must not raise."""
    mc = MenuController(MagicMock())
    mc.actions = {}  # nothing registered

    # Must not raise even though every name in MANIFEST_ACTIONS is missing.
    mc.set_manifest_actions(True)


@pytest.mark.parametrize("name", list(MANIFEST_ACTIONS))
def test_each_manifest_action_individually_toggleable(name):
    """Per-action toggling still works — the bulk method is a convenience,
    not a replacement for enable_action()."""
    mc = _make_controller_with_actions()
    mc.enable_action(name, False)
    mc.actions[name].setEnabled.assert_called_once_with(False)


# ── Real-Qt setup_menus exercise ─────────────────────────────────────────
# These tests catch the kind of bug where someone removes a menu key from
# setup_menus but leaves it in MANIFEST_ACTIONS — the no-op silent skip in
# enable_action would otherwise hide the breakage. Running setup_menus
# against a real QMainWindow is the only honest way to verify the action
# dict matches what the menu bar actually registers.


def test_setup_menus_registers_every_manifest_action(qapp):
    from PySide6.QtWidgets import QMainWindow

    win = QMainWindow()
    mc = MenuController(win)
    actions = mc.setup_menus()
    for name in MANIFEST_ACTIONS:
        assert name in actions, (
            f"MANIFEST_ACTIONS lists {name!r} but setup_menus didn't register it; "
            f"set_manifest_actions would silently skip this action."
        )


def test_setup_menus_marks_manifest_actions_disabled_initially(qapp):
    from PySide6.QtWidgets import QMainWindow

    win = QMainWindow()
    mc = MenuController(win)
    actions = mc.setup_menus()
    for name in MANIFEST_ACTIONS:
        assert actions[name].isEnabled() is False, (
            f"Action {name!r} should start disabled until a manifest loads."
        )


def test_setup_menus_then_set_manifest_actions_enables_all(qapp):
    from PySide6.QtWidgets import QMainWindow

    win = QMainWindow()
    mc = MenuController(win)
    actions = mc.setup_menus()
    mc.set_manifest_actions(True)
    for name in MANIFEST_ACTIONS:
        assert actions[name].isEnabled() is True


def test_connect_actions_wires_handlers(qapp):
    """connect_actions hooks the QAction.triggered signal to the named handler."""
    from PySide6.QtWidgets import QMainWindow

    win = QMainWindow()
    mc = MenuController(win)
    actions = mc.setup_menus()

    calls: list[str] = []
    # QAction.triggered passes a `checked` bool — accept and ignore it.
    handlers = {
        name: (lambda *_, n=name: calls.append(n)) for name in actions
    }
    mc.connect_actions(handlers)

    # Emit the triggered signal directly. trigger() requires an active
    # event loop to deliver the signal; emit() bypasses that and is enough
    # to verify connect_actions wired the right handler to the right action.
    actions["save_manifest"].triggered.emit()
    actions["execute_action"].triggered.emit()
    assert "save_manifest" in calls
    assert "execute_action" in calls


def test_get_action_returns_registered_action(qapp):
    from PySide6.QtWidgets import QMainWindow

    win = QMainWindow()
    mc = MenuController(win)
    mc.setup_menus()
    assert mc.get_action("save_manifest") is not None
    assert mc.get_action("nonexistent_xyz") is None


def test_get_all_actions_returns_a_copy(qapp):
    from PySide6.QtWidgets import QMainWindow

    win = QMainWindow()
    mc = MenuController(win)
    mc.setup_menus()
    snapshot = mc.get_all_actions()
    snapshot["sentinel"] = "intruder"
    assert "sentinel" not in mc.actions, "get_all_actions must return a copy, not the live dict"


# ── #135: top-level menus must declare Alt+letter mnemonics ────────────────

def test_top_level_menus_have_mnemonic_prefixes(qapp):
    """#135 — every top-level menu title contains a Qt ``&`` mnemonic
    prefix so Alt+letter opens it without a mouse. Without this, UIA's
    AccessKey property is empty for these QActions and the keyboard-
    nav scenario (#125) cannot test mnemonic-driven menu opens.

    Pin both that the titles include ``&`` AND that the mnemonics are
    pairwise unique — Qt's QMenuBar resolves ambiguous mnemonics in
    insertion order, which would silently shadow whichever menu came
    second on a collision. List + Log both start with L; this test
    guards against an unconscious regression that flips List from
    ``Alt+L`` to ``Lo&g`` (or vice versa) and accidentally collides.
    """
    from PySide6.QtWidgets import QMainWindow

    win = QMainWindow()
    mc = MenuController(win)
    mc.setup_menus()

    menubar = win.menuBar()
    titles = [a.menu().title() for a in menubar.actions() if a.menu() is not None]
    assert titles, "no top-level menus were registered"

    # Each title must contain a single ``&`` followed by a letter.
    mnemonics: list[str] = []
    for title in titles:
        idx = title.find("&")
        assert idx != -1, (
            f"menu {title!r} has no Alt mnemonic; add a '&' before the "
            f"intended Alt letter"
        )
        # Reject the ``&&`` literal-ampersand escape, which has no mnemonic.
        assert title[idx + 1:idx + 2] != "&", (
            f"menu {title!r} starts an escaped '&&'; intended mnemonic "
            f"missing"
        )
        letter = title[idx + 1:idx + 2].upper()
        assert letter, f"menu {title!r} has '&' at the end with no letter after"
        mnemonics.append(letter)

    duplicates = {m for m in mnemonics if mnemonics.count(m) > 1}
    assert not duplicates, (
        f"top-level menu mnemonics collide: {sorted(duplicates)}; "
        f"titles={titles}"
    )


def test_top_level_menus_specific_mnemonic_assignment(qapp):
    """Pin the deliberate List=Alt+L / Log=Alt+G choice so a future
    refactor that "obviously" assigns Alt+L to Log (alphabetical) or
    swaps the two breaks loudly — that swap would change muscle memory
    for users who already learned the layout.
    """
    from PySide6.QtWidgets import QMainWindow

    win = QMainWindow()
    mc = MenuController(win)
    mc.setup_menus()

    menubar = win.menuBar()
    title_by_first_word = {
        a.menu().title().lstrip("&").split()[0].rstrip("…").rstrip(":")
        .replace("&", "").lower(): a.menu().title()
        for a in menubar.actions() if a.menu() is not None
    }
    # Expected: File→F, Action→A, List→L, Log→G
    expected = {
        "file": "&File",
        "action": "&Action",
        "list": "&List",
        "log": "Lo&g",
    }
    for name, want in expected.items():
        # Match by mnemonic-stripped lowercase (so the test still works
        # if a future change adds extra title decoration like
        # ``&File (Recent)``).
        candidates = [t for t in title_by_first_word.values()
                      if t.replace("&", "").lower().startswith(name)]
        assert candidates, f"no menu starting with {name!r} found in {title_by_first_word}"
        assert want in candidates, (
            f"expected {want!r} for {name!r} mnemonic; got {candidates!r}"
        )


# ── View → Language submenu — radio-button exclusivity ────────────────────
# Without the QActionGroup, individually-checkable QActions accumulate
# ticks across clicks. These tests pin the radio-button behaviour so a
# regression that drops the group is caught at PR time rather than
# discovered when the user reports two locales checked at once.


def test_language_actions_share_one_exclusive_action_group(qapp):
    """Every locale entry in View → Language must belong to the same
    QActionGroup, and that group must be exclusive."""
    from PySide6.QtWidgets import QMainWindow

    win = QMainWindow()
    mc = MenuController(win)
    mc.setup_menus()

    group = getattr(mc, "_language_group", None)
    assert group is not None, "MenuController should expose _language_group"
    assert group.isExclusive(), "Language QActionGroup must be exclusive"

    for code, action in mc._language_actions.items():
        assert action.actionGroup() is group, (
            f"Language action {code!r} is not in the exclusive group"
        )


def test_language_picker_exclusive_check_state(qapp):
    """Clicking a second locale must uncheck the previously-active one.

    Reproduces the multi-select bug: before QActionGroup, both English
    and 繁體中文 stayed checked after a switch. With the group in place,
    Qt enforces 'at most one checked' automatically.
    """
    from PySide6.QtWidgets import QMainWindow

    win = QMainWindow()
    mc = MenuController(win)
    mc.setup_menus()

    actions = list(mc._language_actions.values())
    if len(actions) < 2:
        pytest.skip("need at least 2 shipped locales to exercise exclusivity")

    first, second = actions[0], actions[1]
    first.setChecked(True)
    second.setChecked(True)
    assert second.isChecked()
    assert not first.isChecked(), (
        "Setting a second locale checked must uncheck the first when the "
        "actions share an exclusive QActionGroup."
    )


def test_on_language_chosen_noops_on_same_locale(qapp):
    """Picking the active locale must short-circuit before any prompt
    or relocalize call. Without this guard, the user would see a
    pointless 'Switch language?' dialog every time they accidentally
    re-clicked the current locale.
    """
    from unittest.mock import patch

    from PySide6.QtWidgets import QMainWindow

    from infrastructure.i18n import get_translator

    host = QMainWindow()
    calls = {"hit": 0}
    host.relocalize = lambda: calls.update(hit=calls["hit"] + 1)  # type: ignore[attr-defined]

    mc = MenuController(host)
    mc.setup_menus()

    current = get_translator().locale
    with patch("PySide6.QtWidgets.QMessageBox.question") as q:
        mc._on_language_chosen(current)

    q.assert_not_called(), (
        "Same-locale click should short-circuit before showing the prompt"
    )
    assert calls["hit"] == 0


def test_on_language_chosen_yes_invokes_relocalize(qapp):
    """User picks a different locale and clicks Yes → relocalize fires."""
    from unittest.mock import patch

    from PySide6.QtWidgets import QMainWindow, QMessageBox

    from infrastructure.i18n import get_translator

    host = QMainWindow()
    calls = {"hit": 0}
    host.relocalize = lambda: calls.update(hit=calls["hit"] + 1)  # type: ignore[attr-defined]

    mc = MenuController(host)
    mc.setup_menus()

    current = get_translator().locale
    other = "zh_TW" if current != "zh_TW" else "en"

    with patch(
        "PySide6.QtWidgets.QMessageBox.question",
        return_value=QMessageBox.Yes,
    ) as q:
        mc._on_language_chosen(other)

    q.assert_called_once()
    assert calls["hit"] == 1, (
        f"_on_language_chosen({other!r}) did not call host.relocalize after Yes"
    )


def test_on_language_chosen_no_skips_relocalize(qapp):
    """User picks a different locale and clicks No → no relocalize, no
    settings write. The QActionGroup auto-flipped on click, so we must
    also revert the check state to the originally-active locale.
    """
    from unittest.mock import MagicMock, patch

    from PySide6.QtWidgets import QMainWindow, QMessageBox

    from infrastructure.i18n import get_translator

    host = QMainWindow()
    calls = {"hit": 0}
    host.relocalize = lambda: calls.update(hit=calls["hit"] + 1)  # type: ignore[attr-defined]

    settings = MagicMock()
    mc = MenuController(host, settings=settings)
    mc.setup_menus()

    current = get_translator().locale
    other = "zh_TW" if current != "zh_TW" else "en"

    # Simulate the QActionGroup's auto-check on click by flipping
    # the picked action's checked state.
    if other in mc._language_actions:
        mc._language_actions[other].setChecked(True)

    with patch(
        "PySide6.QtWidgets.QMessageBox.question",
        return_value=QMessageBox.No,
    ):
        mc._on_language_chosen(other)

    assert calls["hit"] == 0, "relocalize fired despite the user clicking No"
    settings.set.assert_not_called()
    settings.save.assert_not_called()
    # Revert: the active locale's action should be checked again,
    # not the one the user almost-picked.
    if current in mc._language_actions:
        assert mc._language_actions[current].isChecked()

