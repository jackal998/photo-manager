"""Tests for SingletonPruneConfirmDialog — focused on the keep-all reentrance
crash (#544-followup) plus the verdict resolution the caller relies on.

The crash: clicking "Keep all" reentered unboundedly. The keep button has
``QDialogButtonBox.RejectRole`` (so the button box routes its click through
``reject() → rejected``), AND ``_on_keep`` was also wired to ``clicked`` AND
called ``self.done(QDialog.Rejected)``. Because ``QDialog.Rejected == 0``, the
guard ``if self.result() == 0`` could never tell "open" from "rejected", so
``done(Rejected)`` re-emitted ``rejected`` → ``_on_keep`` → ``done`` → … until
the C++ signal stack overflowed (segfault / 閃退). The fix: ``_on_keep`` is
reached only via ``rejected`` and merely records the verdict — it never calls
``done()`` again, and the redundant ``clicked`` wire is gone.
"""
from __future__ import annotations

from app.views.dialogs.singleton_prune_confirm_dialog import (
    PruneVerdict,
    SingletonPruneConfirmDialog,
)


def test_keep_all_click_does_not_reenter(qapp):
    """Regression: clicking 'Keep all' fires `rejected` exactly once (no
    reentrant done() loop) and resolves to a keep-all verdict. Before the fix
    this recursed 300+ deep → RecursionError / segfault."""
    dlg = SingletonPruneConfirmDialog(None, count_plain=1, count_actioned=0)
    rejected_emissions: list[int] = []
    dlg.rejected.connect(lambda: rejected_emissions.append(1))

    # Programmatic click fires the same cascade as a real click: the RejectRole
    # button → button box → dialog.reject() → done(Rejected) → emit rejected.
    dlg._keep_btn.click()

    assert len(rejected_emissions) == 1, (
        f"`rejected` emitted {len(rejected_emissions)}x — `_on_keep` re-called "
        f"done() and reentered (the keep-all crash). Expected exactly 1."
    )
    assert dlg.to_prune_verdict() == PruneVerdict.keep_all()


def test_keep_click_actually_closes_via_reject(qapp):
    """The keep button must route through the dialog's reject() so the dialog
    actually CLOSES (result == Rejected) with a keep-all verdict — guards the
    failure mode where dropping the wire leaves the button connected to
    nothing and the dialog never closes."""
    from PySide6.QtWidgets import QDialog

    dlg = SingletonPruneConfirmDialog(None, count_plain=1, count_actioned=0)
    dlg._keep_btn.click()
    assert dlg.result() == QDialog.Rejected
    assert dlg.to_prune_verdict() == PruneVerdict.keep_all()


def test_escape_window_close_resolves_to_keep_all(qapp):
    """Esc / window-close → reject() → `rejected` → keep-all (the safe default,
    #182 CANCEL-on-close pattern). Must also not reenter."""
    dlg = SingletonPruneConfirmDialog(None, count_plain=3, count_actioned=2)
    rejected_emissions: list[int] = []
    dlg.rejected.connect(lambda: rejected_emissions.append(1))
    dlg.reject()
    assert len(rejected_emissions) == 1
    v = dlg.to_prune_verdict()
    assert v.prune_plain is False and v.prune_actioned is False


def test_remove_click_resolves_to_prune_plain(qapp):
    """Guard the unaffected path: clicking Remove (plain-only, no actioned
    checkbox) prunes the plain bucket and leaves actioned untouched."""
    dlg = SingletonPruneConfirmDialog(None, count_plain=2, count_actioned=0)
    dlg._remove_btn.click()
    v = dlg.to_prune_verdict()
    assert v.prune_plain is True and v.prune_actioned is False


def test_remove_with_actioned_optin(qapp):
    """Mixed bucket: Remove with the actioned checkbox ticked prunes both."""
    dlg = SingletonPruneConfirmDialog(None, count_plain=1, count_actioned=1)
    assert dlg._actioned_checkbox is not None
    dlg._actioned_checkbox.setChecked(True)
    dlg._remove_btn.click()
    v = dlg.to_prune_verdict()
    assert v.prune_plain is True and v.prune_actioned is True
