"""Web scenario s20 — Right-click multi-selection → context-menu action.

Ported from qa/scenarios/s20_multi_remove_from_list.py (Qt UIA).

Qt intent:
  Ctrl+click several file rows in the main result tree, right-click one of the
  selected rows, and pick "Remove from List" — the action applies to EVERY
  selected row (not just the right-clicked one).  Desktop FINALIZES the rows to
  outcome='ignored' immediately (the group→file expansion sub-branch widens a
  selected group header to its children).

Web slice — cluster D / PR-D1 (main-tree multi-selection):
  The novel mechanic this PR adds is the multi-selection itself: Ctrl/Cmd+click
  toggles rows into an ordered selection set, and the right-click context menu's
  verbs act on the WHOLE selection (App.handleContextMenu's target-resolution
  rule: right-clicked-in-selection → the selection, else just that row).  This
  scenario is the layer-3 proof that one context-menu click fans out across the
  multi-selection.

  Two deliberate web divergences from desktop:

  D1. STAGE vs FINALIZE.  The web result-tree "Remove from list" STAGES
      user_decision='ignore' (the web's commit-at-Execute model — nothing is
      finalized until Execute), whereas desktop finalizes outcome='ignored'
      immediately.  That parity gap is tracked as #694 and is intentionally NOT
      changed here (s53/s60 depend on the staged behaviour).  So this port
      asserts the staged user_decision the multi-select wrote, not an
      outcome-column finalize.  The execute-dialog "Remove from list" (s54) is
      the surface that finalizes.

  D2. NO GROUP-HEADER SELECTION.  The web GroupRow is collapse-only (no
      "select the whole group as one item"), so the desktop group→file
      expansion sub-branch (Branch B) has no web equivalent and is dropped.

  The scenario also guards the target-resolution rule itself (Branch C): a
  right-click on a row OUTSIDE the current selection acts on just that one row
  (this is what keeps the single-row scenarios s15/s25/s35/s53/s60 valid under
  the new selection-aware menu).

Qt divergences (assertion mechanics):
  - Manifest read via GET /api/manifest JSON vs Qt SQLite.
  - "Removed N items from list" status-bar echo is a Qt-only signal; the web
    asserts the persisted user_decision split instead (the load-bearing check).

Desktop source: qa/scenarios/s20_multi_remove_from_list.py
Fixture:        qa/sandbox/near-duplicates/ (5 JPEGs, one group)
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    click_context_item,
    click_row,
    ctrl_click_row,
    right_click_row,
    run_scan,
)
from qa.web.testid_constants import (
    CTX_SET_ACTION_DELETE,
    CTX_SET_ACTION_KEEP,
    CTX_SET_ACTION_REMOVE,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

# Three rows multi-selected for Branch A (remove → staged ignore).
_Q95 = "neardup_00_q95.jpg"
_Q88 = "neardup_01_q88.jpg"
_Q80 = "neardup_02_q80.jpg"
# Two rows multi-selected for Branch B (delete).
_Q72 = "neardup_03_q72.jpg"
_Q65 = "neardup_04_q65.jpg"
_ALL = (_Q95, _Q88, _Q80, _Q72, _Q65)


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _decisions(manifest: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for group in manifest.get("groups", []):
        for it in group.get("items", []):
            out[Path(it["file_path"]).name] = it.get("user_decision", "") or ""
    return out


def _await_decisions(
    base_url: str,
    db_path: str,
    expected: dict[str, str],
    *,
    timeout: float = 6.0,
) -> dict[str, str]:
    """Poll GET /api/manifest until every (basename → decision) in ``expected``
    matches (absorbs the async PATCH /api/decision round-trip)."""
    deadline = time.monotonic() + timeout
    decisions: dict[str, str] = {}
    while time.monotonic() < deadline:
        decisions = _decisions(_get_manifest(base_url, db_path))
        if all(decisions.get(name) == val for name, val in expected.items()):
            return decisions
        time.sleep(0.1)
    return decisions


def _first_group_id(base_url: str, db_path: str) -> str:
    data = _get_manifest(base_url, db_path)
    assert data["total_groups"] >= 1, "expected at least one group in the manifest"
    return str(data["groups"][0]["group_number"])


def run(*, base_url: str) -> None:
    """Multi-select via Ctrl+click, prove one context-menu click fans out over
    the whole selection, and guard the single-row target-resolution rule."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s20_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # /api/decision does NOT validate manifest_path under the allowed
            # roots (only /api/remove and /api/execute do), so — like s30/s53 —
            # the repo fixture dir is scanned directly with a temp db.
            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )
            pre = _decisions(_get_manifest(base_url, db_path))
            assert set(pre) == set(_ALL), (
                f"expected 5 fixture rows {sorted(_ALL)}, got {sorted(pre)}"
            )
            assert all(v == "" for v in pre.values()), (
                f"fixture should start undecided, got {pre}"
            )

            gid = _first_group_id(base_url, db_path)

            # ── Branch A — Ctrl-multi-select 3 rows → Remove from list ────────
            # Plain click anchors q95; Ctrl+click adds q88 then q80.
            click_row(page, row_file_testid(gid, _Q95))
            ctrl_click_row(page, row_file_testid(gid, _Q88))
            ctrl_click_row(page, row_file_testid(gid, _Q80))
            # Right-click one of the selected rows → the menu targets all three.
            right_click_row(page, row_file_testid(gid, _Q80))
            click_context_item(page, CTX_SET_ACTION_REMOVE)

            post_a = _await_decisions(
                base_url,
                db_path,
                {_Q95: "ignore", _Q88: "ignore", _Q80: "ignore"},
            )
            print(f"probe_status: s20 branch_a post={ {k: post_a[k] for k in sorted(post_a)} }")
            for name in (_Q95, _Q88, _Q80):
                assert post_a.get(name) == "ignore", (
                    f"branch A: {name} expected staged 'ignore' (acted on the "
                    f"whole multi-selection), got {post_a.get(name)!r}"
                )
            for name in (_Q72, _Q65):
                assert post_a.get(name) == "", (
                    f"branch A leaked into the unselected {name}: "
                    f"{post_a.get(name)!r}"
                )

            # ── Branch B — a fresh 2-row selection → Delete ───────────────────
            # Plain click q72 REPLACES the selection (drops the A set), Ctrl+q65
            # adds the second row. Proves delete fans out too + selection replace.
            click_row(page, row_file_testid(gid, _Q72))
            ctrl_click_row(page, row_file_testid(gid, _Q65))
            right_click_row(page, row_file_testid(gid, _Q72))
            click_context_item(page, CTX_SET_ACTION_DELETE)

            post_b = _await_decisions(
                base_url,
                db_path,
                {_Q72: "delete", _Q65: "delete"},
            )
            print(f"probe_status: s20 branch_b post={ {k: post_b[k] for k in sorted(post_b)} }")
            for name in (_Q72, _Q65):
                assert post_b.get(name) == "delete", (
                    f"branch B: {name} expected 'delete', got {post_b.get(name)!r}"
                )
            # The Branch-A rows must be untouched by the replaced selection.
            for name in (_Q95, _Q88, _Q80):
                assert post_b.get(name) == "ignore", (
                    f"branch B replaced selection but disturbed {name}: "
                    f"{post_b.get(name)!r} (expected still 'ignore')"
                )

            # ── Branch C — right-click OUTSIDE the selection acts on one row ──
            # q95 is NOT in the current [q72, q65] selection, so the menu must
            # resolve to [q95] alone (the rule that keeps s15/s25/s35 single-row).
            right_click_row(page, row_file_testid(gid, _Q95))
            click_context_item(page, CTX_SET_ACTION_KEEP)

            post_c = _await_decisions(base_url, db_path, {_Q95: ""})
            print(f"probe_status: s20 branch_c post={ {k: post_c[k] for k in sorted(post_c)} }")
            assert post_c.get(_Q95) == "", (
                f"branch C: right-clicking q95 (outside the selection) must clear "
                f"only q95, got {post_c.get(_Q95)!r}"
            )
            # The other Branch-A rows stay 'ignore' (not cleared) — single-target.
            for name in (_Q88, _Q80):
                assert post_c.get(name) == "ignore", (
                    f"branch C cleared {name} too — target resolution acted on "
                    f"the stale selection instead of just q95: {post_c.get(name)!r}"
                )
            for name in (_Q72, _Q65):
                assert post_c.get(name) == "delete", (
                    f"branch C disturbed {name}: {post_c.get(name)!r} (expected "
                    f"still 'delete')"
                )
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
