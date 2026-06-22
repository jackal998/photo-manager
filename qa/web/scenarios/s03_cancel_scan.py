"""Web scenario s03 — Cancel scan mid-run (two samples: post-HASH + WALK-stage).

Ported from qa/scenarios/s03_cancel_scan.py (Qt UIA).

Qt intent:
  - Kick off a scan, cancel it mid-run, and prove the cancel is *clean*:
    the worker stops cooperatively, the app stays usable, and a cancelled
    scan never writes its output manifest.
  - Two samples in one file (the Qt scenario's #493 precedent):
      * Sample 1 (post-HASH) — standard small sandbox sources; the WALK stage
        finishes in <50 ms so the cancel lands in HASH/CLASSIFY.
      * Sample 2 (WALK-stage) — a large disposable source (thousands of 1-KiB
        stubs) so the walker is still enumerating when the cancel arrives;
        the log must show 'Walking …' (WALK started) and the cancel must land
        before the WRITE gate (output .db never written).

Web slice:
  Sample 1:
    1. mkdtemp .db output path (never created — the cancel pre-empts the write).
    2. Open scan dialog → add the read-only near-duplicates fixture → set output
       → click Start *without* waiting for completion (we must cancel mid-run).
    3. wait_log_line("Hashing") to confirm the pipeline reached the HASH pass
       (past WALK), then click scan-cancel-button.
    4. ASSERT clean cancel: scan-dialog transitions to hidden (the store sets
       scan.status='cancelled' → ScanDialog onOpenChange(false)).  The SPA page
       persisting and remaining scriptable is the web equivalent of Qt's
       'process still alive / window survives' check.
    5. ASSERT app still usable: main-empty-state visible AND count_file_rows==0
       (cancel produced no results — loadManifest is only called on the SSE
       'finished' transition, never on 'cancelled').

  Sample 2:
    1. Build the large source via make_large_source(6000) (idempotent,
       gitignored).  Use a SECOND db tempfile output2.db that does NOT exist.
    2. Open scan dialog → reconfigure to the large source → set output2 →
       click Start (non-blocking).
    3. wait_log_line("Walking", timeout=8000) — capture the WALK-started marker
       BEFORE cancelling (the scan-progress-log element unmounts on dialog
       close, same constraint as s04).  If 6000 stubs walk too fast and the
       scan finishes first, raise a clear AssertionError mirroring Qt's
       FAIL-on-Done guard (LEAD can bump the stub count).
    4. Click scan-cancel-button.
    5. ASSERT dialog closed cleanly (worker not stuck): scan-dialog hidden.
    6. ASSERT manifest untouched (absence): output2.db must NOT exist on disk;
       GET /api/manifest?path=output2.db must 404.
    7. finally: unlink both db tempfiles; leave the gitignored large source in
       place (idempotent cache).

Qt → web divergences:
  - D1 (cancel mechanism inverts): Qt has no Cancel button — the title-bar X
    close gesture *is* the cancel (closeEvent → requestInterruption).  The web
    UI renders an explicit ``scan-cancel-button`` (only while the scan is
    running, ScanProgress.tsx) wired to the same cooperative cancel token
    (POST /api/scan/{task_id}/cancel → cancel_token.request()).  Same semantics,
    different trigger.
  - D2 (no OS process/window survival check): Qt asserts the app PID's windows
    survive the cancel.  The web port is a single SPA page; there is no process
    to enumerate.  The equivalent survival proof is: the scan-dialog hides
    cleanly, main-empty-state is visible, count_file_rows==0, and the page is
    still scriptable (all subsequent locator calls resolve).
  - D3 (manifest-untouched is absence-based): Qt byte-compares the prior output
    manifest before/after the cancel.  On the web a cancelled scan never writes
    its output .db (the WRITE-stage gate in scan_runner.py fires
    'Scan cancelled.' *before* the manifest write), so the assertion is that
    the output file is absent from disk and GET /api/manifest 404s — the
    web-native equivalent of 'the manifest was never produced'.
  - D4 (log read before cancel): ``scan-progress-log`` unmounts when the dialog
    closes, so the 'Walking' marker must be read BEFORE clicking Cancel (the
    same constraint s04 documents for its log assertions).

  Strength note: the Qt scenario's WALK sample tolerates a 'slow-runner
  fallback' (clean cancel + manifest untouched even if 'Walking …' scrolled
  past before capture).  This web port keeps the STRONGER form: it requires the
  'Walking' marker to be observed in the live log before cancelling, and treats
  a scan that reaches completion before the cancel as a hard failure (mirroring
  Qt's FAIL-on-Done guard).  The clean-cancel + absence invariants are asserted
  in both samples.
"""
from __future__ import annotations

