"""SingletonPruneConfirmDialog — offer to prune orphaned single-item
groups after a destructive operation (#426 / Improvement 2 in #482-bundle).

Surfaced once at the tail of any destructive op (Execute Action delete,
Remove from List) that left at least one group with exactly one item
remaining. A near-duplicate group whose peers were dropped is no
longer a duplicate-review item, so the user is offered the option to
clear those distractions in one batch.

Two classifications of singleton, surfaced together in this one modal:

  * **Plain singletons** — the remaining item has no pending decision.
    The default "remove from list" affordance (button + Remember
    checkbox) targets this bucket.
  * **Actioned singletons** — the remaining item has a pending
    non-keep-able decision (``delete`` / ``remove_from_list``) that
    has NOT been executed yet. Commonly produced by the partial-
    execute flow (Improvement 1 in the same bundle): the user
    executes a subset of decisions, the executed peers vanish, the
    not-yet-executed singleton remains in its now-empty group. An
    opt-in checkbox (default **unchecked**) lets the user explicitly
    surface "this row has an action I never ran — clear it from the
    list too?" without the dialog silently sweeping it up.

Three states encode the user's standing preference in JsonSettings
under ``ui.prune_singletons``:

  * ``"ask"`` (default) — fire this dialog whenever singletons appear.
  * ``"always"`` — silently prune; never show the dialog again.
  * ``"never"`` — silently keep; never show the dialog again.

The "Remember my choice" checkbox flips ``"ask"`` to either
``"always"`` (when the user clicks Remove) or ``"never"`` (when the
user clicks Keep all). Leaving the checkbox unchecked keeps the
setting at ``"ask"`` — the next destructive op will prompt again.

The dialog is **batched** — one modal per destructive op covering ALL
singletons it produced, not one per group. The caller passes the
per-bucket counts; the actual prune is the caller's responsibility (so
the caller controls the per-bucket ``vm.remove_from_list`` +
``repo.remove_from_review`` DB writes, satisfying the original issue's
perf-aware acceptance criterion).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from infrastructure.i18n import t


@dataclass(frozen=True)
class PruneVerdict:
    """User's response to :class:`SingletonPruneConfirmDialog`.

    Fields:
        prune_plain: True if the user clicked **Remove** AND the plain
            bucket had at least one singleton. False on Keep-all and
            when the dialog was constructed with ``count_plain == 0``.
        prune_actioned: True if the user clicked **Remove** AND opted
            into the actioned bucket. When the dialog renders both
            buckets, opt-in is the explicit checkbox (default
            unchecked). When the dialog renders only the actioned
            bucket, **Remove** itself is the opt-in.
        remember: True if the user checked "Remember my choice" —
            caller flips ``ui.prune_singletons`` to ``"always"`` (on
            Remove) or ``"never"`` (on Keep all).
    """

    prune_plain: bool
    prune_actioned: bool
    remember: bool

    @classmethod
    def keep_all(cls, remember: bool = False) -> "PruneVerdict":
        return cls(prune_plain=False, prune_actioned=False, remember=remember)


class SingletonPruneConfirmDialog(QDialog):
    """Two-button confirm with "don't ask again" checkbox and optional
    opt-in for the actioned-singleton bucket."""

    REMOVE = 1
    KEEP = 2

    def __init__(
        self,
        parent=None,
        *,
        count_plain: int,
        count_actioned: int = 0,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("singleton_prune_confirm.title"))
        self.setModal(True)
        self._count_plain = count_plain
        self._count_actioned = count_actioned
        self._verdict = self.KEEP
        self._remember = False

        layout = QVBoxLayout(self)

        # Body label adapts to which buckets are populated.
        if count_plain > 0 and count_actioned > 0:
            body_text = t(
                "singleton_prune_confirm.body_mixed",
                count_plain=count_plain,
                count_actioned=count_actioned,
            )
        elif count_actioned > 0:
            body_text = t(
                "singleton_prune_confirm.body_actioned_only",
                count_actioned=count_actioned,
            )
        else:
            body_text = t("singleton_prune_confirm.body", count=count_plain)
        label = QLabel(body_text, self)
        label.setWordWrap(True)
        layout.addWidget(label)

        # Actioned-bucket opt-in checkbox: only shown when BOTH buckets
        # exist (the only-actioned case lets Remove itself be the opt-in,
        # so no extra checkbox is needed). Default UNCHECKED so the
        # default Remove click matches the pre-bundle behaviour — sweep
        # plain singletons, leave actioned ones for the user to confirm
        # explicitly.
        self._actioned_checkbox: QCheckBox | None = None
        if count_plain > 0 and count_actioned > 0:
            self._actioned_checkbox = QCheckBox(
                t(
                    "singleton_prune_confirm.checkbox_include_actioned",
                    count=count_actioned,
                ),
                self,
            )
            self._actioned_checkbox.setChecked(False)
            layout.addWidget(self._actioned_checkbox)

        self._remember_checkbox = QCheckBox(
            t("singleton_prune_confirm.checkbox_dont_ask"), self
        )
        layout.addWidget(self._remember_checkbox)

        button_box = QDialogButtonBox(self)
        # Button label reflects the dominant bucket: when only-actioned,
        # the count IS the actioned count (since plain is zero).
        remove_count = count_plain if count_plain > 0 else count_actioned
        self._remove_btn = QPushButton(
            t("singleton_prune_confirm.btn_remove", count=remove_count), self
        )
        self._keep_btn = QPushButton(t("singleton_prune_confirm.btn_keep"), self)
        button_box.addButton(self._remove_btn, QDialogButtonBox.AcceptRole)
        button_box.addButton(self._keep_btn, QDialogButtonBox.RejectRole)
        layout.addWidget(button_box)

        self._remove_btn.clicked.connect(self._on_remove)
        # The keep button routes through the dialog's reject() — the safe
        # pattern locked_rows_confirm_dialog uses. reject() emits `rejected`,
        # which fires `_on_keep` to record the verdict; that single wire ALSO
        # covers Esc / window close (the safe default, #182's CANCEL-on-close
        # pattern). `_on_keep` must NOT call done()/reject() itself — doing so
        # re-emitted `rejected` → `_on_keep` → … unbounded reentrance → the
        # keep-all stack-overflow crash.
        self._keep_btn.clicked.connect(self.reject)
        self.rejected.connect(self._on_keep)

        self._keep_btn.setDefault(True)

    def _on_remove(self) -> None:
        self._verdict = self.REMOVE
        self._remember = self._remember_checkbox.isChecked()
        self.accept()

    def _on_keep(self) -> None:
        # Invoked ONLY from the `rejected` signal (keep button RejectRole /
        # Esc / window close), so the dialog is already being rejected — just
        # record the verdict. Do NOT call done()/reject() again.
        #
        # The previous guard `if self.result() == 0: self.done(QDialog.Rejected)`
        # was broken and crashed the app: `QDialog.Rejected == 0`, so the guard
        # could never tell "still open" from "rejected", and re-calling
        # `done(Rejected)` re-emitted `rejected` → `_on_keep` → `done` → …
        # unbounded reentrance → C++ stack overflow / segfault when the user
        # clicked "Keep all" (#544-followup).
        self._verdict = self.KEEP
        self._remember = self._remember_checkbox.isChecked()

    @property
    def verdict(self) -> int:
        return self._verdict

    @property
    def remember(self) -> bool:
        return self._remember

    def to_prune_verdict(self) -> PruneVerdict:
        """Resolve the raw button verdict + opt-in state into the
        per-bucket :class:`PruneVerdict` the caller acts on.
        """
        if self._verdict != self.REMOVE:
            return PruneVerdict.keep_all(remember=self._remember)

        # Remove was clicked. Resolve per-bucket inclusion.
        prune_plain = self._count_plain > 0
        if self._count_plain > 0 and self._count_actioned > 0:
            # Mixed case: actioned bucket is opt-in via checkbox.
            prune_actioned = bool(
                self._actioned_checkbox is not None
                and self._actioned_checkbox.isChecked()
            )
        else:
            # Only-actioned case: Remove itself opts in.
            prune_actioned = self._count_actioned > 0

        return PruneVerdict(
            prune_plain=prune_plain,
            prune_actioned=prune_actioned,
            remember=self._remember,
        )

    @classmethod
    def ask(
        cls,
        parent,
        *,
        count_plain: int,
        count_actioned: int = 0,
    ) -> PruneVerdict:
        """Convenience entry point — returns a :class:`PruneVerdict`."""
        dlg = cls(parent, count_plain=count_plain, count_actioned=count_actioned)
        dlg.exec()
        return dlg.to_prune_verdict()
