"""Web scenario s20 — Right-click multi-selection → context-menu action.

Ported from qa/scenarios/s20_multi_remove_from_list.py (Qt UIA).

Qt intent:
  Ctrl+click several file rows in the main result tree, right-click one of the
  selected rows, and pick "Remove from List" — the action applies to EVERY
  selected row (not just the right-clicked one).  Desktop FINALIZES the rows to
  outcome='ignored' immediately (the rows leave the review tree, no Execute).

Web slice — main-tree multi-selection + #694 finalize:
  Two things this proves:
    (1) the multi-selection mechanic — Ctrl/Cmd+click toggles rows into an
        ordered selection set, and one right-click context-menu verb acts on the
        WHOLE selection (App.handleContextMenu's target-resolution rule:
        right-clicked-in-selection → the selection, else just that row); and
    (2) the #694 parity fix — the result-tree "Remove from list" now FINALIZES
        outcome='ignored' (the rows leave the manifest immediately, no Execute
        step), matching desktop.  It previously only STAGED user_decision=
        'ignore'; staging a reversible 'ignore' now lives on the per-row
        DecisionControl dropdown (s53/s60 use that path).

  Three branches:

  A. FAN-OUT (non-destructive): Ctrl-multi-select 3 rows → Delete.  One menu
     click stages 'delete' on all three (proves the verb fans out over the whole
     selection) without removing any row, leaving all 5 for the later branches.
  B. TARGET RESOLUTION: with a fresh [q72, q65] selection, right-click q95
     (OUTSIDE the selection) → Keep.  Only q95 clears — the menu resolves to the
     single right-clicked row, not the stale selection (the rule that keeps the
     single-row scenarios s15/s25/s35/s53 valid under the selection-aware menu).
  C. MULTI-REMOVE FINALIZE (#694 headline): the [q72, q65] selection → Remove
     from list.  One click finalizes BOTH (fan-out + finalize): the rows vanish
     from GET /api/manifest (WHERE outcome='' excludes outcome='ignored') AND
     their files stay on disk (ignored ≠ deleted) — the desktop contract.

  NO GROUP-HEADER SELECTION: the web GroupRow is collapse-only, so the desktop
  group→file expansion sub-branch has no web equivalent and is dropped.

Qt divergences (assertion mechanics):
  - Manifest read via GET /api/manifest JSON vs Qt SQLite.
  - "Removed N items from list" status-bar echo is a Qt-only signal; the web
    asserts the persisted decision split + the finalize (row absent, file on
    disk) instead.
  - /api/remove enforces the allowed-roots guard, so (like s54) the fixtures are
    copied into a tmpdir and scanned THERE, with the db written under that root.

Desktop source: qa/scenarios/s20_multi_remove_from_list.py
Fixture:        qa/sandbox/near-duplicates/ (5 JPEGs, one group), copied to a tmpdir
"""
from __future__ import annotations

import json
import os
import shutil
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

# Three rows multi-selected for Branch A (delete — staging fan-out proof).
_Q95 = "neardup_00_q95.jpg"
_Q88 = "neardup_01_q88.jpg"
_Q80 = "neardup_02_q80.jpg"
# Two rows multi-selected for Branch C (remove → finalize).
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


def _await_removed(
    base_url: str,
    db_path: str,
    names: tuple[str, ...],
    *,
    timeout: float = 6.0,
) -> dict[str, str]:
    """Poll GET /api/manifest until every basename in ``names`` is ABSENT — a
    finalize-remove excises the rows (WHERE outcome='' filters outcome=
    'ignored')."""
    deadline = time.monotonic() + timeout
    decisions: dict[str, str] = {}
    while time.monotonic() < deadline:
        decisions = _decisions(_get_manifest(base_url, db_path))
        if all(n not in decisions for n in names):
            return decisions
        time.sleep(0.1)
    return decisions


def _first_group_id(base_url: str, db_path: str) -> str:
    data = _get_manifest(base_url, db_path)
    assert data["total_groups"] >= 1, "expected at least one group in the manifest"
    return str(data["groups"][0]["group_number"])


