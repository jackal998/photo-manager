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

        match_fn = MagicMock(return_value=(2, 3, ["a.jpg", "b.jpg"]))
        dlg = ActionDialog(fields=["File Name"], match_fn=match_fn)
        # Phase B introduced Beginner-mode default — switch to Regex
        # mode so dlg.regex is the active pattern source.
        dlg._mode_regex_btn.setChecked(True)

        match_fn.reset_mock()
        dlg.regex.setText("IMG")
        dlg._refresh_preview()

        match_fn.assert_called_with("File Name", "IMG")

    def test_preview_lists_samples(self, qapp):
        """Preview list shows the sample names returned by match_fn."""
        from app.views.dialogs.select_dialog import ActionDialog

        match_fn = lambda f, p: (3, 100, ["one.jpg", "two.jpg", "three.jpg"])
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
        match_fn = lambda f, p: (200, 4500, [f"x{i}.jpg" for i in range(50)])
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

        match_fn = lambda f, p: (5, 100, ["a", "b", "c", "d", "e"])
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

        assert dlg._validation_icon.text() == "✓"
        assert dlg._validation_error.isHidden()

    def test_invalid_regex_shows_x_and_error(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        dlg.regex.setText("(unclosed")
        dlg._validate_regex()

        assert dlg._validation_icon.text() == "✗"
        assert not dlg._validation_error.isHidden()
        # Localized prefix from action_dialog.invalid_regex.
        assert "Invalid regex" in dlg._validation_error.text() \
            or "正規式錯誤" in dlg._validation_error.text()

    def test_empty_regex_clears_validation_state(self, qapp):
        """Empty input is neutral — no icon, no error. Otherwise the
        dialog feels broken before the user has typed anything."""
        from app.views.dialogs.select_dialog import ActionDialog

        dlg = ActionDialog(fields=["File Name"])
        dlg.regex.setText("(")
        dlg._validate_regex()
        assert dlg._validation_icon.text() == "✗"

        dlg.regex.setText("")
        dlg._validate_regex()
        assert dlg._validation_icon.text() == ""
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


# ── Phase B: Beginner / Regex mode toggle, cheatsheet, recent ──────────────


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
        # Beginner widgets visible, regex widgets hidden.
        assert not dlg._simple_widget.isHidden()
        assert dlg._regex_widget.isHidden()

    def test_no_match_fn_pins_regex_mode(self, qapp):
        """Without a preview to back it, Beginner is meaningless — the
        dialog falls back to the original Regex-only flat layout."""
        from app.views.dialogs.select_dialog import ActionDialog, MODE_REGEX

        dlg = ActionDialog(fields=["File Name"])
        assert dlg._mode == MODE_REGEX
        # No mode toggle row was constructed in the flat layout.
        assert not hasattr(dlg, "_mode_simple_btn")

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
        # Default Beginner — toggle to Regex.
        dlg._mode_regex_btn.setChecked(True)
        assert settings.get("ui.action_dialog.mode") == "regex"
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
        """Empty Beginner input must NOT produce ``^$`` etc — we return
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
        assert recent == [r"^IMG_\d+$"]

    def test_recent_dedup_and_cap(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings({
            "ui": {"action_dialog": {"recent_patterns": [
                f"pattern_{i}" for i in range(10)
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
        assert recent[0] == "pattern_5"
        assert recent.count("pattern_5") == 1
        assert len(recent) == 10

    def test_apply_skips_empty_pattern(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings()
        match_fn = lambda f, p: (0, 0, [])
        dlg = ActionDialog(
            fields=["File Name"], match_fn=match_fn, settings=settings
        )
        # Beginner mode default — empty text → empty pattern. Don't
        # pollute the recent list with a no-op.
        dlg._simple_text.setText("")
        dlg._btn_set_action.click()
        assert settings.get("ui.action_dialog.recent_patterns", []) == []

    def test_clear_recent(self, qapp):
        from app.views.dialogs.select_dialog import ActionDialog

        settings = _FakeSettings({
            "ui": {"action_dialog": {"recent_patterns": ["a", "b", "c"]}}
        })
        dlg = ActionDialog(
            fields=["File Name"],
            match_fn=lambda f, p: (0, 0, []),
            settings=settings,
        )
        dlg._clear_recent_patterns()
        assert dlg._recent_patterns == []
        assert settings.get("ui.action_dialog.recent_patterns") == []


class TestMatchHighlightDelegate:
    def test_match_span_stored_on_preview_items(self, qapp):
        """Each row that matches should carry its (start, end) span on
        Qt.UserRole — that's what the delegate paints from."""
        from app.views.dialogs.select_dialog import ActionDialog
        from PySide6.QtCore import Qt

        # match_fn returns 2 samples both containing 'IMG'.
        match_fn = lambda f, p: (2, 100, ["IMG_001.jpg", "before_IMG_after.jpg"])
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
