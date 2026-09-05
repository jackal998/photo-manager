"""Web scenario s26 — keyboard decision shortcuts ('d' / 'k') over the main tree.

Ported from qa/scenarios/s26_keyboard_navigation.py (Qt UIA), HONEST PARTIAL.

Qt s26 drives the WHOLE main flow by keyboard to catch focus-management bugs:
  0. 'd' / 'k' decision shortcuts (#615) — single + multi selection.
  1/3. result tree gets focus + ArrowDown roves through rows.
  4. Enter on a row (documented no-op).
  5. Alt+F mnemonic opens the File menu (#135).
  6. Tab-cycle through the Scan dialog reaches the canonical widgets.
  7. Esc dismisses the Scan dialog.

Web slice — the DECISION shortcuts (step 0) only:
  Pressing bare 'd' on the current main-tree selection sets user_decision=
  'delete'; bare 'k' clears it back to '' (canonical no-decision / keep, #584).
  This is the cross-OS user-facing flow; it ports the Qt DecisionTreeView.
  keyPressEvent (app/views/components/decision_tree_view.py) onto a document-
  level shortcut hook (frontend/src/hooks/useDecisionShortcuts.ts) that drives
  the SAME store.setDecisions the multi-select context menu uses — true parity.

  Three branches:
    A. 'd' single   — click q95 → press 'd' → q95 stages 'delete', others clean.
    B. 'k' single   — same selection → press 'k' → q95 clears back to '' (the
                      d-vs-k discriminator: a stuck-on-'delete' or no-op handler
                      fails here; a stuck-on-keep handler fails branch A).
    C. 'd' multi    — click q80 + Ctrl+click q72 → press 'd' → BOTH stage
                      'delete' (the shortcut fans out over the whole selection),
                      the unselected rows untouched.

Honest omits (no web equivalent / separate item — see the hook header):
  - ArrowDown roving focus through the virtualizer — a separate FE item (D3b).
  - Alt+F menu mnemonic, Tab-cycle, Esc-dismiss — Qt-native UIA menu/dialog
    keyboard traversal with no web analog (Radix owns dialog focus + Esc; the
    browser owns Tab order). The decision shortcuts are the portable surface.
  - 'p' play/pause — a PreviewPane (video) concern, out of the decision scope.

Qt divergences (assertion mechanics):
  - Manifest read via GET /api/manifest JSON (user_decision per item) vs Qt
    SQLite. A staged 'delete' keeps outcome='' so the row stays visible with
    user_decision='delete' (no Execute) — asserted by value, not by absence.
  - Modifier-bearing presses / editable-field focus / open-modal inertness are
    covered by the hook unit test (useDecisionShortcuts.test.ts); this scenario
    pins the happy path + the d-vs-k discrimination end-to-end.
  - /api/decision skips the allowed-roots guard (unlike /api/remove), so the
    repo fixture dir is scanned directly with a temp db (s30/s53 precedent).

Desktop source: qa/scenarios/s26_keyboard_navigation.py
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
    click_row,
    ctrl_click_row,
    run_scan,
)
from qa.web.testid_constants import row_file_testid

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

_Q95 = "neardup_00_q95.jpg"
_Q88 = "neardup_01_q88.jpg"
_Q80 = "neardup_02_q80.jpg"
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
    """Prove the bare 'd' / 'k' decision shortcuts act on the main-tree
    selection (single + multi), end-to-end through the live store + API."""
    # /api/decision does NOT enforce the allowed-roots guard, so scan the repo
    # fixture dir directly and write the db to a temp file (s30/s53 precedent).
    db_fd, db_path = tempfile.mkstemp(prefix="qa_s26_", suffix=".db")
    os.close(db_fd)
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
            pre = _decisions(_get_manifest(base_url, db_path))
            assert set(pre) == set(_ALL), (
                f"expected 5 fixture rows {sorted(_ALL)}, got {sorted(pre)}"
            )
            assert all(v == "" for v in pre.values()), (
                f"fixture should start undecided, got {pre}"
            )

            gid = _first_group_id(base_url, db_path)

            # ── Branch A — 'd' single: select q95, press 'd' → stages delete ──
            click_row(page, row_file_testid(gid, _Q95))
            page.keyboard.press("d")
            post_a = _await_decisions(base_url, db_path, {_Q95: "delete"})
            print(f"probe_status: s26 branch_a post={ {k: post_a[k] for k in sorted(post_a)} }")
            assert post_a.get(_Q95) == "delete", (
                f"branch A: pressing 'd' on the q95 selection must stage 'delete', "
                f"got {post_a.get(_Q95)!r}"
            )
            for name in (_Q88, _Q80, _Q72, _Q65):
                assert post_a.get(name) == "", (
                    f"branch A leaked into the unselected {name}: {post_a.get(name)!r}"
                )

            # ── Branch B — 'k' single: same selection, press 'k' → clears ─────
            # The d-vs-k discriminator: a handler stuck on 'delete' (or a no-op)
            # leaves q95 == 'delete' and fails here; branch A guards the inverse.
            page.keyboard.press("k")
            post_b = _await_decisions(base_url, db_path, {_Q95: ""})
            print(f"probe_status: s26 branch_b post={ {k: post_b[k] for k in sorted(post_b)} }")
            assert post_b.get(_Q95) == "", (
                f"branch B: pressing 'k' must clear q95 back to '' (no-decision), "
                f"got {post_b.get(_Q95)!r}"
            )

            # ── Branch C — 'd' multi: q80 + Ctrl+q72, press 'd' → both delete ─
            # Plain click q80 REPLACES the selection; Ctrl+click q72 adds it.
            # One 'd' press must fan out over BOTH rows.
            click_row(page, row_file_testid(gid, _Q80))
            ctrl_click_row(page, row_file_testid(gid, _Q72))
            page.keyboard.press("d")
            post_c = _await_decisions(
                base_url, db_path, {_Q80: "delete", _Q72: "delete"}
            )
            print(f"probe_status: s26 branch_c post={ {k: post_c[k] for k in sorted(post_c)} }")
            for name in (_Q80, _Q72):
                assert post_c.get(name) == "delete", (
                    f"branch C: 'd' must fan out over the whole selection — "
                    f"{name} expected 'delete', got {post_c.get(name)!r}"
                )
            # q95 was cleared in branch B; the never-selected rows stay clean.
            for name in (_Q95, _Q88, _Q65):
                assert post_c.get(name) == "", (
                    f"branch C disturbed the unselected {name}: {post_c.get(name)!r}"
                )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
