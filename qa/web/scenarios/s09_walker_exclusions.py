"""Web scenario s09 — walker exclusion rules.

Ported from qa/scenarios/s09_walker_exclusions.py (Qt UIA, print-only probe).

Qt intent:
  - Scan qa/sandbox/walker-exclusions (2 real JPEGs + Thumbs.db + desktop.ini
    + a .json sidecar) and verify only the 2 real photos appear in the result
    rows — Thumbs.db, desktop.ini and the sidecar are skipped (the walker's
    SKIP_FILENAMES + MEDIA_EXTENSIONS guards). When the fixture also carries a
    symlink (creatable only with privilege), additionally verify the walker's
    symlink-skip guard fires end-to-end.

Web slice (UPGRADED to hard assertions — see "Divergences"):
  The two real photos (real_photo_a.jpg, real_photo_b.jpg) are DISTINCT images
  (different content hash), so on the raw fixture each is a singleton and the
  grouped manifest (GET /api/manifest drops groups < 2 members) is EMPTY — a
  naive port would assert "Thumbs.db absent" over an empty manifest, which is
  vacuously true (everything is absent). To make the real photos observable,
  this port gives each a byte-identical twin (the s05/s06/s08 duplicate-to-
  observe pattern): each real photo then forms an exact-dup PAIR, survives the
  singleton-drop, and appears in the manifest. The excluded non-media files
  (Thumbs.db, desktop.ini, the sidecar) are copied alongside so the walker has
  something real to skip.

Assertions:
  1. The manifest basenames are EXACTLY the 4 real-media files (2 reals x 2
     byte-identical twins) — nothing more, nothing less.
  2. Each excluded non-media file (Thumbs.db, desktop.ini, real_photo_a.jpg.json)
     is absent from the manifest (exact-basename check).
  3. Symlink-skip guard (CI/Linux only): when os.symlink succeeds in the temp
     source, the walker must NOT hash the symlink — symlink_to_real.jpg must be
     absent from the manifest. Skipped with a probe note on platforms without
     symlink privilege (the layer-1 mock test covers that branch).

Qt divergences:
  D1. SOURCE READ: Qt scrapes the per-file Date/Name cells from the UIA result
      tree. The web port reads basenames from GET /api/manifest JSON — the
      authoritative server serialisation, immune to UI virtualisation / label
      drift.
  D2. PRINT -> ASSERT: Qt prints "excluded_file_present: X=False"; the web port
      hard-asserts an exact media-basename set, turning the observation into a
      gate.
  D3. DUPLICATE-TO-OBSERVE: Qt scans the raw fixture (real photos are
      singletons → its result tree would also be empty for the dedup view);
      the web port duplicates each real photo so it appears in the manifest.
  D4. NO LOG PROBE: Qt reads the "-> N files" walker-count line from the scan
      log. The web port does NOT probe the progress log — ScanProgress unmounts
      on the SSE 'finished' event, so for this tiny scan the log is gone before
      a wait could observe it (a guaranteed flake on fast CI, per the s08 D4
      note). Exclusion is asserted from the manifest instead.

Desktop source: qa/scenarios/s09_walker_exclusions.py
Fixture:        qa/sandbox/walker-exclusions/
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

_REPO = Path(__file__).resolve().parents[3]
_WALKER_DIR = _REPO / "qa" / "sandbox" / "walker-exclusions"

# Real media files — each gets a byte-identical twin to survive the
# singleton-drop in the grouped manifest.
_REAL_MEDIA = ("real_photo_a.jpg", "real_photo_b.jpg")
# Non-media files the walker MUST exclude (copied alongside the reals so the
# walker has real content to skip). SKIP_FILENAMES covers Thumbs.db /
# desktop.ini; MEDIA_EXTENSIONS excludes the .json sidecar.
_EXCLUDED = ("Thumbs.db", "desktop.ini", "real_photo_a.jpg.json")
# Optional symlink probe (Linux CI / privileged Windows only).
_SYMLINK_NAME = "symlink_to_real.jpg"


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _manifest_basenames(manifest: dict) -> set[str]:
    """Return the set of file basenames across all groups in the manifest."""
    names: set[str] = set()
    for group in manifest.get("groups", []):
        for file_row in group.get("items", []):
            names.add(Path(file_row["file_path"]).name)
    return names


def run(*, base_url: str) -> None:
    """Scan walker-exclusions (reals duplicated); assert only real media survive."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s09_")
    src_subdir = os.path.join(tmpdir, "s09_source")
    db_path = os.path.join(tmpdir, "s09_manifest.db")
    try:
        os.makedirs(src_subdir, exist_ok=True)

        # ── Copy each real photo + a byte-identical twin ────────────────────
        expected_media: set[str] = set()
        for basename in _REAL_MEDIA:
            src = _WALKER_DIR / basename
            assert src.exists(), (
                f"walker-exclusions fixture missing: {src} "
                "(expected qa/sandbox/walker-exclusions/ with the real JPEGs)"
            )
            stem, ext = os.path.splitext(basename)
            twin = f"{stem}__dup{ext}"
            shutil.copy(str(src), os.path.join(src_subdir, basename))
            shutil.copy(str(src), os.path.join(src_subdir, twin))
            expected_media.add(basename)
            expected_media.add(twin)

        # ── Copy the excluded non-media files (give the walker something to skip)
        for basename in _EXCLUDED:
            src = _WALKER_DIR / basename
            assert src.exists(), (
                f"walker-exclusions fixture missing: {src} "
                "(expected Thumbs.db / desktop.ini / the .json sidecar)"
            )
            shutil.copy(str(src), os.path.join(src_subdir, basename))

        # ── Best-effort symlink (tests the walker symlink-skip guard on CI) ──
        symlink_created = False
        try:
            os.symlink(
                os.path.join(src_subdir, "real_photo_a.jpg"),
                os.path.join(src_subdir, _SYMLINK_NAME),
            )
            symlink_created = True
        except OSError:
            pass  # no symlink privilege on this platform — probe falls through

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            run_scan(page, sources=[src_subdir], output_path=db_path,
                     scan_timeout=60_000)

            manifest = _get_manifest(base_url, db_path)
            names = _manifest_basenames(manifest)
            print(
                f"probe_status: s09 manifest_basenames={sorted(names)} "
                f"symlink_created={symlink_created}"
            )

            # ── Assertion 1: exactly the 4 real-media files (reals + twins) ──
            assert names == expected_media, (
                f"Walker results must be exactly the real media files "
                f"{sorted(expected_media)}, got {sorted(names)}. A non-media "
                f"file leaked, or a real photo was dropped."
            )

            # ── Assertion 2: each excluded non-media file is absent ─────────
            for excluded in _EXCLUDED:
                assert excluded not in names, (
                    f"Excluded file {excluded!r} leaked into the manifest — the "
                    f"walker SKIP_FILENAMES / MEDIA_EXTENSIONS guard failed."
                )

            # ── Assertion 3: symlink-skip guard (CI/Linux only) ─────────────
            if symlink_created:
                assert _SYMLINK_NAME not in names, (
                    f"Walker leaked symlink {_SYMLINK_NAME!r} into the manifest — "
                    f"scanner/walker.py symlink-skip guard failed (the symlink "
                    f"target's content was hashed and grouped)."
                )
            else:
                print(
                    "probe_status: s09 symlink-skip check SKIPPED "
                    "(no symlink privilege on this platform; layer-1 mock test "
                    "covers the branch)"
                )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
