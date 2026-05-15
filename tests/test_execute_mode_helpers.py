"""Tests for the #165 Execute Mode prototype helpers.

Covers the small pure-logic pieces that drive the main window's
Execute-mode UI (banner content, menu wiring) so the prototype is
honest about coverage rather than relying on the s46 qa scenario
alone. The destructive run itself is still tested via the existing
``test_execute_action_dialog.py`` against ``ExecuteActionDialog`` —
``ExecuteRunner`` inherits the same flow.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

# Import from the side module (NOT main_window.py) so this test file
# doesn't cascade-load the QMainWindow view stack (PreviewPane,
# ImageTaskRunner, DialogHandler, GroupMediaController, VideoPlayer)
# into coverage measurement — those modules carry ~1000 statements
# with no layer-1 coverage and would tank the global coverage gate.
from app.views.execute_mode_helpers import complete_delete_group_numbers


def _rec(path: str, user_decision: str = "") -> SimpleNamespace:
    return SimpleNamespace(file_path=path, user_decision=user_decision)


def _group(group_number: int, items: list) -> SimpleNamespace:
    return SimpleNamespace(group_number=group_number, items=items)


# ── complete_delete_group_numbers ────────────────────────────────────────


class TestCompleteDeleteGroupNumbers:
    """Banner content driver. Every group_number whose every row is
    decided ``delete`` should appear in the result. Empty groups are
    skipped — they wouldn't trigger a destructive op."""

    def test_empty_input_returns_empty(self):
        assert complete_delete_group_numbers([]) == []
        assert complete_delete_group_numbers(None) == []

    def test_returns_group_when_every_row_delete(self):
        g = _group(7, [_rec("/a", "delete"), _rec("/b", "delete")])
        assert complete_delete_group_numbers([g]) == [7]

    def test_skips_group_with_mixed_decisions(self):
        g = _group(3, [_rec("/a", "delete"), _rec("/b", "keep")])
        assert complete_delete_group_numbers([g]) == []

    def test_skips_group_with_any_undecided_row(self):
        g = _group(4, [_rec("/a", "delete"), _rec("/b", "")])
        assert complete_delete_group_numbers([g]) == []

    def test_skips_empty_group(self):
        """A group with no items is not a "fully-delete" group — it
        would otherwise pass the ``all()`` predicate vacuously and
        produce a misleading banner entry."""
        g = _group(9, [])
        assert complete_delete_group_numbers([g]) == []

    def test_sorts_output(self):
        groups = [
            _group(5, [_rec("/a", "delete")]),
            _group(1, [_rec("/b", "delete")]),
            _group(3, [_rec("/c", "delete")]),
        ]
        assert complete_delete_group_numbers(groups) == [1, 3, 5]


# ── execute_mode menu action wiring ───────────────────────────────────────


class TestExecuteModeMenuAction:
    """The View → Execute Mode QAction is checkable, carries Ctrl+E,
    and is registered in MANIFEST_ACTIONS so the manifest-gating
    pathway flips it together with Save / Execute Action / Remove."""

    def test_listed_in_manifest_actions(self):
        from app.views.components.menu_controller import MANIFEST_ACTIONS

        assert "execute_mode" in MANIFEST_ACTIONS

    def test_setup_menus_registers_checkable_action_with_shortcut(self, qapp):
        from PySide6.QtGui import QKeySequence
        from PySide6.QtWidgets import QMainWindow

        from app.views.components.menu_controller import MenuController

        win = QMainWindow()
        mc = MenuController(win)
        mc.setup_menus()
        act = mc.actions.get("execute_mode")
        assert act is not None, "View → Execute Mode action not registered"
        assert act.isCheckable() is True
        # The shortcut must be Ctrl+E — keyboard parity with the issue
        # body's open-questions list.
        assert act.shortcut() == QKeySequence("Ctrl+E")
        # Disabled until a manifest loads. Re-enabled by
        # set_manifest_actions(True) in the load path.
        assert act.isEnabled() is False

    def test_set_manifest_actions_true_enables_execute_mode(self, qapp):
        from PySide6.QtWidgets import QMainWindow

        from app.views.components.menu_controller import MenuController

        win = QMainWindow()
        mc = MenuController(win)
        mc.setup_menus()
        mc.set_manifest_actions(True)
        assert mc.actions["execute_mode"].isEnabled() is True
