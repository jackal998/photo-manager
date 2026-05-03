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
