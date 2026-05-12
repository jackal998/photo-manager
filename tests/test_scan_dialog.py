"""Tests for app/views/dialogs/scan_dialog.py — _auto_label and _SourceListWidget."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

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
# Advanced settings collapse (photo-manager#163)
# ---------------------------------------------------------------------------

class TestAdvancedSettingsCollapse:
    """The Grouping-Parameters group (pHash threshold + mean-color gate)
    is rendered as a checkable ``QGroupBox`` collapsed by default. State
    persists across dialog open via ``ui.scan_dialog.advanced_expanded``.
    """

    def _make_settings_file(self, tmp_path: Path, data: dict) -> Path:
        p = tmp_path / "settings.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_groupbox_is_checkable(self, qapp, tmp_path):
        """``setCheckable(True)`` is what makes Qt render the title with
        a built-in toggle checkbox and hide children when unchecked."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {"sources": {}})
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)
        assert dlg._params_group.isCheckable() is True

    def test_default_collapsed_with_no_setting(self, qapp, tmp_path):
        """First-run UX: open scan dialog with no prior settings → advanced
        is collapsed. The 95% case never needs to see these sliders."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {"sources": {}})
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)
        assert dlg._params_group.isChecked() is False

    def test_expanded_state_loaded_from_settings(self, qapp, tmp_path):
        """A user who left advanced expanded sees it expanded on next open."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {
            "sources": {},
            "ui": {"scan_dialog": {"advanced_expanded": True}},
        })
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)
        assert dlg._params_group.isChecked() is True

    def test_collapsed_state_loaded_from_settings(self, qapp, tmp_path):
        """A user who collapsed it explicitly stays collapsed across opens."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {
            "sources": {},
            "ui": {"scan_dialog": {"advanced_expanded": False}},
        })
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)
        assert dlg._params_group.isChecked() is False

    def test_toggle_persists_immediately(self, qapp, tmp_path):
        """Clicking the disclosure toggles the state AND writes to
        settings.json on every change — no waiting for ``_save_to_settings``
        which only fires on scan start."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {"sources": {}})
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)

        # Toggle to expanded — Qt emits the `toggled` signal automatically.
        dlg._params_group.setChecked(True)
        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        assert saved["ui"]["scan_dialog"]["advanced_expanded"] is True

        # Toggle back to collapsed.
        dlg._params_group.setChecked(False)
        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        assert saved["ui"]["scan_dialog"]["advanced_expanded"] is False

    def test_sliders_still_default_values_when_loaded(self, qapp, tmp_path):
        """Collapsing the group must NOT change the threshold values
        themselves — they stay at their canonical defaults regardless
        of the disclosure state. (Persisting the threshold values
        themselves is a separate concern; out of #163 scope.)"""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {"sources": {}})
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)
        assert dlg._phash_slider.value() == 10
        assert dlg._color_slider.value() == 30

    def test_content_actually_hidden_when_collapsed(self, qapp, tmp_path):
        """Qt's checkable QGroupBox by default DISABLES children when
        unchecked — they stay visible (just greyed) and keep occupying
        vertical space. Wrap the content in a child QWidget whose
        visibility tracks the checked state so collapse genuinely
        reclaims space."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {"sources": {}})
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)
        # The wrapper QWidget exists and is hidden in the default state
        assert hasattr(dlg, "_params_content")
        assert dlg._params_content.isVisibleTo(dlg) is False

    def test_content_visible_when_expanded(self, qapp, tmp_path):
        """Expanding the groupbox shows the wrapper QWidget (and thereby
        the sliders inside it)."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {
            "sources": {},
            "ui": {"scan_dialog": {"advanced_expanded": True}},
        })
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)
        assert dlg._params_content.isVisibleTo(dlg) is True

    def test_toggle_flips_content_visibility(self, qapp, tmp_path):
        """The toggle handler must flip ``_params_content.setVisible``
        AND save the state — both happen on every click."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {"sources": {}})
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)

        assert dlg._params_content.isVisibleTo(dlg) is False
        dlg._params_group.setChecked(True)
        assert dlg._params_content.isVisibleTo(dlg) is True
        dlg._params_group.setChecked(False)
        assert dlg._params_content.isVisibleTo(dlg) is False


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


# ---------------------------------------------------------------------------
# Layout / picker UX (#50 + #40)
# ---------------------------------------------------------------------------


class TestSourceListLayout:
    def test_source_list_has_minimum_height(self, qapp):
        """#50 — source list table must reserve room for ~6 rows minimum."""
        widget = _SourceListWidget()
        assert widget._table.minimumHeight() >= 180


# ---------------------------------------------------------------------------
# Post-scan terminal-state focus (#86)
# ---------------------------------------------------------------------------


