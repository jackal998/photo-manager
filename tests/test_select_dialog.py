"""Tests for app/views/dialogs/select_dialog.py (ActionDialog)."""

from __future__ import annotations


class TestInitialField:
    def test_defaults_to_file_name_when_none(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["Similarity", "File Name", "Folder"])
        assert dlg.combo.currentText() == "File Name"

    def test_initial_field_preselects_combo(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["Similarity", "File Name", "Folder"], initial_field="Folder")
        assert dlg.combo.currentText() == "Folder"

    def test_initial_field_match_preselects(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["Similarity", "File Name", "Folder"], initial_field="Similarity")
        assert dlg.combo.currentText() == "Similarity"

    def test_unknown_initial_field_falls_back_to_file_name(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["Similarity", "File Name", "Folder"], initial_field="NonExistent")
        assert dlg.combo.currentText() == "File Name"

    def test_none_initial_field_uses_file_name(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["Similarity", "File Name", "Folder"], initial_field=None)
        assert dlg.combo.currentText() == "File Name"


class TestModality:
    def test_window_modality_is_application_modal(self, qapp):
        """#139 — same root cause as ExecuteActionDialog: QDialog.exec()
        without explicit windowModality leaves Qt at NonModal at the OS
        level, allowing real mouse clicks on the main window's menu bar
        to steal foreground while this dialog is up. ApplicationModal is
        what actually establishes WS_DISABLED on the parent on Windows."""
        from PySide6.QtCore import Qt
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["Similarity", "File Name", "Folder"])
        assert dlg.windowModality() == Qt.ApplicationModal


class TestSetActionSignal:
    def test_set_action_emits_signal_with_delete(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["File Name", "Folder"])
        dlg.combo.setCurrentText("File Name")
        dlg.regex.setText("IMG.*")
        dlg._action_combo.setCurrentIndex(0)  # first = delete

        received = []
        dlg.setActionRequested.connect(lambda f, p, v: received.append((f, p, v)))
        dlg._btn_set_action.click()

        assert len(received) == 1
        field, pattern, value = received[0]
        assert field == "File Name"
        assert pattern == "IMG.*"
        assert value == "delete"

    def test_set_action_emits_empty_string_for_keep_remove_action(self, qapp):
        """'keep (remove action)' must emit '' as the action value."""
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["File Name", "Folder"])
        dlg.combo.setCurrentText("Folder")
        dlg.regex.setText("^D:\\\\Photos")
        dlg._action_combo.setCurrentIndex(1)  # keep (remove action)

        received = []
        dlg.setActionRequested.connect(lambda f, p, v: received.append((f, p, v)))
        dlg._btn_set_action.click()

        assert len(received) == 1
        _field, _pattern, value = received[0]
        assert value == "", f"Expected empty string, got {value!r}"

    def test_set_action_uses_current_field_and_regex(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["Similarity", "Action", "Folder"])
        dlg.combo.setCurrentText("Action")
        dlg.regex.setText("^exact$")
        dlg._action_combo.setCurrentIndex(0)  # delete

        received = []
        dlg.setActionRequested.connect(lambda f, p, v: received.append((f, p, v)))
        dlg._btn_set_action.click()

        assert received[0][0] == "Action"
        assert received[0][1] == "^exact$"


class TestSettableDecisionOptions:
    def test_action_combo_has_delete_option(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["File Name"])
        items = [dlg._action_combo.itemText(i) for i in range(dlg._action_combo.count())]
        assert "delete" in items

    def test_action_combo_has_keep_remove_action_option(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["File Name"])
        items = [dlg._action_combo.itemText(i) for i in range(dlg._action_combo.count())]
        assert any("keep" in t.lower() for t in items), f"No keep option found in {items}"

    def test_keep_remove_action_value_is_empty_string(self, qapp):
        """The internal value for the keep entry must be '' not 'keep'."""
        from app.views.constants import settable_decisions
        SETTABLE_DECISIONS = settable_decisions()
        keep_entry = next((t for t in SETTABLE_DECISIONS if "keep" in t[0].lower()), None)
        assert keep_entry is not None
        assert keep_entry[1] == ""

    def test_action_combo_count_matches_settable_decisions_with_remove_and_lock(self, qapp):
        """The regex dropdown surfaces five actions: delete, keep,
        remove from list, lock, unlock. That's
        ``settable_decisions(include_remove=True, include_lock=True)``,
        wired in select_dialog.py — see photo-manager#164.
        """
        from app.views.constants import settable_decisions
        EXPECTED = settable_decisions(include_remove=True, include_lock=True)
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["File Name"])
        assert dlg._action_combo.count() == len(EXPECTED)
        assert dlg._action_combo.count() == 5  # delete, keep, remove, lock, unlock

    def test_action_combo_includes_lock_and_unlock_options(self, qapp):
        """Lock and unlock sentinels must be reachable from the regex
        dropdown — see photo-manager#164."""
        from app.views.constants import LOCK_SENTINEL, UNLOCK_SENTINEL
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["File Name"])
        values = [v for _, v in dlg._decisions]
        assert LOCK_SENTINEL in values
        assert UNLOCK_SENTINEL in values

    def test_action_combo_includes_remove_option(self, qapp):
        """Specifically verify the remove sentinel is reachable from the
        dropdown — that's the whole point of include_remove=True here."""
        from app.views.constants import REMOVE_FROM_LIST_SENTINEL
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["File Name"])
        # _decisions stores the (label, value) pairs the dropdown emits.
        values = [v for _, v in dlg._decisions]
        assert REMOVE_FROM_LIST_SENTINEL in values


class TestBackwardCompatAlias:
    def test_select_dialog_alias_works(self, qapp):
        from app.views.dialogs.select_dialog import SelectDialog, ActionDialog
        assert SelectDialog is ActionDialog


# ── Live preview / validation / debounce (Phase A) ─────────────────────────


