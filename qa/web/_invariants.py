"""Cross-cutting web-UI invariant helpers for Playwright-based qa drivers.

All functions in this module accept a Playwright ``Page`` object and
perform assertions or waits that are reused across multiple scenario
drivers.  They are analogous to the ``assert_*`` / ``wait_*`` helpers
in ``qa/scenarios/_uia.py`` for the Qt harness.

Playwright is imported inside each function (lazy) so this module is
importable in CI unit-test runs where playwright is not installed.
"""
from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Union

from qa.web.testid_constants import scan_source_recursive_testid

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


def wait_scan_complete(page: "Page", timeout: float = 60_000) -> str:
    """Wait until the scan completes and the manifest is loaded.

    Delegates to ``wait_manifest_loaded`` which waits on
    ``data-testid="main-status-bar"`` for the ``"N groups · M files"`` text.

    .. warning::
        Do NOT wait on ``scan-status-text`` here.  ``ScanDialog``
        auto-unmounts ``ScanProgress`` (and therefore removes
        ``scan-status-text`` from the DOM) on the SSE ``finished`` event,
        *before* ``loadManifest`` resolves.  Waiting on ``scan-status-text``
        races with the unmount and will raise a TimeoutError.  The
        ``main-status-bar`` element is always present and is the correct
        post-scan signal.

    Returns
    -------
    str
        The status bar text once the manifest is loaded
        (e.g. ``"3 groups · 8 files"``).
    """
    return wait_manifest_loaded(page, timeout=timeout)


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


# ---------------------------------------------------------------------------
# Status pattern: manifest loaded (N groups · M files)
# ---------------------------------------------------------------------------

# The App.tsx status bar emits "N groups · M files" once a manifest is loaded.
# Matches both "0 groups · 0 files" (empty scan) and "5 groups · 12 files".
_STATUS_MANIFEST_LOADED = re.compile(r"\d+\s*groups?\s*·\s*\d+\s*files?", re.IGNORECASE)


def wait_manifest_loaded(page: "Page", timeout: float = 60_000) -> str:
    """Wait until the main status bar reflects a loaded manifest.

    The status bar shows ``"N groups · M files"`` once ``loadManifest``
    completes.  This is the correct post-scan / post-load signal because
    ``ScanDialog`` auto-unmounts ``ScanProgress`` on the SSE ``finished``
    event (removing ``scan-status-text`` from the DOM), then calls
    ``loadManifest`` which updates ``manifest.path`` and the status bar.

    Parameters
    ----------
    page:
        The Playwright Page to query.
    timeout:
        Maximum milliseconds to wait.

    Returns
    -------
    str
        The status bar text at the moment the wait resolved.
    """
    return wait_status(page, _STATUS_MANIFEST_LOADED, timeout=timeout)


# ---------------------------------------------------------------------------
# ScanDialog page-object helpers
# ---------------------------------------------------------------------------


def open_scan_dialog(page: "Page", *, timeout: float = 5_000) -> None:
    """Click the main Scan button and wait for the scan dialog to appear.

    Parameters
    ----------
    page:
        The Playwright Page (must be on the app root ``/``).
    timeout:
        Maximum milliseconds to wait for the dialog to become visible.
    """
    page.get_by_test_id("main-scan-button").click()
    page.get_by_test_id("scan-dialog").wait_for(state="visible", timeout=timeout)


def add_scan_source(
    page: "Page",
    path: str,
    *,
    idx: int = 0,
    label: str = "",
    timeout: float = 5_000,
) -> None:
    """Ensure a source row exists at position ``idx`` and fill its inputs.

    The ScanDialog always starts with one blank source row (idx 0).  For
    ``idx > 0`` this helper clicks ``scan-add-source-button`` first so the
    row exists before trying to fill it.  Agent B adds
    ``data-testid="scan-source-{idx}-label"`` and
    ``data-testid="scan-source-{idx}-path"`` to the ``SourceRow`` component;
    this helper targets those testids directly.

    The ``canStart`` guard in ScanDialog requires both ``label`` and ``path``
    to be non-empty.  If ``label`` is omitted here it defaults to the
    basename of ``path``.

    Parameters
    ----------
    page:
        The Playwright Page with an open ScanDialog.
    path:
        The filesystem path to fill into the path input.
    idx:
        0-based position of the source row (default 0 — the pre-existing row).
    label:
        Label text to fill.  Defaults to ``os.path.basename(path)``.
    timeout:
        Maximum milliseconds to wait for each element.
    """
    effective_label = label if label else os.path.basename(path)
    if idx > 0:
        page.get_by_test_id("scan-add-source-button").click()
    label_input = page.get_by_test_id(f"scan-source-{idx}-label")
    label_input.wait_for(state="visible", timeout=timeout)
    label_input.fill(effective_label)
    page.get_by_test_id(f"scan-source-{idx}-path").fill(path)


