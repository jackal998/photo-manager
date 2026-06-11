"""QTreeView subclass that catches 'd' / 'k' presses via keyPressEvent (#615).

QShortcut(QKeySequence("K"), tree, WidgetWithChildrenShortcut) silently
fails to match the K key event inside the photo-manager runtime — Qt's
ShortcutMap does not dispatch the QShortcutEvent even though D on the
same pattern works and a minimal QTreeView+QShortcut reproduction in
isolation works for both keys. Root cause is still open (#626 follow-up).

Overriding keyPressEvent bypasses the ShortcutMap entirely: Qt delivers
the QKeyEvent to the focused widget, the widget's own handler runs, and
we emit the signal. Deterministic, no shortcut-framework state to
debug. Industry pattern (Lightroom / Capture One / digiKam all use the
keyPressEvent path for the same reason).

Tradeoff vs the original QShortcut design is unchanged: the bare letters
'd' / 'k' replace QTreeView's default first-letter type-ahead navigation
for those two keys. All other letters still fall through to
``super().keyPressEvent`` and reach ``keyboardSearch``.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QTreeView


class DecisionTreeView(QTreeView):
    """Tree view that emits ``decisionRequested`` on bare 'd' / 'k'
    presses and ``playPauseRequested`` on bare 'p' presses.

    Signal contracts:

    - ``decisionRequested(str)`` carries the decision value to apply —
      ``"delete"`` for D, ``""`` (canonical no-decision / keep per
      #584) for K. The receiver is responsible for resolving against
      the current selection, lock state, and SQLite write — see
      :class:`app.views.handlers.file_operations.FileOperationsHandler.set_decision_to_highlighted`.
    - ``playPauseRequested()`` fires on P. PR #624 killed video
      autoplay; this shortcut is how the user toggles playback from
      the focused tree row without reaching for the mouse. The
      receiver (PreviewPane.toggle_play_pause) decides what's
      currently focused — no payload needed since the active video
      player is the PreviewPane's responsibility, not the tree's.

    Modifier-bearing presses (Ctrl+D, Shift+K, Alt+P, ...) fall
    through to ``super().keyPressEvent`` so they don't accidentally
    fire the shortcut path; this also leaves Ctrl+Shift+arrow
    selection-extension and any future Ctrl+letter shortcuts
    unaffected.
    """

    decisionRequested = Signal(str)
    playPauseRequested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.modifiers() == Qt.NoModifier:
            if event.key() == Qt.Key_D:
                self.decisionRequested.emit("delete")
                event.accept()
                return
            if event.key() == Qt.Key_K:
                self.decisionRequested.emit("")
                event.accept()
                return
            if event.key() == Qt.Key_P:
                self.playPauseRequested.emit()
                event.accept()
                return
        super().keyPressEvent(event)
