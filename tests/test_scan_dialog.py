"""Tests for app/views/dialogs/scan_dialog.py — _auto_label and _SourceListWidget."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.views.dialogs.scan_dialog import _SourceEntry, _SourceListWidget, _auto_label


@pytest.fixture(autouse=True)
def _dispose_orphan_widgets():
    """Dispose this module's bare top-level widgets after each test (#507).

    The ``_SourceListWidget`` / ``ScanDialog`` tests in this file construct
    *unparented* top-level widgets and let them fall out of scope without an
    explicit ``close()``/parent. Their child ``QTableWidget`` cell widgets
    carry live signal connections that Qt tears down only via *deferred*
    delete; nothing drains that queue until a *later, unrelated* test calls
    ``processEvents()``, which then runs the cell-widget destructors against
    an already-freed Python receiver — a use-after-free that aborted ~1/3 of
    full-suite runs on Windows/3.12, surfacing far downstream at
    ``test_select_dialog::test_both_sections_visible_with_match_fn`` (#507;
    the residual orphan #495 did not reach). In production these widgets are
    always parented to their dialog and destroyed synchronously, so the leak
    is test-only.

    The fix is local Qt test hygiene: after each test in *this* module,
    ``deleteLater`` every unparented top-level widget and drain the
    deferred-delete queue right here, where an event loop exists — so each
    orphan's C++ tree is torn down inside the test that created it. Scoped to
    this file (not a global ``conftest`` fixture) so a future widget leak in
    another module still surfaces instead of being silently swept, and it is
    NOT a blanket ``gc.collect()`` + sweep (which merely relocates the crash).
    """
    yield
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        if widget.parent() is None:
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


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


class TestSourceListDisplaySort:
    """#213 — the table displays entries sorted alphabetically by path
    (case-insensitive), regardless of insertion order. The underlying
    ``self._entries`` list stays insertion-ordered so add_entry's
    duplicate-path check keeps working.
    """

    def _table_paths(self, widget: _SourceListWidget) -> list[str]:
        """Read the path column from the QTableWidget in display order."""
        table = widget._table
        return [
            (table.item(row, 0).text() if table.item(row, 0) else "")
            for row in range(table.rowCount())
        ]

    def test_display_sorted_by_path_ascending(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/zeta")
        widget.add_entry("/alpha")
        widget.add_entry("/mu")
        assert self._table_paths(widget) == ["/alpha", "/mu", "/zeta"]

    def test_display_sort_is_case_insensitive(self, qapp):
        widget = _SourceListWidget()
        widget.add_entry("/Zebra")
        widget.add_entry("/apple")
        widget.add_entry("/Banana")
        # Case-insensitive: apple < Banana < Zebra
        assert self._table_paths(widget) == ["/apple", "/Banana", "/Zebra"]

    def test_entries_list_keeps_insertion_order(self, qapp):
        """``entries()`` returns the underlying insertion-ordered list —
        the sort only applies to the visible table. This is what lets
        ``add_entry`` keep doing a cheap duplicate check on the list."""
        widget = _SourceListWidget()
        widget.add_entry("/zeta")
        widget.add_entry("/alpha")
        assert [e.path for e in widget.entries()] == ["/zeta", "/alpha"]

    def test_remove_button_targets_correct_entry_after_sort(self, qapp):
        """The remove-button lambda must capture the entry's index in
        ``self._entries`` (not the display row), otherwise sorting would
        cause a click on row 0 to delete the wrong entry."""
        widget = _SourceListWidget()
        widget.add_entry("/zeta")      # entries[0], displays at row 2
        widget.add_entry("/alpha")     # entries[1], displays at row 0
        widget.add_entry("/mu")        # entries[2], displays at row 1

        # Simulate clicking × on the top display row (/alpha).
        # display_row 0 corresponds to entries[1]; the callback must
        # remove entries[1], leaving /zeta and /mu.
        widget._remove(1)
        remaining = sorted(e.path for e in widget.entries())
        assert remaining == ["/mu", "/zeta"]

    def test_recursive_toggle_targets_correct_entry_after_sort(self, qapp):
        """Same lambda-capture invariant as the remove button — toggling
        row 0 in the display (which is the alphabetically-first entry)
        must update that entry, not the one at entries[0]."""
        widget = _SourceListWidget()
        widget.add_entry("/zeta", recursive=True)    # entries[0]
        widget.add_entry("/alpha", recursive=True)   # entries[1] — top of display

        # Find /alpha's index in entries (it's 1) and toggle off.
        alpha_idx = next(
            i for i, e in enumerate(widget.entries()) if e.path == "/alpha"
        )
        widget._on_recursive_changed(alpha_idx, 0)

        by_path = {e.path: e.recursive for e in widget.entries()}
        assert by_path["/alpha"] is False
        assert by_path["/zeta"] is True

    def test_table_has_three_columns(self, qapp):
        """#213 — the priority column and the ↑↓ reorder column are gone;
        only path, recursive checkbox, and × remove remain."""
        widget = _SourceListWidget()
        assert widget._table.columnCount() == 3


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
# #486-PR3c — hash-pool calibration checkbox + modal state machine
# ---------------------------------------------------------------------------

class TestResolveHashPool:
    """The Advanced-settings re-calibrate checkbox + auto-mode modal flow,
    tested via _resolve_hash_pool (extracted from _start_scan so no worker
    thread is launched). The modal is monkeypatched — never shown live."""

    SOURCES = {"s": "/x"}
    RECMAP = {"s": True}

    def _dialog(self, tmp_path, data):
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        p = tmp_path / "settings.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return ScanDialog(JsonSettings(p))

    def _fp(self):
        import os
        from app.views.workers.scan_worker import hash_pool_fingerprint

        return hash_pool_fingerprint(self.SOURCES, self.RECMAP, os.cpu_count() or 4)

    def test_recalibrate_checked_forces_auto_and_unchecks(self, qapp, tmp_path):
        """Checked → force fresh measure (rates None), persist auto, clear box,
        set a fingerprint so the worker's emit gets cached."""
        dlg = self._dialog(tmp_path, {"sources": {}, "scan": {"hash_pool": "thread"}})
        dlg._recalibrate_check.setChecked(True)

        pool, rates = dlg._resolve_hash_pool(self.SOURCES, self.RECMAP)

        assert (pool, rates) == ("auto", None)
        assert dlg.settings.get("scan.hash_pool") == "auto"  # persisted
        assert dlg._recalibrate_check.isChecked() is False  # auto-unchecked
        assert dlg._hash_pool_fp == self._fp()

    def test_explicit_thread_used_directly_no_modal(self, qapp, tmp_path, monkeypatch):
        """thread/process override → returned as-is, no fingerprint, no modal."""
        dlg = self._dialog(tmp_path, {"sources": {}, "scan": {"hash_pool": "thread"}})
        monkeypatch.setattr(
            dlg, "_prompt_calibrate_or_thread",
            lambda: (_ for _ in ()).throw(AssertionError("modal must not fire")),
        )

        pool, rates = dlg._resolve_hash_pool(self.SOURCES, self.RECMAP)

        assert (pool, rates) == ("thread", None)
        assert dlg._hash_pool_fp is None

    def test_auto_cache_hit_reuses_rates(self, qapp, tmp_path, monkeypatch):
        """unchecked + auto + cache hit → cached rates returned, no modal."""
        rates = {"thread_per_file": 2.0, "process_per_file": 1.0, "spawn": 0.5}
        dlg = self._dialog(tmp_path, {
            "sources": {},
            "scan": {"hash_pool": "auto", "hash_pool_cache": {self._fp(): rates}},
        })
        monkeypatch.setattr(
            dlg, "_prompt_calibrate_or_thread",
            lambda: (_ for _ in ()).throw(AssertionError("no modal on cache hit")),
        )

        pool, got = dlg._resolve_hash_pool(self.SOURCES, self.RECMAP)

        assert (pool, got) == ("auto", rates)
        assert dlg._hash_pool_fp == self._fp()

    def test_auto_cache_miss_modal_calibrate(self, qapp, tmp_path, monkeypatch):
        """unchecked + auto + miss + user picks Calibrate → auto, rates None,
        fingerprint kept so the fresh measurement gets cached."""
        dlg = self._dialog(tmp_path, {"sources": {}, "scan": {"hash_pool": "auto"}})
        monkeypatch.setattr(dlg, "_prompt_calibrate_or_thread", lambda: True)

        pool, rates = dlg._resolve_hash_pool(self.SOURCES, self.RECMAP)

        assert (pool, rates) == ("auto", None)
        assert dlg._hash_pool_fp == self._fp()

    def test_auto_cache_miss_modal_thread(self, qapp, tmp_path, monkeypatch):
        """unchecked + auto + miss + user picks Use-thread → thread this run,
        no fingerprint (nothing cached), auto mode left intact for next time."""
        dlg = self._dialog(tmp_path, {"sources": {}, "scan": {"hash_pool": "auto"}})
        monkeypatch.setattr(dlg, "_prompt_calibrate_or_thread", lambda: False)

        pool, rates = dlg._resolve_hash_pool(self.SOURCES, self.RECMAP)

        assert (pool, rates) == ("thread", None)
        assert dlg._hash_pool_fp is None
        assert dlg.settings.get("scan.hash_pool") == "auto"  # not persisted away


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

    def test_descriptions_one_line_tooltip_on_desc_not_title(self, qapp, tmp_path):
        """The Advanced-Settings design: bold TITLE + short one-line muted
        description, with the full detail in a hover tooltip **on the
        description only — never on the title**.

        Two regressions are pinned here:
        * #521 inlined the full multi-line text with ``setWordWrap(True)``;
          PySide6 6.11 stopped flagging ``hasHeightForWidth`` on wrapped
          QLabels, so the QVBoxLayout clipped descriptions to one line. The
          muted (#555) descriptions must therefore NOT word-wrap (a one-line
          label can't be height-clipped) and must each carry a tooltip.
        * The titles (bold slider labels + the three checkboxes) must NOT
          carry a tooltip — hovering a title popping the full blurb was
          unwanted. Only the description line gets the tooltip.

        A flip back to wrapped inline text, or a tooltip creeping back onto a
        title, fails here."""
        from PySide6.QtWidgets import QLabel
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings = JsonSettings(self._make_settings_file(tmp_path, {"sources": {}}))
        dlg = ScanDialog(settings)
        labels = dlg._params_content.findChildren(QLabel)
        descs = [lab for lab in labels if "#555" in lab.styleSheet()]
        titles = [lab for lab in labels if "#555" not in lab.styleSheet()]

        assert len(descs) >= 6, "expected one muted description per advanced control"
        for lab in descs:
            assert not lab.wordWrap(), (
                f"description must be one-line, not wrapped: {lab.text()[:30]!r}"
            )
            assert lab.toolTip(), (
                f"description missing hover tooltip: {lab.text()[:30]!r}"
            )

        # Titles (bold slider labels + checkboxes) must NOT pop a tooltip.
        assert len(titles) >= 3, "expected the bold slider title labels"
        for lab in titles:
            assert not lab.toolTip(), (
                f"title label must NOT have a tooltip: {lab.text()[:30]!r}"
            )
        for cb in (
            dlg._auto_select_check,
            dlg._auto_select_aggressive_check,
            dlg._recalibrate_check,
        ):
            assert not cb.toolTip(), "checkbox title must NOT have a tooltip"


# ---------------------------------------------------------------------------
# Auto-select after scan (photo-manager#212)
# ---------------------------------------------------------------------------

class TestAutoSelectCheckbox:
    """The Advanced Settings section gains an "Auto select after scan"
    checkbox (#212). Default off; state persists via
    ``ui.scan_dialog.auto_select_enabled``; toggling writes through to
    the on-disk settings file immediately (mirrors the
    ``advanced_expanded`` save path) so the user's choice survives the
    next dialog open."""

    def _make_settings_file(self, tmp_path: Path, data: dict) -> Path:
        p = tmp_path / "settings.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_default_unchecked_with_no_setting(self, qapp, tmp_path):
        """Catches: opt-in flag defaults to ON. Auto-select must be
        opt-in — a user who hasn't enabled it should get the pre-#212
        behaviour (no decisions pre-marked). Flipping the default would
        silently change every existing user's scan output."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {"sources": {}})
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)
        assert dlg._auto_select_check.isChecked() is False

    def test_checked_state_loaded_from_settings(self, qapp, tmp_path):
        """Catches: load idiom regression. A user who enabled it
        previously must see the checkbox checked when they reopen."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {
            "sources": {},
            "ui": {"scan_dialog": {"auto_select_enabled": True}},
        })
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)
        assert dlg._auto_select_check.isChecked() is True

    def test_toggle_persists_to_disk_immediately(self, qapp, tmp_path):
        """Catches: save not wired, or save() not called. The toggle
        signal MUST write through to disk on each change — without
        this the user toggles the setting, closes the dialog without
        starting a scan, reopens, and sees the toggle reverted."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {"sources": {}})
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)

        dlg._auto_select_check.setChecked(True)
        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        assert saved["ui"]["scan_dialog"]["auto_select_enabled"] is True

        dlg._auto_select_check.setChecked(False)
        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        assert saved["ui"]["scan_dialog"]["auto_select_enabled"] is False

    def test_state_round_trips_across_dialog_instances(self, qapp, tmp_path):
        """Catches: toggle saves but next dialog instance doesn't read
        it back. End-to-end round trip — exactly what the user sees:
        toggle on, close, reopen, expect on. Independent of the two
        narrower load/save tests above so a regression in the wiring
        between them still fails this case."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {"sources": {}})
        # First dialog: toggle on.
        dlg1 = ScanDialog(JsonSettings(settings_path))
        assert dlg1._auto_select_check.isChecked() is False
        dlg1._auto_select_check.setChecked(True)
        # Second dialog (fresh JsonSettings on the same file): reads back.
        dlg2 = ScanDialog(JsonSettings(settings_path))
        assert dlg2._auto_select_check.isChecked() is True