def run(*, base_url: str) -> None:
    """Prove one context-menu verb fans out over the whole multi-selection,
    guard the single-row target-resolution rule, and prove the result-tree
    "Remove from list" FINALIZES (outcome='ignored', rows leave the tree, files
    stay on disk) to match desktop (#694)."""
    # /api/remove enforces the allowed-roots guard, so copy the fixtures into a
    # tmpdir and write the db there too (s54 pattern) — then manifest_path AND
    # the removed file_paths all fall under the scanned (allowed) root.
    tmpdir = tempfile.mkdtemp(prefix="qa_s20_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        for name in _ALL:
            shutil.copy(os.path.join(_NEAR_DUPS_DIR, name), os.path.join(tmpdir, name))

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            run_scan(
                page,
                sources=[tmpdir],
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

            # ── Branch A — fan-out via Delete (staging, non-destructive) ──────
            # Plain click anchors q95; Ctrl+click adds q88 then q80.  One menu
            # click must stage 'delete' on all three without removing any row.
            click_row(page, row_file_testid(gid, _Q95))
            ctrl_click_row(page, row_file_testid(gid, _Q88))
            ctrl_click_row(page, row_file_testid(gid, _Q80))
            right_click_row(page, row_file_testid(gid, _Q80))
            click_context_item(page, CTX_SET_ACTION_DELETE)

            post_a = _await_decisions(
                base_url,
                db_path,
                {_Q95: "delete", _Q88: "delete", _Q80: "delete"},
            )
            print(f"probe_status: s20 branch_a post={ {k: post_a[k] for k in sorted(post_a)} }")
            for name in (_Q95, _Q88, _Q80):
                assert post_a.get(name) == "delete", (
                    f"branch A: {name} expected staged 'delete' (acted on the "
                    f"whole multi-selection), got {post_a.get(name)!r}"
                )
            for name in (_Q72, _Q65):
                assert post_a.get(name) == "", (
                    f"branch A leaked into the unselected {name}: "
                    f"{post_a.get(name)!r}"
                )

            # ── Branch B — right-click OUTSIDE the selection acts on one row ──
            # Plain click q72 REPLACES the selection (drops the A set), Ctrl+q65
            # adds the second row → selection [q72, q65].  Right-clicking q95
            # (NOT in that selection) must resolve to [q95] alone.
            click_row(page, row_file_testid(gid, _Q72))
            ctrl_click_row(page, row_file_testid(gid, _Q65))
            right_click_row(page, row_file_testid(gid, _Q95))
            click_context_item(page, CTX_SET_ACTION_KEEP)

            post_b = _await_decisions(base_url, db_path, {_Q95: ""})
            print(f"probe_status: s20 branch_b post={ {k: post_b[k] for k in sorted(post_b)} }")
            assert post_b.get(_Q95) == "", (
                f"branch B: right-clicking q95 (outside the selection) must clear "
                f"only q95, got {post_b.get(_Q95)!r}"
            )
            # The Branch-A rows stay 'delete' (not cleared) — single-target.
            for name in (_Q88, _Q80):
                assert post_b.get(name) == "delete", (
                    f"branch B cleared {name} too — target resolution acted on "
                    f"the stale selection instead of just q95: {post_b.get(name)!r}"
                )
            for name in (_Q72, _Q65):
                assert post_b.get(name) == "", (
                    f"branch B disturbed {name}: {post_b.get(name)!r}"
                )

            # ── Branch C — multi-REMOVE finalizes (the #694 headline) ─────────
            # Re-establish the [q72, q65] selection explicitly, then Remove.  One
            # click finalizes BOTH: since #694 the result-tree "Remove from list"
            # finalizes outcome='ignored', so the rows leave the manifest
            # immediately (no Execute) and their files stay on disk.
            click_row(page, row_file_testid(gid, _Q72))
            ctrl_click_row(page, row_file_testid(gid, _Q65))
            right_click_row(page, row_file_testid(gid, _Q72))
            click_context_item(page, CTX_SET_ACTION_REMOVE)

            post_c = _await_removed(base_url, db_path, (_Q72, _Q65))
            print(f"probe_status: s20 branch_c post={ {k: post_c[k] for k in sorted(post_c)} }")
            for name in (_Q72, _Q65):
                assert name not in post_c, (
                    f"branch C: {name} expected to LEAVE the tree (finalize "
                    f"outcome='ignored', fan-out over the selection), still "
                    f"present as {post_c.get(name)!r}"
                )
                assert os.path.exists(os.path.join(tmpdir, name)), (
                    f"branch C: {name} left the list but its file was deleted — "
                    f"'Remove from list' must finalize outcome='ignored' (file "
                    f"stays on disk), not delete it"
                )
            # The unselected rows survive untouched (q95 cleared in B; q88/q80
            # still staged 'delete').
            assert post_c.get(_Q95) == "", (
                f"branch C disturbed q95: {post_c.get(_Q95)!r} (expected '')"
            )
            for name in (_Q88, _Q80):
                assert post_c.get(name) == "delete", (
                    f"branch C disturbed {name}: {post_c.get(name)!r} (expected "
                    f"still 'delete')"
                )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
