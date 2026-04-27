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
        from app.views.constants import SETTABLE_DECISIONS
        keep_entry = next((t for t in SETTABLE_DECISIONS if "keep" in t[0].lower()), None)
        assert keep_entry is not None
        assert keep_entry[1] == ""

    def test_action_combo_count_matches_settable_decisions(self, qapp):
        from app.views.constants import SETTABLE_DECISIONS
        from app.views.dialogs.select_dialog import ActionDialog
        dlg = ActionDialog(fields=["File Name"])
        assert dlg._action_combo.count() == len(SETTABLE_DECISIONS)


class TestBackwardCompatAlias:
    def test_select_dialog_alias_works(self, qapp):
        from app.views.dialogs.select_dialog import SelectDialog, ActionDialog
        assert SelectDialog is ActionDialog
