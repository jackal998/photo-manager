"""Tests for app/views/dialogs/scan_dialog.py — _auto_label and _SourceListWidget."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.views.dialogs.scan_dialog import _SourceEntry, _SourceListWidget, _auto_label


# ---------------------------------------------------------------------------
# _auto_label
# ---------------------------------------------------------------------------

class TestAutoLabel:
    def test_uses_name_when_not_in_use(self):
        assert _auto_label("Photos", set()) == "Photos"

    def test_appends_2_on_first_collision(self):
        assert _auto_label("Photos", {"Photos"}) == "Photos_2"

    def test_appends_3_on_second_collision(self):
        assert _auto_label("Photos", {"Photos", "Photos_2"}) == "Photos_3"

    def test_skips_occupied_numbers(self):
        existing = {"Photos", "Photos_2", "Photos_3"}
        assert _auto_label("Photos", existing) == "Photos_4"

    def test_empty_existing_set(self):
        assert _auto_label("Downloads", set()) == "Downloads"

    def test_does_not_modify_existing_set(self):
        existing: set[str] = {"Foo"}
        _auto_label("Foo", existing)
        assert existing == {"Foo"}


# ---------------------------------------------------------------------------
# _SourceListWidget
# ---------------------------------------------------------------------------

class TestSourceListWidget:
    def test_starts_empty(self, qapp):
        widget = _SourceListWidget()
        assert widget.entries() == []

    def test_add_entry(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a")
        entries = widget.entries()
        assert len(entries) == 1
        assert entries[0].path == "/path/a"
        assert entries[0].recursive is True

    def test_add_entry_with_recursive_false(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/b", recursive=False)
        assert widget.entries()[0].recursive is False

    def test_duplicate_path_silently_ignored(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a")
        widget.add_entry("/path/a")
        assert len(widget.entries()) == 1

    def test_add_multiple_entries(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a")
        widget.add_entry("/path/b")
        widget.add_entry("/path/c")
        assert len(widget.entries()) == 3

    def test_clear_removes_all(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a")
        widget.add_entry("/path/b")
        widget.clear()
        assert widget.entries() == []

    def test_remove_first_entry(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a")
        widget.add_entry("/path/b")
        widget._remove(0)
        entries = widget.entries()
        assert len(entries) == 1
        assert entries[0].path == "/path/b"

    def test_remove_last_entry(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a")
        widget.add_entry("/path/b")
        widget._remove(1)
        assert widget.entries()[0].path == "/path/a"

    def test_remove_out_of_range_is_noop(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a")
        widget._remove(5)
        assert len(widget.entries()) == 1

    def test_move_up(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a")
        widget.add_entry("/path/b")
        widget._move(1, -1)   # move b up
        assert widget.entries()[0].path == "/path/b"
        assert widget.entries()[1].path == "/path/a"

    def test_move_down(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a")
        widget.add_entry("/path/b")
        widget._move(0, +1)   # move a down
        assert widget.entries()[0].path == "/path/b"
        assert widget.entries()[1].path == "/path/a"

    def test_move_up_at_top_is_noop(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a")
        widget.add_entry("/path/b")
        widget._move(0, -1)   # already at top
        assert widget.entries()[0].path == "/path/a"

    def test_move_down_at_bottom_is_noop(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a")
        widget.add_entry("/path/b")
        widget._move(1, +1)   # already at bottom
        assert widget.entries()[1].path == "/path/b"

    def test_set_entries_replaces_list(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/old/path")
        widget.set_entries([
            _SourceEntry(path="/new/a", recursive=True),
            _SourceEntry(path="/new/b", recursive=False),
        ])
        entries = widget.entries()
        assert len(entries) == 2
        assert entries[0].path == "/new/a"
        assert entries[1].path == "/new/b"
        assert entries[1].recursive is False

    def test_changed_signal_on_add(self, qapp):
        widget = _SourceListWidget()
        received: list[None] = []
        widget.changed.connect(lambda: received.append(None))
        widget.add_entry("/path/x")
        assert len(received) == 1

    def test_changed_signal_on_remove(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/x")
        received: list[None] = []
        widget.changed.connect(lambda: received.append(None))
        widget._remove(0)
        assert len(received) == 1

    def test_changed_signal_on_clear(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/x")
        received: list[None] = []
        widget.changed.connect(lambda: received.append(None))
        widget.clear()
        assert len(received) == 1

    def test_changed_signal_on_move(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a")
        widget.add_entry("/path/b")
        received: list[None] = []
        widget.changed.connect(lambda: received.append(None))
        widget._move(0, +1)
        assert len(received) == 1

    def test_on_recursive_changed_true(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a", recursive=False)
        widget._on_recursive_changed(0, 2)   # 2 = Qt.CheckState.Checked value
        assert widget.entries()[0].recursive is True

    def test_on_recursive_changed_false(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a", recursive=True)
        widget._on_recursive_changed(0, 0)   # 0 = unchecked
        assert widget.entries()[0].recursive is False

    def test_on_recursive_changed_out_of_range_is_noop(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/path/a")
        widget._on_recursive_changed(99, 2)  # no-op
        assert widget.entries()[0].recursive is True


# ---------------------------------------------------------------------------
# ScanDialog — settings loading / saving (tested without showing the dialog)
# ---------------------------------------------------------------------------

class TestScanDialogSettings:
    def _make_settings_file(self, tmp_path: Path, data: dict) -> Path:
        """Write JSON to a temp settings file and return the path."""
        p = tmp_path / "settings.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_load_new_format(self, qapp, tmp_path):
        """sources.list entries are loaded into the source list."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {
            "sources": {
                "list": [
                    {"path": "/foo/bar", "recursive": True},
                    {"path": "/baz/qux", "recursive": False},
                ],
                "output": "/out/manifest.sqlite",
            }
        })
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)

        entries = dlg._source_list.entries()
        assert len(entries) == 2
        assert entries[0].path == "/foo/bar"
        assert entries[0].recursive is True
        assert entries[1].path == "/baz/qux"
        assert entries[1].recursive is False
        assert dlg._output_field.text() == "/out/manifest.sqlite"

    def test_migrate_legacy_format(self, qapp, tmp_path):
        """Old iphone/takeout/jdrive keys are migrated to entries."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {
            "sources": {
                "iphone": "/nas/iphone",
                "takeout": "/nas/takeout",
                "jdrive": "/nas/jdrive",
                "output": "migration_manifest.sqlite",
            }
        })
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)

        entries = dlg._source_list.entries()
        assert len(entries) == 3
        paths = [e.path for e in entries]
        assert "/nas/iphone" in paths
        assert "/nas/takeout" in paths
        assert "/nas/jdrive" in paths
        # All migrated entries default to recursive=True
        assert all(e.recursive for e in entries)

    def test_migrate_skips_empty_legacy_keys(self, qapp, tmp_path):
        """Empty legacy source keys are not added as entries."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {
            "sources": {
                "iphone": "/nas/iphone",
                "takeout": "",           # empty — should be skipped
                "jdrive": "",            # empty — should be skipped
                "output": "manifest.sqlite",
            }
        })
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)

        entries = dlg._source_list.entries()
        assert len(entries) == 1
        assert entries[0].path == "/nas/iphone"

    def test_save_writes_list_format(self, qapp, tmp_path):
        """_save_to_settings writes the new sources.list format."""
        from app.views.dialogs.scan_dialog import ScanDialog, _SourceEntry
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {"sources": {}})
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)

        dlg._source_list.set_entries([
            _SourceEntry(path="/new/a", recursive=True),
            _SourceEntry(path="/new/b", recursive=False),
        ])
        dlg._output_field.setText("/out/manifest.sqlite")
        dlg._save_to_settings()

        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        assert saved["sources"]["list"] == [
            {"path": "/new/a", "recursive": True},
            {"path": "/new/b", "recursive": False},
        ]
        assert saved["sources"]["output"] == "/out/manifest.sqlite"


