"""Tests for LockedRowsConfirmDialog — the unified confirm-or-cancel
dialog that fires whenever an action would touch locked rows (#182).

Pin:
  - body text shape (count, first-5-basenames, "…and N more" suffix)
  - "Apply to Unlocked Only" disabled in the all-locked degenerate case
  - each button yields the right verdict
  - Esc / reject yields CANCEL
"""

from __future__ import annotations

import pytest


@pytest.fixture
def make_dialog(qapp):
    from app.views.dialogs.locked_rows_confirm_dialog import (
        LockedRowsConfirmDialog,
    )

    def _make(*, action_label: str = "delete", affected: int = 3, locked: list[str] | None = None):
        return LockedRowsConfirmDialog(
            None,
            action_label=action_label,
            affected_count=affected,
            locked_paths=list(locked or []),
        )

    return _make


class TestBodyText:
    def test_mixed_locked_unlocked_body_contains_counts(self, make_dialog):
        dlg = make_dialog(action_label="delete", affected=3, locked=["/p/IMG_1.jpg"])
        text = dlg._body_text()
        # The body must surface the three counts: total, locked, unlocked.
        # We assert on the digits rather than the surrounding prose so an
        # i18n string tweak doesn't break the test.
        assert "3" in text
        assert "1" in text  # locked count
        assert "2" in text  # unlocked count = 3 - 1
        assert "delete" in text
        assert "IMG_1.jpg" in text

    def test_all_locked_uses_dedicated_body(self, make_dialog):
        dlg = make_dialog(
            action_label="delete",
            affected=2,
            locked=["/p/IMG_1.jpg", "/p/IMG_2.jpg"],
        )
        text = dlg._body_text()
        assert "IMG_1.jpg" in text
        assert "IMG_2.jpg" in text
        # The dedicated all-locked phrasing doesn't reference the
        # "unlocked only" choice (no such option in this case).
        # Heuristic: the mixed body interpolates `unlocked={n}`; the
        # all-locked body doesn't. We assert positively via the
        # `body_all_locked` translation, not negatively, to avoid
        # being fooled by overlap.
        from infrastructure.i18n import t
        # Render the all-locked string with a unique sentinel and
        # check the dialog's body matches its general shape.
        expected = t(
            "locked_confirm.body_all_locked",
            action="delete",
            locked=2,
            list="",  # we test list lines separately
        )
        # The dialog's text minus the list-block lines should share
        # the all-locked template's framing words.
        first_line = expected.splitlines()[0]
        assert first_line in text

    def test_more_than_five_locked_truncates_with_suffix(self, make_dialog):
        locked = [f"/p/IMG_{i:02d}.jpg" for i in range(7)]
        dlg = make_dialog(action_label="delete", affected=7, locked=locked)
        text = dlg._body_text()
        # First 5 basenames visible
        for i in range(5):
            assert f"IMG_{i:02d}.jpg" in text
        # Remaining 2 are NOT enumerated, but the "and N more" suffix
        # must appear with N=2.
        assert "IMG_05.jpg" not in text
        assert "IMG_06.jpg" not in text
        assert "2" in text  # the "and 2 more" count

    def test_exactly_five_locked_shows_all_no_suffix(self, make_dialog):
        locked = [f"/p/IMG_{i:02d}.jpg" for i in range(5)]
        dlg = make_dialog(action_label="delete", affected=5, locked=locked)
        text = dlg._body_text()
        for i in range(5):
            assert f"IMG_{i:02d}.jpg" in text
        # Suffix wording from the translation — only appears when truncated
        from infrastructure.i18n import t
        suffix_template = t("locked_confirm.list_truncated_suffix", n=999)
        # Replace the numeric placeholder for a substring check on the
        # surrounding prose (e.g. "…and N more" without the N).
        suffix_prose = suffix_template.replace("999", "").strip()
        # Some prose is short enough to overlap with the body legitimately;
        # the safer check is that no integer-truncation suffix renders.
        # Simpler: assert that "…and" isn't present.
        assert "…and" not in text and "以及其餘" not in text


