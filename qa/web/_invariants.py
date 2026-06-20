"""Cross-cutting web-UI invariant helpers for Playwright-based qa drivers.

All functions in this module accept a Playwright ``Page`` object and
perform assertions or waits that are reused across multiple scenario
drivers.  They are analogous to the ``assert_*`` / ``wait_*`` helpers
in ``qa/scenarios/_uia.py`` for the Qt harness.

Playwright is imported inside each function (lazy) so this module is
importable in CI unit-test runs where playwright is not installed.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Only evaluated by type-checkers, never at runtime.
    from playwright.sync_api import Page


# ---------------------------------------------------------------------------
# Status-bar helpers
# ---------------------------------------------------------------------------

# Regex fragments used to recognise common status-bar states.
# These mirror the status-bar wording enforced by the Qt harness (s37).

_STATUS_IDLE = re.compile(r"no manifest|ready|idle", re.IGNORECASE)
_STATUS_SCAN_RUNNING = re.compile(r"scanning|progress", re.IGNORECASE)
_STATUS_SCAN_DONE = re.compile(r"scan complete|finished|groups?\s*found", re.IGNORECASE)
_STATUS_EMPTY = re.compile(r"no (duplicates|groups|results) found|empty", re.IGNORECASE)


def wait_status(
    page: "Page",
    pattern: str | re.Pattern[str],
    *,
    testid: str = "main-status-bar",
    timeout: float = 10_000,
) -> str:
    """Wait until the status bar text matches ``pattern`` and return the text.

    Parameters
    ----------
    page:
        The Playwright Page to query.
    pattern:
        A regex string or compiled pattern.  The wait succeeds when the
        element's ``inner_text()`` matches this pattern (``re.search``).
    testid:
        The ``data-testid`` value of the status-bar element.
        Defaults to ``"main-status-bar"``.
    timeout:
        Maximum milliseconds to wait before raising a timeout error.

    Returns
    -------
    str
        The matched status-bar text at the moment the wait resolved.

    Raises
    ------
    playwright.sync_api.TimeoutError
        If the status bar does not match ``pattern`` within ``timeout`` ms.
    """
    compiled = re.compile(pattern) if isinstance(pattern, str) else pattern
    locator = page.get_by_test_id(testid)
    locator.wait_for(state="visible", timeout=timeout)
    # Poll until text matches.
    page.wait_for_function(
        """([testid, pat]) => {
            const el = document.querySelector('[data-testid="' + testid + '"]');
            return el && new RegExp(pat, 'i').test(el.innerText);
        }""",
        arg=[testid, compiled.pattern],
        timeout=timeout,
    )
    return locator.inner_text()


def assert_status_idle(page: "Page", timeout: float = 5_000) -> None:
    """Assert that the status bar shows an idle / ready state."""
    wait_status(page, _STATUS_IDLE, timeout=timeout)


def assert_status_scan_done(page: "Page", timeout: float = 30_000) -> None:
    """Assert that the status bar shows a completed scan."""
    wait_status(page, _STATUS_SCAN_DONE, timeout=timeout)


def assert_status_empty(page: "Page", timeout: float = 5_000) -> None:
    """Assert that the status bar shows an empty-result state."""
    wait_status(page, _STATUS_EMPTY, timeout=timeout)


# ---------------------------------------------------------------------------
# Result-tree helpers
# ---------------------------------------------------------------------------


def count_groups(page: "Page", *, testid: str = "main-result-tree") -> int:
    """Return the number of visible group-header rows in the result tree.

    Each group header row is expected to carry a ``data-testid`` of the
    form ``row-group-{group_id}`` (see ``testid_constants.row_group_testid``).
    """
    locator = page.locator(f'[data-testid^="row-group-"][data-testid*="-"]')
    return locator.count()


def count_file_rows(page: "Page") -> int:
    """Return the number of visible file rows in the result tree.

    Each file row carries a ``data-testid`` of the form
    ``row-file-{group_id}-{basename}`` (see ``testid_constants.row_file_testid``).
    """
    return page.locator('[data-testid^="row-file-"]').count()


# ---------------------------------------------------------------------------
# Scan-progress helpers
# ---------------------------------------------------------------------------


def wait_scan_complete(page: "Page", timeout: float = 60_000) -> None:
    """Wait until the SSE stream delivers a ``finished`` or ``failed`` event.

    In practice this waits for the scan-status element to show either a
    completion message or an error.  Mirrors what the Qt harness does by
    waiting for the status-bar text to change.
    """
    wait_status(
        page,
        r"scan complete|finished|failed|error|cancelled",
        testid="scan-status-text",
        timeout=timeout,
    )


def wait_log_line(page: "Page", pattern: str, timeout: float = 10_000) -> str:
    """Wait until a line matching ``pattern`` appears in the scan progress log.

    Returns the matching line text.
    """
    log_locator = page.get_by_test_id("scan-progress-log")
    log_locator.wait_for(state="visible", timeout=timeout)
    page.wait_for_function(
        """([pat]) => {
            const el = document.querySelector('[data-testid="scan-progress-log"]');
            return el && new RegExp(pat, 'i').test(el.innerText);
        }""",
        arg=[pattern],
        timeout=timeout,
    )
    return log_locator.inner_text()
