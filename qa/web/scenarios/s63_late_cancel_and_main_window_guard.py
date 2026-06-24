"""Web scenario s63 — late-stage cancel + beforeunload scan-guard.

Ported from qa/scenarios/s63_late_cancel_and_main_window_guard.py (Qt UIA).

Qt intent (ONE scenario, TWO parts):
  - Part 1 (_part1_late_cancel): cancel a scan at the post-WALK HASH stage and
    prove the cancel is clean and the output manifest is never written.
  - Part 2 (_part2_main_window_x_guard, #468): while a scan is running, the
    MainWindow.closeEvent pops a "Scan in progress" Yes/No QMessageBox
    (Yes = cancel the worker and close, No = keep running). Qt guards on the
    MainWindow.scan_running flag.

Web port:
  The browser has no interceptable window-close with a custom dialog — the only
  close-time hook is the native ``beforeunload`` event, which we can ARM
  (preventDefault) but whose Leave/Stay UI the browser owns. So the web analog
  of Qt's close-guard is ``useBeforeUnloadGuard`` (mounted in App): it blocks
  unload only while a scan is running. We cannot wire "Leave -> cancel the
  worker" (beforeunload exposes no async continuation), so cancellation stays on
  the ScanDialog Cancel button (the s03 path). Part 1's late-cancel and Part 2's
  close-guard share one running scan, bracketed by two negative checks:

    1. Idle baseline — before any scan, dispatch a cancelable ``beforeunload``
       and assert it is NOT defaultPrevented (the guard is dormant when idle).
       This is the non-vacuity anchor: it proves the guard is CONDITIONAL, so
       the armed assertion below is not a tautology of an always-on handler.
    2. Start a scan of the large disposable source (thousands of 1-KiB stub
       JPEGs, the s03 fixture) and wait for the HASH stage marker — running now.
    3. Part 2 (ARMED): dispatch ``beforeunload`` while running and assert it IS
       defaultPrevented — the close-guard fires (the #687 feature; web analog of
       Qt's "Scan in progress" box appearing).
    4. Part 1 (LATE CANCEL): click scan-cancel-button, assert the dialog hides
       cleanly (cooperative stop) and the output .db was never written (absent on
       disk + GET /api/manifest 404) — the WRITE gate skipped the manifest write.
    5. RELEASED: after the cancel settles (empty-state visible), dispatch
       ``beforeunload`` again and assert it is NOT defaultPrevented — the guard
       released when status left 'running'. Together with step 1 this brackets
       the armed assertion with two negatives, so a broken / always-on / never-on
       guard cannot pass.

Assertion strategy (why page.evaluate, not page.on('dialog')):
  A native beforeunload confirm is browser chrome Playwright auto-dismisses and
  cannot reliably observe; driving it via page.close(run_before_unload=True) or
  a reload risks a CI hang (playwright#20624). Instead we dispatch a cancelable
  ``beforeunload`` Event through window.dispatchEvent and read
  event.defaultPrevented — the deterministic, platform-identical
  (Windows == ubuntu CI) signal of whether the app's handler called
  preventDefault. The test never calls preventDefault itself (that would be a
  tautology); only the app under test does.

Qt -> web divergences:
  - D1 (no custom close dialog): Qt renders a QMessageBox with Yes/No; the web
    can only arm the browser-native confirm. The asserted contract is "unload is
    blocked while running", not the dialog's button labels.
  - D2 (cancel trigger): Qt's title-bar X *is* the cancel; the web keeps the
    explicit ScanDialog Cancel button (same cooperative cancel token) — Part 1
    cancels via that button, matching s03.
  - D3 (manifest-untouched is absence-based): a cancelled web scan never writes
    its output .db (the WRITE gate logs 'Scan cancelled.' first), so untouched =
    absent on disk + 404, as in s03.
  - D4 (large source): the HASH stage on the tiny sandbox finishes sub-second on
    fast CI, so the armed window would close before we can dispatch; the large
    source guarantees a multi-second running window on any runner (s03 lesson).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    add_scan_source,
    open_scan_dialog,
    set_output_path,
    start_scan,
    wait_log_line,
)
from qa.web.testid_constants import (
    MAIN_EMPTY_STATE,
    SCAN_CANCEL_BUTTON,
    SCAN_DIALOG,
)

_REPO = Path(__file__).resolve().parents[3]

# Stub count for the large source.  Big enough that the HASH pass is still
# running when the armed-guard dispatch + cancel arrive a beat after the
# 'Hashing' marker logs.  Mirrors s03's _WALK_SOURCE_STUB_COUNT.
_WALK_SOURCE_STUB_COUNT = 6000

# Dispatch a *cancelable* beforeunload and report event.defaultPrevented.  The
# test never preventDefaults the event — only the app's window listener may — so
# a True result is a genuine signal that useBeforeUnloadGuard fired.
_DISPATCH_BEFOREUNLOAD = (
    "() => { const e = new Event('beforeunload', { cancelable: true }); "
    "window.dispatchEvent(e); return e.defaultPrevented; }"
)


def _beforeunload_prevented(page) -> bool:
    """Whether the app's beforeunload guard called preventDefault (see above)."""
    return bool(page.evaluate(_DISPATCH_BEFOREUNLOAD))


