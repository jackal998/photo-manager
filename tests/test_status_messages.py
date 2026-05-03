"""Tests for app.views.components.status_messages."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.views.components.status_messages import DEFAULT_TIMEOUT_MS, report_count


def test_report_count_singular_omits_s():
    reporter = MagicMock()
    report_count(reporter, "Removed", 1, "item from list")
    reporter.show_status.assert_called_once_with(
        "Removed 1 item from list", DEFAULT_TIMEOUT_MS
    )


def test_report_count_plural_appends_s():
    reporter = MagicMock()
    report_count(reporter, "Executed", 5, "action")
    reporter.show_status.assert_called_once_with("Executed 5 actions", DEFAULT_TIMEOUT_MS)


def test_report_count_zero_pluralizes():
    """Zero is plural in English: '0 items', not '0 item'."""
    reporter = MagicMock()
    report_count(reporter, "Saved", 0, "decision")
    reporter.show_status.assert_called_once_with("Saved 0 decisions", DEFAULT_TIMEOUT_MS)


def test_report_count_negative_pluralizes():
    """Defensive: a negative count (shouldn't happen, but...) gets the plural form."""
    reporter = MagicMock()
    report_count(reporter, "Recovered", -1, "row")
    reporter.show_status.assert_called_once_with("Recovered -1 rows", DEFAULT_TIMEOUT_MS)


def test_report_count_custom_timeout():
    reporter = MagicMock()
    report_count(reporter, "Loaded", 3, "group", timeout=10000)
    reporter.show_status.assert_called_once_with("Loaded 3 groups", 10000)
