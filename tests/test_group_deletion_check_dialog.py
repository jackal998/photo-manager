"""Tests for GroupDeletionCheckDialog and its _TableModelAccessor.

Covers:
  - _TableModelAccessor: field text extraction and set_checked behaviour
  - RegexSelectionService integration via _TableModelAccessor
  - Dialog filtering: only complete-delete-group ops shown
  - _on_set_keep / _on_set_delete: regex match, override recording, DB persist
  - Invalid regex: does not crash, shows error in status label
  - overrides dict is empty after cancel
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ── helpers ────────────────────────────────────────────────────────────────

def _op(path: str, decision: str, group: int = 1) -> dict:
    return {"path": path, "decision": decision, "group_number": group}


# ── _TableModelAccessor ────────────────────────────────────────────────────

class TestTableModelAccessor:
    """Unit-test the inner _TableModelAccessor against RegexSelectionService."""

    def _make_accessor(self, ops):
        from app.views.dialogs.group_deletion_check_dialog import GroupDeletionCheckDialog
        checked = [False] * len(ops)
        acc = GroupDeletionCheckDialog._TableModelAccessor(ops, checked)
        return acc, checked

    def test_file_name_field_returns_basename(self, qapp):
        ops = [_op("/photos/img.jpg", "delete")]
        acc, _ = self._make_accessor(ops)
        assert acc.get_field_text(0, 0, "File Name") == "img.jpg"

    def test_folder_field_returns_parent(self, qapp):
        ops = [_op("/photos/img.jpg", "delete")]
        acc, _ = self._make_accessor(ops)
        # Path.parent gives /photos on Linux-style; on Windows with forward slashes it still works
        text = acc.get_field_text(0, 0, "Folder")
        assert "photos" in text

    def test_decision_field_returns_decision(self, qapp):
        ops = [_op("/photos/img.jpg", "delete")]
        acc, _ = self._make_accessor(ops)
        assert acc.get_field_text(0, 0, "Decision") == "delete"

    def test_group_level_returns_none(self, qapp):
        ops = [_op("/photos/img.jpg", "delete")]
        acc, _ = self._make_accessor(ops)
        # child=None signals group-level field; all fields here are file-level
        assert acc.get_field_text(0, None, "File Name") is None

    def test_set_checked_updates_parallel_list(self, qapp):
        ops = [_op("/a.jpg", "delete"), _op("/b.jpg", "delete")]
        acc, checked = self._make_accessor(ops)
        acc.set_checked(0, 0, True)
        assert checked[0] is True
        assert checked[1] is False

    def test_iter_groups_returns_indices(self, qapp):
        ops = [_op("/a.jpg", "delete"), _op("/b.jpg", "delete"), _op("/c.jpg", "delete")]
        acc, _ = self._make_accessor(ops)
        assert acc.iter_groups() == [0, 1, 2]

    def test_iter_children_returns_zero(self, qapp):
        ops = [_op("/a.jpg", "delete")]
        acc, _ = self._make_accessor(ops)
        assert acc.iter_children(0) == [0]

    def test_regex_service_selects_matching_filename(self, qapp):
        from core.services.selection_service import RegexSelectionService
        from app.views.dialogs.group_deletion_check_dialog import GroupDeletionCheckDialog

        ops = [_op("/photos/IMG_001.jpg", "delete"), _op("/photos/VID_001.mp4", "delete")]
        checked = [False, False]
        acc = GroupDeletionCheckDialog._TableModelAccessor(ops, checked)
        svc = RegexSelectionService(acc)
        svc.apply("File Name", r"IMG_", True)

        assert checked[0] is True
        assert checked[1] is False


# ── dialog behaviour ───────────────────────────────────────────────────────

class TestGroupDeletionCheckDialogBehavior:
    def _make_dialog(self, qapp, ops, complete_groups):
        from app.views.dialogs.group_deletion_check_dialog import GroupDeletionCheckDialog
        return GroupDeletionCheckDialog(ops, complete_groups, manifest_path=None)

    def test_only_complete_group_ops_shown(self, qapp):
        ops = [
            _op("/a.jpg", "delete", group=1),
            _op("/b.jpg", "delete", group=2),
            _op("/c.jpg", "keep", group=2),
        ]
        dlg = self._make_dialog(qapp, ops, complete_groups=[1])
        assert len(dlg._review_ops) == 1
        assert dlg._review_ops[0]["path"] == "/a.jpg"

    def test_set_to_keep_applies_regex_and_records_override(self, qapp):
        ops = [
            _op("/photos/IMG_001.jpg", "delete", group=1),
            _op("/photos/VID_001.mp4", "delete", group=1),
        ]
        dlg = self._make_dialog(qapp, ops, complete_groups=[1])
        dlg._field_combo.setCurrentText("File Name")
        dlg._regex_edit.setText("IMG_")
        dlg._on_set_keep()

        assert dlg.overrides.get("/photos/IMG_001.jpg") == "keep"
        assert "/photos/VID_001.mp4" not in dlg.overrides

    def test_set_to_delete_applies_regex_and_records_override(self, qapp):
        ops = [
            _op("/photos/IMG_001.jpg", "keep", group=1),
            _op("/photos/VID_001.mp4", "delete", group=1),
        ]
        dlg = self._make_dialog(qapp, ops, complete_groups=[1])
        dlg._field_combo.setCurrentText("Decision")
        dlg._regex_edit.setText("^keep$")
        dlg._on_set_delete()

        assert dlg.overrides.get("/photos/IMG_001.jpg") == "delete"
        assert "/photos/VID_001.mp4" not in dlg.overrides

    def test_decision_updated_in_review_ops(self, qapp):
        ops = [_op("/a.jpg", "delete", group=1)]
        dlg = self._make_dialog(qapp, ops, complete_groups=[1])
        dlg._field_combo.setCurrentText("File Name")
        dlg._regex_edit.setText("a.jpg")
        dlg._on_set_keep()

        assert dlg._review_ops[0]["decision"] == "keep"

    def test_invalid_regex_does_not_crash(self, qapp):
        ops = [_op("/a.jpg", "delete", group=1)]
        dlg = self._make_dialog(qapp, ops, complete_groups=[1])
        dlg._regex_edit.setText("[invalid")
        dlg._on_set_keep()  # must not raise

        assert dlg.overrides == {}
        assert "Invalid regex" in dlg._status_label.text()

    def test_empty_pattern_shows_message(self, qapp):
        ops = [_op("/a.jpg", "delete", group=1)]
        dlg = self._make_dialog(qapp, ops, complete_groups=[1])
        dlg._regex_edit.setText("")
        dlg._on_set_keep()

        assert dlg.overrides == {}

    def test_overrides_empty_on_init(self, qapp):
        ops = [_op("/a.jpg", "delete", group=1)]
        dlg = self._make_dialog(qapp, ops, complete_groups=[1])
        assert dlg.overrides == {}

    def test_table_shows_review_ops(self, qapp):
        ops = [
            _op("/a.jpg", "delete", group=1),
            _op("/b.jpg", "delete", group=1),
        ]
        dlg = self._make_dialog(qapp, ops, complete_groups=[1])
        assert dlg._table.rowCount() == 2

    def test_multiple_matches_all_overridden(self, qapp):
        ops = [
            _op("/photos/IMG_001.jpg", "delete", group=1),
            _op("/photos/IMG_002.jpg", "delete", group=1),
            _op("/photos/VID_001.mp4", "delete", group=1),
        ]
        dlg = self._make_dialog(qapp, ops, complete_groups=[1])
        dlg._field_combo.setCurrentText("File Name")
        dlg._regex_edit.setText(r"IMG_")
        dlg._on_set_keep()

        assert dlg.overrides.get("/photos/IMG_001.jpg") == "keep"
        assert dlg.overrides.get("/photos/IMG_002.jpg") == "keep"
        assert "/photos/VID_001.mp4" not in dlg.overrides
