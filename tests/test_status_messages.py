"""Tests for app.views.components.status_messages."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.views.components.status_messages import (
    DEFAULT_TIMEOUT_MS,
    plural_form,
    pluralize,
    report_count,
)


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


# ── pluralize / plural_form (added for #109) ──────────────────────────────


def test_pluralize_one_uses_singular():
    """The bug from #109: '1 pairs' should be '1 pair'."""
    assert pluralize(1, "pair") == "1 pair"


def test_pluralize_many_appends_default_s():
    assert pluralize(5, "pair") == "5 pairs"


def test_pluralize_zero_is_plural():
    """Zero is plural in English: '0 pairs', not '0 pair'."""
    assert pluralize(0, "pair") == "0 pairs"


def test_pluralize_multi_word_noun():
    """Multi-word nouns get the trailing 's' on the whole phrase."""
    assert pluralize(3, "isolated file") == "3 isolated files"
    assert pluralize(1, "isolated file") == "1 isolated file"


def test_pluralize_irregular_explicit_plural():
    """Irregular forms supply the plural explicitly."""
    assert pluralize(2, "child", "children") == "2 children"
    assert pluralize(1, "child", "children") == "1 child"


def test_plural_form_returns_word_only():
    """plural_form is the primitive — count formatting is the caller's job."""
    assert plural_form(1, "group") == "group"
    assert plural_form(5, "group") == "groups"
    assert plural_form(0, "group") == "groups"


def test_plural_form_supports_thousands_formatted_count():
    """The motivating callsite: status bar with thousands-separator count."""
    n = 12345
    assert f"{n:,} {plural_form(n, 'isolated file')}" == "12,345 isolated files"
    assert f"{1:,} {plural_form(1, 'isolated file')}" == "1 isolated file"
