"""Web scenario s17 — scan dialog browse widgets + grouping-sensitivity
thresholds + display-only alphabetized source list (#736).

Ported from qa/scenarios/s17_scan_dialog_widgets.py (Qt UIA).

Qt intent:
  - The scan dialog's Browse buttons open a file picker for the source folder
    and the output manifest; picking writes back into the corresponding field.

Web-observable assertions (the part most at risk — a nested modal):
  - Source-row Browse opens the FsBrowser (directory mode) *over* the still-open
    scan dialog. Crucially the scan dialog must NOT be dismissed by the nested
    layer. Confirming returns the folder into the source path field.
  - Output Browse opens the FsBrowser (save mode) with the filename prefilled;
    confirming writes ``<dir><sep><filename>`` into the output field.

Qt divergences:
  - Qt's native QFileDialog is replaced by the in-DOM FsBrowser. The picker is
    opened pre-positioned at the field's current directory (initialPath hint),
    so the test confirms the round-trip without deep filesystem navigation.

#736 additions (Grouping-sensitivity thresholds + alphabetized source list):
  - Threshold wiring: the three Advanced-settings number inputs
    (SCAN_PHASH_THRESHOLD / SCAN_DHASH_THRESHOLD / SCAN_COLOR_THRESHOLD) are
    set to non-default values and proven to land in the POST /api/scan
    request body — captured via ``page.expect_request`` +
    ``request.post_data_json()`` (the s70 network-request lesson: assert on
    the wire, never on a grouping-count delta).
  - Display-only alphabetization: three sources are added in NON-alphabetical
    order. The rendered DOM order (read via the path-input testids in
    document order) must be alphabetized case-insensitively, while the
    POST /api/scan body's ``sources`` key order stays the original INSERTION
    order — proving the sort never touches the array that
    ``sourcesMap``/``recursiveMap`` (and dedup keeper priority) are built
    from (BUILD RISK #1), and that each row's testid ``idx`` stays bound to
    its original position (BUILD RISK #3).
  - Both new phases use a guaranteed-nonexistent source path so the scan
    fails fast (mirrors s38's bad-path pattern) — the POST body is already
    captured by the time the failure surfaces, and a fast failure keeps this
    scenario's runtime small and leaves no orphaned running scan.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import add_scan_source, open_scan_dialog, set_output_path
from qa.web.testid_constants import (
    FS_BROWSER,
    FS_BROWSER_CONFIRM,
    FS_BROWSER_FILENAME,
    SCAN_ADVANCED,
    SCAN_COLOR_THRESHOLD,
    SCAN_DHASH_THRESHOLD,
    SCAN_DIALOG,
    SCAN_OUTPUT_BROWSE,
    SCAN_OUTPUT_PATH,
    SCAN_PHASH_THRESHOLD,
    SCAN_START_BUTTON,
    scan_source_browse_testid,
    scan_source_label_testid,
    scan_source_path_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_SANDBOX_DIR = str(_REPO / "qa" / "sandbox")


def _close_failed_scan_dialog(page) -> None:
    """Wait for the failure alert, then Escape-close the (now non-running) dialog.

    Mirrors s38: a bad source path fails the scan quickly; the dialog renders
    a role="alert" and stays open (isRunning is false by then, so Escape is
    no longer blocked by the running-scan guard).
    """
    alert = page.get_by_test_id(SCAN_DIALOG).get_by_role("alert")
    alert.wait_for(state="visible", timeout=30_000)
    page.keyboard.press("Escape")
    page.get_by_test_id(SCAN_DIALOG).wait_for(state="hidden", timeout=5_000)


def _phase_threshold_wiring(page) -> None:
    """Set custom threshold values; assert they land in the POST /api/scan body."""
    tmpdir = tempfile.mkdtemp(prefix="s17_thresholds_")
    try:
        bad_path = str(Path(tmpdir) / "does_not_exist_s17_thresholds")
        output_path = str(Path(tmpdir) / "out.sqlite")

        open_scan_dialog(page)
        add_scan_source(page, bad_path, idx=0, label="bad")
        set_output_path(page, output_path)

        # The threshold inputs live inside the Advanced Settings <details>,
        # which a fresh dialog renders COLLAPSED — expand it before filling
        # (the inputs are present-but-hidden until the disclosure is open).
        page.get_by_test_id(SCAN_ADVANCED).click()
        phash = page.get_by_test_id(SCAN_PHASH_THRESHOLD)
        phash.wait_for(state="visible", timeout=5_000)
        phash.fill("5")
        page.get_by_test_id(SCAN_DHASH_THRESHOLD).fill("7")
        page.get_by_test_id(SCAN_COLOR_THRESHOLD).fill("42")

        with page.expect_request("**/api/scan") as req_info:
            page.get_by_test_id(SCAN_START_BUTTON).click()
        body = req_info.value.post_data_json
        assert body["threshold"] == 5, f"pHash threshold not wired into POST body: {body!r}"
        assert body["dhash_threshold"] == 7, f"dHash threshold not wired into POST body: {body!r}"
        assert body["mean_color_threshold"] == 42, (
            f"mean-color threshold not wired into POST body: {body!r}"
        )

        _close_failed_scan_dialog(page)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _phase_alphabetized_display_order(page) -> None:
    """Sources added out of order render alphabetized while submission order
    (and each row's testid idx) stays bound to the original insertion order."""
    tmpdir = tempfile.mkdtemp(prefix="s17_alpha_")
    try:
        root = Path(tmpdir)
        zebra, alpha, mango = (
            str(root / "zebra_missing"),
            str(root / "alpha_missing"),
            str(root / "mango_missing"),
        )
        output_path = str(root / "out.sqlite")

        open_scan_dialog(page)
        add_scan_source(page, zebra, idx=0, label="Zebra")
        add_scan_source(page, alpha, idx=1, label="Alpha")
        add_scan_source(page, mango, idx=2, label="Mango")
        set_output_path(page, output_path)

        # Rendered DOM (document) order must be alphabetized case-insensitively:
        # alpha, mango, zebra.
        path_values = page.eval_on_selector_all(
            '[data-testid^="scan-source-"][data-testid$="-path"]',
            "els => els.map(el => el.value)",
        )
        assert path_values == [alpha, mango, zebra], (
            f"source rows are not alphabetized in the DOM: {path_values!r}"
        )

        # Each row's testid stays bound to its ORIGINAL index — idx 0 is still
        # "Zebra" even though it renders last (BUILD RISK #3).
        assert page.get_by_test_id(scan_source_label_testid(0)).input_value() == "Zebra"
        assert page.get_by_test_id(scan_source_label_testid(1)).input_value() == "Alpha"
        assert page.get_by_test_id(scan_source_label_testid(2)).input_value() == "Mango"

        with page.expect_request("**/api/scan") as req_info:
            page.get_by_test_id(SCAN_START_BUTTON).click()
        body = req_info.value.post_data_json
        # Insertion order preserved (BUILD RISK #1) — NOT the alphabetized
        # display order. sourcesMap/recursiveMap must keep iterating the
        # original unsorted `sources` array so dedup keeper priority
        # (scanner/dedup.py) never silently changes.
        assert list(body["sources"].keys()) == ["Zebra", "Alpha", "Mango"], (
            f"POST /api/scan body reordered sources — dedup keeper priority "
            f"would silently change: {body['sources']!r}"
        )
        assert body["sources"] == {"Zebra": zebra, "Alpha": alpha, "Mango": mango}

        _close_failed_scan_dialog(page)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run(*, base_url: str) -> None:
    """Drive the scan dialog's Browse buttons, threshold inputs, and source
    list display-only alphabetization."""
    with PWContext(base_url=base_url) as ctx:
        page = ctx.new_page()
        page.goto("/")
        open_scan_dialog(page)

        # --- Source browse (directory mode), opened at a known folder ---------
        # Seed the path field so the picker opens there (one-click confirm).
        src_path = page.get_by_test_id(scan_source_path_testid(0))
        src_path.fill(_NEAR_DUPS_DIR)
        page.get_by_test_id(scan_source_browse_testid(0)).click()

        # Nested picker must appear AND the scan dialog must stay open.
        page.get_by_test_id(FS_BROWSER).wait_for(state="visible", timeout=5_000)
        assert page.get_by_test_id(SCAN_DIALOG).is_visible(), (
            "scan dialog was dismissed when the nested picker opened"
        )

        # Confirm the current folder; picker closes, scan dialog survives.
        page.get_by_test_id(FS_BROWSER_CONFIRM).click()
        page.get_by_test_id(FS_BROWSER).wait_for(state="hidden", timeout=5_000)
        assert page.get_by_test_id(SCAN_DIALOG).is_visible(), (
            "scan dialog was dismissed after confirming the picker"
        )
        picked_src = src_path.input_value()
        assert picked_src.lower().endswith("near-duplicates"), (
            f"source path not written by picker: {picked_src!r}"
        )

        # --- Output browse (save mode), filename prefilled --------------------
        out_field = page.get_by_test_id(SCAN_OUTPUT_PATH)
        out_field.fill(os.path.join(_SANDBOX_DIR, "out.db"))
        page.get_by_test_id(SCAN_OUTPUT_BROWSE).click()
        page.get_by_test_id(FS_BROWSER).wait_for(state="visible", timeout=5_000)
        assert page.get_by_test_id(FS_BROWSER_FILENAME).input_value() == "out.db", (
            "save-mode filename was not prefilled from the output field"
        )
        page.get_by_test_id(FS_BROWSER_CONFIRM).click()
        page.get_by_test_id(FS_BROWSER).wait_for(state="hidden", timeout=5_000)
        picked_out = out_field.input_value()
        assert picked_out.endswith("out.db"), (
            f"output path not written by picker: {picked_out!r}"
        )

        # Clean up: close the dialog.
        page.keyboard.press("Escape")
        page.get_by_test_id(SCAN_DIALOG).wait_for(state="hidden", timeout=5_000)

        # --- #736: grouping-sensitivity threshold wiring -----------------------
        _phase_threshold_wiring(page)

        # --- #736: display-only alphabetized source list -----------------------
        _phase_alphabetized_display_order(page)
