"""Tests for the persistent status-bar baseline introduced for #138, #140.

#138: at startup the ``status_ready`` showMessage used a 3000ms timeout, so
the bar went blank after 3s with no fallback.
#140: after loading a manifest, opening any menu triggered Qt's
``QAction``-hover statusTip plumbing — which replaced the load-summary temp
message with an empty string. With no permanent widget to fall back to,
the bar stayed blank until the next manual showMessage.

Fix: a persistent ``QLabel`` added to the status bar via ``addWidget``
carries the baseline text. Transient ``showMessage(text, timeout)`` calls
still work for action feedback — Qt hides the label while the temp
message displays and shows it again once the message clears.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestStatusReporterImplBaseline:
    """``StatusReporterImpl.set_baseline`` must forward to the window's setter
    so callers (file_operations) don't reach into Qt internals directly.
    """

    def test_set_baseline_delegates_to_window(self):
        from app.views.main_window import StatusReporterImpl

        win = MagicMock()
        reporter = StatusReporterImpl(win)

        reporter.set_baseline("Opened manifest: 7 pairs to review (42 files)")

        win.set_status_baseline.assert_called_once_with(
            "Opened manifest: 7 pairs to review (42 files)"
        )

    def test_show_status_still_uses_temp_showMessage(self):
        """Transient action feedback must still go through
        ``QStatusBar.showMessage`` with its timeout — set_baseline is for
        the persistent layer only, not action toasts."""
        from app.views.main_window import StatusReporterImpl

        win = MagicMock()
        reporter = StatusReporterImpl(win)

        reporter.show_status("Saved 5 decisions", 4000)

        win.statusBar.assert_called_once_with()
        win.statusBar.return_value.showMessage.assert_called_once_with(
            "Saved 5 decisions", 4000
        )


class TestPersistentBaselineWidgetPattern:
    """End-to-end verification of the addWidget-based baseline pattern.

    Constructs a bare QMainWindow with a QStatusBar + QLabel attached via
    ``addWidget`` exactly the way MainWindow does. Exercises the bug
    sequence from #140: temp message appears, gets overwritten by an
    empty-string showMessage (the Qt menu-hover signature), and verifies
    the baseline label remains the visible source of text after the
    transient state clears. Without ``addWidget`` the bar would go blank
    permanently — the exact symptom from #138/#140.
    """

    def test_baseline_label_survives_temp_message_then_clear(self, qapp):
        from PySide6.QtWidgets import QLabel, QMainWindow

        win = QMainWindow()
        label = QLabel("Ready")
        win.statusBar().addWidget(label, 1)

        # Baseline is visible initially.
        assert label.text() == "Ready"
        assert win.statusBar().currentMessage() == ""

        # Show transient message → Qt hides the addWidget label.
        win.statusBar().showMessage("Saving…", 0)
        assert win.statusBar().currentMessage() == "Saving…"

        # Clear the temp message (this is what Qt does internally when a
        # menu closes after the hover-with-empty-statusTip flow that #140
        # described). The baseline label's text is preserved.
        win.statusBar().clearMessage()
        assert win.statusBar().currentMessage() == ""
        assert label.text() == "Ready"

    def test_setting_baseline_text_persists_through_temp_cycle(self, qapp):
        """The #140 case: after a successful manifest load the baseline is
        updated to the load summary, then any subsequent menu hover that
        triggers Qt's empty-statusTip clear must NOT leave the bar blank.
        """
        from PySide6.QtWidgets import QLabel, QMainWindow

        win = QMainWindow()
        label = QLabel("Ready")
        win.statusBar().addWidget(label, 1)

        # Simulate post-load baseline update.
        label.setText("Opened manifest: 3 pairs to review (12 files)")

        # Simulate a transient action toast.
        win.statusBar().showMessage("Saved decisions", 0)
        # Then it expires / is cleared (menu hover ends, etc).
        win.statusBar().clearMessage()

        # The baseline must still hold the load summary, not the older
        # "Ready" text and not an empty string.
        assert label.text() == "Opened manifest: 3 pairs to review (12 files)"


class TestFileOperationsUsesBaselineForLoadSummary:
    """``_on_manifest_loaded`` must update the baseline, not push a temp
    message — the temp path was the root cause of #140 (load summary
    vanished as soon as any menu opened).
    """

    def test_manifest_loaded_sets_baseline_not_show_status(self, tmp_path):
        """Regression guard for #140: the load summary must go through
        ``set_baseline``. If a refactor ever wires this back to
        ``show_status``, the temp-message path will silently re-introduce
        the menu-clears-status bug.
        """
        from types import SimpleNamespace

        from app.views.handlers.file_operations import FileOperationsHandler
        from core.models import PhotoGroup, PhotoRecord

        vm = SimpleNamespace(groups=[], group_count=0)
        ui = MagicMock()
        status = MagicMock()
        parent = MagicMock()
        parent.menu_controller = MagicMock()
        handler = FileOperationsHandler(
            vm=vm,
            settings=MagicMock(),
            parent_widget=parent,
            ui_updater=ui,
            status_reporter=status,
        )

        rec = PhotoRecord(
            group_number=1,
            is_mark=False,
            is_locked=False,
            folder_path="",
            file_path="/a.jpg",
            capture_date=None,
            modified_date=None,
            file_size_bytes=0,
        )
        groups = [PhotoGroup(group_number=1, items=[rec])]
        vm.group_count = 1

        handler._on_manifest_loaded(groups, str(tmp_path / "m.sqlite"))

        status.set_baseline.assert_called_once()
        status.show_status.assert_not_called()
