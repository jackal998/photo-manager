"""Web scenario s05 — Heavy-image preview + full-res render.

Ported from qa/scenarios/s05_huge_preview.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/huge (a single 50-MP JPEG) without crashing.
  - Select the (only) file row, then hammer it with rapid Enter / arrow
    keys and three rapid double-clicks as a perf-TIMING smoke probe.
  - Read the PreviewPane text by automation_id and print it.
  - The Qt driver has ZERO assertions: it prints every step, tolerates
    "no file row found" (the lone huge file is a singleton the scanner
    drops, so there is often nothing to select), and times the rapid
    clicks only to print the elapsed seconds — it never asserts on them.

Web slice (UPGRADED — see "Strengthening" below):
  1. Copy qa/sandbox/huge/huge_50mp.jpg into a tmpdir AND make a second
     byte-identical copy in the same tmpdir, so the two form an EXACT-hash
     duplicate pair.  A lone heavy file is a singleton that the scanner
     drops (scanner/dedup.py:300 `if len(group) < 2: continue`), yielding
     0 groups / 0 rows — exactly the "no file row" case the Qt driver
     merely tolerated.  Duplicating the file is what gives us a
     SELECTABLE result-tree row to drive the preview from.
  2. run_scan([tmpdir]) → fresh manifest.db under the same tmpdir.
  3. GET /api/manifest → assert total_groups >= 1; derive group_id =
     str(groups[0]["group_number"]) and the huge basename from items
     (mirrors s51 lines 95-104 / s13 lines 190-201).
  4. Click the file row → store.setSelectedFile fires → PreviewPane mounts.
  5. ASSERT the preview rendered: PREVIEW_SINGLE_IMAGE becomes visible
     (and PREVIEW_INFO, the metadata sidebar).  This is the real check
     the Qt driver lacked — proof the heavy image decoded into the
     bounded size=512 thumbnail without crashing the server.
  6. Heavy-image full-res probe: double-click the same row →
     store.openFullRes fires → FullResViewer mounts.  ASSERT FULLRES_IMAGE
     becomes visible.  The full-res <img> carries inline
     `visibility: hidden` until its onLoad fires (FullResViewer.tsx:224),
     so wait_for(state="visible") only resolves once the heavy GET
     /api/image?size=0 decode actually completes.  The 1.48 MB fixture is
     far under the 40 MiB full-res guard (app/web/routes/image.py:24), so
     the 413 too-large path never triggers and the image renders.
  7. Press Escape to close the full-res overlay.
  8. finally: shutil.rmtree(tmpdir).

Strengthening (Qt -> web, intentional, NOT a literal port):
  The Qt s05 has NO assertions and treats "no file row found" as an
  acceptable outcome — it is a print-only smoke probe.  The web port turns
  the preview + full-res render into HARD assertions (PREVIEW_SINGLE_IMAGE
  visible, FULLRES_IMAGE visible).  This is a deliberate upgrade: the
  desktop intent ("scan a heavy image without crashing, then exercise the
  preview affordances") is better verified by asserting the affordances
  actually rendered than by printing and tolerating their absence.

Qt divergences:
  - DUPLICATED FIXTURE: Qt scans qa/sandbox/huge directly (1 file).  Both
    the Qt and web scanners drop singleton (non-duplicate) files from the
    manifest, so scanning the lone huge file yields 0 rows.  The web port
    copies the file into a tmpdir AND makes a byte-identical second copy so
    they form a 2-member EXACT-dup group with a selectable row.  Without
    this there is nothing to preview.  (The tmpdir copy also guards the repo
    fixture — though this scenario is non-destructive, the copy-to-tmpdir
    pattern is the harness convention from s13.)
  - NO MICRO-BENCHMARK: Qt drives rapid keyboard nav + three rapid
    double-clicks and prints rapid_click_elapsed_s as a TIMING probe.  The
    web port omits this micro-benchmark: Qt never asserted on the timing
    (only printed it), and Playwright has no faithful "rapid-click timing"
    assertion that would not be flaky across CI hardware.  The single
    double-click that opens full-res is retained because it exercises a real
    affordance (the heavy full-res decode), not a timing curve.
  - PREVIEW READ BY TESTID, NOT automation_id: Qt reads PreviewPane text by
    walking descendants and matching `automation_id contains "PreviewPane"`,
    then prints the text.  The web port asserts PREVIEW_SINGLE_IMAGE /
    PREVIEW_INFO visibility directly via data-testid — the structural
    equivalent of "the preview pane rendered".
  - BOUNDED THUMBNAIL DECODE: the web inline preview is a bounded size=512
    decode (PreviewPane.tsx:82, `thumbnailUrl(path, 512)`), not Qt's native
    full-window render of the source image.  The full-res overlay
    (FULLRES_IMAGE) is the web equivalent of viewing the heavy image at
    native resolution (GET /api/image?size=0).
  - FULL-RES TRIGGER: in the web UI a double-click on the result-tree FILE
    ROW opens full-res (FileRow.tsx:57 onDoubleClick -> onOpenFullRes).
    Double-clicking the preview thumbnail (PreviewPane.tsx:85) is an
    equivalent path; the row dblclick is used here because the row is
    already located.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan
from qa.web.testid_constants import (
    PREVIEW_SINGLE_IMAGE,
    PREVIEW_INFO,
    FULLRES_IMAGE,
    row_file_testid,
)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_HUGE_FILE = _REPO / "qa" / "sandbox" / "huge" / "huge_50mp.jpg"


# ---------------------------------------------------------------------------
# HTTP helper (self-contained per convention — mirrors s13 lines 119-128)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>.

    Uses urllib (stdlib only) to keep the module dependency-free at import
    time, matching the convention established by s13/s51.
    """
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Scan a duplicated heavy image, select the row, assert preview + full-res.

    Copies the 50-MP JPEG into a tmpdir twice (byte-identical) so the two
    form an EXACT-hash duplicate group with a selectable result-tree row,
    then asserts that selecting the row renders the bounded preview and that
    double-clicking it renders the full-resolution overlay — the heavy-image
    "did it decode without crashing" checks the Qt driver only printed.
    """
    tmpdir = tempfile.mkdtemp(prefix="qa_s05_")
    try:
        # ── Set up tmpdir with two byte-identical copies of the heavy file ──
        assert _HUGE_FILE.exists(), (
            f"Heavy fixture missing: {_HUGE_FILE} "
            "(expected qa/sandbox/huge/huge_50mp.jpg)"
        )
        orig_name = _HUGE_FILE.name  # "huge_50mp.jpg"
        copy_name = f"{_HUGE_FILE.stem}_copy{_HUGE_FILE.suffix}"  # "huge_50mp_copy.jpg"
        shutil.copy(str(_HUGE_FILE), os.path.join(tmpdir, orig_name))
        shutil.copy(str(_HUGE_FILE), os.path.join(tmpdir, copy_name))

        db_path = os.path.join(tmpdir, "s05_manifest.db")

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan the tmpdir (two identical heavy files) ─────────
            run_scan(
                page,
                sources=[tmpdir],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # ── Step 2: read manifest; derive group_id + a huge basename ────
            manifest = _get_manifest(base_url, db_path)
            assert manifest["total_groups"] >= 1, (
                "Expected at least 1 group after scanning two byte-identical "
                f"copies of the heavy image, got "
                f"total_groups={manifest['total_groups']} — the EXACT-dup "
                "group did not form (scanner/dedup.py _classify_exact)."
            )
            first_group = manifest["groups"][0]
            group_id = str(first_group["group_number"])

            basenames = [
                Path(f["file_path"]).name
                for f in first_group.get("items", [])
            ]
            assert basenames, (
                f"No file rows found in group {group_id} — the heavy-image "
                "EXACT-dup pair produced no selectable rows."
            )
            target_basename = basenames[0]

            # ── Step 3: click the file row → preview pane mounts ────────────
            # The result tree is virtualised (@tanstack/react-virtual); scroll
            # the row into view so the virtualiser renders it before clicking.
            row_tid = row_file_testid(group_id, target_basename)
            row = page.get_by_test_id(row_tid)
            row.wait_for(state="visible", timeout=15_000)
            row.scroll_into_view_if_needed(timeout=15_000)
            row.click()

            # ── Step 4: ASSERT the bounded preview rendered (the real check) ─
            # PreviewPane shows thumbnailUrl(path, 512); the <img> only becomes
            # visible once the heavy image decodes into the size=512 thumbnail.
            page.get_by_test_id(PREVIEW_SINGLE_IMAGE).wait_for(
                state="visible", timeout=15_000
            )
            # The metadata sidebar must also mount alongside the image.
            page.get_by_test_id(PREVIEW_INFO).wait_for(
                state="visible", timeout=10_000
            )

            # ── Step 5: heavy-image full-res probe ──────────────────────────
            # Double-clicking the FILE ROW fires onOpenFullRes (FileRow.tsx:57)
            # → store.openFullRes → FullResViewer mounts and requests GET
            # /api/image?size=0.  The 1.48 MB fixture is well under the 40 MiB
            # full-res guard, so the 413 path never fires.  The full-res <img>
            # carries inline visibility:hidden until onLoad fires
            # (FullResViewer.tsx:224), so wait_for(state="visible") resolves
            # only once the heavy native decode actually completes.
            row.dblclick()
            page.get_by_test_id(FULLRES_IMAGE).wait_for(
                state="visible", timeout=20_000
            )

            # ── Step 6: close the full-res overlay ──────────────────────────
            page.keyboard.press("Escape")
    finally:
        # Non-destructive scenario, but the copy-to-tmpdir pattern means we
        # own the temp tree; remove it so the filesystem stays tidy.
        shutil.rmtree(tmpdir, ignore_errors=True)
