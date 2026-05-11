"""LockedRowsConfirmDialog — unified confirm for any action touching locked rows.

Replaces the silent skip / silent override / silent execute-filter
asymmetries that landed with #175. Under the new rule (#182):

    is_locked = freeze

Any path that would change a locked row's ``user_decision`` OR
delete it surfaces this dialog first. The dialog returns one of three
verdicts:

  * ``APPLY_ALL_UNLOCKED`` — caller unlocks the locked rows and
    applies the original action to the full affected set.
  * ``APPLY_UNLOCKED_ONLY`` — caller applies to the unlocked subset
    only; locked rows stay locked and untouched (the old
    silent-skip behavior, now an explicit user choice).
  * ``CANCEL`` — caller abandons the action.

The "Apply to Unlocked Only" button is disabled when every affected
row is locked (no unlocked subset to apply to). The "Cancel" button is
also wired to ``rejected`` so Escape / close-button yield ``CANCEL``.

Trigger sites: single-row right-click on a locked row, bulk regex
flows (main + execute dialog), bulk multi-select, and the
pre-execute scan that fires when locked rows have ``decision='delete'``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from infrastructure.i18n import t


_LOCK_GLYPH = "\U0001F512"  # 🔒 — same as tree_model_builder for visual parity
_MAX_VISIBLE_BASENAMES = 5


class LockedRowsConfirmDialog(QDialog):
    """Three-button confirm: Unlock & Apply All / Apply to Unlocked Only / Cancel."""

    APPLY_ALL_UNLOCKED = 1
    APPLY_UNLOCKED_ONLY = 2
    CANCEL = 3

    def __init__(
        self,
        parent=None,
        *,
        action_label: str,
        affected_count: int,
        locked_paths: list[str],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("locked_confirm.title"))
        # The user can dismiss with Esc or window close button — both
        # land on CANCEL, matching the explicit Cancel button.
        self._verdict = self.CANCEL
        self._action_label = action_label
        self._affected_count = affected_count
        self._locked_paths = list(locked_paths)
        self._build_ui()

    @property
    def verdict(self) -> int:
        return self._verdict

    @classmethod
    def ask(
        cls,
        parent,
        *,
        action_label: str,
        affected_count: int,
        locked_paths: list[str],
    ) -> int:
        """Show the dialog modally and return the chosen verdict.

        Convenience wrapper so trigger sites can do
        ``verdict = LockedRowsConfirmDialog.ask(self, action_label=..., ...)``
        without managing the dialog lifecycle themselves.
        """
        dlg = cls(
            parent,
            action_label=action_label,
            affected_count=affected_count,
            locked_paths=locked_paths,
        )
        dlg.exec()
        return dlg.verdict

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        label = QLabel(self._body_text())
        label.setWordWrap(True)
        # PlainText — the basename list is data, not markup; we don't
        # want a stray "<" in a filename to be parsed as HTML.
        label.setTextFormat(Qt.PlainText)
        layout.addWidget(label)

        # QDialogButtonBox so layout matches the platform convention for
        # destructive / non-destructive button ordering. Custom roles let
        # us read the click directly without binding to a generic
        # accepted / rejected signal that would obscure which of the
        # three options the user chose.
        self._btn_box = QDialogButtonBox()
        self._btn_unlock_apply = QPushButton(t("locked_confirm.btn_unlock_apply"))
        self._btn_unlocked_only = QPushButton(t("locked_confirm.btn_unlocked_only"))
        self._btn_cancel = QPushButton(t("locked_confirm.btn_cancel"))

        # AcceptRole for the "go" action so Enter triggers it; the
        # "skip locked" middle button uses ActionRole so it doesn't
        # carry default-button semantics; Cancel uses RejectRole so
        # Escape maps to it.
        self._btn_box.addButton(self._btn_unlock_apply, QDialogButtonBox.AcceptRole)
        self._btn_box.addButton(self._btn_unlocked_only, QDialogButtonBox.ActionRole)
        self._btn_box.addButton(self._btn_cancel, QDialogButtonBox.RejectRole)

        unlocked_count = self._affected_count - len(self._locked_paths)
        # All-locked degenerate case: the "Apply to Unlocked Only" path
        # has no rows to apply to. Disabling rather than hiding keeps
        # the dialog shape consistent across cases.
        self._btn_unlocked_only.setEnabled(unlocked_count > 0)

        self._btn_unlock_apply.clicked.connect(self._on_unlock_apply)
        self._btn_unlocked_only.clicked.connect(self._on_unlocked_only)
        self._btn_cancel.clicked.connect(self.reject)  # rejected → CANCEL
        # Default focus on Cancel so a quick Enter doesn't fire a
        # destructive action; the user must deliberately Tab to or
        # click the desired button.
        self._btn_cancel.setDefault(True)
        self._btn_cancel.setAutoDefault(True)

        layout.addWidget(self._btn_box)

    def _body_text(self) -> str:
        total = self._affected_count
        locked_count = len(self._locked_paths)
        unlocked_count = total - locked_count

        visible = [Path(p).name for p in self._locked_paths[:_MAX_VISIBLE_BASENAMES]]
        list_lines = [f"  {_LOCK_GLYPH} {name}" for name in visible]
        if locked_count > _MAX_VISIBLE_BASENAMES:
            list_lines.append(
                t(
                    "locked_confirm.list_truncated_suffix",
                    n=locked_count - _MAX_VISIBLE_BASENAMES,
                )
            )
        list_block = "\n".join(list_lines)

        if unlocked_count == 0:
            # All-locked degenerate case: dedicated phrasing so the
            # user isn't asked to "apply to unlocked only" when there
            # are no unlocked rows.
            return t(
                "locked_confirm.body_all_locked",
                action=self._action_label,
                locked=locked_count,
                list=list_block,
            )
        return t(
            "locked_confirm.body",
            action=self._action_label,
            total=total,
            locked=locked_count,
            unlocked=unlocked_count,
            list=list_block,
        )

    def _on_unlock_apply(self) -> None:
        self._verdict = self.APPLY_ALL_UNLOCKED
        self.accept()

    def _on_unlocked_only(self) -> None:
        self._verdict = self.APPLY_UNLOCKED_ONLY
        self.accept()
