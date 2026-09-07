"""Web scenario s40 — result-tree group header expand/collapse toggle.

Ported from qa/scenarios/s40_results_tree_double_click.py (Qt UIA).

Qt intent:
  - Double-click the "Group 1" GROUP HEADER row → it collapses (is_expanded →
    False); double-click again → it re-expands. Exercises the group-branch
    toggle of TreeController._on_double_click (with setExpandsOnDoubleClick(False)
    so Qt's default doesn't race). It explicitly does NOT exercise the file-row
    branch (full-res open) — that is s68.

Web slice (observable via aria-expanded + row visibility):
  - The web GroupRow is a <button data-testid="row-group-{n}" aria-expanded=…
    onClick={onToggle}> (GroupRow.tsx:21-29). ResultTree keeps a Set<number> of
    collapsed groups; collapsing drops the group's file rows from the virtual
    list (ResultTree.tsx:223). Toggling flips aria-expanded and adds/removes the
    file rows from the DOM.

Qt divergence:
  - Qt toggles via DOUBLE-click on the header (its native single-click expand is
    disabled and a custom dispatcher handles the gesture). The web GroupRow
    toggles on SINGLE click (onClick) — there is no onDoubleClick on GroupRow.
    The web scenario uses click(), not dblclick(), to match the web's real
    interaction model while asserting the identical observable outcome
    (aria-expanded flips + file rows hide/show).
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
from qa.web.testid_constants import row_file_testid, row_group_testid

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_A_MEMBER = "neardup_00_q95.jpg"


def _first_group_id(base_url: str, db_path: str) -> str:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        data = json.loads(resp.read())
    assert data["total_groups"] >= 1, "expected at least one group in the manifest"
    return str(data["groups"][0]["group_number"])


def run(*, base_url: str) -> None:
    """Scan, toggle the group header collapse/expand, assert aria + row vis."""
    from playwright.sync_api import expect

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            group_id = _first_group_id(base_url, db_path)
            header = page.get_by_test_id(row_group_testid(group_id))
            member = page.get_by_test_id(row_file_testid(group_id, _A_MEMBER))

            # Groups render expanded by default (collapsed set starts empty).
            expect(header).to_have_attribute("aria-expanded", "true")
            member.wait_for(state="visible", timeout=10_000)

            # Collapse: single-click → aria flips false + the member row detaches.
            header.click()
            expect(header).to_have_attribute("aria-expanded", "false")
            member.wait_for(state="hidden", timeout=10_000)

            # Re-expand: single-click again → aria true + the member row returns.
            header.click()
            expect(header).to_have_attribute("aria-expanded", "true")
            member.wait_for(state="visible", timeout=10_000)
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