class TestAutoSelectAggressiveCheckbox:
    """The aggressive sub-option (#393) sits under the auto-select
    parent: disabled when the parent is off, opt-in even when parent
    is on, persists via ``ui.scan_dialog.auto_select_aggressive_delete``.
    Together they let the user open Execute Action with a fully
    pre-populated triage (keepers locked, non-keepers marked delete)."""

    def _make_settings_file(self, tmp_path: Path, data: dict) -> Path:
        p = tmp_path / "settings.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_default_unchecked_with_no_setting(self, qapp, tmp_path):
        """Catches: aggressive flag defaults to ON. A user who hasn't
        opted in must NOT have non-keepers auto-marked for delete —
        flipping the default would silently tag thousands of rows for
        deletion across every existing user's next scan."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {"sources": {}})
        dlg = ScanDialog(JsonSettings(settings_path))
        assert dlg._auto_select_aggressive_check.isChecked() is False

    def test_aggressive_disabled_when_parent_off(self, qapp, tmp_path):
        """Catches: gating regression — aggressive remains enabled
        when its parent (auto-select) is off. The aggressive option is
        meaningless without auto-select; an enabled-but-orphaned
        checkbox would confuse users into thinking they can opt into
        the destructive mode without enabling auto-select first.

        Expands Advanced Settings first so we test our own gating
        wiring, not Qt's checkable-groupbox auto-disable of children.
        """
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {
            "sources": {},
            "ui": {"scan_dialog": {"advanced_expanded": True}},
        })
        dlg = ScanDialog(JsonSettings(settings_path))
        # Parent defaults off → aggressive is disabled on initial load.
        assert dlg._auto_select_check.isChecked() is False
        assert dlg._auto_select_aggressive_check.isEnabled() is False

    def test_aggressive_enables_when_parent_toggled_on(self, qapp, tmp_path):
        """Catches: gating signal not wired. Toggling the parent on
        must re-enable the aggressive checkbox so the user can opt in
        right after enabling auto-select, in the same dialog session
        without a close/reopen."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {
            "sources": {},
            "ui": {"scan_dialog": {"advanced_expanded": True}},
        })
        dlg = ScanDialog(JsonSettings(settings_path))
        dlg._auto_select_check.setChecked(True)
        assert dlg._auto_select_aggressive_check.isEnabled() is True

    def test_aggressive_disables_when_parent_toggled_off(self, qapp, tmp_path):
        """Catches: gating only fires one-way (on→enables, off→leaves
        enabled). Toggling parent off must disable the aggressive
        sub-option — otherwise a user who toggles parent on, then
        aggressive on, then parent off, would leave an orphan-enabled
        widget pointing at a setting whose precondition is unmet."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {
            "sources": {},
            "ui": {
                "scan_dialog": {
                    "auto_select_enabled": True,
                    "advanced_expanded": True,
                },
            },
        })
        dlg = ScanDialog(JsonSettings(settings_path))
        # Sanity: starts with parent on so aggressive is enabled.
        assert dlg._auto_select_aggressive_check.isEnabled() is True
        dlg._auto_select_check.setChecked(False)
        assert dlg._auto_select_aggressive_check.isEnabled() is False

    def test_toggle_persists_to_disk_immediately(self, qapp, tmp_path):
        """Catches: save not wired. Same persistence contract as the
        parent — toggle must write through on every change so the
        user's choice survives a close/reopen even if they don't run
        a scan in this session."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {
            "sources": {},
            "ui": {"scan_dialog": {"auto_select_enabled": True}},
        })
        settings = JsonSettings(settings_path)
        dlg = ScanDialog(settings)

        dlg._auto_select_aggressive_check.setChecked(True)
        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        assert (
            saved["ui"]["scan_dialog"]["auto_select_aggressive_delete"]
            is True
        )

        dlg._auto_select_aggressive_check.setChecked(False)
        saved = json.loads(settings_path.read_text(encoding="utf-8"))
        assert (
            saved["ui"]["scan_dialog"]["auto_select_aggressive_delete"]
            is False
        )

    def test_state_round_trips_across_dialog_instances(self, qapp, tmp_path):
        """Catches: toggle saves but next dialog instance doesn't read
        back. End-to-end: parent on + aggressive on, close, reopen —
        both must be on. Pins the read+write wiring together."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = self._make_settings_file(tmp_path, {
            "sources": {},
            "ui": {"scan_dialog": {"auto_select_enabled": True}},
        })
        dlg1 = ScanDialog(JsonSettings(settings_path))
        dlg1._auto_select_aggressive_check.setChecked(True)
        dlg2 = ScanDialog(JsonSettings(settings_path))
        assert dlg2._auto_select_aggressive_check.isChecked() is True


# ---------------------------------------------------------------------------
# _build_sources (label auto-generation)
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
        sources, recursive_map = dlg._build_sources()

        assert "Takeout" in sources
        assert "Photos" in sources
        assert recursive_map["Takeout"] is True
        assert recursive_map["Photos"] is False

    def test_returns_two_tuple(self, qapp, tmp_path):
        """#213 — the third element (source_priority) is gone; the
        scanner auto-infers from scan order when source_priority is
        omitted from the ScanWorker call."""
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = tmp_path / "settings.json"
        settings_path.write_text('{"sources":{}}', encoding="utf-8")
        dlg = ScanDialog(JsonSettings(settings_path))
        result = dlg._build_sources()
        assert isinstance(result, tuple)
        assert len(result) == 2

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
        sources, _ = dlg._build_sources()

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


class TestProgressFrameResetOnTerminal:
    """#510 — the stage progress frame is revealed on the first
    ``stage_progress`` emit and was previously only reset at the TOP of
    the NEXT ``_start_scan``. The terminal handlers that leave the
    dialog OPEN (``_on_completed_empty`` / ``_on_failed``) must hide the
    frame themselves, or the bar + "scanning…" label stay stuck —
    making a benign empty result (or a scan aborted by an unreadable
    entry, #509) look frozen.
    """

    def _make_dialog(self, qapp, tmp_path):
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = tmp_path / "settings.json"
        settings_path.write_text('{"sources":{}}', encoding="utf-8")
        return ScanDialog(JsonSettings(settings_path))

    def _reveal_frame(self, dlg):
        """Drive a real stage_progress emit so the frame is visible and
        the labels carry residual text — the exact state a terminal
        handler must clean up."""
        dlg._on_stage_progress("WALK", 42, 0, 12.5)
        assert dlg._progress_frame.isVisibleTo(dlg) is True
        assert dlg._stage_label.text() != ""

    def test_completed_empty_hides_progress_frame(self, qapp, tmp_path):
        dlg = self._make_dialog(qapp, tmp_path)
        self._reveal_frame(dlg)
        dlg._on_completed_empty()
        assert dlg._progress_frame.isVisibleTo(dlg) is False
        assert dlg._stage_label.text() == ""
        assert dlg._stage_rate_label.text() == ""
        assert dlg._current_stage is None

    def test_failed_hides_progress_frame(self, qapp, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: None)

        dlg = self._make_dialog(qapp, tmp_path)
        self._reveal_frame(dlg)
        dlg._on_failed("simulated pipeline error")
        assert dlg._progress_frame.isVisibleTo(dlg) is False
        assert dlg._stage_label.text() == ""
        assert dlg._current_stage is None

    def test_finished_hides_progress_frame(self, qapp, tmp_path):
        dlg = self._make_dialog(qapp, tmp_path)
        self._reveal_frame(dlg)
        dlg._on_finished("manifest.csv")
        assert dlg._progress_frame.isVisibleTo(dlg) is False
        assert dlg._stage_label.text() == ""
        assert dlg._current_stage is None


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
                # #424 — stage_progress is the new typed signal the
                # dialog connects in _start_scan.
                self.stage_progress = MagicMock()
                self.failed = MagicMock()
                self.finished = MagicMock()
                self.completed_empty = MagicMock()
                # #486-PR3b — dialog connects this to cache fresh calibrations.
                self.hash_pool_measured = MagicMock()

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


class TestBrowseOutputStartPath:
    """#216 — ``_browse_output`` must never hand Qt a bare relative
    filename. Qt's ``QFileDialog.getSaveFileName`` interprets a relative
    string against the process CWD (unpredictable in a launched app),
    and on Windows the resulting dialog can render as the OS folder
    picker rather than the standard save-file UI. Fix: pass an absolute
    path when the field has a value, ``""`` (empty) otherwise so Qt uses
    its remembered last-visited directory.
    """

    def _make_dialog(self, qapp, tmp_path):
        from app.views.dialogs.scan_dialog import ScanDialog
        from infrastructure.settings import JsonSettings

        settings_path = tmp_path / "settings.json"
        settings_path.write_text('{"sources":{}}', encoding="utf-8")
        return ScanDialog(JsonSettings(settings_path))

    def test_empty_field_passes_empty_string_not_relative_filename(
        self, qapp, tmp_path, monkeypatch
    ):
        """When the output field is empty, ``start`` must be ``""`` — NOT
        the legacy ``"migration_manifest.sqlite"`` bare-relative default
        that triggered the wrong-dialog regression on Windows."""
        from app.views.dialogs import scan_dialog as sd

        dlg = self._make_dialog(qapp, tmp_path)
        dlg._output_field.setText("")

        captured: dict = {}

        def fake_get_save(parent, title, start, filt):
            captured["start"] = start
            return ("", "")

        monkeypatch.setattr(sd.QFileDialog, "getSaveFileName", fake_get_save)
        dlg._browse_output()

        assert captured["start"] == "", (
            f"empty field must pass '' to Qt (last-visited dir), "
            f"got {captured['start']!r}"
        )

    def test_whitespace_only_field_treated_as_empty(
        self, qapp, tmp_path, monkeypatch
    ):
        """A field with only spaces is conceptually empty — must NOT
        be resolve()'d into a meaningless absolute path."""
        from app.views.dialogs import scan_dialog as sd

        dlg = self._make_dialog(qapp, tmp_path)
        dlg._output_field.setText("   ")

        captured: dict = {}

        def fake_get_save(parent, title, start, filt):
            captured["start"] = start
            return ("", "")

        monkeypatch.setattr(sd.QFileDialog, "getSaveFileName", fake_get_save)
        dlg._browse_output()

        assert captured["start"] == ""

    def test_populated_field_passes_absolute_path(
        self, qapp, tmp_path, monkeypatch
    ):
        """When the field has a value, it must be passed as an absolute
        path so Qt opens to the right parent directory regardless of
        the process CWD."""
        from app.views.dialogs import scan_dialog as sd

        dlg = self._make_dialog(qapp, tmp_path)
        target = tmp_path / "subdir" / "my_manifest.sqlite"
        dlg._output_field.setText(str(target))

        captured: dict = {}

        def fake_get_save(parent, title, start, filt):
            captured["start"] = start
            return ("", "")

        monkeypatch.setattr(sd.QFileDialog, "getSaveFileName", fake_get_save)
        dlg._browse_output()

        assert Path(captured["start"]).is_absolute(), (
            f"populated field must yield an absolute path, "
            f"got {captured['start']!r}"
        )
        # Compare resolved forms — Path.resolve() may collapse symlinks
        # or apply OS path canonicalisation (e.g. case on Windows).
        assert Path(captured["start"]) == Path(str(target)).resolve()

    def test_relative_field_value_is_resolved_to_absolute(
        self, qapp, tmp_path, monkeypatch
    ):
        """If a stored relative path leaks through from settings, the
        browse dialog must still receive an absolute path — otherwise
        we're back at the #216 regression."""
        from app.views.dialogs import scan_dialog as sd

        dlg = self._make_dialog(qapp, tmp_path)
        dlg._output_field.setText("migration_manifest.sqlite")

        captured: dict = {}

        def fake_get_save(parent, title, start, filt):
            captured["start"] = start
            return ("", "")

        monkeypatch.setattr(sd.QFileDialog, "getSaveFileName", fake_get_save)
        dlg._browse_output()

        assert Path(captured["start"]).is_absolute(), (
            f"bare relative filename {dlg._output_field.text()!r} must be "
            f"resolved to absolute before reaching Qt; got {captured['start']!r}"
        )

    def test_chosen_path_with_extension_used_verbatim(
        self, qapp, tmp_path, monkeypatch
    ):
        """When the user picks a file ending in .sqlite, the path goes
        into the field as-is (no double-extension)."""
        from app.views.dialogs import scan_dialog as sd

        dlg = self._make_dialog(qapp, tmp_path)
        target = tmp_path / "picked.sqlite"

        monkeypatch.setattr(
            sd.QFileDialog, "getSaveFileName",
            lambda *a, **kw: (str(target), ""),
        )
        dlg._browse_output()
        assert dlg._output_field.text() == str(target)

    def test_chosen_path_without_extension_gets_sqlite_appended(
        self, qapp, tmp_path, monkeypatch
    ):
        """Preserve the existing post-pick auto-extension behaviour —
        the fix is scoped to the ``start`` arg, not the return path."""
        from app.views.dialogs import scan_dialog as sd

        dlg = self._make_dialog(qapp, tmp_path)
        target = tmp_path / "picked"  # no extension

        monkeypatch.setattr(
            sd.QFileDialog, "getSaveFileName",
            lambda *a, **kw: (str(target), ""),
        )
        dlg._browse_output()
        assert dlg._output_field.text() == str(target) + ".sqlite"


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