class TestPostScanCloseFocus:
    """#86 — after a terminal scan event that produces no manifest (empty
    input or hard failure), focus must move to the Close button so the
    user has a visible "way out" cue rather than a UI that looks identical
    to pre-scan state. Uses ``focusWidget()`` rather than ``hasFocus()``
    because the latter requires the window to be active, while
    ``focusWidget()`` tracks the last ``setFocus`` target on a top-level
    widget regardless of visibility — fine for a unit test.
    """

    def _make_dialog(self, qapp, tmp_path):
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = tmp_path / "settings.json"
        settings_path.write_text('{"sources":{}}', encoding="utf-8")
        return ScanDialog(JsonSettings(settings_path))

    def test_completed_empty_focuses_close_button(self, qapp, tmp_path):
        dlg = self._make_dialog(qapp, tmp_path)
        dlg._on_completed_empty()
        assert dlg.focusWidget() is dlg._btn_close

    def test_completed_empty_re_enables_start_scan(self, qapp, tmp_path):
        """Focus on Close must not foreclose retry — Start Scan stays
        enabled so the user can fix the source list and re-scan."""
        dlg = self._make_dialog(qapp, tmp_path)
        dlg._btn_scan.setEnabled(False)
        dlg._on_completed_empty()
        assert dlg._btn_scan.isEnabled()

    def test_failed_focuses_close_button(self, qapp, tmp_path, monkeypatch):
        """Same focus cue after a real scan failure. Modal blocks in a real
        run; no-op it for the test so the synchronous flow continues."""
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)

        dlg = self._make_dialog(qapp, tmp_path)
        dlg._on_failed("simulated pipeline error")
        assert dlg.focusWidget() is dlg._btn_close

    def test_failed_re_enables_start_scan(self, qapp, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)

        dlg = self._make_dialog(qapp, tmp_path)
        dlg._btn_scan.setEnabled(False)
        dlg._on_failed("simulated pipeline error")
        assert dlg._btn_scan.isEnabled()


