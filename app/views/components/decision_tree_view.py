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
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import QTreeView

from app.views.constants import DECISION_ROLE
from app.views.theme import DAYLIGHT


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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Pre-build the two full-row band colours once (QColor parse per
        # paint would be wasteful on a tree with thousands of rows).
        self._group_band = QColor(DAYLIGHT["group_band"])
        self._delete_row_bg = QColor(DAYLIGHT["delete_row_bg"])

    def drawRow(self, painter, option, index) -> None:  # type: ignore[override]
        """Paint a full-row background band before the cells are drawn.

        QSS can't target group-header vs file rows in a QTreeView, so the
        two structural cues the UX audit asked for live here:

        * **Group-header rows** (no valid parent) get a warm band so the
          group reads as the scannable spine of the view.
        * **File rows marked for deletion** get a red wash so the single
          most consequential state is unmistakable (the audit's top gap).

        ``index`` is always the row's column-0 index, where the model
        stores :data:`DECISION_ROLE`. We fill *before* ``super().drawRow``
        so the cell text paints crisply on top (no translucent overlay
        dimming the text) and Qt's selection highlight still wins for a
        selected row — selection paints over our fill in ``super``.
        """
        if not index.parent().isValid():
            painter.fillRect(option.rect, self._group_band)
        elif index.data(DECISION_ROLE) == "delete":
            painter.fillRect(option.rect, self._delete_row_bg)
        super().drawRow(painter, option, index)

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
