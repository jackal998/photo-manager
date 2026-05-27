"""SingletonPruneConfirmDialog — offer to prune orphaned single-item
groups after a destructive operation (#426).

Surfaced once at the tail of any destructive op (Execute Action delete,
Remove from List) that left at least one group with exactly one item
remaining. A near-duplicate group whose peers were dropped is no
longer a duplicate-review item, so the user is offered the option to
clear those distractions in one batch.

Three states encode the user's standing preference in JsonSettings under
``ui.prune_singletons``:

  * ``"ask"`` (default) — fire this dialog whenever singletons appear.
  * ``"always"`` — silently prune; never show the dialog again.
  * ``"never"`` — silently keep; never show the dialog again.

The "Remember my choice" checkbox flips ``"ask"`` to either
``"always"`` (when the user clicks Remove) or ``"never"`` (when the
user clicks Keep all). Leaving the checkbox unchecked keeps the
setting at ``"ask"`` — the next destructive op will prompt again.

The dialog is **batched** — one modal per destructive op covering ALL
singletons it produced, not one per group. The caller passes the
total count; the actual prune is the caller's responsibility (so the
caller controls the single ``vm.remove_from_list`` + single
``repo.remove_from_review`` DB write, satisfying the issue's
perf-aware acceptance criterion).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from infrastructure.i18n import t


class SingletonPruneConfirmDialog(QDialog):
    """Two-button confirm with "don't ask again" checkbox."""

    REMOVE = 1
    KEEP = 2

    def __init__(self, parent=None, *, count: int) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("singleton_prune_confirm.title"))
        self.setModal(True)
        self._count = count
        self._verdict = self.KEEP
        self._remember = False

        layout = QVBoxLayout(self)
        label = QLabel(t("singleton_prune_confirm.body", count=count), self)
        label.setWordWrap(True)
        layout.addWidget(label)

        self._checkbox = QCheckBox(
            t("singleton_prune_confirm.checkbox_dont_ask"), self
        )
        layout.addWidget(self._checkbox)

        button_box = QDialogButtonBox(self)
        self._remove_btn = QPushButton(
            t("singleton_prune_confirm.btn_remove", count=count), self
        )
        self._keep_btn = QPushButton(t("singleton_prune_confirm.btn_keep"), self)
        button_box.addButton(self._remove_btn, QDialogButtonBox.AcceptRole)
        button_box.addButton(self._keep_btn, QDialogButtonBox.RejectRole)
        layout.addWidget(button_box)

        self._remove_btn.clicked.connect(self._on_remove)
        self._keep_btn.clicked.connect(self._on_keep)
        # Esc / window close → KEEP (the safe default, matching #182's
        # CANCEL-on-close pattern). User must explicitly click Remove.
        self.rejected.connect(self._on_keep)

        self._keep_btn.setDefault(True)

    def _on_remove(self) -> None:
        self._verdict = self.REMOVE
        self._remember = self._checkbox.isChecked()
        self.accept()

    def _on_keep(self) -> None:
        self._verdict = self.KEEP
        self._remember = self._checkbox.isChecked()
        # Only call done() if the dialog is still open (rejected signal
        # can fire after explicit reject as well).
        if self.result() == 0:
            self.done(QDialog.Rejected)

    @property
    def verdict(self) -> int:
        return self._verdict

    @property
    def remember(self) -> bool:
        return self._remember

    @classmethod
    def ask(cls, parent, *, count: int) -> tuple[int, bool]:
        """Convenience entry point — returns (verdict, remember_choice)."""
        dlg = cls(parent, count=count)
        dlg.exec()
        return dlg.verdict, dlg.remember