class TestBodyTextZhTW:
    """Locale-switch tests for the dialog body. The i18n parity test in
    test_i18n.py guarantees every English key exists in zh_TW, but it
    doesn't verify that the zh_TW strings actually render through
    LockedRowsConfirmDialog's body composition with format-placeholder
    substitution intact. These tests close that gap so a malformed YAML
    placeholder (`{action」` instead of `{action}`) or a missing
    `body_all_locked` variant trips CI rather than a user.
    """

    def _swap_locale(self, code: str):
        """Replace the process-global translator with one in ``code``.

        Returns a callable that restores the original locale — use in a
        try/finally so subsequent tests see English again.
        """
        from pathlib import Path
        from infrastructure import i18n
        translations_dir = Path(__file__).resolve().parent.parent / "translations"
        prior = getattr(i18n, "_translator", None)
        i18n.init_translator(code, translations_dir)

        def _restore() -> None:
            i18n._translator = prior

        return _restore

    def test_mixed_body_renders_in_zh_TW(self, qapp):
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        restore = self._swap_locale("zh_TW")
        try:
            dlg = LockedRowsConfirmDialog(
                None,
                action_label="刪除",
                affected_count=3,
                locked_paths=["/p/IMG_1.jpg"],
            )
            text = dlg._body_text()
            # Format-placeholder substitution worked: counts appear as
            # digits in the rendered string.
            assert "3" in text
            assert "1" in text  # locked count
            assert "2" in text  # unlocked count
            assert "IMG_1.jpg" in text
            # At least one zh_TW marker rules out a fallback-to-English
            # regression (which would render no Chinese at all).
            assert any(marker in text for marker in ("鎖定", "取消", "套用"))
        finally:
            restore()

    def test_all_locked_body_renders_in_zh_TW(self, qapp):
        """All-locked degenerate variant uses a different YAML key
        (``body_all_locked``); pin it separately so a missing zh_TW
        entry fails this test rather than silently falling back to
        English on the user."""
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        restore = self._swap_locale("zh_TW")
        try:
            dlg = LockedRowsConfirmDialog(
                None,
                action_label="刪除",
                affected_count=2,
                locked_paths=["/p/IMG_1.jpg", "/p/IMG_2.jpg"],
            )
            text = dlg._body_text()
            assert "IMG_1.jpg" in text
            assert "IMG_2.jpg" in text
            assert any(marker in text for marker in ("鎖定", "取消", "套用"))
        finally:
            restore()

    def test_button_labels_render_in_zh_TW(self, qapp):
        """Button labels come from a separate translation key path
        (``locked_confirm.btn_*``). Pin the zh_TW rendering so a
        missing key fails CI rather than the user."""
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        restore = self._swap_locale("zh_TW")
        try:
            dlg = LockedRowsConfirmDialog(
                None,
                action_label="刪除",
                affected_count=3,
                locked_paths=["/p/IMG_1.jpg"],
            )
            for btn in (dlg._btn_unlock_apply, dlg._btn_unlocked_only, dlg._btn_cancel):
                label = btn.text()
                assert label, "button has no text"
                # Any CJK Unified Ideograph rules out an English fallback.
                assert any("一" <= c <= "鿿" for c in label), (
                    f"button label {label!r} has no CJK char — likely "
                    f"fell back to English"
                )
        finally:
            restore()


class TestContextWordingOverride:
    """#417 — the dialog accepts caller-driven body + apply-button keys so
    each trigger context (IMMEDIATE delete-now vs DEFERRED queue-a-decision)
    can supply wording matched to its consequence. Omitting the overrides
    must keep the generic shared phrasing as a fallback."""

    def test_default_fallback_uses_shared_keys(self, make_dialog):
        from infrastructure.i18n import t
        dlg = make_dialog(action_label="delete", affected=3, locked=["/p/a.jpg"])
        # No overrides → generic body + generic apply label.
        assert dlg._btn_unlock_apply.text() == t("locked_confirm.btn_unlock_apply")
        # The default mixed body contains the generic "would affect" framing.
        assert t("locked_confirm.body").split("{")[0].strip()[:6] in dlg._body_text()

    def test_immediate_override_renders_delete_now_wording(self, qapp):
        from infrastructure.i18n import t
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        dlg = LockedRowsConfirmDialog(
            None,
            action_label="delete",
            affected_count=3,
            locked_paths=["/p/a.jpg"],
            body_key="locked_confirm.body_immediate",
            body_all_locked_key="locked_confirm.body_all_locked_immediate",
            btn_apply_label=t("locked_confirm.btn_unlock_apply_immediate"),
        )
        # Apply button carries the delete-now label, distinct from default.
        assert dlg._btn_unlock_apply.text() == t(
            "locked_confirm.btn_unlock_apply_immediate"
        )
        assert dlg._btn_unlock_apply.text() != t("locked_confirm.btn_unlock_apply")
        # Body renders the immediate template (counts substituted, DELETE cue).
        text = dlg._body_text()
        assert "3" in text
        assert "DELETE" in text

    def test_deferred_override_renders_queue_decision_wording(self, qapp):
        from infrastructure.i18n import t
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        dlg = LockedRowsConfirmDialog(
            None,
            action_label="delete",
            affected_count=3,
            locked_paths=["/p/a.jpg"],
            body_key="locked_confirm.body_deferred",
            body_all_locked_key="locked_confirm.body_all_locked_deferred",
            btn_apply_label=t("locked_confirm.btn_unlock_apply_deferred"),
        )
        assert dlg._btn_unlock_apply.text() == t(
            "locked_confirm.btn_unlock_apply_deferred"
        )
        text = dlg._body_text()
        assert "3" in text
        # The deferred body must make the "nothing deleted yet" promise.
        assert "Nothing is deleted yet" in text

    def test_all_locked_override_uses_all_locked_key(self, qapp):
        """The all-locked degenerate body honours the override key too —
        otherwise the immediate/deferred split would leak the generic
        wording when every matched row is locked."""
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        dlg = LockedRowsConfirmDialog(
            None,
            action_label="delete",
            affected_count=2,
            locked_paths=["/p/a.jpg", "/p/b.jpg"],
            body_key="locked_confirm.body_immediate",
            body_all_locked_key="locked_confirm.body_all_locked_immediate",
            btn_apply_label="Unlock && Delete All",
        )
        text = dlg._body_text()
        assert "DELETE" in text
        # The all-locked immediate body interpolates {locked}=2 not {total}.
        assert "2" in text


