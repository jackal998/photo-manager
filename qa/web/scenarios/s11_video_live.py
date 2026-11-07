"""Web scenario s11 — Live Photo pair (photo-manager#88 regression guard).

Ported from the desktop s11_video_live driver (desktop + SQLite, #88).

Qt intent:
  - Scan qa/sandbox/live-photo (IMG_0001.HEIC + IMG_0001.MOV).
  - Assert the still is in the manifest and the pair is NOT joined by a stem.
  - Assert user_decision is '' (undecided) for the still pre-execute.

GROUPING "10" (scanner/dedup.py ``_collect_pair_edges``): a filename never
creates a pair edge. A MOV joins its still only when both carry the same Apple
ContentIdentifier. The fixture pair carries none, so the MOV stays ungrouped.
The scenario adds a byte copy of IMG_0001.HEIC in a second subfolder, so the
still is a grouped member through content.

Web slice:
  1. Copy IMG_0001.HEIC and IMG_0001.MOV from qa/sandbox/live-photo into
     ``<tmpdir>/s11_source/`` (so the scan does NOT target the repo fixture
     dir directly — harness convention from s24/s13). Copy IMG_0001.HEIC once
     more into ``<tmpdir>/s11_source/dupe/``.
  2. run_scan([src_subdir]) → fresh manifest at ``<tmpdir>/s11_manifest.db``
     (db lives BESIDE the source subdir in tmpdir, same as s24).
  3. wait_manifest_loaded(page) to ensure GET /api/manifest is stable before
     fetching (the SSE 'finished' event fires before loadManifest resolves;
     reading between them gets stale/empty data).
  4. GET /api/manifest?path=<db_path> → parse groups.
  5. Assert:
     A. IMG_0001.HEIC present among manifest basenames (#88 bug guard: HEIC).
     B. IMG_0001.MOV NOT among the grouped manifest rows: no ContentIdentifier
        on either file, so no content pair (GROUPING 10). Replaces the old
        "MOV present and same group" assertions, which the stem edge made true.
     D. user_decision == '' for the root HEIC (undecided at scan time).
     E. The byte copy in ``dupe/`` is in the same group_number as the still.
     The positive ContentIdentifier case is layer-1 only
     (tests/test_dedup_live_pair.py, synthetic content_identifier values).
  6. finally: shutil.rmtree(tmpdir, ignore_errors=True) — Windows .db lock.

Qt divergences:
  (i)   Qt reads the manifest via sqlite3 directly from a shared
        ``qa/run-manifest.sqlite`` file produced by the GUI session.  The web
        port fetches GET /api/manifest (the server-serialised JSON) after
        a full Playwright scan flow.  This is the canonical web verification
        pattern (s24/s13 precedent) and strictly stronger: it validates the
        server serialisation as well as the scanner grouping.
  (ii)  Qt asserts via group_id (a file-system path fragment).  The web
        manifest uses group_number (integer).  The assertion is semantically
        identical: both halves must share the same group identifier.
  (iii) Qt verifies user_decision via a sqlite3 SELECT; the web port reads
        user_decision from GET /api/manifest JSON items — the same field,
        same semantics, different transport.
  (iv)  Qt prints a WARN (not FAIL) when MOV user_decision != ''.  The web
        port asserts it (assert user_decision == '') — a strict port of the
        intent, not the lenient print/warn form.  This is a deliberate
        upgrade: undecided-at-scan is always expected and not flaky.
  (v)   The pair is >= 2 members by design (HEIC + MOV), so the
        SINGLETON-DROP TRAP does not apply: manifest_repository never drops
        this group before serialisation.
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
from qa.web._invariants import run_scan, wait_manifest_loaded

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
_LIVE_PHOTO_DIR = _REPO / "qa" / "sandbox" / "live-photo"

_HEIC_NAME = "IMG_0001.HEIC"
_MOV_NAME = "IMG_0001.MOV"
_DUPE_DIR = "dupe"  # second folder holding the byte copy of the still


# ---------------------------------------------------------------------------
# HTTP helper (self-contained, mirrors s24 _get_manifest exactly)
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>.

    Uses urllib (stdlib only) to keep the module import-time dependency-free,
    matching the convention established by s24/s13/s15/s31/s51/s56.
    """
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """Scan live-photo pair; assert HEIC+MOV both appear and share a group.

    Raises AssertionError on any regression.  The batch runner treats any
    exception as a failure; this function returns None on success.
    """
    tmpdir = tempfile.mkdtemp(prefix="qa_s11_")
    src_subdir = os.path.join(tmpdir, "s11_source")
    db_path = os.path.join(tmpdir, "s11_manifest.db")
    try:
        # ── Copy fixture files into the dedicated source subdir ──────────────
        os.makedirs(src_subdir, exist_ok=True)
        shutil.copy(str(_LIVE_PHOTO_DIR / _HEIC_NAME), os.path.join(src_subdir, _HEIC_NAME))
        shutil.copy(str(_LIVE_PHOTO_DIR / _MOV_NAME), os.path.join(src_subdir, _MOV_NAME))
        # Byte copy of the still in another folder (GROUPING "9"): gives the
        # still a group, so the pair's MOV joins it.
        dupe_dir = os.path.join(src_subdir, _DUPE_DIR)
        os.makedirs(dupe_dir, exist_ok=True)
        shutil.copy(str(_LIVE_PHOTO_DIR / _HEIC_NAME), os.path.join(dupe_dir, _HEIC_NAME))

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Scan live-photo source → fresh manifest.db ───────────────────
            # recursive=True is REQUIRED: the byte copy lives in the dupe/
            # subfolder, and a non-recursive scan would never walk it.
            run_scan(
                page,
                sources=[src_subdir],
                output_path=db_path,
                scan_timeout=120_000,
                recursive=True,
            )
            assert os.path.exists(db_path), f"scan did not write manifest: {db_path}"

            # ── Wait for manifest to be fully loaded before fetching JSON ─────
            # The SSE 'finished' event fires before loadManifest() resolves;
            # run_scan already calls wait_manifest_loaded internally, but we
            # call it again after the page settles to be safe against the race.
            wait_manifest_loaded(page, timeout=30_000)

            # ── Fetch manifest JSON via GET /api/manifest ────────────────────
            manifest = _get_manifest(base_url, db_path)

            # Build a lookup: basename -> (group_number, user_decision)
            # so we can assert HEIC and MOV both appear and share a group.
            # The byte copy in the dupe folder shares the still's basename, so
            # it is kept apart and only checked by assertion E.
            found: dict[str, dict] = {}
            dupe_group: int | None = None
            for group in manifest.get("groups", []):
                group_number = group["group_number"]
                for item in group.get("items", []):
                    path = Path(item["file_path"])
                    if path.parent.name == _DUPE_DIR:
                        dupe_group = group_number
                        continue
                    found[path.name] = {
                        "group_number": group_number,
                        "user_decision": item["user_decision"],
                    }

            # ── Assertion A: HEIC present ────────────────────────────────────
            assert _HEIC_NAME in found, (
                f"IMG_0001.HEIC missing from manifest (basenames found: "
                f"{sorted(found)}). Walker should emit a FileRecord for the "
                f"HEIC half of a Live Photo pair."
            )

            # ── Assertion B: MOV is NOT grouped with the still (GROUPING 10) ──
            # The fixture pair carries no Apple ContentIdentifier (read-only
            # exiftool check, 2026-11-08: neither file has the tag), so a stem
            # alone never pairs them. An ungrouped row is not in the review
            # manifest (manifest_repository loads grouped rows only), so the MOV
            # must be absent from the groups.
            assert _MOV_NAME not in found, (
                f"IMG_0001.MOV is in the manifest groups (basenames found: "
                f"{sorted(found)}), but the fixture pair has no matching "
                f"ContentIdentifier. GROUPING 10: a filename never creates a "
                f"pair edge, so the MOV must stay ungrouped."
            )

            # ── Assertion D: the still is undecided at scan time ─────────────
            heic_group = found[_HEIC_NAME]["group_number"]
            heic_decision = found[_HEIC_NAME]["user_decision"]
            assert heic_decision == "", (
                f"IMG_0001.HEIC has unexpected user_decision={heic_decision!r} "
                f"immediately after scan (expected '' = undecided)."
            )

            # ── Assertion E: the byte copy is in the still's group ───────────
            assert dupe_group is not None, (
                f"the byte copy {_DUPE_DIR}/{_HEIC_NAME} is missing from the "
                f"manifest groups (GROUPING 9: a byte copy is a grouped member)."
            )
            assert dupe_group == heic_group, (
                f"byte copy group_number={dupe_group!r} != still group_number="
                f"{heic_group!r}; a byte copy must share the still's group."
            )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