def set_output_path(page: "Page", path: str) -> None:
    """Fill the output-path input inside the open ScanDialog.

    Parameters
    ----------
    page:
        The Playwright Page with an open ScanDialog.
    path:
        The output ``.db`` path to type into the field.
    """
    page.get_by_test_id("scan-output-path").fill(path)


def start_scan(page: "Page", *, timeout: float = 5_000) -> None:
    """Click the Start Scan button inside the open ScanDialog.

    The button is only enabled when at least one source row has both a
    non-empty label and path, and an output path is set (``canStart`` in
    ScanDialog.tsx).  If it is still disabled when this helper runs the
    Playwright ``click()`` will succeed but the handler will not fire;
    callers should ensure ``add_scan_source`` and ``set_output_path`` ran
    first.

    Parameters
    ----------
    page:
        The Playwright Page with an open ScanDialog.
    timeout:
        Maximum milliseconds to wait for the button to be visible.
    """
    btn = page.get_by_test_id("scan-start-button")
    btn.wait_for(state="visible", timeout=timeout)
    btn.click()


def run_scan(
    page: "Page",
    *,
    sources: list[Union[str, tuple[str, str]]],
    output_path: str,
    scan_timeout: float = 120_000,
    recursive: bool = False,
) -> str:
    """Full scan flow: open dialog → add sources → set output → start → wait.

    Opens ScanDialog, adds each source row, sets the output path, clicks
    Start Scan, then waits for ``main-status-bar`` to show the
    ``"N groups · M files"`` manifest-loaded text.  Returns when the result
    tree / status bar reflects the loaded manifest.

    ScanDialog auto-closes on the SSE ``finished`` event (before
    ``loadManifest`` is called), so this helper waits on ``main-status-bar``
    (always present) rather than ``scan-status-text`` (unmounts with the dialog).

    Parameters
    ----------
    page:
        The Playwright Page (must be on the app root ``/``).
    sources:
        List of source specs.  Each entry is either:
        - A plain ``str`` path — label defaults to ``os.path.basename(path)``.
        - A ``(label, path)`` tuple — both used verbatim.
    output_path:
        The output ``.db`` path passed to the scan.
    scan_timeout:
        Maximum milliseconds to wait for the manifest-loaded status bar text.
        Scans on large directories can take several minutes.
    recursive:
        Tick every source row's "recursive" checkbox before starting.  The
        ScanDialog default is UNCHECKED (``ScanDialog.tsx:60`` —
        ``recursive: false``), so a scenario whose fixture has a nested
        subdirectory gets that subdirectory silently skipped unless it passes
        ``recursive=True``.  Default False so existing callers are unchanged.

    Returns
    -------
    str
        The status bar text once the manifest is loaded
        (e.g. ``"3 groups · 8 files"``).
    """
    open_scan_dialog(page)
    for idx, spec in enumerate(sources):
        if isinstance(spec, tuple):
            lbl, pth = spec
            add_scan_source(page, pth, idx=idx, label=lbl)
        else:
            add_scan_source(page, spec, idx=idx)
        if recursive:
            box = page.get_by_test_id(scan_source_recursive_testid(idx))
            box.check()
            assert box.is_checked(), (
                f"recursive checkbox for source row {idx} did not tick — the "
                f"scan would silently skip every subdirectory of that source"
            )
    set_output_path(page, output_path)
    start_scan(page)
    return wait_manifest_loaded(page, timeout=scan_timeout)


def _patch_settings(base_url: str, updates: dict) -> None:
    """PATCH /api/settings with ``updates``. Fail-open (loopback only)."""
    import json
    import urllib.request

    payload = json.dumps({"updates": updates}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/settings",
        data=payload,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5):  # noqa: S310 (loopback only)
            pass
    except Exception:  # noqa: BLE001 — fail-open, see reset_scan_persistence docstring
        pass


