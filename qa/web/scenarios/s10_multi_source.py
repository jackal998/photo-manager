"""Web scenario s10 — multi-source priority + cross-source dedup.

Ported from qa/scenarios/s10_multi_source.py (Qt UIA, print-only probe).

Qt intent:
  - Scan TWO source folders in configured order (multi-source-a FIRST, then
    multi-source-b). The folders share a byte-identical shared.jpg, so the
    scanner dedups it across sources into one group. Source ORDER is
    load-bearing: the first-listed source (multi-source-a) wins ties, so every
    "Ref"-tier row must originate from multi-source-a.

Web slice (UPGRADED to hard assertions — see "Divergences"):
  1. run_scan with sources=[multi-source-a, multi-source-b] — the helper adds
     them in order (idx 0 then idx 1), so multi-source-a is configured first.
  2. GET /api/manifest and assert from the JSON:
     A. The manifest is non-empty and has >= 1 group (the cross-source
        shared.jpg pair groups; the source-only a_only.jpg / b_only.jpg are
        singleton-dropped).
     B. At least one Ref winner exists.
     C. EVERY is_ref_winner item's file_path contains "multi-source-a" — the
        source-order-priority invariant (A configured first wins cross-source
        ties; the byte-identical shared.jpg from A, not B, is the keeper).
     D. At least one group spans BOTH sources (an item from multi-source-a and
        an item from multi-source-b in the same group) — proving cross-source
        dedup actually happened, so assertion C is not vacuously true.

Qt divergences:
  D1. NO UIA ROW SCRAPING: Qt reads result-tree cells and finds the "Ref" cell;
      the web port reads is_ref_winner + file_path from GET /api/manifest JSON
      (the authoritative serialisation, immune to UI virtualisation / ordering).
  D2. PRINT -> ASSERT: Qt only prints ref_folder; the web port hard-asserts the
      source-order-priority invariant (every Ref from multi-source-a) plus
      cross-source grouping.
  D3. PRIORITY BY PATH: the source-order priority is asserted by checking the
      Ref winner's file_path contains the first source's directory name
      ("multi-source-a") — robust to whether the manifest serialises a separate
      "folder" label.
  D4. NO LOG PROBE: Qt searches the scan log; the web port asserts purely from
      the manifest (ScanProgress unmounts on SSE 'finished' — log-probe is a
      fast-CI flake, per the s07/s08 notes).

Desktop source: qa/scenarios/s10_multi_source.py
Fixtures:       qa/sandbox/multi-source-a/, qa/sandbox/multi-source-b/
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan

_REPO = Path(__file__).resolve().parents[3]
_SOURCE_A = str(_REPO / "qa" / "sandbox" / "multi-source-a")
_SOURCE_B = str(_REPO / "qa" / "sandbox" / "multi-source-b")
# Directory-name markers — present in every file_path from each source.
_A_MARKER = "multi-source-a"
_B_MARKER = "multi-source-b"


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def run(*, base_url: str) -> None:
    """Scan two ordered sources; assert every Ref winner is from source A."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # Source ORDER is load-bearing: A is configured first -> A wins ties.
            run_scan(
                page,
                sources=[_SOURCE_A, _SOURCE_B],
                output_path=db_path,
                scan_timeout=60_000,
            )

            manifest = _get_manifest(base_url, db_path)
            groups = manifest.get("groups", [])
            print(
                f"probe_status: s10 total_groups={manifest.get('total_groups')} "
                f"total_files={manifest.get('total_files')}"
            )

            # ── Assertion A: non-empty manifest with >= 1 group ─────────────
            assert manifest.get("total_files", 0) > 0 and groups, (
                f"Multi-source scan produced an empty manifest; the byte-identical "
                f"cross-source shared.jpg pair should group. "
                f"total_files={manifest.get('total_files')}, groups={len(groups)}"
            )

            # ── Collect Ref winners + cross-source evidence ─────────────────
            ref_winner_paths: list[str] = []
            cross_source_group = False
            for group in groups:
                items = group.get("items", [])
                paths = [
                    (it.get("file_path", "") or "").replace("\\", "/") for it in items
                ]
                has_a = any(_A_MARKER in p for p in paths)
                has_b = any(_B_MARKER in p for p in paths)
                if has_a and has_b:
                    cross_source_group = True
                for it in items:
                    if it.get("is_ref_winner") is True:
                        ref_winner_paths.append(
                            (it.get("file_path", "") or "").replace("\\", "/")
                        )
            print(
                f"probe_status: s10 ref_winners={ref_winner_paths} "
                f"cross_source_group={cross_source_group}"
            )

            # ── Assertion B: at least one Ref winner exists ─────────────────
            assert ref_winner_paths, (
                "No Ref winner found in the multi-source manifest — the cross-source "
                "group should have a Ref-tier keeper."
            )

            # ── Assertion C: every Ref winner originates from source A ──────
            for path in ref_winner_paths:
                assert _A_MARKER in path, (
                    f"Ref winner {path!r} is NOT from {_A_MARKER!r}; source-order "
                    f"priority (A configured first) must make A win cross-source "
                    f"ties (the byte-identical shared.jpg from A is the keeper)."
                )

            # ── Assertion D: cross-source dedup happened ────────────────────
            assert cross_source_group, (
                "Expected at least one group spanning both multi-source-a and "
                "multi-source-b (the byte-identical shared.jpg pair); cross-source "
                "dedup did not occur, so the priority assertion would be vacuous."
            )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
