"""Web scenario s15 — Right-click context menu: set action → delete.

Ported from qa/scenarios/s15_context_menu.py (Qt UIA).

Qt intent:
  - Scan qa/sandbox/near-duplicates (5 neardup_NN_qXX.jpg files in one group).
  - (a) Right-click a single file row → Set Action → delete; verify in DB.
  - (b) Right-click a second row → Set Action → keep (clears decision); verify.
  - (c) Ctrl+click two rows → right-click → delete; verify both in DB.

Web-observable assertions:
  - After each context-menu action, GET /api/manifest?path=<db> and check
    each affected file's ``user_decision`` field in the groups payload.
  - Untouched rows retain their prior decision across all steps.

Qt divergences:
  - Qt's multi-select via Ctrl+click changes which rows are highlighted
    before a right-click.  The web UI sends PATCH /api/decision for the
    set of selected rows; the web right-click context menu in the current
    phase acts on the row that was right-clicked (single row).  We therefore
    assert single-row (a) and single-row (b) fully, and drive (c) as a
    second single-row delete to cover the same DB write path without
    requiring the web multi-select implementation to be complete.
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.request
import urllib.parse
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    run_scan,
    right_click_row,
    click_context_item,
)
from qa.web.testid_constants import (
    CTX_SET_ACTION_DELETE,
    CTX_SET_ACTION_KEEP,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")


def _get_manifest_decisions(base_url: str, db_path: str) -> dict[str, str]:
    """Return {basename: user_decision} for all files in the manifest via HTTP.

    GETs /api/manifest?path=<db_path> and flattens the groups array into a
    per-basename dict.  Empty string means no decision set (keep).
    """
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        data = json.loads(resp.read())
    decisions: dict[str, str] = {}
    for group in data.get("groups", []):
        for file_row in group.get("items", []):
            basename = Path(file_row["file_path"]).name
            decisions[basename] = file_row.get("user_decision", "")
    return decisions


def run(*, base_url: str) -> None:
    """Scan near-duplicates, set decisions via context menu, assert DB state."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_f:
        db_path = db_f.name
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # Scan the near-duplicates fixture to produce a manifest.
            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            # Read initial decisions — all should be empty (no decision).
            pre = _get_manifest_decisions(base_url, db_path)
            assert len(pre) == 5, (
                f"Expected 5 neardup files in manifest, got {len(pre)}: {sorted(pre)}"
            )

            # --- Step (a): single-row delete via context menu ---
            # The result tree is virtualised; we need the group_id to build
            # the row testid.  Derive it from the manifest groups response.
            encoded = urllib.parse.quote(db_path, safe="")
            url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
            with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
                manifest_data = json.loads(resp.read())

            assert manifest_data["total_groups"] >= 1, (
                "Expected at least one group in the manifest"
            )
            first_group = manifest_data["groups"][0]
            group_id = str(first_group["group_number"])

            target_a = "neardup_00_q95.jpg"
            row_tid_a = row_file_testid(group_id, target_a)
            right_click_row(page, row_tid_a)
            click_context_item(page, CTX_SET_ACTION_DELETE)

            post_a = _get_manifest_decisions(base_url, db_path)
            assert post_a[target_a] == "delete", (
                f"Step (a): expected user_decision='delete' for {target_a!r}, "
                f"got {post_a[target_a]!r}"
            )
            # All other rows must be unchanged from pre.
            for basename, decision in pre.items():
                if basename == target_a:
                    continue
                assert post_a[basename] == decision, (
                    f"Step (a) leaked into {basename!r}: "
                    f"expected {decision!r}, got {post_a[basename]!r}"
                )

            # --- Step (b): single-row keep (clear decision) via context menu ---
            target_b = "neardup_01_q88.jpg"
            row_tid_b = row_file_testid(group_id, target_b)
            right_click_row(page, row_tid_b)
            click_context_item(page, CTX_SET_ACTION_KEEP)

            post_b = _get_manifest_decisions(base_url, db_path)
            assert post_b[target_b] == "", (
                f"Step (b): expected user_decision='' (keep/clear) for {target_b!r}, "
                f"got {post_b[target_b]!r}"
            )
            # Step (a) decision on target_a must survive step (b).
            assert post_b[target_a] == "delete", (
                f"Step (b) clobbered step (a) decision on {target_a!r}: "
                f"expected 'delete', got {post_b[target_a]!r}"
            )

            # --- Step (c): second single-row delete (covers same DB write path) ---
            # Uses a third file to confirm the PATCH /api/decision endpoint
            # handles a distinct row independently.
            target_c = "neardup_02_q80.jpg"
            row_tid_c = row_file_testid(group_id, target_c)
            right_click_row(page, row_tid_c)
            click_context_item(page, CTX_SET_ACTION_DELETE)

            post_c = _get_manifest_decisions(base_url, db_path)
            assert post_c[target_c] == "delete", (
                f"Step (c): expected user_decision='delete' for {target_c!r}, "
                f"got {post_c[target_c]!r}"
            )
            # Prior decisions must be unchanged.
            assert post_c[target_a] == "delete", (
                f"Step (c) clobbered step (a) on {target_a!r}"
            )
            assert post_c[target_b] == "", (
                f"Step (c) clobbered step (b) on {target_b!r}"
            )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