def reset_scan_persistence(base_url: str) -> None:
    """Reset per-scenario persisted UI prefs via PATCH /api/settings.

    The web batch runs every scenario against ONE long-lived server with ONE
    settings.json, so any pref a scenario writes leaks into the next unless it
    is reset here. This is the web analog of the Qt batch runner's per-scenario
    ``configure`` step, which writes settings.json fresh before each launch.

    Two prefs are reset:

    * ``sources.list`` / ``sources.output`` — the ScanDialog persists its source
      list + output path (#678-E / s23a/s23b). Without the reset, a prior
      scenario's sources pre-fill the next dialog and break the
      ``add_scan_source`` "always one blank row" assumption.
    * ``ui.prune_singletons`` → ``"never"`` — the #686 prune flow reads this on
      every destructive op. ``"never"`` is the QA default (mirrors the Qt
      ``PRUNE_PREF_OVERRIDES`` default), so no scenario sees a surprise prune
      dialog. s61 (``"ask"``) and s67 (``"always"``) opt in explicitly via
      :func:`set_prune_pref` at the top of their run.

    Fail-open: any error (server not up yet, route missing on an older build)
    is swallowed — the reset is a test-isolation convenience, not a correctness
    assertion. A genuine allowlist regression is caught by the backend unit
    tests; a reset that silently no-ops surfaces as a clear scenario failure
    (stale sources → wrong scan, or a leaked prune pref → unexpected dialog),
    never a confusing batch crash.

    Parameters
    ----------
    base_url:
        Base URL of the running FastAPI server, e.g. ``http://127.0.0.1:8765``.
    """
    _patch_settings(
        base_url,
        {
            "sources.list": [],
            "sources.output": "",
            "ui.prune_singletons": "never",
        },
    )


def set_prune_pref(base_url: str, pref: str) -> None:
    """Set ``ui.prune_singletons`` to ``pref`` (#686).

    Used by s61 (``"ask"``) and s67 (``"always"``) to opt into the prune flow
    before driving it — the web analog of the Qt ``PRUNE_PREF_OVERRIDES``. The
    per-scenario :func:`reset_scan_persistence` resets it back to ``"never"``
    before the next scenario, so the opt-in never leaks. The store reads the
    pref authoritatively (GET /api/settings) on each destructive op, so setting
    it before the page triggers the flow is sufficient — no reload needed.

    ``pref`` must be one of ``"ask"`` / ``"always"`` / ``"never"``.
    """
    _patch_settings(base_url, {"ui.prune_singletons": pref})


# ---------------------------------------------------------------------------
# Context-menu helpers
# ---------------------------------------------------------------------------


def right_click_row(page: "Page", row_testid: str, *, timeout: float = 10_000) -> None:
    """Scroll a file row into view and right-click it to open the context menu.

    The result tree is virtualised (``@tanstack/react-virtual``), so rows
    that are off-screen are not in the DOM until they scroll into view.
    This helper calls ``scroll_into_view_if_needed()`` before right-clicking,
    which forces the virtualiser to render the target row.  After the
    right-click it waits for ``data-testid="context-menu"`` to appear.

    Parameters
    ----------
    page:
        The Playwright Page with a loaded manifest.
    row_testid:
        The full ``data-testid`` value of the file row, e.g.
        ``row_file_testid(group_id, basename)`` from ``testid_constants``.
    timeout:
        Maximum milliseconds to wait for the row and menu to appear.
    """
    row = page.get_by_test_id(row_testid)
    row.wait_for(state="visible", timeout=timeout)
    row.scroll_into_view_if_needed(timeout=timeout)
    row.click(button="right")
    page.get_by_test_id("context-menu").wait_for(state="visible", timeout=timeout)


def click_context_item(page: "Page", item_testid: str, *, timeout: float = 5_000) -> None:
    """Click an item inside the open context menu and wait for it to close.

    Parameters
    ----------
    page:
        The Playwright Page with an open context menu.
    item_testid:
        The ``data-testid`` of the menu item, e.g.
        ``CTX_SET_ACTION_DELETE`` from ``testid_constants``.
    timeout:
        Maximum milliseconds to wait for the item to be clickable.
    """
    item = page.get_by_test_id(item_testid)
    item.wait_for(state="visible", timeout=timeout)
    item.click()
    # Context menu unmounts on click; wait for it to disappear.
    page.get_by_test_id("context-menu").wait_for(state="hidden", timeout=timeout)


# DecisionControl button labels → testid option slug (row_decision_option_testid).
_DECISION_LABEL_TO_SLUG = {"None": "none", "Delete": "delete", "Ignore": "ignore"}