class TestButtonStates:
    def test_unlocked_only_button_enabled_when_some_unlocked(self, make_dialog):
        dlg = make_dialog(affected=3, locked=["/p/a.jpg"])  # 2 unlocked
        assert dlg._btn_unlocked_only.isEnabled() is True

    def test_unlocked_only_button_disabled_when_all_locked(self, make_dialog):
        dlg = make_dialog(affected=2, locked=["/p/a.jpg", "/p/b.jpg"])
        assert dlg._btn_unlocked_only.isEnabled() is False

    def test_three_buttons_present(self, make_dialog):
        dlg = make_dialog()
        # Two action buttons plus Cancel. We don't assert on the
        # platform-specific ordering — just that all three live on
        # the button box.
        buttons = dlg._btn_box.buttons()
        assert dlg._btn_unlock_apply in buttons
        assert dlg._btn_unlocked_only in buttons
        assert dlg._btn_cancel in buttons


class TestVerdicts:
    def test_unlock_apply_click_yields_apply_all_unlocked(self, make_dialog):
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        dlg = make_dialog()
        dlg._btn_unlock_apply.click()
        assert dlg.verdict == LockedRowsConfirmDialog.APPLY_ALL_UNLOCKED

    def test_unlocked_only_click_yields_apply_unlocked_only(self, make_dialog):
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        dlg = make_dialog()
        dlg._btn_unlocked_only.click()
        assert dlg.verdict == LockedRowsConfirmDialog.APPLY_UNLOCKED_ONLY

    def test_cancel_click_yields_cancel(self, make_dialog):
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        dlg = make_dialog()
        dlg._btn_cancel.click()
        assert dlg.verdict == LockedRowsConfirmDialog.CANCEL

    def test_reject_yields_cancel(self, make_dialog):
        """Esc / window close routes through reject() → CANCEL.

        The user-friendly affordance: any dismissal that isn't an
        explicit destructive choice falls back to CANCEL. Test the
        contract directly by calling reject().
        """
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        dlg = make_dialog()
        dlg.reject()
        assert dlg.verdict == LockedRowsConfirmDialog.CANCEL

    def test_initial_verdict_is_cancel(self, make_dialog):
        """If the dialog is constructed but never shown / never clicked,
        the verdict must default to CANCEL — no surprises for callers
        that defensively check verdict before/after a programmatic open."""
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )
        dlg = make_dialog()
        assert dlg.verdict == LockedRowsConfirmDialog.CANCEL


class TestAskHelper:
    def test_ask_returns_verdict_from_button_click(self, qapp, monkeypatch):
        from app.views.dialogs.locked_rows_confirm_dialog import (
            LockedRowsConfirmDialog,
        )

        captured: list[LockedRowsConfirmDialog] = []

        # Intercept exec() to simulate a user clicking Unlock & Apply
        # without entering the Qt event loop. We tag the dialog before
        # exec() returns so the captured.verdict assertion below reads
        # the simulated post-click state.
        def fake_exec(self):
            self._btn_unlock_apply.click()
            captured.append(self)
            return 1  # QDialog.Accepted

        monkeypatch.setattr(LockedRowsConfirmDialog, "exec", fake_exec)

        verdict = LockedRowsConfirmDialog.ask(
            None,
            action_label="delete",
            affected_count=2,
            locked_paths=["/p/a.jpg"],
        )
        assert verdict == LockedRowsConfirmDialog.APPLY_ALL_UNLOCKED
        assert captured  # the dialog was actually instantiated
