"""DeleteRegexConfirmDialog — confirm gate before a regex-driven bulk delete.

D3 from #350 (Wave 10): clicking Apply with the "delete" action via the
``ActionDialog`` previously emitted ``setActionRequested`` immediately, with no
second pause. The match counter + the post-Apply B9 flash gave feedback, but
the destruction itself was a single click — easy to misfire on a large batch.

This dialog inserts a confirm step ONLY for the "delete" action — every other
choice (keep, remove from list, lock, unlock) emits immediately, unchanged.
The Cancel button is the default focus + Esc target so a misfired Enter or
window-close gesture lands on the safe path. The confirm button echoes the
matched count back to the user ("Delete 47 files") for one last visual
double-check before the irreversible op.

Trigger sites: ``ActionDialog._emit_set_action`` when the chosen action is
"delete" and a live preview count is available (``match_fn`` supplied,
``_last_matched_count`` populated). The flat-layout branch (no preview, no
count) skips the confirm — there's no count to confirm against and the
downstream receiver still emits its "Decision set to ..." status-bar message.

Pattern mirrors ``LockedRowsConfirmDialog`` for consistency: classmethod
``ask()`` wrapper, ``_verdict`` + property accessor, ``QDialogButtonBox``
with explicit roles, Cancel as default focus.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from infrastructure.i18n import t


class DeleteRegexConfirmDialog(QDialog):
    """Two-button confirm: Delete N files / Cancel."""

    CONFIRMED = 1
    CANCELLED = 2

    def __init__(
        self,
        parent=None,
        *,
        matched: int,
        pattern_summary: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("delete_regex_confirm.title"))
        # Esc + window close both land on CANCELLED (matches the explicit
        # Cancel button) — safe default for an irreversible op.
        self._verdict = self.CANCELLED
        self._matched = matched
        self._pattern_summary = pattern_summary
        self._build_ui()

    @property
    def verdict(self) -> int:
        return self._verdict

    @classmethod
    def ask(
        cls,
        parent,
        *,
        matched: int,
        pattern_summary: str,
    ) -> bool:
        """Show modally and return True iff the user confirmed the delete.

        Returns ``True`` on CONFIRMED, ``False`` on CANCELLED — callers that
        want the enum can read ``dlg.verdict`` directly, but this wrapper
        covers the common case of "did the user say yes?".
        """
        dlg = cls(parent, matched=matched, pattern_summary=pattern_summary)
        dlg.exec()
        return dlg.verdict == cls.CONFIRMED

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        label = QLabel(self._body_text())
        label.setWordWrap(True)
        # PlainText — pattern_summary may include user-typed regex with `<`
        # characters; we don't want them parsed as HTML markup.
        label.setTextFormat(Qt.PlainText)
        layout.addWidget(label)

        self._btn_box = QDialogButtonBox()
        # Confirm button label echoes the count for one last visual
        # double-check ("Delete 47 files") before the irreversible op.
        self._btn_confirm = QPushButton(
            t("delete_regex_confirm.confirm_button", matched=self._matched)
        )
        self._btn_cancel = QPushButton(t("delete_regex_confirm.cancel"))

        # AcceptRole + RejectRole so Enter and Esc map to the right button
        # via Qt's default key-handling. ActionRole would also work but
        # Accept/Reject is the strict-correct mapping for confirm/cancel.
        self._btn_box.addButton(self._btn_confirm, QDialogButtonBox.AcceptRole)
        self._btn_box.addButton(self._btn_cancel, QDialogButtonBox.RejectRole)

        self._btn_confirm.clicked.connect(self._on_confirm)
        self._btn_cancel.clicked.connect(self.reject)  # rejected → CANCELLED
        # Cancel is the default focus so a quick Enter does NOT fire the
        # destructive action — user must deliberately Tab to or click the
        # Delete button. Matches LockedRowsConfirmDialog's safe-default
        # pattern (Cancel takes the focus).
        self._btn_cancel.setDefault(True)
        self._btn_cancel.setAutoDefault(True)

        layout.addWidget(self._btn_box)

    def _body_text(self) -> str:
        return t(
            "delete_regex_confirm.body",
            matched=self._matched,
            pattern_summary=self._pattern_summary,
        )

    def _on_confirm(self) -> None:
        self._verdict = self.CONFIRMED
        self.accept()