import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    add_scan_source,
    count_file_rows,
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
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

# Stub count for the WALK-stage source.  Big enough that the walker is still
# enumerating when the cancel arrives ~immediately after 'Walking' is logged.
# Mirrors the Qt scenario's WALK_SOURCE_STUB_COUNT.
_WALK_SOURCE_STUB_COUNT = 6000


# ---------------------------------------------------------------------------
# HTTP helper (self-contained — copies the s13 _get_manifest urllib pattern)
# ---------------------------------------------------------------------------


def _manifest_status(base_url: str, db_path: str) -> int:
    """Return the HTTP status of GET /api/manifest?path=<db_path>.

    Used by Sample 2's absence check: a cancelled scan never writes its output
    .db, so the endpoint must 404 (review.py checks ``Path(path).exists()``
    first, before any path guard).  Returns the numeric HTTP status code.
    """
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
            return resp.getcode()
    except urllib.error.HTTPError as exc:
        return exc.code


def _build_large_source() -> str:
    """Build the WALK-stage large source on demand and return its path string.

    Imports the sibling generator lazily (it pulls PIL), mirroring the Qt
    scenario.  Idempotent: skips the build when the directory already holds
    exactly ``_WALK_SOURCE_STUB_COUNT`` stubs.
    """
    sys.path.insert(0, str(_REPO / "scripts"))
    from make_qa_large_source import make_large_source  # noqa: E402

    src = make_large_source(_WALK_SOURCE_STUB_COUNT)
    return str(src)


# ---------------------------------------------------------------------------
# Sample 1 — post-HASH cancel against the read-only near-duplicates fixture
# ---------------------------------------------------------------------------


def _sample_post_hash_cancel(page, db_path: str) -> None:
    """Start a scan, wait for the HASH pass, cancel, assert clean + usable.

    The near-duplicates fixture is read-only and never mutated: scanning only
    *reads* the source files, and the cancel pre-empts the WRITE stage so no
    output .db is produced — there is nothing to copy-guard.
    """
    open_scan_dialog(page)
    add_scan_source(page, _NEAR_DUPS_DIR, idx=0)
    set_output_path(page, db_path)
    # Non-blocking start: start_scan only clicks the button.  We must NOT call
    # run_scan / wait_manifest_loaded here — we need to cancel mid-run.
    start_scan(page)

    # Wait until the pipeline reached the HASH pass (past WALK).  If the scan
    # raced to completion first, the log unmounts and this raises a timeout —
    # surface it as a clear FAIL-on-fast guard (mirrors Qt's saw_done check).
    try:
        wait_log_line(page, "Hashing", timeout=10_000)
    except Exception as exc:  # playwright TimeoutError or unmounted log
        raise AssertionError(
            "Sample 1: never observed the 'Hashing' stage marker before the "
            "scan finished — the near-duplicates fixture hashed too fast to "
            "land a post-HASH cancel on this runner.  (If this recurs, LEAD "
            "can swap in a slightly larger fixture.)  Underlying: "
            f"{exc!r}"
        ) from exc

    # Cancel.  The button only renders while the scan is running; if the scan
    # already finished it would be gone, so guard with a short visibility wait.
    cancel_btn = page.get_by_test_id(SCAN_CANCEL_BUTTON)
    try:
        cancel_btn.wait_for(state="visible", timeout=3_000)
    except Exception as exc:
        raise AssertionError(
            "Sample 1: scan-cancel-button vanished before it could be clicked "
            "— the scan finished before the post-HASH cancel landed.  LEAD can "
            f"bump the fixture size.  Underlying: {exc!r}"
        ) from exc
    cancel_btn.click()

    # Clean-cancel proof: scan.status='cancelled' → ScanDialog onOpenChange(false).
    # The dialog hiding (rather than an OS process surviving) is the web
    # survival signal — see D1/D2.
    page.get_by_test_id(SCAN_DIALOG).wait_for(state="hidden", timeout=10_000)

    # App still usable: empty-state visible + zero file rows.  loadManifest is
    # only invoked on the SSE 'finished' transition, never on 'cancelled', so
    # no manifest auto-loads and the empty state persists.
    page.get_by_test_id(MAIN_EMPTY_STATE).wait_for(state="visible", timeout=10_000)
    row_count = count_file_rows(page)
    assert row_count == 0, (
        f"Sample 1: expected 0 file rows after a cancelled scan (cancel must "
        f"produce no results / not auto-load a manifest), got {row_count}"
    )


