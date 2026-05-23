"""Tests for app/views/dialogs/delete_regex_confirm_dialog.py.

D3 from #350 (Wave 10): confirm gate inserted before regex-driven bulk
delete. Mirrors the test layout of tests/test_locked_rows_confirm_dialog.py
— body text, button states, verdicts, ask() helper.
"""

from __future__ import annotations


def _make_dialog(qapp, **kwargs):
    from app.views.dialogs.delete_regex_confirm_dialog import (
        DeleteRegexConfirmDialog,
    )
    defaults = dict(matched=47, pattern_summary="File Name regex 'IMG'")
    defaults.update(kwargs)
    return DeleteRegexConfirmDialog(**defaults)


class TestBodyText:
    def test_body_includes_matched_count(self, qapp):
        dlg = _make_dialog(qapp, matched=47)
        body = dlg._body_text()
        assert "47" in body, (
            f"Body must include matched count for visual confirmation; "
            f"got {body!r}"
        )

    def test_body_includes_pattern_summary(self, qapp):
        dlg = _make_dialog(
            qapp, pattern_summary="Folder contains 'archive'"
        )
        body = dlg._body_text()
        assert "Folder contains 'archive'" in body, (
            f"Body must surface the pattern_summary so the user can "
            f"recognise what they're about to apply; got {body!r}"
        )

    def test_body_warns_about_irreversibility(self, qapp):
        """The body must convey that this is a destructive action —
        ambiguous wording was the original D3 audit complaint."""
        dlg = _make_dialog(qapp)
        body = dlg._body_text().lower()
        # English wording: "cannot be easily undone" / "recycle bin"
        # zh_TW wording: 難以輕易復原 / 資源回收筒
        assert any(
            marker in body
            for marker in ("undone", "recycle bin", "復原", "回收筒")
        ), (
            f"Body must convey irreversibility — got {body!r}"
        )


class TestButtonStates:
    def test_cancel_is_default(self, qapp):
        """Safe-default invariant: Enter must NOT fire delete. Cancel
        owns the default + auto-default flags so a misfired Enter
        cancels rather than destroys."""
        dlg = _make_dialog(qapp)
        assert dlg._btn_cancel.isDefault(), (
            "Cancel must be the default button — Enter is the most "
            "common accidental keypress and must land on the safe path."
        )
        assert dlg._btn_cancel.autoDefault(), (
            "Cancel must also be auto-default to win the focus on dialog show."
        )

    def test_confirm_button_label_echoes_count(self, qapp):
        """Final visual confirmation: 'Delete 47 files' — the count
        re-appears in the primary action label, not just the body."""
        dlg = _make_dialog(qapp, matched=47)
        assert "47" in dlg._btn_confirm.text(), (
            f"Confirm button must echo the matched count for one last "
            f"double-check; got label {dlg._btn_confirm.text()!r}"
        )


class TestVerdicts:
    def test_default_verdict_is_cancelled(self, qapp):
        """Esc / window close land on CANCELLED — matches the explicit
        Cancel button. The default before any user interaction must be
        the safe path so a dialog dismissed by Esc doesn't accidentally
        confirm."""
        from app.views.dialogs.delete_regex_confirm_dialog import (
            DeleteRegexConfirmDialog,
        )
        dlg = _make_dialog(qapp)
        assert dlg.verdict == DeleteRegexConfirmDialog.CANCELLED

    def test_confirm_click_sets_confirmed(self, qapp):
        """The confirm path must set the verdict; the parent ActionDialog
        checks this via the ask() classmethod."""
        from app.views.dialogs.delete_regex_confirm_dialog import (
            DeleteRegexConfirmDialog,
        )
        dlg = _make_dialog(qapp)
        dlg._on_confirm()
        assert dlg.verdict == DeleteRegexConfirmDialog.CONFIRMED

    def test_reject_keeps_cancelled(self, qapp):
        """reject() is called by Esc / Cancel click / window close —
        all must leave verdict as CANCELLED (no path through reject
        should accidentally set CONFIRMED)."""
        from app.views.dialogs.delete_regex_confirm_dialog import (
            DeleteRegexConfirmDialog,
        )
        dlg = _make_dialog(qapp)
        dlg.reject()
        assert dlg.verdict == DeleteRegexConfirmDialog.CANCELLED


class TestAskHelper:
    def test_ask_returns_true_on_confirm(self, qapp, monkeypatch):
        """ask() is the classmethod wrapper trigger sites use. It must
        return True iff the user confirmed (CONFIRMED verdict). Tests
        bypass the modal exec by monkey-patching `_on_confirm` to fire
        synchronously."""
        from app.views.dialogs.delete_regex_confirm_dialog import (
            DeleteRegexConfirmDialog,
        )

        def fake_exec(self):
            self._verdict = self.CONFIRMED
            return 1

        monkeypatch.setattr(DeleteRegexConfirmDialog, "exec", fake_exec)
        result = DeleteRegexConfirmDialog.ask(
            None, matched=5, pattern_summary="File Name regex 'foo'"
        )
        assert result is True

    def test_ask_returns_false_on_cancel(self, qapp, monkeypatch):
        from app.views.dialogs.delete_regex_confirm_dialog import (
            DeleteRegexConfirmDialog,
        )

        def fake_exec(self):
            # CANCELLED is the default; do nothing — simulates Esc /
            # window close / Cancel click without explicitly setting
            # the verdict.
            return 0

        monkeypatch.setattr(DeleteRegexConfirmDialog, "exec", fake_exec)
        result = DeleteRegexConfirmDialog.ask(
            None, matched=5, pattern_summary="File Name regex 'foo'"
        )
        assert result is False
