"""Web scenario s38 — Invalid scan source path surfaces a named, recoverable error.

Ported from ``qa/scenarios/s38_scan_dialog_invalid_path.py`` (Qt UIA).

Qt intent (#144, #216):
  #144 — typing a non-existent source path must NOT silently no-op: the
         dialog surfaces an inline error that NAMES the offending path, the
         source list does not grow, and a subsequent valid path clears it.
  #216 — the output Browse… button must open the polished "Save Manifest As"
         native save-file dialog (not a folder picker).

Web port — what carries over, and the one divergence:

  #144 (PORTED — via the web-idiomatic scan-failure surface, with a
  validation-TIMING divergence):
    The desktop validates the typed path UP FRONT, when the user clicks
    ``+ Add`` (a dialog-level gate, before any scan), via
    ``Path(raw).is_dir()`` in ``_FolderTreePanel._on_add_typed``
    (scan_dialog.py). The web ``SourceRow`` is a bare text input with NO
    pre-scan existence check: a bad source path is accepted by
    ``POST /api/scan``, then the scanner's ``scan_sources`` raises
    ``FileNotFoundError: Source directory not found: <path>``
    (scanner/walker.py). The SSE stream emits that as a ``failed`` event;
    the store sets ``scan.status='failed'`` + ``scan.error=<msg>``
    (useAppStore.ts), and ``ScanDialog`` renders a ``role="alert"``
    paragraph carrying the message — the dialog STAYS OPEN on failure.

    So the same user-facing GUARANTEE holds — a bad path produces a visible
    error that NAMES the path, never a silent no-op — through the
    web-idiomatic scan-failure surface rather than an inline pre-add label.
    The "valid path clears the error" half ports as "a corrected path +
    re-scan clears the failure alert and the dialog runs to completion".

    Pre-scan inline validation (a ``GET /api/fs/stat`` probe so the web
    could block BEFORE the scan, matching the desktop timing exactly) is a
    deferred enhancement — tracked as #678 item C (Inline path validation) —
    declined here to avoid adding a filesystem-information-disclosure
    endpoint for a timing-only nicety. This port mirrors #677's honest
    single-select s64 port: port the observable guarantee now, leave the
    exact-parity surface as tracked backlog.

  #216 (SKIP): the web output Browse uses the in-DOM ``FsBrowser`` save-mode
    picker, already exercised by s17. There is no native OS "Save Manifest
    As" window in a browser, so the #216 dialog-flavour assertion has no web
    analog.

Assertions:
  1. Open scan dialog; fill source row 0 with a NON-EXISTENT path + a valid
     output path; Start Scan.
  2. A ``role="alert"`` appears inside the scan dialog and NAMES the
     offending path (the #144 "no silent no-op" guarantee).
  3. The scan dialog STAYS OPEN on failure (the user can correct + retry).
  4. Correct the source path to a real (empty) directory; Start Scan again.
  5. The failure alert clears (status leaves ``'failed'``) AND the dialog
     closes once the corrected scan completes — the failed state is
     recoverable, not sticky.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    add_scan_source,
    open_scan_dialog,
    set_output_path,
    start_scan,
)
from qa.web.testid_constants import SCAN_DIALOG, scan_source_path_testid

# A path component distinctive enough to assert on regardless of OS path
# separators — this scenario runs on Windows locally and Linux in CI.
_BAD_LEAF = "nonexistent_xyz123_s38"


def run(*, base_url: str) -> None:
    # A real, empty temp dir is the "valid" recovery source; the bad path is
    # a leaf UNDER it that we never create, so it is guaranteed not to exist.
    valid_dir = tempfile.mkdtemp(prefix="s38_valid_")
    bad_path = str(Path(valid_dir) / _BAD_LEAF)
    output_path = str(Path(valid_dir) / "out_manifest.sqlite")
    try:
        assert not Path(bad_path).exists(), (
            f"precondition broken — {bad_path!r} unexpectedly exists; the "
            f"failure case stops being a failure case"
        )

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── 1. Bad source path → scan fails with a named error ──────────
            open_scan_dialog(page)
            add_scan_source(page, bad_path, idx=0, label="bad")
            set_output_path(page, output_path)
            start_scan(page)

            # 2. The error surfaces as a role="alert" paragraph INSIDE the scan
            # dialog (ScanDialog renders it only while status==='failed').
            # Scope to the dialog so we never match an unrelated page alert.
            alert = page.get_by_test_id(SCAN_DIALOG).get_by_role("alert")
            alert.wait_for(state="visible", timeout=30_000)
            alert_text = alert.inner_text()
            assert _BAD_LEAF in alert_text, (
                f"scan-failure alert must NAME the offending path (the #144 "
                f"'no silent no-op' guarantee). Expected substring "
                f"{_BAD_LEAF!r} in {alert_text!r}"
            )

            # 3. The dialog STAYS OPEN on failure (the user can fix + retry).
            assert page.get_by_test_id(SCAN_DIALOG).is_visible(), (
                "scan dialog must stay open after a failed scan so the user "
                "can correct the path — it closed, hiding the error"
            )

            # ── 4. Corrected path → error clears, scan recovers ────────────
            # Re-fill ONLY with a valid (empty) dir — label stays non-empty so
            # canStart still holds; the dir exists but has no media →
            # completed_empty → finished → the dialog closes.
            add_scan_source(page, valid_dir, idx=0, label="retry")
            assert page.get_by_test_id(scan_source_path_testid(0)).input_value() == valid_dir
            start_scan(page)

            # 5a. The failure alert clears the moment the new run flips status
            # off 'failed' (the #144 "valid input clears the error" analog)...
            alert.wait_for(state="hidden", timeout=10_000)
            # 5b. ...and the corrected scan runs to completion, closing the
            # dialog (proves the failed state was recoverable, not sticky).
            page.get_by_test_id(SCAN_DIALOG).wait_for(state="hidden", timeout=30_000)
    finally:
        shutil.rmtree(valid_dir, ignore_errors=True)