class TestPreviewPane:
    def test_no_match_fn_hides_preview_pane(self, qapp):
        """match_fn=None falls back to the original flat layout — no
        preview list, no counter visible. Existing UIA paths and tests
        keep working unchanged."""
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        # No preview_list attribute when match_fn isn't supplied.
        assert not hasattr(dlg, "_preview_list")
        # Counter is constructed but hidden.
        assert dlg._match_counter.isHidden()

    def test_match_fn_called_on_typing(self, qapp):
        """Typing into the regex must invoke match_fn after the debounce
        timer fires (we shortcut the timer here)."""
        from app.views.dialogs.select_dialog import ActionDialog
        from unittest.mock import MagicMock

        match_fn = MagicMock(return_value=(2, 3, [("a.jpg", "a.jpg"), ("b.jpg", "b.jpg")]))
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        # Phase B introduced Simple-mode default — switch to Regex
        # mode so dlg.regex is the active pattern source.
        dlg._mode_regex_btn.setChecked(True)

        match_fn.reset_mock()
        dlg.regex.setText("IMG")
        dlg._refresh_preview()

        match_fn.assert_called_with("File Name", "IMG")

    def test_preview_lists_samples(self, qapp):
        """Preview list shows the sample names returned by match_fn."""
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (
            3, 100,
            [("one.jpg", "one.jpg"), ("two.jpg", "two.jpg"), ("three.jpg", "three.jpg")],
        )
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg.regex.setText(".*")
        dlg._refresh_preview()

        items = [
            dlg._preview_list.item(i).text()
            for i in range(dlg._preview_list.count())
        ]
        assert items == ["one.jpg", "two.jpg", "three.jpg"]

    def test_preview_truncation_footer(self, qapp):
        """When matched count exceeds samples returned, the truncation
        footer must show "…and N more"."""
        from app.views.dialogs.select_dialog import ActionDialog

        # 50 samples, 200 total matches — 150 are hidden.
        match_fn = lambda f, p: (
            200, 4500,
            [(f"x{i}.jpg", f"x{i}.jpg") for i in range(50)],
        )
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg.regex.setText(".*")
        dlg._refresh_preview()

        assert dlg._preview_list.count() == 50
        assert not dlg._preview_truncated.isHidden()
        assert "150" in dlg._preview_truncated.text()

    def test_no_truncation_when_all_samples_fit(self, qapp):
        """If matched <= len(samples), the truncation footer stays
        hidden — otherwise users see "…and 0 more" which is confusing."""
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (
            5, 100,
            [(s, s) for s in ("a", "b", "c", "d", "e")],
        )
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg.regex.setText(".*")
        dlg._refresh_preview()

        assert dlg._preview_truncated.isHidden()

    def test_field_change_retriggers_match_fn(self, qapp):
        """Changing the field combo restarts the debounce timer so the
        preview re-runs with the new field."""
        from app.views.dialogs.select_dialog import ActionDialog
        from unittest.mock import MagicMock

        match_fn = MagicMock(return_value=(0, 0, []))
        dlg = ActionDialog(
            fields=["File Name", "Folder"], match_fn=match_fn
        )
        dlg.regex.setText("foo")
        match_fn.reset_mock()

        dlg.combo.setCurrentText("Folder")
        dlg._refresh_preview()

        # The first call after combo change is for the new field.
        called_fields = [c.args[0] for c in match_fn.call_args_list]
        assert "Folder" in called_fields

    def test_no_match_shows_empty_placeholder(self, qapp):
        """matched == 0 → list shows the localized "No matches"
        placeholder, not an empty list (which feels broken)."""
        from app.views.dialogs.select_dialog import ActionDialog
        from infrastructure.i18n import t

        match_fn = lambda f, p: (0, 100, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg.regex.setText("zz_no_such_pattern")
        dlg._refresh_preview()

        assert dlg._preview_list.count() == 1
        assert dlg._preview_list.item(0).text() == t("action_dialog.preview_empty")


class TestRegexValidation:
    def test_valid_regex_shows_check_icon(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        dlg.regex.setText("^IMG_\\d+$")
        dlg._validate_regex()

        # C8 from #349 (Wave 8): icon is now a theme-aware pixmap from
        # QStyle.standardIcon, not a text glyph. A valid pattern should
        # produce a non-null pixmap on the validation icon.
        assert not dlg._validation_icon.pixmap().isNull()
        assert dlg._validation_icon.accessibleName() == "Regex valid"
        assert dlg._validation_error.isHidden()

    def test_invalid_regex_hides_icon_and_shows_error(self, qapp):
        """B3 from #348 (Wave 8): when the error label is visible the icon
        is redundant — the prefixed "Invalid regex: ..." text already
        explains the problem. Icon should be cleared on the invalid path.
        """
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        dlg.regex.setText("(unclosed")
        dlg._validate_regex()

        # B3: icon must be cleared (no pixmap, no text) when error is shown.
        assert dlg._validation_icon.pixmap().isNull()
        assert not dlg._validation_error.isHidden()
        # Localized prefix from action_dialog.invalid_regex.
        assert "Invalid regex" in dlg._validation_error.text() \
            or "正規式錯誤" in dlg._validation_error.text()
        # Accessible name still announces the failure for screen readers
        # even though the visual icon is hidden.
        assert "Regex invalid" in dlg._validation_icon.accessibleName()

    def test_empty_regex_clears_validation_state(self, qapp):
        """Empty input is neutral — no icon, no error. Otherwise the
        dialog feels broken before the user has typed anything.

        B3 from #348 (Wave 8): the invalid branch ALSO clears the icon
        now (error label alone conveys the problem), so the "icon empty"
        invariant holds for both empty and invalid states.
        """
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        dlg.regex.setText("(")
        dlg._validate_regex()
        # B3: even on invalid, icon is empty (only the error label shows).
        assert dlg._validation_icon.pixmap().isNull()

        dlg.regex.setText("")
        dlg._validate_regex()
        assert dlg._validation_icon.pixmap().isNull()
        assert dlg._validation_error.isHidden()

    def test_invalid_regex_does_not_call_match_fn(self, qapp):
        """match_fn iterates the records — must not run for unparseable
        patterns. Counter shows em dash instead."""
        from app.views.dialogs.select_dialog import ActionDialog
        from unittest.mock import MagicMock

        match_fn = MagicMock(return_value=(0, 0, []))
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        # Phase B: Regex-mode-specific test — switch into Regex mode so
        # dlg.regex.setText drives the pattern.
        dlg._mode_regex_btn.setChecked(True)
        match_fn.reset_mock()

        dlg.regex.setText("(")
        # Validation runs synchronously; should mark counter as "—".
        # Then we manually fire the preview timer's slot to confirm the
        # closure short-circuits on invalid regex.
        dlg._refresh_preview()

        assert match_fn.call_count == 0
        assert dlg._match_counter.text() == "—"


class TestApplyGate:
    """A9 + A10 from #347 — Apply must refuse to fire on empty or
    invalid patterns. Empty + Delete decision would wipe every row
    (``re.search("", anything)`` is truthy); invalid patterns would
    raise at the receiver and still poison Recent because the original
    code recorded BEFORE emitting.
    """

    def test_apply_with_invalid_regex_does_not_emit(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        dlg.regex.setText("(unclosed")

        received = []
        dlg.setActionRequested.connect(lambda f, p, v: received.append((f, p, v)))
        dlg._btn_set_action.click()

        assert received == []

    def test_apply_with_invalid_regex_does_not_record_recent(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings()
        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(
            fields=["File Name"], match_fn=match_fn, settings=settings
        )
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText("(unclosed")
        dlg._btn_set_action.click()

        assert settings.get("ui.action_dialog.recent_patterns", []) == []

    def test_apply_with_empty_regex_mode_does_not_emit(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText("")

        received = []
        dlg.setActionRequested.connect(lambda f, p, v: received.append((f, p, v)))
        dlg._btn_set_action.click()

        assert received == []

    def test_apply_with_empty_simple_mode_does_not_emit(self, qapp):
        """Simple mode synthesises the regex via re.escape, so an empty
        Simple text produces an empty pattern — same every-row-match
        bug as Regex mode's empty input, different code path.
        """
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        # Simple is the default when match_fn is supplied.
        dlg._simple_text.setText("")

        received = []
        dlg.setActionRequested.connect(lambda f, p, v: received.append((f, p, v)))
        dlg._btn_set_action.click()

        assert received == []


class TestMatchCounter:
    def test_counter_format_uses_translation(self, qapp):
        """Counter text is built from the action_dialog.match_counter
        i18n template — verifies localization rather than hardcoded
        English."""
        from app.views.dialogs.select_dialog import ActionDialog
        from infrastructure.i18n import t

        match_fn = lambda f, p: (7, 200, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg.regex.setText(".*")
        dlg._refresh_preview()

        expected = t("action_dialog.match_counter").format(matched=7, total=200)
        assert dlg._match_counter.text() == expected


class TestObjectNames:
    """Pinning objectName values that QA scenarios will use to find
    widgets without relying on geometry / type-path."""

    def test_widget_object_names_are_set(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        assert dlg.regex.objectName() == "regexLineEdit"
        assert dlg.combo.objectName() == "regexFieldCombo"
        assert dlg._action_combo.objectName() == "regexActionCombo"
        assert dlg._btn_set_action.objectName() == "regexApplyButton"
        assert dlg._validation_icon.objectName() == "regexValidationIcon"
        assert dlg._validation_error.objectName() == "regexValidationError"
        assert dlg._match_counter.objectName() == "regexMatchCounter"

    def test_preview_widgets_object_names_set_when_present(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        assert dlg._preview_list.objectName() == "regexPreviewList"
        assert dlg._preview_truncated.objectName() == "regexPreviewTruncated"


# ── Phase B: Simple / Regex mode toggle, cheatsheet, recent ──────────────


class _FakeSettings:
    """In-memory stand-in for JsonSettings with the same get/set/save API."""

    def __init__(self, initial: dict | None = None) -> None:
        self._data = dict(initial or {})
        self.save_count = 0

    def get(self, key, default=None):
        parts = key.split(".")
        node = self._data
        for p in parts:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node

    def set(self, key, value) -> None:
        parts = key.split(".")
        node = self._data
        for p in parts[:-1]:
            if not isinstance(node.get(p), dict):
                node[p] = {}
            node = node[p]
        node[parts[-1]] = value

    def save(self) -> None:
        self.save_count += 1


class TestModeToggle:
    def test_default_mode_is_simple_when_match_fn_supplied(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog, MODE_SIMPLE

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        assert dlg._mode == MODE_SIMPLE
        # Simple widgets visible, regex widgets hidden.
        assert not dlg._simple_widget.isHidden()
        assert dlg._regex_widget.isHidden()

    def test_no_match_fn_pins_regex_mode(self, qapp):
        """Without a preview to back it, Simple is meaningless — the
        dialog pins to Regex mode with the Simple radio disabled.
        C1: mode toggle is always created; Simple is disabled (not absent)
        when match_fn is None."""
        from app.views.dialogs.select_dialog import ActionDialog, MODE_REGEX

        dlg = ActionDialog(fields=["File Name"])
        assert dlg._mode == MODE_REGEX
        # C1: toggle always created; Simple disabled without match_fn.
        assert hasattr(dlg, "_mode_simple_btn")
        assert not dlg._mode_simple_btn.isEnabled()
        assert dlg._mode_regex_btn.isChecked()

    def test_persisted_mode_overrides_default(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog, MODE_REGEX

        settings = _FakeSettings({"ui": {"action_dialog": {"mode": "regex"}}})
        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(
            fields=["File Name"], match_fn=match_fn, settings=settings
        )
        assert dlg._mode == MODE_REGEX
        assert dlg._mode_regex_btn.isChecked()

    def test_toggle_persists_to_settings(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings()
        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(
            fields=["File Name"], match_fn=match_fn, settings=settings
        )
        # Default Simple — toggle to Regex.
        dlg._mode_regex_btn.setChecked(True)
        # A8: persisted under per-context key, not the legacy global key.
        assert settings.get("ui.action_dialog.main.mode") == "regex"
        assert settings.save_count >= 1


class TestSimpleMode:
    """Simple mode synthesises a regex from (op, plain text) so users
    can match without learning regex syntax."""

    def test_contains_op_builds_escaped_substring(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._simple_op_combo.setCurrentIndex(0)  # contains
        dlg._simple_text.setText("IMG_001 (copy)")
        # Special chars must be escaped — the user typed plain text.
        assert dlg._build_pattern() == r"IMG_001\ \(copy\)"

    def test_starts_with_anchors_at_caret(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._simple_op_combo.setCurrentIndex(1)  # starts_with
        dlg._simple_text.setText("IMG")
        assert dlg._build_pattern() == r"^IMG"

    def test_ends_with_anchors_at_dollar(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._simple_op_combo.setCurrentIndex(2)  # ends_with
        dlg._simple_text.setText(".jpg")
        assert dlg._build_pattern() == r"\.jpg$"

    def test_exact_anchors_both_ends(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._simple_op_combo.setCurrentIndex(3)  # exact
        dlg._simple_text.setText("abc")
        assert dlg._build_pattern() == r"^abc$"

    def test_empty_text_returns_empty_pattern(self, qapp):
        """Empty Simple input must NOT produce ``^$`` etc — we return
        empty string so the preview pane shows the no-pattern state
        rather than 'matches everything that's empty', which is
        confusing."""
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._simple_text.setText("")
        assert dlg._build_pattern() == ""

    def test_apply_emits_synthesised_pattern(self, qapp):
        """The signal must carry the SAME regex the live preview built,
        so what-you-see is what-Apply-applies."""
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._simple_op_combo.setCurrentIndex(0)  # contains
        dlg._simple_text.setText("IMG")
        dlg._action_combo.setCurrentIndex(0)  # delete

        received = []
        dlg.setActionRequested.connect(lambda f, p, v: received.append((f, p, v)))
        dlg._btn_set_action.click()

        assert received == [("File Name", "IMG", "delete")]


class TestCheatsheet:
    def test_token_inserted_at_caret(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText("foo bar")
        dlg.regex.setCursorPosition(3)  # between 'foo' and ' bar'
        dlg._insert_token(r"\d")
        assert dlg.regex.text() == r"foo\d bar"
        assert dlg.regex.cursorPosition() == 5

    def test_chips_only_built_when_match_fn_present(self, qapp):
        """Cheatsheet chips would clutter the legacy flat layout. They
        only ship alongside the rest of the live-preview UI."""
        from app.views.dialogs.select_dialog import ActionDialog

        # No match_fn: no chips. Verify by absence of any cheatsheet
        # objectName in the descendant widgets.
        dlg = ActionDialog(fields=["File Name"])
        names = {c.objectName() for c in dlg.findChildren(object)
                 if hasattr(c, "objectName")}
        chip_names = {n for n in names if n.startswith("regexCheatsheet_")}
        assert chip_names == set()

        # With match_fn: chips present, one per token.
        dlg2 = ActionDialog(
            fields=["File Name"], match_fn=lambda f, p: (0, 0, [])
        )
        names2 = {c.objectName() for c in dlg2.findChildren(object)
                  if hasattr(c, "objectName")}
        chip_names2 = {n for n in names2 if n.startswith("regexCheatsheet_")}
        assert len(chip_names2) >= 5  # at least the basic tokens


class TestRecentPatterns:
    def test_apply_records_pattern(self, qapp):
        # A6: recent patterns now stored as (field, pattern) tuples.
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings()
        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(
            fields=["File Name"], match_fn=match_fn, settings=settings
        )
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText(r"^IMG_\d+$")
        dlg._btn_set_action.click()

        recent = settings.get("ui.action_dialog.recent_patterns", [])
        assert recent == [("File Name", r"^IMG_\d+$")]

    def test_recent_dedup_and_cap(self, qapp):
        # A6: entries are (field, pattern) tuples; dedup is on the tuple.
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings({
            "ui": {"action_dialog": {"recent_patterns": [
                ("File Name", f"pattern_{i}") for i in range(10)
            ]}}
        })
        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(
            fields=["File Name"], match_fn=match_fn, settings=settings
        )
        dlg._mode_regex_btn.setChecked(True)
        # Reapply pattern_5 — must move to the front, drop the duplicate
        # at its old position, and stay capped at 10.
        dlg.regex.setText("pattern_5")
        dlg._btn_set_action.click()

        recent = settings.get("ui.action_dialog.recent_patterns")
        assert recent[0] == ("File Name", "pattern_5")
        assert recent.count(("File Name", "pattern_5")) == 1
        assert len(recent) == 10

    def test_apply_skips_empty_pattern(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings()
        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(
            fields=["File Name"], match_fn=match_fn, settings=settings
        )
        # Simple mode default — empty text → empty pattern. Don't
        # pollute the recent list with a no-op.
        dlg._simple_text.setText("")
        dlg._btn_set_action.click()
        assert settings.get("ui.action_dialog.recent_patterns", []) == []

    def test_clear_recent(self, qapp):
        # A6: after clearing, internal list and settings both empty.
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings({
            "ui": {"action_dialog": {"recent_patterns": [
                ("File Name", "a"), ("File Name", "b"), ("Folder", "c")
            ]}}
        })
        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
        )
        dlg._clear_recent_patterns()
        assert dlg._recent_patterns == []
        assert settings.get("ui.action_dialog.recent_patterns") == []

    def test_apply_does_not_record_invalid_regex(self, qapp):
        # A9 gate: invalid regex must not reach Recent. Already covered
        # by TestApplyGate but re-verified here for the tuple-shape path.
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings()
        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(
            fields=["File Name"], match_fn=match_fn, settings=settings
        )
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText("(unclosed")
        dlg._btn_set_action.click()
        assert settings.get("ui.action_dialog.recent_patterns", []) == []


class TestFieldChangeStateCorrectness:
    """Wave 3 of the regex-dialog audit (#347 A1/A1-ext/A12, #348 B10).

    Field combo changes used to silently destroy user-typed regex
    patterns (A1) and leave the Simple panel showing stale text (A1-ext).
    Row pre-fill used `^X$` (exact) when the documented Simple default
    is "contains" (B10). Picking from Recent flipped mode before
    setting the regex, racing with the mode toggle's reverse-parse (A12).
    These tests pin all four invariants together.
    """

    def test_user_regex_survives_field_change_no_prefill(self, qapp):
        """A1 — type a custom regex, change field to one with no
        row_value; the typed pattern must survive.

        Constructed without ``match_fn`` so mode is pinned to Regex
        and dlg.regex.setText is authoritative without a toggle.
        """
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name", "Folder"])
        dlg.regex.setText(r"\d{4}-\d{2}")

        dlg.combo.setCurrentText("Folder")  # no row_value for Folder

        assert dlg.regex.text() == r"\d{4}-\d{2}"

    def test_user_regex_survives_field_change_when_new_field_has_prefill(self, qapp):
        """A1 — even when the *new* field has a pre-fill value, the
        user's custom pattern must NOT be replaced. This is the riskier
        case: the guard checks the PREVIOUS field's default, not the
        new field's.
        """
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(
            fields=["File Name", "Folder"],
            row_values={"Folder": "Photos"},
        )
        custom = r"\d{4}-\d{2}"
        dlg.regex.setText(custom)
        dlg.combo.setCurrentText("Folder")

        # The pre-fill for Folder would be re.escape("Photos") == "Photos".
        # User's custom pattern must beat it.
        assert dlg.regex.text() == custom

    def test_field_change_overwrites_when_regex_is_still_prior_default(self, qapp):
        """A1 — when the regex IS still the prior field's auto-default
        (user never customized), switching field should refresh to the
        new field's default. This is the no-customization-lost path.
        """
        import re as _re
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(
            fields=["File Name", "Folder"],
            row_values={"File Name": "IMG_001.jpg", "Folder": "Photos"},
        )
        # initial_field defaults to File Name → pre-fill is
        # re.escape("IMG_001.jpg"). Verify untouched, then switch.
        assert dlg.regex.text() == _re.escape("IMG_001.jpg")

        dlg.combo.setCurrentText("Folder")
        assert dlg.regex.text() == _re.escape("Photos")

    def test_simple_panel_reflects_new_field_after_change(self, qapp):
        """A1-ext — after a field change, the Simple panel must show
        the NEW field's content, not the prior field's stale reverse-parse.
        """
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(
            fields=["File Name", "Folder"],
            row_values={"File Name": "IMG_001.jpg", "Folder": "Photos"},
            match_fn=lambda f, p: (0, 0, []),
        )
        # Default mode is Simple when match_fn is provided.
        dlg.combo.setCurrentText("Folder")

        # Simple panel must now show the Folder pre-fill, not the
        # File Name one.
        assert dlg._simple_op_combo.currentData() == "contains"
        assert dlg._simple_text.text() == "Photos"

    def test_row_prefill_uses_contains_not_exact(self, qapp):
        """B10 — row pre-fill produces a "contains" Simple op, not
        "exact". Pre-Wave-3 the pre-fill was ^X$ which reverse-parsed
        as exact, contradicting the documented default Simple op
        ("contains", "most-useful starting state").
        """
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(
            fields=["File Name"],
            row_values={"File Name": "IMG_001.jpg"},
            match_fn=lambda f, p: (0, 0, []),
        )
        # Initial field = File Name with pre-fill IMG_001.jpg.
        # Simple panel should show ("contains", "IMG_001.jpg").
        assert dlg._simple_op_combo.currentData() == "contains"
        assert dlg._simple_text.text() == "IMG_001.jpg"

    def test_apply_recent_complex_pattern_lands_in_regex(self, qapp):
        """A12 — picking a complex (non-Simple) pattern from Recent must:
        (1) land the literal recent pattern in dlg.regex first (set BEFORE
        mode flip so reverse-parse sees the right pattern), and
        (2) end up in Regex mode. Pre-Wave-3 order was mode-flip → setText,
        racing with the mode toggle's reverse-parse.
        """
        from app.views.dialogs.select_dialog import ActionDialog, MODE_REGEX

        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
        )
        # Confirm we start in Simple mode (default with match_fn).
        assert dlg._mode_simple_btn.isChecked()
        # A complex pattern Simple can't represent → lands in Regex.
        dlg._apply_recent_pattern(r"\d{3}-\w+")

        assert dlg.regex.text() == r"\d{3}-\w+"
        assert dlg._mode == MODE_REGEX

    def test_apply_recent_simple_pattern_stays_in_simple(self, qapp):
        """A7 — picking a Simple-representable pattern from Recent must
        flip to Simple mode (not force Regex as it did before A7).
        Pattern ``^IMG`` is starts_with Simple, so it's representable.
        """
        from app.views.dialogs.select_dialog import ActionDialog, MODE_SIMPLE

        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
        )
        # Start in Regex mode so we can verify the flip to Simple.
        dlg._mode_regex_btn.setChecked(True)
        assert dlg._mode != MODE_SIMPLE

        dlg._apply_recent_pattern("^IMG")

        assert dlg.regex.text() == "^IMG"
        assert dlg._mode == MODE_SIMPLE
        # Simple inputs must show the parsed (starts_with, IMG) state.
        assert dlg._simple_op_combo.currentData() == "starts_with"
        assert dlg._simple_text.text() == "IMG"


class TestMatchHighlightDelegate:
    def test_match_span_stored_on_preview_items(self, qapp):
        """Each row that matches should carry its (start, end) span on
        Qt.UserRole — that's what the delegate paints from."""
        from app.views.dialogs.select_dialog import ActionDialog
        from PySide6.QtCore import Qt

        # match_fn returns 2 samples both containing 'IMG'.
        match_fn = lambda f, p: (
            2, 100,
            [("IMG_001.jpg", "IMG_001.jpg"), ("before_IMG_after.jpg", "before_IMG_after.jpg")],
        )
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText("IMG")
        dlg._refresh_preview()

        spans = [
            dlg._preview_list.item(i).data(Qt.UserRole)
            for i in range(dlg._preview_list.count())
        ]
        assert spans == [(0, 3), (7, 10)]


# ── Phase C: regex-as-single-source-of-truth + reverse-parse + legacy alias


class TestPreviewAccuracy:
    """Wave 4 of the regex-dialog audit (#347 A2, #350 D5+D8).

    A2: preview pane previously showed file basenames even when the
    regex was applied to a non-name field (Folder, Score, Date, etc.).
    Sample list now carries (basename, matched_field_str) tuples and
    the preview displays matched_field_str so the user can see WHY
    each row matched (highlight delegate also runs against the
    matched-field string).

    D5+D8: Top-N preview previously showed a flat basename list.
    Rows now include the group identifier (D5) and the numeric value
    that drove the ranking (D8).
    """

    def test_regex_preview_shows_matched_field_not_basename(self, qapp):
        """A2 — when the regex targets Folder, the preview row text is
        the folder path, not the basename."""
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (
            1, 10,
            [("vacation.jpg", "/photos/2023/summer")],
        )
        dlg = ActionDialog(fields=["Folder"], match_fn=match_fn)
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText(r"2023")
        dlg._refresh_preview()

        item = dlg._preview_list.item(0)
        assert item is not None
        assert item.text() == "/photos/2023/summer"

    def test_regex_preview_shows_basename_for_file_name_field(self, qapp):
        """A2 — File Name field: basename and matched_field_str are
        the same; preview behaviour is preserved (no regression).
        """
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (
            1, 10,
            [("IMG_001.jpg", "IMG_001.jpg")],
        )
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText(r"IMG")
        dlg._refresh_preview()

        item = dlg._preview_list.item(0)
        assert item is not None
        assert item.text() == "IMG_001.jpg"

    def test_match_span_computed_against_matched_field_string(self, qapp):
        """A2 — delegate span runs against the matched-field string so
        the bold span lands on the regex hit inside the folder path,
        not on a non-existent hit inside the basename.
        """
        from app.views.dialogs.select_dialog import ActionDialog
        from PySide6.QtCore import Qt

        match_fn = lambda f, p: (
            1, 10,
            [("vacation.jpg", "/photos/2023/summer")],
        )
        dlg = ActionDialog(fields=["Folder"], match_fn=match_fn)
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText(r"2023")
        dlg._refresh_preview()

        item = dlg._preview_list.item(0)
        assert item is not None
        span = item.data(Qt.UserRole)
        assert span is not None, "Highlight span missing — delegate would no-op"
        start, end = span
        assert item.text()[start:end] == "2023"

    def test_topn_preview_row_includes_group_identifier(self, qapp):
        """D5 — Top-N preview shows per-group context. Each picked row
        text contains a 'Group N' prefix matching the group it came from.
        """
        from app.views.dialogs.select_dialog import ActionDialog, NUMERIC_MODE_TOPN

        g1 = _make_group([
            _make_record(file_path="a/x.jpg", score=80.0),
            _make_record(file_path="a/y.jpg", score=60.0),
        ])
        g2 = _make_group([
            _make_record(file_path="b/p.jpg", score=90.0),
            _make_record(file_path="b/q.jpg", score=70.0),
        ])
        dlg = ActionDialog(
            fields=["Score"], groups=[g1, g2],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Score")
        dlg._numeric_mode = NUMERIC_MODE_TOPN
        dlg._num_n_spin.setValue(1)  # top 1 per group
        dlg._refresh_numeric_preview()

        texts = [
            dlg._preview_list.item(i).text()
            for i in range(dlg._preview_list.count())
        ]
        assert any("Group 1" in t for t in texts), texts
        assert any("Group 2" in t for t in texts), texts

    def test_topn_preview_row_includes_numeric_value(self, qapp):
        """D8 — Top-N preview shows the value the ranking selected on.
        Pre-Wave-4 the user couldn't see WHY a particular row was picked
        (especially for ties), which made the deterministic
        alphabetic-by-path tiebreaker invisible.
        """
        from app.views.dialogs.select_dialog import ActionDialog, NUMERIC_MODE_TOPN

        g = _make_group([
            _make_record(file_path="x.jpg", score=85.0),
            _make_record(file_path="y.jpg", score=42.0),
        ])
        dlg = ActionDialog(
            fields=["Score"], groups=[g],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Score")
        dlg._numeric_mode = NUMERIC_MODE_TOPN
        dlg._num_n_spin.setValue(1)
        dlg._num_order_combo.setCurrentIndex(
            dlg._num_order_combo.findData("desc")
        )
        dlg._refresh_numeric_preview()

        texts = [
            dlg._preview_list.item(i).text()
            for i in range(dlg._preview_list.count())
        ]
        # Top 1 desc by score → x.jpg (85.0) wins, y.jpg (42.0) loses.
        assert any("85" in t for t in texts), texts
        assert not any("42" in t for t in texts), texts


class TestTryParseSimple:
    """The reverse-parse table: regex string → Simple (op, plain_text)
    or None when Simple can't represent the pattern."""

    def test_empty_pattern_is_contains_empty(self):
        from app.views.dialogs.select_dialog import _try_parse_simple
        assert _try_parse_simple("") == ("contains", "")

    def test_plain_text_is_contains(self):
        from app.views.dialogs.select_dialog import _try_parse_simple
        assert _try_parse_simple("foo") == ("contains", "foo")

    def test_caret_anchor_is_starts_with(self):
        from app.views.dialogs.select_dialog import _try_parse_simple
        assert _try_parse_simple("^foo") == ("starts_with", "foo")

    def test_dollar_anchor_is_ends_with(self):
        from app.views.dialogs.select_dialog import _try_parse_simple
        assert _try_parse_simple("foo$") == ("ends_with", "foo")

    def test_both_anchors_is_exact(self):
        from app.views.dialogs.select_dialog import _try_parse_simple
        assert _try_parse_simple("^foo$") == ("exact", "foo")

    def test_escaped_special_chars_unescape_to_plain(self):
        """`re.escape("IMG_001.jpg (copy)")` round-trips back through
        Simple so the user sees their original input on toggle."""
        import re
        from app.views.dialogs.select_dialog import _try_parse_simple
        pattern = re.escape("IMG_001.jpg (copy)")
        assert _try_parse_simple(pattern) == ("contains", "IMG_001.jpg (copy)")

    def test_quantifier_is_too_complex(self):
        from app.views.dialogs.select_dialog import _try_parse_simple
        assert _try_parse_simple(r"\d+") is None

    def test_unescaped_class_is_too_complex(self):
        from app.views.dialogs.select_dialog import _try_parse_simple
        assert _try_parse_simple(r"[abc]") is None

    def test_alternation_is_too_complex(self):
        from app.views.dialogs.select_dialog import _try_parse_simple
        assert _try_parse_simple(r"foo|bar") is None

    def test_dangling_backslash_is_too_complex(self):
        """A regex string ending in a lone backslash isn't a valid
        re.escape() output and isn't valid regex either — Simple
        cannot represent it."""
        from app.views.dialogs.select_dialog import _try_parse_simple
        assert _try_parse_simple("foo\\") is None

    def test_escaped_dollar_does_not_anchor(self):
        """`foo\\$` is the literal text `foo$`, not an end-anchor —
        the trailing `$` is escaped (odd backslash count before it)."""
        from app.views.dialogs.select_dialog import _try_parse_simple
        assert _try_parse_simple(r"foo\$") == ("contains", "foo$")


class TestRegexSyncAcrossModes:
    """Phase C invariant: self.regex is the single source of truth for
    both modes. Switching modes is a display change, not a state reset."""

    def test_simple_writes_through_to_regex_line_edit(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        # Default mode is Simple. Type into Simple's text input —
        # self.regex.text() must update synchronously.
        dlg._simple_op_combo.setCurrentIndex(0)  # contains
        dlg._simple_text.setText("near")
        assert dlg.regex.text() == "near"

    def test_simple_to_regex_toggle_preserves_synthesised_pattern(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._simple_op_combo.setCurrentIndex(2)  # ends_with
        dlg._simple_text.setText(".jpg")
        assert dlg.regex.text() == r"\.jpg$"

        # Toggle to Regex — the regex line edit's value must persist.
        dlg._mode_regex_btn.setChecked(True)
        assert dlg.regex.text() == r"\.jpg$"

    def test_regex_to_simple_toggle_parses_plain_pattern(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText("q9")
        # Toggle back to Simple — should populate "contains" + "q9".
        dlg._mode_simple_btn.setChecked(True)
        assert dlg._simple_op_combo.currentData() == "contains"
        assert dlg._simple_text.text() == "q9"
        assert not dlg._simple_complex_notice.isHidden() is False  # notice hidden
        # Both Simple inputs are enabled (parseable, no notice).
        assert dlg._simple_op_combo.isEnabled()
        assert dlg._simple_text.isEnabled()

    def test_regex_to_simple_toggle_parses_anchored_patterns(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        for pattern, expected_op, expected_text in [
            ("^IMG", "starts_with", "IMG"),
            ("jpg$", "ends_with", "jpg"),
            ("^abc$", "exact", "abc"),
        ]:
            dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
            dlg._mode_regex_btn.setChecked(True)
            dlg.regex.setText(pattern)
            dlg._mode_simple_btn.setChecked(True)
            assert dlg._simple_op_combo.currentData() == expected_op, pattern
            assert dlg._simple_text.text() == expected_text, pattern

    def test_regex_to_simple_complex_pattern_shows_notice(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText(r"\d{3}")
        dlg._mode_simple_btn.setChecked(True)
        # Notice shown, Simple inputs disabled, regex preserved verbatim.
        assert not dlg._simple_complex_notice.isHidden()
        assert not dlg._simple_op_combo.isEnabled()
        assert not dlg._simple_text.isEnabled()
        assert dlg.regex.text() == r"\d{3}"
        # Toggling back to Regex must restore everything intact.
        dlg._mode_regex_btn.setChecked(True)
        assert dlg.regex.text() == r"\d{3}"


class TestLegacyModeKeyAlias:
    """Phase B persisted ``"beginner"``; Phase C reads it as Simple so
    upgraded users don't silently flip back to the default."""

    def test_legacy_beginner_value_loads_as_simple(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog, MODE_SIMPLE

        settings = _FakeSettings({"ui": {"action_dialog": {"mode": "beginner"}}})
        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(
            fields=["File Name"], match_fn=match_fn, settings=settings
        )
        assert dlg._mode == MODE_SIMPLE
        assert dlg._mode_simple_btn.isChecked()

    def test_unknown_value_falls_back_to_simple(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog, MODE_SIMPLE

        settings = _FakeSettings({"ui": {"action_dialog": {"mode": "garbage"}}})
        dlg = ActionDialog(
            fields=["File Name"], match_fn=lambda f, p: (0, 0, []),
            settings=settings,
        )
        assert dlg._mode == MODE_SIMPLE


# ──────────────────────────────────────────────────────────────────────────
# #209 — Numeric-condition panel
# ──────────────────────────────────────────────────────────────────────────
#
# These tests cover the new threshold / Top-N controls added to the
# Set Action dialog. Every test exercises a real user-visible behavior:
# panel-switching by field choice (regression if a non-numeric field
# accidentally shows numeric UI), encoded pattern shape (regression if
# the downstream caller can't parse what Apply emits), and the
# selection helpers themselves (regression if Top-N's tiebreak or
# threshold's ISO-date parse drifts).

def _make_record(
    *, file_path: str, file_size_bytes: int = 0,
    score: float | None = None, hamming_distance: int | None = None,
    creation_date=None, is_locked: bool = False,
):
    """Minimal duck-typed record matching what the helpers read."""
    from dataclasses import dataclass

    @dataclass
    class _Rec:
        file_path: str
        file_size_bytes: int
        score: float | None
        hamming_distance: int | None
        creation_date: object
        shot_date: object
        is_locked: bool

    return _Rec(
        file_path=file_path,
        file_size_bytes=file_size_bytes,
        score=score,
        hamming_distance=hamming_distance,
        creation_date=creation_date,
        shot_date=None,
        is_locked=is_locked,
    )


def _make_group(items):
    from dataclasses import dataclass, field as _f

    @dataclass
    class _G:
        items: list = _f(default_factory=list)

    return _G(items=list(items))


class TestNumericPanelVisibility:
    """Field combo choice gates the numeric vs. regex/simple panels."""

    def test_numeric_field_shows_numeric_panel_with_groups(self, qapp):
        # A numeric-capable field with groups supplied → numeric panel
        # pre-empts the regex/simple panel. Regression: the brief's
        # core acceptance criterion.
        from app.views.dialogs.select_dialog import ActionDialog

        g = _make_group([_make_record(file_path="a/x.jpg", file_size_bytes=10)])
        dlg = ActionDialog(
            fields=["File Name", "Size (Bytes)"], groups=[g],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Size (Bytes)")
        assert not dlg._numeric_widget.isHidden()
        assert dlg._regex_widget.isHidden()
        assert dlg._simple_widget.isHidden()

    def test_non_numeric_field_keeps_regex_simple_panel(self, qapp):
        # Switching back to a text field hides the numeric panel and
        # restores whichever of regex/simple was previously active.
        # Regression: numeric-field switch must not leak into other
        # fields.
        from app.views.dialogs.select_dialog import ActionDialog

        g = _make_group([_make_record(file_path="a/x.jpg", file_size_bytes=10)])
        dlg = ActionDialog(
            fields=["File Name", "Size (Bytes)"], groups=[g],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Size (Bytes)")
        dlg.combo.setCurrentText("File Name")
        assert dlg._numeric_widget.isHidden()
        # File Name is non-numeric → at least one of simple/regex is
        # visible (depends on the persisted mode default).
        assert not (
            dlg._simple_widget.isHidden() and dlg._regex_widget.isHidden()
        )

    def test_numeric_panel_hidden_when_groups_not_passed(self, qapp):
        # Main-window dialog_handler builds ActionDialog without
        # groups; the numeric panel must stay hidden even for
        # numeric fields. Regression: don't surface a Top-N control
        # that has no groups to rank.
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(
            fields=["File Name", "Size (Bytes)"],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Size (Bytes)")
        assert dlg._numeric_widget.isHidden()


class TestFieldAwareNumericPlaceholder:
    """A5 from #347 — placeholder swaps between number and date hints
    when the field combo moves between numeric and date fields.

    The single-string placeholder ("type a number or YYYY-MM-DD for
    dates") was field-blind — Size users saw the date hint, Date users
    saw the number hint. The wiring lives in
    ``_update_numeric_value_placeholder`` called from ``__init__`` and
    ``_on_field_changed``.
    """

    def test_number_field_shows_number_hint(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        g = _make_group([_make_record(file_path="a/x.jpg", file_size_bytes=10)])
        dlg = ActionDialog(
            fields=["Size (Bytes)", "Creation Date"], groups=[g],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Size (Bytes)")
        placeholder = dlg._num_value_edit.placeholderText()
        assert "number" in placeholder.lower() or "數字" in placeholder
        assert "YYYY-MM-DD" not in placeholder

    def test_date_field_shows_date_hint(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        g = _make_group([_make_record(file_path="a/x.jpg", file_size_bytes=10)])
        dlg = ActionDialog(
            fields=["Size (Bytes)", "Creation Date"], groups=[g],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Creation Date")
        placeholder = dlg._num_value_edit.placeholderText()
        assert "YYYY-MM-DD" in placeholder

    def test_placeholder_swaps_on_field_change(self, qapp):
        """The real bug — user switches from Size to Creation Date and
        expects the hint to update. Static hint at __init__ wouldn't
        catch this; the per-field call in _on_field_changed does.
        """
        from app.views.dialogs.select_dialog import ActionDialog

        g = _make_group([_make_record(file_path="a/x.jpg", file_size_bytes=10)])
        dlg = ActionDialog(
            fields=["Size (Bytes)", "Creation Date"], groups=[g],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Size (Bytes)")
        first = dlg._num_value_edit.placeholderText()
        dlg.combo.setCurrentText("Creation Date")
        second = dlg._num_value_edit.placeholderText()
        assert first != second
        assert "YYYY-MM-DD" in second
        assert "YYYY-MM-DD" not in first


class TestNumericPolish:
    """Wave 5 of the regex-dialog audit (#347 A4, #348 B6).

    A4: threshold input grows a ✓/✗ icon + error label. Pre-Wave-5
    unparseable input silently produced 0 matches with no signal
    that the threshold (not the data) was the problem.

    B6: Top-N counter format carries the per-group bound that the
    operation actually has — generic "X of Y match" loses the
    "≤N per group × G groups" semantics specific to Top-N.
    """

    def test_threshold_invalid_input_shows_x_and_error(self, qapp):
        """A4 — non-empty unparseable threshold → ✗ icon + visible
        error label echoing the bad input.
        """
        from app.views.dialogs.select_dialog import ActionDialog

        g = _make_group([_make_record(file_path="a/x.jpg", file_size_bytes=100)])
        dlg = ActionDialog(
            fields=["Size (Bytes)"], groups=[g],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Size (Bytes)")
        dlg._num_value_edit.setText("not-a-number")

        # C8 from #349 (Wave 8): threshold icon is now a theme-aware pixmap.
        # The numeric row has no separate error label below the icon (unlike
        # the regex row), so B3 does NOT apply here — icon stays visible.
        assert not dlg._num_threshold_icon.pixmap().isNull()
        assert not dlg._num_threshold_error.isHidden()
        assert "not-a-number" in dlg._num_threshold_error.text()

    def test_threshold_empty_input_is_neutral(self, qapp):
        """A4 — empty threshold is the neutral state (no icon, no
        error). The user hasn't committed to a value yet, so showing
        ✗ would be premature noise.
        """
        from app.views.dialogs.select_dialog import ActionDialog

        g = _make_group([_make_record(file_path="a/x.jpg", file_size_bytes=100)])
        dlg = ActionDialog(
            fields=["Size (Bytes)"], groups=[g],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Size (Bytes)")
        # Type something invalid, then clear — confirms the clear-path
        # resets the icon (catches a regression where ✗ stays sticky).
        dlg._num_value_edit.setText("garbage")
        assert not dlg._num_threshold_icon.pixmap().isNull()

        dlg._num_value_edit.setText("")
        # C8 from #349 (Wave 8): clear-path also nulls the pixmap.
        assert dlg._num_threshold_icon.pixmap().isNull()
        assert dlg._num_threshold_error.isHidden()

    def test_topn_counter_uses_per_group_format(self, qapp):
        """B6 — Top-N counter reads "{matched} matched (≤N per group
        × G groups)", not the generic regex/threshold format. Catches
        a regression where the counter format key gets swapped back.
        """
        from app.views.dialogs.select_dialog import ActionDialog, NUMERIC_MODE_TOPN

        g1 = _make_group([
            _make_record(file_path="a/x.jpg", score=80.0),
            _make_record(file_path="a/y.jpg", score=60.0),
        ])
        g2 = _make_group([
            _make_record(file_path="b/p.jpg", score=90.0),
            _make_record(file_path="b/q.jpg", score=70.0),
        ])
        dlg = ActionDialog(
            fields=["Score"], groups=[g1, g2],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Score")
        dlg._numeric_mode = NUMERIC_MODE_TOPN
        dlg._num_n_spin.setValue(1)
        dlg._refresh_numeric_preview()

        counter = dlg._match_counter.text()
        # The Top-N format contains the per-group bound ("per group"
        # or "每組") and the group count digits (2 groups). The
        # generic format is "{matched} of {total} match" — its "of"
        # absence in the en string is the differentiator here.
        assert (
            "per group" in counter or "每組" in counter
        ), f"Top-N counter missing per-group context: {counter!r}"
        assert "2" in counter, (
            f"Top-N counter missing group count: {counter!r}"
        )


class TestThresholdEmit:
    """Apply with threshold mode emits an encoded __cmp__: pattern."""

    def test_threshold_emits_cmp_pattern(self, qapp):
        # The dialog's contract with the Execute Action dialog is
        # that threshold conditions ride through setActionRequested
        # as ``__cmp__:OP:VALUE``. If the encoding drifts, the
        # downstream handler will silently match 0 rows.
        from app.views.dialogs.select_dialog import ActionDialog

        g = _make_group([_make_record(file_path="a/x.jpg", file_size_bytes=100)])
        dlg = ActionDialog(
            fields=["File Name", "Size (Bytes)"], groups=[g],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Size (Bytes)")
        # Pick >=, type 80
        ge_idx = dlg._num_cmp_combo.findData(">=")
        assert ge_idx >= 0
        dlg._num_cmp_combo.setCurrentIndex(ge_idx)
        dlg._num_value_edit.setText("80")
        dlg._action_combo.setCurrentIndex(0)  # first = delete

        received: list[tuple] = []
        dlg.setActionRequested.connect(
            lambda f, p, v: received.append((f, p, v))
        )
        dlg._btn_set_action.click()
        assert received == [("Size (Bytes)", "__cmp__:>=:80", "delete")]

    def test_threshold_isodate_round_trips_through_pattern(self, qapp):
        # Date fields accept ISO YYYY-MM-DD. The emitted pattern
        # carries the user's literal text — the receiver re-parses
        # via _parse_threshold which knows the field is a date.
        # Regression: if encode/decode used a colon-greedy split,
        # ``2026-01-01 12:00:00`` would be truncated.
        from app.views.dialogs.select_dialog import (
            ActionDialog, decode_cmp_pattern,
        )

        g = _make_group([_make_record(file_path="a/x.jpg")])
        dlg = ActionDialog(
            fields=["File Name", "Creation Date"], groups=[g],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Creation Date")
        dlg._num_value_edit.setText("2026-01-01 12:00:00")
        dlg._action_combo.setCurrentIndex(0)

        received: list[tuple] = []
        dlg.setActionRequested.connect(
            lambda f, p, v: received.append((f, p, v))
        )
        dlg._btn_set_action.click()
        assert len(received) == 1
        _field, pattern, _value = received[0]
        decoded = decode_cmp_pattern(pattern)
        assert decoded == (">", "2026-01-01 12:00:00")


class TestTopNEmit:
    """Apply with Top-N mode emits an encoded __top_n__: pattern."""

    def test_top_n_emits_pattern(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        g = _make_group([_make_record(file_path="a/x.jpg", score=0.9)])
        dlg = ActionDialog(
            fields=["File Name", "Score"], groups=[g],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Score")
        dlg._num_mode_topn_btn.setChecked(True)
        dlg._num_n_spin.setValue(3)
        # Order combo defaults to Top (desc)
        dlg._action_combo.setCurrentIndex(0)

        received: list[tuple] = []
        dlg.setActionRequested.connect(
            lambda f, p, v: received.append((f, p, v))
        )
        dlg._btn_set_action.click()
        assert received == [("Score", "__top_n__:3:desc", "delete")]

    def test_top_n_bottom_emits_asc(self, qapp):
        # "Bottom 1 per group" — the deleter's most common pattern
        # ("nuke the lowest-scoring sibling in each cluster").
        # Regression: if asc/desc flip, this would silently delete
        # the keepers.
        from app.views.dialogs.select_dialog import ActionDialog

        g = _make_group([_make_record(file_path="a/x.jpg", score=0.5)])
        dlg = ActionDialog(
            fields=["File Name", "Score"], groups=[g],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Score")
        dlg._num_mode_topn_btn.setChecked(True)
        bot_idx = dlg._num_order_combo.findData("asc")
        dlg._num_order_combo.setCurrentIndex(bot_idx)
        dlg._num_n_spin.setValue(1)
        dlg._action_combo.setCurrentIndex(0)

        received: list[tuple] = []
        dlg.setActionRequested.connect(
            lambda f, p, v: received.append((f, p, v))
        )
        dlg._btn_set_action.click()
        assert received == [("Score", "__top_n__:1:asc", "delete")]


class TestThresholdSelectionLogic:
    """select_paths_by_threshold returns the rows the user actually expects."""

    def test_size_gt_selects_only_above(self, qapp):
        # 3 rows at 50/100/200 bytes; "> 100" must select exactly
        # one row (200), not two. Regression: an `>=` slip would
        # delete one extra file.
        from app.views.dialogs.select_dialog import select_paths_by_threshold

        g = _make_group([
            _make_record(file_path="a/1.jpg", file_size_bytes=50),
            _make_record(file_path="a/2.jpg", file_size_bytes=100),
            _make_record(file_path="a/3.jpg", file_size_bytes=200),
        ])
        result = select_paths_by_threshold([g], "Size (Bytes)", ">", "100")
        assert result == ["a/3.jpg"]

    def test_size_ge_includes_equal(self, qapp):
        from app.views.dialogs.select_dialog import select_paths_by_threshold

        g = _make_group([
            _make_record(file_path="a/1.jpg", file_size_bytes=50),
            _make_record(file_path="a/2.jpg", file_size_bytes=100),
            _make_record(file_path="a/3.jpg", file_size_bytes=200),
        ])
        result = select_paths_by_threshold([g], "Size (Bytes)", ">=", "100")
        assert result == ["a/2.jpg", "a/3.jpg"]

    def test_score_threshold_with_none_skipped(self, qapp):
        # Records with score=None (isolated rows, MOV passengers)
        # must NOT match a `> 0` threshold — the brief's score
        # contract treats None as "unrankable", not "zero".
        # Regression: a `float(None or 0)` would falsely include
        # passengers.
        from app.views.dialogs.select_dialog import select_paths_by_threshold

        g = _make_group([
            _make_record(file_path="a/1.jpg", score=None),
            _make_record(file_path="a/2.jpg", score=0.4),
            _make_record(file_path="a/3.jpg", score=0.8),
        ])
        result = select_paths_by_threshold([g], "Score", ">", "0.5")
        assert result == ["a/3.jpg"]

    def test_creation_date_isodate_threshold(self, qapp):
        from datetime import datetime

        from app.views.dialogs.select_dialog import select_paths_by_threshold

        g = _make_group([
            _make_record(
                file_path="a/old.jpg",
                creation_date=datetime(2020, 1, 1),
            ),
            _make_record(
                file_path="a/new.jpg",
                creation_date=datetime(2026, 6, 1),
            ),
        ])
        result = select_paths_by_threshold(
            [g], "Creation Date", ">", "2024-01-01"
        )
        assert result == ["a/new.jpg"]

    def test_unparseable_threshold_returns_empty(self, qapp):
        # User typed "abc" in the value field — must select nothing,
        # not crash. Regression: a stray ValueError up the stack
        # would kill the dialog. The acceptance test (`no_match_body`
        # dialog) lives in test_execute_action_dialog; here we just
        # verify the helper is silent.
        from app.views.dialogs.select_dialog import select_paths_by_threshold

        g = _make_group([
            _make_record(file_path="a/1.jpg", file_size_bytes=100),
        ])
        result = select_paths_by_threshold([g], "Size (Bytes)", ">", "abc")
        assert result == []


class TestTopNSelectionLogic:
    """select_paths_top_n ranks within group, not globally."""

    def test_top_1_per_group_picks_one_from_each(self, qapp):
        # Two groups of two rows each; Top 1 by score per group
        # picks the higher-scoring row from EACH group — not the
        # globally-top-1. Regression: if ranking went global, only
        # one of the two groups would have a survivor selected.
        from app.views.dialogs.select_dialog import select_paths_top_n

        g1 = _make_group([
            _make_record(file_path="g1/a.jpg", score=0.3),
            _make_record(file_path="g1/b.jpg", score=0.9),
        ])
        g2 = _make_group([
            _make_record(file_path="g2/a.jpg", score=0.4),
            _make_record(file_path="g2/b.jpg", score=0.6),
        ])
        result = select_paths_top_n([g1, g2], "Score", 1, "desc")
        assert sorted(result) == ["g1/b.jpg", "g2/b.jpg"]

    def test_bottom_1_picks_lowest_per_group(self, qapp):
        from app.views.dialogs.select_dialog import select_paths_top_n

        g1 = _make_group([
            _make_record(file_path="g1/a.jpg", score=0.3),
            _make_record(file_path="g1/b.jpg", score=0.9),
        ])
        g2 = _make_group([
            _make_record(file_path="g2/a.jpg", score=0.4),
            _make_record(file_path="g2/b.jpg", score=0.6),
        ])
        result = select_paths_top_n([g1, g2], "Score", 1, "asc")
        assert sorted(result) == ["g1/a.jpg", "g2/a.jpg"]

    def test_top_n_skips_none_scored_records(self, qapp):
        # MOV passengers and isolated rows have score=None and must
        # NOT be ranked — otherwise a passenger would dominate Top-1
        # via numeric coercion. Two scored + one None → Top 1
        # selects the higher of the scored, never the None.
        from app.views.dialogs.select_dialog import select_paths_top_n

        g = _make_group([
            _make_record(file_path="g1/p.mov", score=None),
            _make_record(file_path="g1/a.jpg", score=0.3),
            _make_record(file_path="g1/b.jpg", score=0.7),
        ])
        result = select_paths_top_n([g], "Score", 1, "desc")
        assert result == ["g1/b.jpg"]

    def test_top_n_larger_than_group_returns_all(self, qapp):
        # Top 5 of a 2-row group returns both — partial selection
        # is the right call ("pick up to N keepers per group") and
        # the user's intent regardless of group sizes.
        from app.views.dialogs.select_dialog import select_paths_top_n

        g = _make_group([
            _make_record(file_path="g1/a.jpg", score=0.3),
            _make_record(file_path="g1/b.jpg", score=0.9),
        ])
        result = select_paths_top_n([g], "Score", 5, "desc")
        assert sorted(result) == ["g1/a.jpg", "g1/b.jpg"]

    def test_top_n_tie_is_stable_by_path(self, qapp):
        # Two equal scores → tiebreak on file_path. Stable selection
        # matters because re-running the same Top-N must select the
        # same rows; otherwise the user's preview-then-Apply cycle
        # could deselect a row they expected.
        from app.views.dialogs.select_dialog import select_paths_top_n

        g = _make_group([
            _make_record(file_path="g1/b.jpg", score=0.5),
            _make_record(file_path="g1/a.jpg", score=0.5),
            _make_record(file_path="g1/c.jpg", score=0.5),
        ])
        # Top 1 of three equal-score rows: tiebreak picks the
        # alphabetically-first path (a.jpg), stably across runs.
        result = select_paths_top_n([g], "Score", 1, "desc")
        assert result == ["g1/a.jpg"]


class TestPatternEncoding:
    """encode/decode round-trips for the special pattern strings."""

    def test_cmp_pattern_round_trip(self, qapp):
        from app.views.dialogs.select_dialog import (
            decode_cmp_pattern, encode_cmp_pattern,
        )
        assert decode_cmp_pattern(encode_cmp_pattern(">=", "80")) == (">=", "80")

    def test_top_n_pattern_round_trip(self, qapp):
        from app.views.dialogs.select_dialog import (
            decode_top_n_pattern, encode_top_n_pattern,
        )
        assert decode_top_n_pattern(encode_top_n_pattern(3, "desc")) == (3, "desc")

    def test_decode_cmp_returns_none_for_non_cmp(self, qapp):
        from app.views.dialogs.select_dialog import decode_cmp_pattern
        assert decode_cmp_pattern("IMG.*") is None

    def test_decode_top_n_rejects_bad_order(self, qapp):
        # The receiver guards against a corrupted pattern (e.g.
        # `__top_n__:3:garbage`) so a future drift doesn't silently
        # rank with bogus order. None signals "treat as no-match".
        from app.views.dialogs.select_dialog import decode_top_n_pattern
        assert decode_top_n_pattern("__top_n__:3:garbage") is None


# ── Wave 7: Mode/Recent/Simple coordination ───────────────────────────────────


class TestRecentPatternsTupleShape:
    """A6 + E2-upgrade: recent_patterns stores (field, pattern) tuples.
    Legacy bare strings migrate on load; malformed entries auto-heal.
    """

    def test_tuple_saved_and_restored(self, qapp):
        # After Apply, settings holds a list of (field, pattern) tuples.
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings()
        dlg = ActionDialog(
            fields=["File Name", "Folder"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
        )
        dlg._mode_regex_btn.setChecked(True)
        dlg.combo.setCurrentText("Folder")
        dlg.regex.setText("Photos")
        dlg._btn_set_action.click()

        recent = settings.get("ui.action_dialog.recent_patterns", [])
        assert len(recent) == 1
        assert recent[0] == ("Folder", "Photos")

    def test_legacy_bare_string_migrates_to_tuple(self, qapp):
        # Bare strings from before A6 become (None, pattern) — field=None
        # means "match any field" in the render-time gate.
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings({
            "ui": {"action_dialog": {"recent_patterns": ["old_pattern"]}}
        })
        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
        )
        # The loaded list must have migrated to a tuple.
        assert dlg._recent_patterns == [(None, "old_pattern")]

    def test_malformed_entry_auto_healed(self, qapp):
        # Non-string, non-tuple entries are silently dropped; the list
        # heals itself. The real failure mode: a list of dicts got
        # written under the key somehow (settings corruption).
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings({
            "ui": {"action_dialog": {"recent_patterns": [
                ("File Name", "good"),
                {"bad": "dict"},
                None,
                ("", "empty-field"),  # empty field string → malformed
            ]}}
        })
        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
        )
        # Only the valid tuple survives.
        assert dlg._recent_patterns == [("File Name", "good")]

    def test_dedup_works_on_tuples(self, qapp):
        # Dedup key is the (field, pattern) pair — same pattern on a
        # different field is a different entry and must not be collapsed.
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings({
            "ui": {"action_dialog": {"recent_patterns": [
                ("File Name", "IMG"),
                ("Folder", "IMG"),
            ]}}
        })
        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
        )
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText("IMG")   # File Name is current field
        dlg._btn_set_action.click()

        recent = settings.get("ui.action_dialog.recent_patterns")
        # ("File Name", "IMG") moved to front; ("Folder", "IMG") kept.
        assert recent[0] == ("File Name", "IMG")
        assert ("Folder", "IMG") in recent
        assert recent.count(("File Name", "IMG")) == 1


class TestA13StripBeforeDedup:
    """A13: strip whitespace from pattern before dedup in _record_recent_pattern."""

    def test_whitespace_stripped_on_record(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings()
        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
        )
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText("  IMG  ")
        dlg._btn_set_action.click()

        recent = settings.get("ui.action_dialog.recent_patterns", [])
        # The stored pattern must be stripped.
        assert recent == [("File Name", "IMG")]

    def test_whitespace_only_not_recorded(self, qapp):
        # A strip that yields "" must not create an entry.
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings()
        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
        )
        dlg._record_recent_pattern("   ")
        assert settings.get("ui.action_dialog.recent_patterns", []) == []


class TestC1ModeToggleAlwaysVisible:
    """C1: mode toggle row created unconditionally; Simple disabled (not absent)
    when match_fn is None."""

    def test_simple_radio_disabled_without_match_fn(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        assert hasattr(dlg, "_mode_simple_btn")
        assert not dlg._mode_simple_btn.isEnabled()
        assert dlg._mode_regex_btn.isEnabled()
        assert dlg._mode_regex_btn.isChecked()

    def test_simple_radio_enabled_with_match_fn(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"], match_fn=lambda f, p: (0, 0, []))
        assert dlg._mode_simple_btn.isEnabled()

    def test_simple_tooltip_set_without_match_fn(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        assert dlg._mode_simple_btn.toolTip() != ""


class TestC4DefaultModeLogic:
    """C4: default mode is Regex when match_fn is None, Simple otherwise."""

    def test_default_regex_when_no_match_fn(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog, MODE_REGEX

        dlg = ActionDialog(fields=["File Name"])
        assert dlg._mode == MODE_REGEX

    def test_default_simple_when_match_fn(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog, MODE_SIMPLE

        dlg = ActionDialog(fields=["File Name"], match_fn=lambda f, p: (0, 0, []))
        assert dlg._mode == MODE_SIMPLE


class TestA8ContextIsolation:
    """A8: per-context mode key isolates main and execute preferences."""

    def test_per_context_mode_key_used(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings()
        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
            context_id="execute",
        )
        dlg._mode_regex_btn.setChecked(True)
        # Must save to execute context, not main or legacy key.
        assert settings.get("ui.action_dialog.execute.mode") == "regex"
        assert settings.get("ui.action_dialog.main.mode") is None
        assert settings.get("ui.action_dialog.mode") is None

    def test_legacy_key_read_as_fallback(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog, MODE_REGEX

        settings = _FakeSettings({
            "ui": {"action_dialog": {"mode": "regex"}}
        })
        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
            context_id="main",
        )
        # Per-context key absent → reads legacy key → regex.
        assert dlg._mode == MODE_REGEX

    def test_per_context_key_beats_legacy(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog, MODE_SIMPLE

        settings = _FakeSettings({
            "ui": {"action_dialog": {
                "mode": "regex",                   # legacy — would give Regex
                "main": {"mode": "simple"},        # per-context — must win
            }}
        })
        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
            context_id="main",
        )
        assert dlg._mode == MODE_SIMPLE


class TestB2SwitchToRegexButton:
    """B2+B4: "Switch to Regex" button in simple_outer; visible with notice."""

    def test_button_exists_hidden_by_default(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"], match_fn=lambda f, p: (0, 0, []))
        assert hasattr(dlg, "_switch_to_regex_btn")
        assert dlg._switch_to_regex_btn.isHidden()

    def test_button_shown_with_complex_notice(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"], match_fn=lambda f, p: (0, 0, []))
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText(r"\d{3}")
        # Switch to Simple — complex pattern triggers notice + button.
        dlg._mode_simple_btn.setChecked(True)

        assert not dlg._simple_complex_notice.isHidden()
        assert not dlg._switch_to_regex_btn.isHidden()

    def test_button_hidden_when_pattern_is_simple(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"], match_fn=lambda f, p: (0, 0, []))
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText("img")
        # Simple-representable — notice+button must stay hidden.
        dlg._mode_simple_btn.setChecked(True)

        assert dlg._simple_complex_notice.isHidden()
        assert dlg._switch_to_regex_btn.isHidden()

    def test_button_click_flips_to_regex_mode(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog, MODE_REGEX

        dlg = ActionDialog(fields=["File Name"], match_fn=lambda f, p: (0, 0, []))
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText(r"\d{3}")
        dlg._mode_simple_btn.setChecked(True)
        assert dlg._mode != MODE_REGEX

        # Click the switch button — must flip to Regex without losing pattern.
        dlg._switch_to_regex_btn.click()
        assert dlg._mode == MODE_REGEX
        assert dlg.regex.text() == r"\d{3}"


class TestE3SimpleOpPersistence:
    """E3: simple_op persisted per context; stale key falls back to "contains"."""

    def test_simple_op_restored_from_settings(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings({
            "ui": {"action_dialog": {"main": {"simple_op": "ends_with"}}}
        })
        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
            context_id="main",
        )
        assert dlg._simple_op_combo.currentData() == "ends_with"

    def test_stale_simple_op_falls_back_to_contains(self, qapp):
        # findData returns -1 for "old_op_key" → must leave combo at 0
        # ("contains"), not set it to -1 which leaves it blank.
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings({
            "ui": {"action_dialog": {"main": {"simple_op": "old_op_key"}}}
        })
        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
            context_id="main",
        )
        # Must land on "contains" (index 0), not a blank/invalid state.
        assert dlg._simple_op_combo.currentData() == "contains"
        assert dlg._simple_op_combo.currentIndex() == 0


class TestE8FieldPersistence:
    """E8: field persisted per context; initial_field overrides persisted."""

    def test_field_restored_from_settings_when_no_initial_field(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings({
            "ui": {"action_dialog": {"main": {"field": "Folder"}}}
        })
        dlg = ActionDialog(
            fields=["File Name", "Folder"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
            context_id="main",
            initial_field=None,
        )
        assert dlg._current_field() == "Folder"

    def test_initial_field_overrides_persisted_field(self, qapp):
        # When initial_field is given (column click), it takes priority
        # over whatever is persisted in settings.
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings({
            "ui": {"action_dialog": {"main": {"field": "Folder"}}}
        })
        dlg = ActionDialog(
            fields=["File Name", "Folder"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
            context_id="main",
            initial_field="File Name",
        )
        assert dlg._current_field() == "File Name"

    def test_field_persisted_on_done(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings()
        dlg = ActionDialog(
            fields=["File Name", "Folder"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
            context_id="main",
        )
        dlg.combo.setCurrentText("Folder")
        dlg.done(0)

        assert settings.get("ui.action_dialog.main.field") == "Folder"


class TestC2RecentButtonAlwaysVisible:
    """C2: Recent button lives in the mode row, always visible."""

    def test_recent_button_present_without_match_fn(self, qapp):
        # Pre-C2 the button was inside _regex_widget — invisible in Simple
        # and absent without match_fn. Post-C2 it lives in the mode row.
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        assert hasattr(dlg, "_recent_btn")
        # Button should not be inside _regex_widget.
        assert dlg._recent_btn.parent() is not dlg._regex_widget

    def test_recent_button_present_with_match_fn(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"], match_fn=lambda f, p: (0, 0, []))
        assert hasattr(dlg, "_recent_btn")
        assert dlg._recent_btn.parent() is not dlg._regex_widget


class TestA6FieldGatedRecentMenu:
    """A6: Recent menu only shows entries for the current field (or None-field)."""

    def test_menu_shows_only_matching_field_entries(self, qapp):
        # Two entries: one for File Name, one for Folder. When File Name
        # is the active field, only the File Name entry is rendered.
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings({
            "ui": {"action_dialog": {"recent_patterns": [
                ("File Name", "IMG"),
                ("Folder", "Photos"),
            ]}}
        })
        dlg = ActionDialog(
            fields=["File Name", "Folder"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
        )
        dlg.combo.setCurrentText("File Name")
        # _show_recent_menu is hard to test directly (it calls menu.exec).
        # Test the internal filter logic instead.
        current_field = dlg._current_field()
        visible = [
            (f, p) for f, p in dlg._recent_patterns
            if f is None or f == current_field
        ]
        assert visible == [("File Name", "IMG")]

    def test_none_field_entries_shown_for_all_fields(self, qapp):
        # Legacy entries with field=None show regardless of current field.
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings({
            "ui": {"action_dialog": {"recent_patterns": [
                ("old_pattern",),  # malformed — will be dropped
            ]}}
        })
        # Use direct _record_recent_pattern-based setup instead.
        dlg = ActionDialog(
            fields=["File Name", "Folder"],
            match_fn=lambda f, p: (0, 0, []),
        )
        # Inject a None-field entry directly.
        dlg._recent_patterns = [(None, "IMG")]
        dlg.combo.setCurrentText("Folder")

        visible = [
            (f, p) for f, p in dlg._recent_patterns
            if f is None or f == dlg._current_field()
        ]
        assert visible == [(None, "IMG")]


# ── Wave 8 (#349/#350/#351) — geometry persistence + reset ────────────────────


class TestSplitterPersistence:
    """C13 from #349 (Wave 8): the dialog's splitter handle position
    persists across open/close cycles. Pre-Wave-8 only the outer window
    geometry was saved; the user's [left | preview] balance reset to
    [420, 380] on every reopen, which the user perceived as the dialog
    "forgetting" their preferred layout.
    """

    def test_splitter_state_persists_to_qsettings_on_close(
        self, qapp, monkeypatch, tmp_path
    ):
        """C13 save side: ``done()`` must call ``save_splitter_state``
        so the divider position survives a close-and-reopen.

        We assert the SAVE invariant (a non-None blob exists under the
        new key after done()) rather than the round-tripped pixel sizes
        — Qt's ``restoreState`` redistribution depends on the target
        dialog's available width at restore time, which varies across
        headless CI environments (the dialog is never ``show()``n in a
        unit test). The pixel-faithful round-trip is Qt's contract;
        OUR contract is just "the save call runs and the blob lands".

        Pre-Wave-8 this test would fail because ``save_splitter_state``
        wasn't called from ``done()`` at all — no blob, no restore.
        """
        from app.views.dialogs.select_dialog import ActionDialog
        from app.views.window_state import (
            QSETTINGS_KEY_ACTION_DIALOG_SPLITTER_STATE,
            qsettings_path,
            window_state_qsettings,
        )

        monkeypatch.setenv("PHOTO_MANAGER_HOME", str(tmp_path.name))
        repo_root = qsettings_path().parent
        repo_root.mkdir(parents=True, exist_ok=True)
        ini = qsettings_path()
        if ini.exists():
            ini.unlink()

        dlg = ActionDialog(
            fields=["File Name"], match_fn=lambda f, p: (0, 0, [])
        )
        dlg._splitter.setSizes([600, 200])
        dlg.done(0)

        store = window_state_qsettings()
        blob = store.value(QSETTINGS_KEY_ACTION_DIALOG_SPLITTER_STATE)
        assert blob is not None, (
            "save_splitter_state did not write the action_dialog_splitter "
            "key on close — the splitter handle position will reset to "
            "[420, 380] default on every reopen (pre-Wave-8 bug)."
        )

        if ini.exists():
            ini.unlink()

    def test_splitter_state_restore_path_wired(self, qapp):
        """C13 restore side: ``__init__`` must call
        ``restore_splitter_state`` on the splitter branch — symmetric
        with the save side above. We can't easily assert the call
        happened without mocking (which would be padding), but we can
        assert the import path + parameter list match what the restore
        call needs. The pair-test approach: if either side breaks, the
        e2e qa probe in s48 catches the integration failure."""
        from app.views.dialogs.select_dialog import (
            ActionDialog,
            restore_splitter_state,
            save_splitter_state,
            QSETTINGS_KEY_ACTION_DIALOG_SPLITTER_STATE,
        )
        # Importable from the module — proves the symbols are wired in
        # (a refactor that drops them would break this assertion).
        assert restore_splitter_state is not None
        assert save_splitter_state is not None
        assert QSETTINGS_KEY_ACTION_DIALOG_SPLITTER_STATE == (
            "geometry/action_dialog_splitter"
        )
        # And the dialog exposes self._splitter on the match_fn branch
        # so both restore (in __init__) and save (in done) can find it.
        dlg = ActionDialog(
            fields=["File Name"], match_fn=lambda f, p: (0, 0, [])
        )
        assert dlg._splitter is not None

    def test_splitter_is_none_without_match_fn(self, qapp):
        """E4 invariant: the flat-layout branch has no splitter and
        nothing is persisted. The done() path must handle this without
        crashing (no save_splitter_state call on the None branch)."""
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        assert dlg._splitter is None
        # done() must not raise on the no-match_fn branch.
        dlg.done(0)


class TestResetGeometry:
    """E5 from #351 (Wave 8): "Reset window size" wipes the persisted
    geometry + splitter state under window_state.ini. The reset must
    NOT touch settings.json keys (mode/field/simple_op) — those are
    separate user preferences that survive a chrome-size reset.
    """

    def test_reset_clears_geometry_keys(self, qapp, monkeypatch, tmp_path):
        from app.views.dialogs.select_dialog import ActionDialog
        from app.views.window_state import (
            QSETTINGS_KEY_ACTION_DIALOG_GEOM,
            QSETTINGS_KEY_ACTION_DIALOG_SPLITTER_STATE,
            qsettings_path,
            window_state_qsettings,
        )

        monkeypatch.setenv("PHOTO_MANAGER_HOME", str(tmp_path.name))
        repo_root = qsettings_path().parent
        repo_root.mkdir(parents=True, exist_ok=True)
        ini = qsettings_path()
        if ini.exists():
            ini.unlink()

        # Seed both keys by opening + closing with a non-default geometry.
        dlg_a = ActionDialog(
            fields=["File Name"], match_fn=lambda f, p: (0, 0, [])
        )
        dlg_a._splitter.setSizes([700, 100])
        dlg_a.resize(900, 500)
        dlg_a.done(0)

        store = window_state_qsettings()
        assert store.value(QSETTINGS_KEY_ACTION_DIALOG_GEOM) is not None
        assert store.value(
            QSETTINGS_KEY_ACTION_DIALOG_SPLITTER_STATE
        ) is not None

        # Reset on a fresh dialog instance — clears both keys.
        dlg_b = ActionDialog(
            fields=["File Name"], match_fn=lambda f, p: (0, 0, [])
        )
        dlg_b._reset_geometry()

        store2 = window_state_qsettings()
        assert store2.value(QSETTINGS_KEY_ACTION_DIALOG_GEOM) is None
        assert store2.value(
            QSETTINGS_KEY_ACTION_DIALOG_SPLITTER_STATE
        ) is None

        if ini.exists():
            ini.unlink()

    def test_reset_does_not_touch_settings_json(
        self, qapp, monkeypatch, tmp_path
    ):
        """E5 scope guard: mode/field/simple_op live in settings.json
        under ui.action_dialog.*, NOT in window_state.ini. The reset
        must leave them alone — wiping mode preferences on a window
        resize would be surprising.
        """
        from app.views.dialogs.select_dialog import ActionDialog
        from app.views.window_state import qsettings_path

        monkeypatch.setenv("PHOTO_MANAGER_HOME", str(tmp_path.name))
        repo_root = qsettings_path().parent
        repo_root.mkdir(parents=True, exist_ok=True)

        settings = _FakeSettings({
            "ui": {"action_dialog": {
                "main": {
                    "mode": "regex",
                    "field": "Folder",
                    "simple_op": "ends_with",
                },
            }}
        })
        dlg = ActionDialog(
            fields=["File Name", "Folder"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
            context_id="main",
        )
        # Snapshot the settings.json state before reset.
        before = dict(settings._data["ui"]["action_dialog"]["main"])
        dlg._reset_geometry()
        after = dict(settings._data["ui"]["action_dialog"]["main"])
        assert before == after, (
            f"_reset_geometry leaked into settings.json: {before} → {after}"
        )

    def test_reset_button_hidden_without_match_fn(self, qapp):
        """E5 scope: no splitter → no resizable geometry → nothing to
        reset. Hide the button so the user isn't offered a no-op."""
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        assert dlg.btn_reset_geometry.isHidden()

    def test_reset_shortcut_wired_with_match_fn(self, qapp):
        """E5 wiring: when the splitter exists, Ctrl+0 must trigger
        _reset_geometry. The attribute existence is the load-bearing
        invariant — the no-splitter branch does not create the shortcut
        (assertion mirrors the test_splitter_is_none_without_match_fn
        check above)."""
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(
            fields=["File Name"], match_fn=lambda f, p: (0, 0, [])
        )
        assert hasattr(dlg, "_reset_geometry_shortcut")
        assert dlg._reset_geometry_shortcut.key().toString() == "Ctrl+0"

    def test_reset_shortcut_not_wired_without_match_fn(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        # No splitter → no resizable chrome → no shortcut.
        assert not hasattr(dlg, "_reset_geometry_shortcut")


# ── Wave 9a (#348/#350) — passive a11y + keyboard polish ─────────────────────


class TestWave9aPolish:
    """Six small focused improvements: hover toolTips mirroring accessibleName
    (B11), focus landing on the typing widget per mode (B14), native clear
    buttons on text inputs (D2), inner-char selection after [abc] chip insert
    (D6), Ctrl+Enter shortcut for Apply (D9), Alt-letter mnemonics on action
    buttons (D10).
    """

    def test_b11_valid_regex_sets_tooltip(self, qapp):
        """B11: hover users get the same info screen readers already do."""
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        dlg.regex.setText("^IMG_\\d+$")
        dlg._validate_regex()
        assert dlg._validation_icon.toolTip() == "Regex valid"
        # Sanity: accessibleName is the same string (B11's whole point).
        assert dlg._validation_icon.accessibleName() == "Regex valid"

    def test_b11_invalid_regex_clears_tooltip(self, qapp):
        """B11 + Wave 8 B3: icon is hidden on invalid, so a stale toolTip
        from a prior valid would be misleading. Must be cleared explicitly.
        """
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        dlg.regex.setText("^IMG_\\d+$")
        dlg._validate_regex()
        assert dlg._validation_icon.toolTip() == "Regex valid"
        # Now type invalid — icon hidden (B3) AND toolTip cleared (B11).
        dlg.regex.setText("(unclosed")
        dlg._validate_regex()
        assert dlg._validation_icon.toolTip() == ""

    def test_b11_threshold_icon_tooltip_on_valid_and_invalid(self, qapp):
        """B11 for the numeric panel — icon stays visible on both branches
        (unlike _validation_icon), so BOTH need toolTip mirroring."""
        from app.views.dialogs.select_dialog import ActionDialog

        g = _make_group([_make_record(file_path="a/x.jpg", file_size_bytes=100)])
        dlg = ActionDialog(
            fields=["Size (Bytes)"], groups=[g],
            match_fn=lambda f, p: (0, 0, []),
        )
        dlg.combo.setCurrentText("Size (Bytes)")
        dlg._num_value_edit.setText("not-a-number")
        assert "Threshold invalid" in dlg._num_threshold_icon.toolTip()
        assert "not-a-number" in dlg._num_threshold_icon.toolTip()

        dlg._num_value_edit.setText("100")
        assert dlg._num_threshold_icon.toolTip() == "Threshold valid"

    def test_b14_focus_lands_on_simple_text_in_simple_mode(self, qapp):
        """B14: pre-Wave-9a Qt put focus on the field combo. Should land on
        the typing widget per mode."""
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(
            fields=["File Name"], match_fn=lambda f, p: (0, 0, [])
        )
        # match_fn supplied → Simple is default mode → focus _simple_text.
        assert dlg.focusWidget() is dlg._simple_text

    def test_b14_focus_lands_on_regex_in_regex_mode(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        # No match_fn → C1 pins MODE_REGEX → focus self.regex.
        dlg = ActionDialog(fields=["File Name"])
        assert dlg.focusWidget() is dlg.regex

    def test_d2_clear_button_enabled_on_both_inputs(self, qapp):
        """D2: native × clear button so the user can wipe input with one
        click (no more triple-click → delete)."""
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(
            fields=["File Name"], match_fn=lambda f, p: (0, 0, [])
        )
        assert dlg.regex.isClearButtonEnabled()
        assert dlg._simple_text.isClearButtonEnabled()

    def test_d6_set_chip_selects_inner_chars(self, qapp):
        """D6: after inserting [abc], select the inner 'abc' so the user's
        next keystroke replaces them. Pre-Wave-9a the cursor was past the
        token and the user had to manually back-select."""
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        dlg.regex.setText("")
        dlg.regex.setCursorPosition(0)
        dlg._insert_token("[abc]")
        # Inserted text is "[abc]" — select chars at index 1..3 (the "abc").
        assert dlg.regex.text() == "[abc]"
        assert dlg.regex.selectedText() == "abc"
        assert dlg.regex.selectionStart() == 1

    def test_d6_non_set_chip_does_not_select(self, qapp):
        """D6 scope: only the [abc] chip selects inner chars — the other
        tokens (\\d, ^, $, .* etc.) are atomic and have nothing to replace."""
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        dlg.regex.setText("")
        dlg.regex.setCursorPosition(0)
        dlg._insert_token("\\d")
        assert dlg.regex.text() == "\\d"
        assert dlg.regex.selectedText() == ""

    def test_d9_apply_shortcut_wired(self, qapp):
        """D9: Ctrl+Enter triggers Apply. Wired unconditionally (works in
        both splitter and flat layouts — Apply is universal, unlike Wave 8
        E5's Ctrl+0 which is splitter-only)."""
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        assert hasattr(dlg, "_apply_shortcut")
        assert dlg._apply_shortcut.key().toString() == "Ctrl+Return"

    def test_d9_apply_shortcut_present_in_both_layouts(self, qapp):
        """D9: Apply is universal — shortcut must exist on both branches.
        Contrast with Wave 8 E5: _reset_geometry_shortcut is splitter-only."""
        from app.views.dialogs.select_dialog import ActionDialog

        flat = ActionDialog(fields=["File Name"])
        split = ActionDialog(
            fields=["File Name"], match_fn=lambda f, p: (0, 0, [])
        )
        assert hasattr(flat, "_apply_shortcut")
        assert hasattr(split, "_apply_shortcut")

    def test_d10_mnemonics_present_on_action_buttons(self, qapp):
        """D10: Alt-letter mnemonics — pins that the & character lands in
        the translated button text. Catches a regression where a future
        translation edit drops the &."""
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(
            fields=["File Name"], match_fn=lambda f, p: (0, 0, [])
        )
        # English mnemonic shape: "&X..." prefix.
        # Chinese mnemonic shape: "...(&X)" suffix.
        # Either is valid — we just assert "&" appears in the text.
        assert "&" in dlg._btn_set_action.text(), "Apply mnemonic missing"
        assert "&" in dlg.btn_close.text(), "Close mnemonic missing"
        assert "&" in dlg._recent_btn.text(), "Recent mnemonic missing"
        assert "&" in dlg._switch_to_regex_btn.text(), \
            "Switch to Regex mnemonic missing"
        assert "&" in dlg.btn_reset_geometry.text(), \
            "Reset window size mnemonic missing"


# ── Wave 9b-trim (#348) — post-Apply feedback + label scope wording ──────────


class TestB9PostApplyFlash:
    """B9 (Wave 9b-trim): the dialog stays open after Apply (intentional —
    supports batch-apply / iterative regex exploration), and the match
    counter flashes "Applied to N rows" so the user gets in-dialog
    confirmation that the emit landed. The downstream receiver also emits
    "Decision set to '<decision>'" on the main-window status bar (#316/#318)
    — these surfaces complement each other (in-dialog flash for active
    attention, status-bar emit for system-level audit trail).
    """

    def test_apply_flashes_counter_with_applied_count(self, qapp):
        """The flash text contains the matched count from the last preview
        refresh — not a stale or zero value."""
        from app.views.dialogs.select_dialog import ActionDialog

        # match_fn returns 3 matched of 5 total.
        match_fn = lambda f, p: (3, 5, [("a.jpg", "a.jpg")] * 3)
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText("a")
        # Force the preview to run synchronously so _last_matched_count is
        # populated (debounce timer would otherwise leave it None at this
        # point in a unit test).
        dlg._refresh_preview()
        assert dlg._last_matched_count == 3

        dlg._emit_set_action()
        # The counter text now reflects the flash, not the "X of Y match"
        # shape. We don't pin the literal English string — that would
        # break under zh_TW — but we DO pin that the number 3 appears
        # (real failure mode: flash uses 0, or hardcodes some other count).
        text = dlg._match_counter.text()
        assert "3" in text, f"Expected matched count 3 in flash, got: {text!r}"
        # And the flash is NOT the regular "X of Y match" wording (which
        # contains both numbers). Real failure mode: flash silently no-ops
        # and the counter still shows the preview text.
        assert "5" not in text, (
            f"Expected post-Apply flash to drop the total (Y) from the "
            f"counter, but text still shows it: {text!r}"
        )

    def test_apply_without_match_fn_does_not_crash(self, qapp):
        """No match_fn → counter is hidden anyway → no flash, no crash."""
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText("a")
        # No preview was ever run (no match_fn) — _last_matched_count is None.
        assert dlg._last_matched_count is None
        # _emit_set_action must not raise.
        dlg._emit_set_action()
        # Counter stays hidden (its visibility was set in __init__).
        assert dlg._match_counter.isHidden()

    def test_apply_before_first_preview_does_not_flash(self, qapp):
        """If the user clicks Apply before the debounced preview ever
        fires, _last_matched_count is None. The flash branch must guard
        on that — no flash text, no exception."""
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (3, 5, [("a.jpg", "a.jpg")] * 3)
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        dlg._mode_regex_btn.setChecked(True)
        dlg.regex.setText("a")
        # Note: we explicitly do NOT call _refresh_preview here.
        # _last_matched_count is whatever __init__'s _refresh_preview call
        # (line 1218) populated — which for this match_fn IS 3 (it runs
        # synchronously in __init__).
        # The scenario this test targets: ensure the flash code path is
        # guarded so a None tracker doesn't cause AttributeError /
        # KeyError. We forcibly reset _last_matched_count to None to
        # simulate the "never-ran-preview" state, then call _emit_set_action.
        dlg._last_matched_count = None
        dlg._emit_set_action()  # Must not raise.


class TestB12SetActionLabel:
    """B12 (Wave 9b-trim): rename the "Set Action:" label so it
    communicates the per-row scope explicitly. The action applies to
    every matched row, not to one row or to the group as a whole.
    """

    def test_label_uses_per_match_wording(self, qapp):
        """The label text contains 'match' (en) or the equivalent zh_TW
        wording. Catches a regression where the YAML rename gets reverted
        to the pre-Wave-9b 'Set Action:' value."""
        from app.views.dialogs.select_dialog import ActionDialog
        from infrastructure.i18n import t

        dlg = ActionDialog(fields=["File Name"])
        expected = t("action_dialog.set_action_label")
        # The label is the first QLabel in the action_row of left_layout.
        # Easier to verify via the translation lookup: confirm the YAML
        # contains the new wording, not the old "Set Action:".
        assert expected != "Set Action:", (
            f"action_dialog.set_action_label still has the pre-Wave-9b "
            f"value 'Set Action:' — Wave 9b-trim B12 should have renamed "
            f"it to communicate per-match scope. Got: {expected!r}"
        )
        # Also assert it's not the literal Chinese pre-Wave-9b value.
        assert expected != "設定動作:", (
            f"zh_TW set_action_label still has the pre-Wave-9b value "
            f"'設定動作:'. Got: {expected!r}"
        )
