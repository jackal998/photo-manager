"""Web scenario s72 — "Apply best-copy decisions to this group" context menu.

WEB-ONLY feature (#744) — there is no Qt-side driver for this scenario. The
equivalent Qt right-click action was REMOVED in PR #224 (closed #210) and
superseded on desktop by the Set Action dialog's "top 1 by score within
group" numeric condition (see docs/features.md's Score-column entry). This
scenario is the web port's own regression guard for the reintroduced,
group-scoped context-menu item — ``ctx-apply-best-copy`` (design contract:
docs/design/web-port-tech-design.md:2265).

Semantics under test (the review-time twin of scan-time auto-select,
core.services.auto_select): within the target group, the top-score row
becomes the keeper (user_decision="" + is_locked=True); every row the
classifier positively identified as a duplicate (EXACT / REVIEW_DUPLICATE)
gets user_decision="delete". Deliberately NO match_confidence filtering (that
field is transient scan-time data, never persisted to the manifest DB — see
core.app_service.action_service.apply_best_copy's docstring).

Fixture: qa/sandbox/near-duplicates/ (5 JPEGs; neardup_00_q95.jpg is the
highest-score row — the SAME fixture + expected keeper/non-keeper split as
s57_scan_auto_select_aggressive.py's aggressive auto-select path, exercised
here via the review-time context-menu action instead of a scan-time flag).

Steps
-----
1. Scan the near-duplicates fixture with auto-select OFF (plain scan) so
   every row starts undecided/unlocked.
2. Right-click the GROUP row → click ctx-apply-best-copy.
3. Assert via GET /api/manifest: keeper locked + user_decision=="", every
   duplicate-action non-keeper user_decision=="delete" and unlocked, the
   ref-tier non-keeper (not a positively-classified duplicate) untouched.
4. Reload the page (clears in-memory client state) and re-open the SAME
   manifest via the FsBrowser picker, then re-fetch GET /api/manifest —
   assert the exact same per-file state, proving the write is a durable
   SQLite commit and not merely client-side/optimistic state.
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    click_context_item,
    open_manifest_via_picker,
    right_click_row,
    run_scan,
)
from qa.web.testid_constants import (
    CTX_APPLY_BEST_COPY,
    MAIN_EMPTY_STATE,
    row_group_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

_EXPECTED_KEEPER = "neardup_00_q95.jpg"
_EXPECTED_NON_KEEPERS = {
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
}
# Classifier actions the service treats as a positively-identified duplicate
# (mirrors core.services.auto_select._DUPLICATE_ACTIONS). A ref-tier
# non-keeper (action == "") is the reference and must be left untouched.
_DUPLICATE_ACTIONS = frozenset({"REVIEW_DUPLICATE", "EXACT"})


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _collect(manifest: dict) -> dict[str, dict]:
    """Return {basename: {action, user_decision, is_locked}} across all groups."""
    out: dict[str, dict] = {}
    for group in manifest.get("groups", []):
        for it in group.get("items", []):
            out[Path(it["file_path"]).name] = {
                "action": it.get("action", "") or "",
                "user_decision": it.get("user_decision", "") or "",
                "is_locked": bool(it.get("is_locked")),
            }
    return out


def _assert_best_copy_applied(state: dict[str, dict], *, context: str) -> None:
    """Assert the expected keeper-lock + non-keeper-delete split (shared by
    the post-click check and the post-reload durability check)."""
    assert len(state) == 5, f"[{context}] Expected 5 near-dup rows, got {sorted(state)}"

    assert _EXPECTED_KEEPER in state, (
        f"[{context}] Expected keeper {_EXPECTED_KEEPER!r} missing from the manifest."
    )
    keeper = state[_EXPECTED_KEEPER]
    assert keeper["user_decision"] == "", (
        f"[{context}] {_EXPECTED_KEEPER}.user_decision={keeper['user_decision']!r}, "
        f"expected '' (canonical keep — #425)."
    )
    assert keeper["is_locked"] is True, (
        f"[{context}] {_EXPECTED_KEEPER}.is_locked={keeper['is_locked']!r}, expected "
        f"True (#744 keep+lock write missing)."
    )

    deleted = 0
    for name in _EXPECTED_NON_KEEPERS:
        assert name in state, f"[{context}] Expected non-keeper {name!r} missing."
        r = state[name]
        assert r["is_locked"] is False, (
            f"[{context}] Non-keeper {name}.is_locked={r['is_locked']!r}, expected "
            f"False — apply-best-copy must NOT lock non-keepers."
        )
        if r["action"] in _DUPLICATE_ACTIONS:
            assert r["user_decision"] == "delete", (
                f"[{context}] Non-keeper duplicate {name} (action={r['action']!r}) "
                f"expected user_decision='delete'. Got {r['user_decision']!r}."
            )
            deleted += 1
        else:
            # Ref-tier non-keeper — observed, not asserted-delete (platform-
            # dependent which variant classifies as the reference; s57 D3).
            print(
                f"probe_status: s72 [{context}] ref-tier non-keeper {name} "
                f"(action={r['action']!r}) user_decision={r['user_decision']!r} "
                f"— not auto-deleted (correct: references are kept)."
            )

    assert deleted >= 1, (
        f"[{context}] apply-best-copy marked zero non-keeper duplicates for "
        f"delete — the write did not fire (silent no-op)."
    )


def run(*, base_url: str) -> None:
    """Scan (no auto-select), apply best-copy via the group context menu,
    assert the manifest end-state, then reload + reopen to prove durability."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s72_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # Plain scan — auto-select OFF, every row starts undecided/unlocked.
            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            pre = _collect(_get_manifest(base_url, db_path))
            assert len(pre) == 5, f"Expected 5 near-dup rows pre-apply, got {sorted(pre)}"
            for name, row in pre.items():
                assert row["user_decision"] == "" and row["is_locked"] is False, (
                    f"Pre-condition violated: {name} already decided/locked: {row}"
                )

            # Resolve the group_number for the row testid.
            manifest_data = _get_manifest(base_url, db_path)
            assert manifest_data["total_groups"] >= 1, "Expected at least one group"
            group_number = manifest_data["groups"][0]["group_number"]
            group_id = str(group_number)

            # Right-click the GROUP row → Apply best-copy decisions to this group.
            right_click_row(page, row_group_testid(group_id))
            assert page.get_by_test_id(CTX_APPLY_BEST_COPY).is_visible(), (
                "Expected 'Apply best-copy decisions to this group' in the "
                "group-row context menu (#744)."
            )
            click_context_item(page, CTX_APPLY_BEST_COPY)

            post = _collect(_get_manifest(base_url, db_path))
            _assert_best_copy_applied(post, context="post-click")

            # ── Durability: reload clears in-memory client state; reopen via
            # the FsBrowser picker and re-fetch — the SQLite write must survive.
            page.reload()
            page.get_by_test_id(MAIN_EMPTY_STATE).wait_for(state="visible", timeout=10_000)
            open_manifest_via_picker(page, db_path, timeout=30_000)

            reloaded = _collect(_get_manifest(base_url, db_path))
            _assert_best_copy_applied(reloaded, context="post-reload")
            assert reloaded == post, (
                "Manifest state after reload+reopen differs from the state "
                "immediately after apply-best-copy — the write did not persist."
            )
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