def set_row_decision(
    page: "Page", decision_testid: str, label: str, *, timeout: float = 5_000
) -> None:
    """Stage a per-row decision via the result-tree DecisionControl buttons.

    The DecisionControl is a row of three direct-click buttons (``None`` /
    ``Delete`` / ``Ignore``); one click stages the decision with no menu to open
    (#744).  Each button's testid is the group decision testid with the option
    slug appended (``row_decision_option_testid``).  This is the affordance for
    STAGING a reversible ``user_decision`` (PATCH /api/decisions) — distinct from
    the context-menu "Remove from list", which FINALIZES ``outcome='ignored'``
    since #694.

    Requires an UNLOCKED row: ``FileRow`` disables the DecisionControl when the
    row is locked (``disabled={row.is_locked}``), so calling this on a locked row
    hits a disabled button and Playwright fails loud ("element is not enabled") —
    stage decisions BEFORE locking.

    Parameters
    ----------
    page:
        The Playwright Page with a loaded manifest.
    decision_testid:
        The DecisionControl group testid, e.g.
        ``row_decision_testid(group_id, basename)`` from ``testid_constants``.
    label:
        The button label: ``"None"``, ``"Delete"`` or ``"Ignore"``.
    timeout:
        Maximum milliseconds to wait for the button.
    """
    slug = _DECISION_LABEL_TO_SLUG[label]  # KeyError = fail loud on a bad label
    button = page.get_by_test_id(f"{decision_testid}-{slug}")
    button.wait_for(state="visible", timeout=timeout)
    button.scroll_into_view_if_needed(timeout=timeout)
    button.click()


# ---------------------------------------------------------------------------
# Row-selection helpers (main result-tree multi-selection)
# ---------------------------------------------------------------------------


def click_row(
    page: "Page",
    row_testid: str,
    *,
    modifiers: "list[str] | None" = None,
    timeout: float = 10_000,
) -> None:
    """Scroll a virtualised file row into view and left-click it.

    The result tree is virtualised (``@tanstack/react-virtual``), so off-screen
    rows are not in the DOM until they scroll into view — this helper forces the
    render via ``scroll_into_view_if_needed`` before clicking (the same pattern
    as ``right_click_row``).

    Parameters
    ----------
    page:
        The Playwright Page with a loaded manifest.
    row_testid:
        The full ``data-testid`` of the file row (``row_file_testid(...)``).
    modifiers:
        Optional Playwright modifier keys held during the click, e.g.
        ``["Control"]`` (toggle) or ``["Shift"]`` (range).  Playwright maps
        ``"Control"`` to Ctrl on Windows/Linux and Meta on macOS, matching the
        app's ``e.ctrlKey || e.metaKey`` check.
    timeout:
        Maximum milliseconds to wait for the row to appear.
    """
    row = page.get_by_test_id(row_testid)
    row.wait_for(state="visible", timeout=timeout)
    row.scroll_into_view_if_needed(timeout=timeout)
    if modifiers:
        row.click(modifiers=modifiers)
    else:
        row.click()


def ctrl_click_row(page: "Page", row_testid: str, *, timeout: float = 10_000) -> None:
    """Ctrl/Cmd-click a file row to toggle it in the multi-selection."""
    click_row(page, row_testid, modifiers=["Control"], timeout=timeout)


def shift_click_row(page: "Page", row_testid: str, *, timeout: float = 10_000) -> None:
    """Shift-click a file row to extend the multi-selection to an inclusive range."""
    click_row(page, row_testid, modifiers=["Shift"], timeout=timeout)


# ---------------------------------------------------------------------------
# Execute dialog helpers
# ---------------------------------------------------------------------------


def dismiss_modal_overlays(
    page: "Page", *, max_attempts: int = 12, settle_ms: int = 250
) -> None:
    """Best-effort: press Escape until no Radix modal overlay remains in the DOM.

    A modal dialog's overlay (``div.fixed.inset-0.z-50`` backdrop) intercepts
    pointer events across the whole viewport, so a lingering one blocks toolbar
    clicks. After a remove/execute the execute dialog auto-closes, but on a fast
    CI runner the close can lag — or a stacked dialog (its overlay
    ``data-state="open"`` / ``aria-hidden``, i.e. a dialog behind another) can
    linger. Radix dialogs close on Escape (they only swallow it while a job runs,
    which is not the case at an idle open point), so press Escape until every
    overlay DETACHES from the DOM (not merely flips ``data-state``, since a
    ``data-state="closed"`` overlay mid-fade still intercepts). Returns as soon
    as nothing is open (the overwhelmingly common path); best-effort — the caller
    (``open_execute_dialog``) retries and fails loud if an overlay persists.
    """
    overlay = page.locator("div.fixed.inset-0.z-50")
    open_overlay = page.locator('div.fixed.inset-0.z-50[data-state="open"]')
    for _ in range(max_attempts):
        if overlay.count() == 0:
            return
        if open_overlay.count() > 0:
            page.keyboard.press("Escape")
        page.wait_for_timeout(settle_ms)