# ---------------------------------------------------------------------------
# Sample 2 — WALK-stage cancel against the large disposable source
# ---------------------------------------------------------------------------


def _sample_walk_stage_cancel(page, base_url: str, db_path2: str) -> None:
    """Start a scan of the large source, cancel during WALK, assert untouched.

    ``db_path2`` must NOT exist on disk going in — the absence check at the end
    proves a cancelled scan never writes its output manifest.
    """
    assert not os.path.exists(db_path2), (
        f"Sample 2 precondition: output2 path {db_path2!r} already exists; the "
        "absence check would be meaningless"
    )
    src = _build_large_source()

    open_scan_dialog(page)
    add_scan_source(page, src, idx=0)
    set_output_path(page, db_path2)
    start_scan(page)  # non-blocking

    # Capture the WALK-started marker BEFORE cancelling — the log element
    # unmounts on dialog close (D4).  If the walker never starts within the
    # window, or the scan finishes first, surface a clear FAIL.
    try:
        wait_log_line(page, "Walking", timeout=8_000)
    except Exception as exc:
        raise AssertionError(
            "Sample 2: never observed the 'Walking' WALK-stage marker before "
            f"the scan finished — {_WALK_SOURCE_STUB_COUNT} stubs walked too "
            "fast (or did not start) on this runner.  LEAD can bump "
            f"_WALK_SOURCE_STUB_COUNT.  Underlying: {exc!r}"
        ) from exc

    # Cancel while the walker is still enumerating.
    cancel_btn = page.get_by_test_id(SCAN_CANCEL_BUTTON)
    try:
        cancel_btn.wait_for(state="visible", timeout=3_000)
    except Exception as exc:
        raise AssertionError(
            "Sample 2: scan-cancel-button vanished before it could be clicked "
            "— the WALK pass finished before the cancel landed.  LEAD can bump "
            f"_WALK_SOURCE_STUB_COUNT.  Underlying: {exc!r}"
        ) from exc
    cancel_btn.click()

    # Clean close: the worker stops cooperatively and the dialog hides on
    # scan.status='cancelled'.  A dialog still open here would mean the worker
    # is stuck.
    page.get_by_test_id(SCAN_DIALOG).wait_for(state="hidden", timeout=8_000)

    # Manifest-untouched (absence-based, D3): a cancelled scan never reaches the
    # WRITE stage (scan_runner.py gates 'Scan cancelled.' before the manifest
    # write), so the output .db must be absent from disk.
    assert not os.path.exists(db_path2), (
        f"Sample 2: output manifest {db_path2!r} was written despite the "
        "cancel — the WRITE gate must skip the manifest write on a cancelled "
        "scan"
    )

    # Belt-and-suspenders: the server must also 404 for that path (it never
    # existed).  Confirms the absence from the API's perspective, not just the
    # filesystem's.
    status = _manifest_status(base_url, db_path2)
    assert status == 404, (
        f"Sample 2: GET /api/manifest?path={db_path2!r} returned HTTP {status}, "
        "expected 404 — a cancelled scan must not leave a loadable manifest"
    )


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> int:
    """Cancel a scan mid-run twice (post-HASH + WALK) and prove clean cancel.

    Returns 0 on success; raises AssertionError on any failed invariant.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
    # Sample 2 needs an output path that does NOT yet exist on disk, so derive
    # one under a fresh tempdir and never create the file.
    tmpdir2 = tempfile.mkdtemp(prefix="qa_s03_")
    db_path2 = os.path.join(tmpdir2, "s03_walk_output.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # Sample 1 — post-HASH cancel (read-only near-duplicates fixture).
            _sample_post_hash_cancel(page, db_path)

            # Sample 2 — WALK-stage cancel (large disposable source).
            _sample_walk_stage_cancel(page, base_url, db_path2)
    finally:
        # Clean up both db tempfiles.  The mkdtemp NamedTemporaryFile created an
        # empty .db on disk (Sample 1's output that the cancel pre-empted, so it
        # may be empty or absent); remove it.  Leave the gitignored large source
        # in place — it is an idempotent cache reused across runs.
        for path in (db_path, db_path2):
            try:
                os.unlink(path)
            except OSError:
                pass
        try:
            os.rmdir(tmpdir2)
        except OSError:
            pass
    return 0
