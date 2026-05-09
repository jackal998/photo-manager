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

    def test_action_combo_count_matches_settable_decisions_with_remove(self, qapp):
        """The regex dropdown surfaces all three actions:
        delete, keep, and the new "remove from list" sentinel.
        That's settable_decisions(include_remove=True), not the default."""
        from app.views.constants import settable_decisions
        SETTABLE_DECISIONS_WITH_REMOVE = settable_decisions(include_remove=True)
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["File Name"])
        assert dlg._action_combo.count() == len(SETTABLE_DECISIONS_WITH_REMOVE)

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
        match_fn.reset_mock()

        dlg.regex.setText("(")
        # Validation runs synchronously; should mark counter as "—".
        # Then we manually fire the preview timer's slot to confirm the
        # closure short-circuits on invalid regex.
        dlg._refresh_preview()

        assert match_fn.call_count == 0
        assert dlg._match_counter.text() == "—"


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