# ---------------------------------------------------------------------------
# _build_sources (label auto-generation + source_priority ordering)
# ---------------------------------------------------------------------------

class TestBuildSources:
    def test_labels_derived_from_folder_name(self, qapp, tmp_path):
        from app.views.dialogs.scan_dialog import ScanDialog, _SourceEntry
        from infrastructure.settings import JsonSettings

        settings_path = tmp_path / "settings.json"
        settings_path.write_text('{"sources":{}}', encoding="utf-8")
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)

        dlg._source_list.set_entries([
            _SourceEntry(path="/nas/Takeout", recursive=True),
            _SourceEntry(path="/nas/Photos", recursive=False),
        ])
        sources, recursive_map, source_priority = dlg._build_sources()

        assert "Takeout" in sources
        assert "Photos" in sources
        assert recursive_map["Takeout"] is True
        assert recursive_map["Photos"] is False

    def test_source_priority_matches_list_order(self, qapp, tmp_path):
        from app.views.dialogs.scan_dialog import ScanDialog, _SourceEntry
        from infrastructure.settings import JsonSettings

        settings_path = tmp_path / "settings.json"
        settings_path.write_text('{"sources":{}}', encoding="utf-8")
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)

        dlg._source_list.set_entries([
            _SourceEntry(path="/nas/First"),
            _SourceEntry(path="/nas/Second"),
            _SourceEntry(path="/nas/Third"),
        ])
        _, _, source_priority = dlg._build_sources()

        # Keys may differ (from folder names), but priority order must be 0, 1, 2
        priorities = sorted(source_priority.values())
        assert priorities == [0, 1, 2]
        # The folder named "First" must have the lowest priority number
        first_label = "First"
        second_label = "Second"
        third_label = "Third"
        assert source_priority[first_label] < source_priority[second_label]
        assert source_priority[second_label] < source_priority[third_label]

    def test_duplicate_folder_names_get_unique_labels(self, qapp, tmp_path):
        from app.views.dialogs.scan_dialog import ScanDialog, _SourceEntry
        from infrastructure.settings import JsonSettings

        settings_path = tmp_path / "settings.json"
        settings_path.write_text('{"sources":{}}', encoding="utf-8")
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)

        dlg._source_list.set_entries([
            _SourceEntry(path="/drive1/Photos"),
            _SourceEntry(path="/drive2/Photos"),   # same folder name
        ])
        sources, _, _ = dlg._build_sources()

        assert len(sources) == 2
        assert "Photos" in sources
        assert "Photos_2" in sources