def _manifest_status(base_url: str, db_path: str) -> int:
    """HTTP status of GET /api/manifest?path=<db_path> (404 == never written)."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
            return resp.getcode()
    except urllib.error.HTTPError as exc:
        return exc.code


def _build_large_source() -> str:
    """Build the large disposable source on demand (idempotent); return its path.

    Imports the sibling generator lazily (it pulls PIL), mirroring s03.
    """
    sys.path.insert(0, str(_REPO / "scripts"))
    from make_qa_large_source import make_large_source  # noqa: E402

    src = make_large_source(_WALK_SOURCE_STUB_COUNT)
    return str(src)


def run(*, base_url: str) -> int:
    """Arm the close-guard while running, then late-cancel cleanly.

    Returns 0 on success; raises AssertionError on any failed invariant.
    """
    # Output path for the cancelled scan must NOT exist going in (absence check).
    tmpdir = tempfile.mkdtemp(prefix="qa_s63_")
    db_path = os.path.join(tmpdir, "s63_cancelled_output.db")
    try:
        assert not os.path.exists(db_path), (
            f"s63 precondition: output path {db_path!r} already exists; the "
            "manifest-untouched absence check would be meaningless"
        )

        src = _build_large_source()

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")
            # Wait for the App shell to mount before the idle baseline — the
            # empty-state only renders once App is interactive, and App is where
            # useBeforeUnloadGuard's useEffect registers the listener.  Without
            # this, the idle dispatch could run before the listener exists and
            # return false "for the wrong reason" (a hollow non-vacuity anchor).
            page.get_by_test_id(MAIN_EMPTY_STATE).wait_for(
                state="visible", timeout=10_000
            )

            # ---- Step 1 — idle baseline: the guard is dormant -----------------
            assert _beforeunload_prevented(page) is False, (
                "s63 idle baseline: beforeunload was defaultPrevented before any "
                "scan ran — useBeforeUnloadGuard must stay dormant while "
                "scan.status != 'running' (else the user can never close the tab)"
            )

            # ---- Step 2 — start a large scan; wait for the HASH stage ---------
            open_scan_dialog(page)
            add_scan_source(page, src, idx=0)
            set_output_path(page, db_path)
            # Non-blocking start: we must observe the running window and cancel
            # mid-run, so do NOT call run_scan / wait_manifest_loaded here.
            start_scan(page)
            try:
                wait_log_line(page, "Hashing", timeout=60_000)
            except Exception as exc:  # playwright TimeoutError or unmounted log
                raise AssertionError(
                    "s63 setup: never observed the 'Hashing' stage marker before "
                    "the scan finished — the large source hashed too fast to land "
                    "the armed-guard check + cancel on this runner.  LEAD can bump "
                    f"_WALK_SOURCE_STUB_COUNT (currently {_WALK_SOURCE_STUB_COUNT})."
                    f"  Underlying: {exc!r}"
                ) from exc

            # ---- Step 3 — Part 2 (ARMED): close-guard fires while running -----
            assert _beforeunload_prevented(page) is True, (
                "s63 Part 2 (close-guard): beforeunload was NOT defaultPrevented "
                "while a scan is running — useBeforeUnloadGuard failed to arm, so "
                "closing the tab would silently abandon the running scan.  (If the "
                "scan raced to completion first, bump _WALK_SOURCE_STUB_COUNT.)"
            )

            # ---- Step 4 — Part 1 (LATE CANCEL): clean + manifest untouched ----
            cancel_btn = page.get_by_test_id(SCAN_CANCEL_BUTTON)
            try:
                cancel_btn.wait_for(state="visible", timeout=3_000)
            except Exception as exc:
                raise AssertionError(
                    "s63 Part 1: scan-cancel-button vanished before it could be "
                    "clicked — the scan finished before the cancel landed.  LEAD "
                    f"can bump _WALK_SOURCE_STUB_COUNT.  Underlying: {exc!r}"
                ) from exc
            cancel_btn.click()

            # Clean-cancel proof: scan.status='cancelled' -> dialog hides; a
            # dialog still open here would mean the worker is stuck.
            page.get_by_test_id(SCAN_DIALOG).wait_for(state="hidden", timeout=10_000)
            # App still usable: empty-state visible (loadManifest is only called on
            # the SSE 'finished' transition, never on 'cancelled').
            page.get_by_test_id(MAIN_EMPTY_STATE).wait_for(
                state="visible", timeout=10_000
            )

            # Manifest-untouched (absence, D3): the cancel pre-empts the WRITE
            # stage so the output .db is never produced.
            assert not os.path.exists(db_path), (
                f"s63 Part 1: output manifest {db_path!r} was written despite the "
                "cancel — the WRITE gate must skip the manifest write on cancel"
            )
            status = _manifest_status(base_url, db_path)
            assert status == 404, (
                f"s63 Part 1: GET /api/manifest?path={db_path!r} returned HTTP "
                f"{status}, expected 404 — a cancelled scan must leave no manifest"
            )

            # ---- Step 5 — RELEASED: guard dormant again after the cancel ------
            assert _beforeunload_prevented(page) is False, (
                "s63 release: beforeunload was still defaultPrevented after the "
                "scan was cancelled — the guard must release when status leaves "
                "'running' (else the user can never close the tab post-cancel)"
            )
    finally:
        # The absence asserts above run BEFORE this cleanup, so a "db was
        # written" bug is captured as a RED before the dir is removed.  rmtree
        # clears the tmpdir (and the cancelled scan's would-be output) regardless
        # of unlink ordering.
        shutil.rmtree(tmpdir, ignore_errors=True)
    return 0