class TestStartScanShouldProceed:
    """#142 — the ``should_proceed`` callback gates the scan worker launch.

    MainWindow injects a callback that returns False when the loaded
    manifest has pending decisions and the user clicks No on the
    confirmation prompt. ScanDialog must respect that and abort before
    ``ScanWorker.start()`` is reached.

    These tests verify the gating contract independent of MainWindow's
    actual prompt logic — they pass a deterministic lambda for ``should_proceed``.
    """

    def _make_dialog(self, qapp, tmp_path, should_proceed):
        from app.views.dialogs.scan_dialog import ScanDialog, _SourceEntry
        from infrastructure.settings import JsonSettings

        settings_path = tmp_path / "settings.json"
        settings_path.write_text('{"sources":{}}', encoding="utf-8")
        dlg = ScanDialog(
            JsonSettings(settings_path), should_proceed=should_proceed
        )
        # Satisfy input validation so we reach the gate.
        dlg._source_list.set_entries(
            [_SourceEntry(path=str(tmp_path), recursive=True)]
        )
        dlg._output_field.setText(str(tmp_path / "out.sqlite"))
        return dlg

    def test_default_should_proceed_is_always_true(self, qapp, tmp_path):
        """Constructor default — caller didn't pass should_proceed — must
        not block any existing flow."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = tmp_path / "settings.json"
        settings_path.write_text('{"sources":{}}', encoding="utf-8")
        dlg = ScanDialog(JsonSettings(settings_path))
        # Default callback is the always-True lambda; calling it must not raise.
        assert dlg._should_proceed() is True

    def test_should_proceed_false_aborts_before_worker_start(
        self, qapp, tmp_path, monkeypatch
    ):
        """When the gate returns False (user clicked No on the prompt),
        ScanWorker must not be instantiated at all."""
        worker_constructed = []

        def fake_worker_init(self, *a, **kw):
            worker_constructed.append((a, kw))
            # Block actual subprocess; let __init__ "succeed" so we'd see
            # the failure if the gate didn't fire.
            self.start = lambda: None

        from app.views.dialogs import scan_dialog as sd
        monkeypatch.setattr(sd.ScanWorker, "__init__", fake_worker_init)

        called = []
        dlg = self._make_dialog(
            qapp, tmp_path, should_proceed=lambda: (called.append(True), False)[1]
        )
        dlg._start_scan()

        assert called == [True], "should_proceed must be called"
        assert worker_constructed == [], (
            "ScanWorker must not be constructed when should_proceed is False"
        )

    def test_should_proceed_true_proceeds_to_worker_start(
        self, qapp, tmp_path, monkeypatch
    ):
        """When the gate returns True (user clicked Yes or no manifest
        loaded), the scan worker is constructed and started as before."""
        worker_started = []

        class FakeWorker:
            def __init__(self, *a, **kw):
                self._args = (a, kw)
                # Provide the signal attributes _start_scan connects to.
                self.progress = MagicMock()
                self.failed = MagicMock()
                self.finished = MagicMock()
                self.completed_empty = MagicMock()

            def start(self):
                worker_started.append(True)

        from app.views.dialogs import scan_dialog as sd
        monkeypatch.setattr(sd, "ScanWorker", FakeWorker)

        dlg = self._make_dialog(
            qapp, tmp_path, should_proceed=lambda: True
        )
        dlg._start_scan()

        assert worker_started == [True], (
            "ScanWorker.start must be called when should_proceed is True"
        )

    def test_validation_failures_short_circuit_before_should_proceed(
        self, qapp, tmp_path, monkeypatch
    ):
        """If the source list is empty or output path missing, the
        validation message fires FIRST and should_proceed is never called.
        This keeps the prompt from interrupting users who haven't even
        configured a valid scan yet."""
        from PySide6.QtWidgets import QMessageBox
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        # Suppress the warning dialog popup.
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

        called = []
        settings_path = tmp_path / "settings.json"
        settings_path.write_text('{"sources":{}}', encoding="utf-8")
        dlg = ScanDialog(
            JsonSettings(settings_path),
            should_proceed=lambda: (called.append(True), True)[1],
        )
        # Don't add any source entries — should fail the "no sources"
        # validation BEFORE reaching should_proceed.
        dlg._start_scan()

        assert called == [], "should_proceed must not fire on validation failure"


class TestPathFieldEntry:
    """#40 — typing/pasting an absolute path should add it to the source list."""

    def test_empty_path_is_silently_ignored(self, qapp):
        from app.views.dialogs.scan_dialog import _FolderTreePanel

        panel = _FolderTreePanel()
        emitted: list[str] = []
        panel.folder_requested.connect(emitted.append)

        panel._path_field.setText("")
        panel._on_add_typed()
        panel._path_field.setText("   ")
        panel._on_add_typed()
        assert emitted == []

    def test_nonexistent_path_surfaces_inline_error(self, qapp, tmp_path):
        """#144 — previously a silent no-op (the bug). Now must surface
        a visible error label below the path row so the user can tell
        ``+ Add`` did something."""
        from app.views.dialogs.scan_dialog import _FolderTreePanel

        panel = _FolderTreePanel()
        emitted: list[str] = []
        panel.folder_requested.connect(emitted.append)

        bad = str(tmp_path / "definitely_does_not_exist")
        panel._path_field.setText(bad)
        panel._on_add_typed()

        # No emit (so the source list is unchanged) AND error label is
        # visible carrying the offending path so the user sees what was
        # rejected — both halves matter; either alone fails to fix #144.
        assert emitted == []
        assert panel._path_error.isVisibleTo(panel) is True
        assert bad in panel._path_error.text()

    def test_typing_clears_existing_error(self, qapp, tmp_path):
        """Error label is stale the moment the user edits the field —
        the message refers to the previous value, not the new one."""
        from app.views.dialogs.scan_dialog import _FolderTreePanel

        panel = _FolderTreePanel()
        panel._path_field.setText(str(tmp_path / "definitely_does_not_exist"))
        panel._on_add_typed()
        assert panel._path_error.isVisibleTo(panel) is True

        # Any keystroke / setText drop reflects "the user is editing"
        # — kill the now-stale error.
        panel._path_field.setText("")
        assert panel._path_error.isVisibleTo(panel) is False
        assert panel._path_error.text() == ""

    def test_valid_path_emits_folder_requested_and_clears_field(self, qapp, tmp_path):
        from app.views.dialogs.scan_dialog import _FolderTreePanel

        real_dir = tmp_path / "real"
        real_dir.mkdir()

        panel = _FolderTreePanel()
        emitted: list[str] = []
        panel.folder_requested.connect(emitted.append)

        panel._path_field.setText(str(real_dir))
        panel._on_add_typed()

        assert emitted == [str(real_dir)]
        assert panel._path_field.text() == ""

    def test_successful_add_clears_prior_error(self, qapp, tmp_path):
        """After a failed add, fixing the path and clicking ``+ Add``
        again must remove the error — otherwise the dialog claims a
        problem the user already resolved."""
        from app.views.dialogs.scan_dialog import _FolderTreePanel

        real_dir = tmp_path / "real"
        real_dir.mkdir()

        panel = _FolderTreePanel()
        emitted: list[str] = []
        panel.folder_requested.connect(emitted.append)

        # 1) Bad path surfaces the error.
        panel._path_field.setText(str(tmp_path / "nope"))
        panel._on_add_typed()
        assert panel._path_error.isVisibleTo(panel) is True

        # 2) Replacing with a real folder and adding clears the error.
        # ``setText`` fires textChanged → _clear_path_error, but we also
        # want to confirm the success path itself is idempotent if the
        # error somehow survived (paranoia is cheap; tests are about
        # documenting the contract).
        panel._path_field.setText(str(real_dir))
        panel._on_add_typed()

        assert emitted == [str(real_dir)]
        assert panel._path_error.isVisibleTo(panel) is False

    def test_quoted_path_is_stripped(self, qapp, tmp_path):
        """Windows users often paste paths from Explorer with surrounding quotes."""
        from app.views.dialogs.scan_dialog import _FolderTreePanel

        real_dir = tmp_path / "with spaces"
        real_dir.mkdir()

        panel = _FolderTreePanel()
        emitted: list[str] = []
        panel.folder_requested.connect(emitted.append)

        panel._path_field.setText(f'"{real_dir}"')
        panel._on_add_typed()

        assert emitted == [str(real_dir)]