def open_execute_dialog(
    page: "Page", *, timeout: float = 5_000, attempts: int = 3
) -> None:
    """Click the toolbar Execute button and wait for the execute dialog.

    Robust against a lingering modal overlay intercepting the toolbar click: a
    prior dialog's backdrop — or a dialog that opens in the race between the
    dismiss and the click — can cover ``main-execute-button`` on a fast CI runner
    and hang the click for 30s (the recurring s61/s67 flake). So each attempt
    first dismisses overlays (``dismiss_modal_overlays``), then clicks with a
    short per-attempt timeout, and retries — re-dismissing any overlay that raced
    in — before failing loud with a clear message instead of an opaque
    "element intercepts pointer events" 30s timeout.

    Parameters
    ----------
    page:
        The Playwright Page (must be on the app root ``/`` with a manifest loaded).
    timeout:
        Maximum milliseconds to wait for the dialog to become visible per attempt.
    attempts:
        How many dismiss+click cycles to try before failing loud.
    """
    last_exc = None
    for _ in range(attempts):
        dismiss_modal_overlays(page)
        try:
            page.get_by_test_id("main-execute-button").click(timeout=8_000)
            page.get_by_test_id("execute-dialog").wait_for(
                state="visible", timeout=timeout
            )
            return
        except Exception as exc:  # noqa: BLE001 — retry on intercepted click / open race
            last_exc = exc
    raise AssertionError(
        "open_execute_dialog: the toolbar Execute button stayed blocked after "
        f"{attempts} dismiss+click attempts — a modal overlay is intercepting "
        "pointer events (a prior execute/confirm/lock/prune dialog did not close "
        f"on Escape). Last error: {last_exc!r}"
    )


# ---------------------------------------------------------------------------
# Manifest open helpers
# ---------------------------------------------------------------------------


def load_manifest(page: "Page", db_path: str, *, timeout: float = 30_000) -> str:
    """Type a manifest path into the toolbar input and open it.

    Fills ``main-manifest-input``, clicks ``main-manifest-open-button``,
    then waits for ``main-status-bar`` to show the ``"N groups · M files"``
    text that indicates the manifest was loaded successfully.

    Parameters
    ----------
    page:
        The Playwright Page (must be on the app root ``/``).
    db_path:
        Absolute path to the ``.db`` manifest file.
    timeout:
        Maximum milliseconds to wait for the manifest to load.

    Returns
    -------
    str
        The status bar text once the manifest is loaded.
    """
    page.get_by_test_id("main-manifest-input").fill(db_path)
    page.get_by_test_id("main-manifest-open-button").click()
    return wait_manifest_loaded(page, timeout=timeout)


def open_manifest_via_picker(
    page: "Page",
    db_path: str,
    *,
    open_trigger_testid: str = "main-empty-open",
    timeout: float = 30_000,
) -> str:
    """Open a manifest through the FsBrowser file picker.

    Seeds ``main-manifest-input`` with the manifest's *directory* so the
    picker opens there (it uses the input value as its initial path), clicks
    the chosen open affordance (the empty-state ``main-empty-open`` button by
    default, or ``menu-file-open``), selects the manifest entry, confirms, and
    waits for the manifest to load.

    Parameters
    ----------
    page:
        The Playwright Page on the app root ``/``.
    db_path:
        Absolute path to the ``.db`` / ``.sqlite`` manifest file.
    open_trigger_testid:
        The testid of the element that opens the picker. Defaults to the
        empty-state ``main-empty-open`` button; pass ``"menu-file-open"`` to
        drive it from the menu bar instead.
    timeout:
        Maximum milliseconds to wait at each step.

    Returns
    -------
    str
        The status bar text once the manifest is loaded.
    """
    folder = os.path.dirname(db_path)
    name = os.path.basename(db_path)
    page.get_by_test_id("main-manifest-input").fill(folder)
    page.get_by_test_id(open_trigger_testid).click()
    page.get_by_test_id("fs-browser").wait_for(state="visible", timeout=timeout)
    entry = page.get_by_test_id("fs-browser-entry").filter(has_text=name)
    entry.first.wait_for(state="visible", timeout=timeout)
    entry.first.click()
    page.get_by_test_id("fs-browser-confirm").click()
    return wait_manifest_loaded(page, timeout=timeout)
