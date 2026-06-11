"""Layer-1 tests for DecisionTreeView keyPressEvent dispatch (#615).

These tests catch the real failure mode that bit PR #625: a QShortcut(K)
wired the same way as QShortcut(D) was silently NOT dispatched by Qt's
ShortcutMap in this app's runtime. The original unit tests used
``.activated.emit()`` directly which bypasses Qt's event dispatch path
entirely, so the bug only surfaced at layer 3 (qa-batch / s26).

The fix here exercises Qt's real key-event path via ``QTest.keyClick``,
so any future regression in the keyPressEvent override is caught at
layer 1 before it reaches the GUI.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtTest import QTest

from app.views.components.decision_tree_view import DecisionTreeView


@pytest.fixture
def tree(qapp):
    """A DecisionTreeView with a small model + focus seeded on row 0."""
    view = DecisionTreeView()
    model = QStandardItemModel()
    model.appendRow([QStandardItem("row 0")])
    model.appendRow([QStandardItem("row 1")])
    view.setModel(model)
    view.show()
    view.setFocus()
    view.setCurrentIndex(model.index(0, 0))
    qapp.processEvents()
    yield view
    view.close()
    view.deleteLater()


class TestDecisionTreeViewKeyDispatch:
    """The four contracts that, if broken, ship a silently-wrong shortcut."""

    def test_d_emits_decision_delete(self, tree):
        """Bare 'd' must emit decisionRequested('delete').

        Catches: keyPressEvent dropped, lambda wired to the wrong string,
        Qt event dispatch bypass.
        """
        seen: list[str] = []
        tree.decisionRequested.connect(seen.append)
        QTest.keyClick(tree, Qt.Key_D)
        assert seen == ["delete"]

    def test_k_emits_decision_empty(self, tree):
        """Bare 'k' must emit decisionRequested('') — '' is the canonical
        no-decision / keep state per #584.

        Catches: the specific PR #625 regression (K silently swallowed).
        """
        seen: list[str] = []
        tree.decisionRequested.connect(seen.append)
        QTest.keyClick(tree, Qt.Key_K)
        assert seen == [""]

    def test_modifier_press_does_not_fire(self, tree):
        """Ctrl+D / Shift+K / Alt+K must NOT fire decisionRequested.

        Catches: a bare-key check that forgets to compare modifiers and
        accidentally consumes Ctrl+D (which the user might bind elsewhere)
        or Shift+K (which should fall through to type-ahead with shift).
        """
        seen: list[str] = []
        tree.decisionRequested.connect(seen.append)
        QTest.keyClick(tree, Qt.Key_D, Qt.ControlModifier)
        QTest.keyClick(tree, Qt.Key_K, Qt.ShiftModifier)
        QTest.keyClick(tree, Qt.Key_K, Qt.AltModifier)
        assert seen == []

    def test_other_letters_fall_through(self, tree):
        """A non-D/K/P letter must not emit, and must reach
        super().keyPressEvent (so the default QTreeView type-ahead
        search still works for the rest of the alphabet).

        Catches: an override that accidentally consumes every
        printable key and breaks QAbstractItemView's keyboardSearch.
        """
        seen: list[str] = []
        tree.decisionRequested.connect(seen.append)
        # Send letters we know we don't handle.
        QTest.keyClick(tree, Qt.Key_J)
        QTest.keyClick(tree, Qt.Key_X)
        assert seen == []

    def test_p_emits_play_pause_request(self, tree):
        """Bare 'p' must emit playPauseRequested with no payload.

        Catches: a wiring regression where P is added to the override
        but the signal definition is missed, or the emit goes to the
        wrong signal.
        """
        fired: list[bool] = []
        tree.playPauseRequested.connect(lambda: fired.append(True))
        QTest.keyClick(tree, Qt.Key_P)
        assert fired == [True]

    def test_p_with_modifier_does_not_fire(self, tree):
        """Ctrl+P / Shift+P / Alt+P must NOT fire playPauseRequested.

        Catches: a bare-key check that forgets to compare modifiers
        and accidentally consumes Ctrl+P (some users may bind it to
        Print) or other modifier combos.
        """
        fired: list[bool] = []
        tree.playPauseRequested.connect(lambda: fired.append(True))
        QTest.keyClick(tree, Qt.Key_P, Qt.ControlModifier)
        QTest.keyClick(tree, Qt.Key_P, Qt.ShiftModifier)
        QTest.keyClick(tree, Qt.Key_P, Qt.AltModifier)
        assert fired == []
