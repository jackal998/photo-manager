"""Web scenario s26 — keyboard navigation over the main result tree.

Ported from qa/scenarios/s26_keyboard_navigation.py (Qt UIA), HONEST PARTIAL.

Qt s26 drives the WHOLE main flow by keyboard to catch focus-management bugs:
  0. 'd' / 'k' decision shortcuts (#615) — single + multi selection.
  1/3. result tree gets focus + ArrowDown roves through rows.
  4. Enter on a row (documented no-op).
  5. Alt+F mnemonic opens the File menu (#135).
  6. Tab-cycle through the Scan dialog reaches the canonical widgets.
  7. Esc dismisses the Scan dialog.

Web slice — the DECISION shortcuts (step 0) and the ROVING CURSOR (steps 1/3):
  Pressing bare 'd' on the current main-tree selection sets user_decision=
  'delete'; bare 'k' clears it back to '' (canonical no-decision / keep, #584).
  This is the cross-OS user-facing flow; it ports the Qt DecisionTreeView.
  keyPressEvent (app/views/components/decision_tree_view.py) onto a document-
  level shortcut hook (frontend/src/hooks/useDecisionShortcuts.ts) that drives
  the SAME store.setDecisions the multi-select context menu uses — true parity.

  Four branches:
    A. 'd' single   — click q95 → press 'd' → q95 stages 'delete', others clean.
    B. 'k' single   — same selection → press 'k' → q95 clears back to '' (the
                      d-vs-k discriminator: a stuck-on-'delete' or no-op handler
                      fails here; a stuck-on-keep handler fails branch A).
    C. 'd' multi    — click q80 + Ctrl+click q72 → press 'd' → BOTH stage
                      'delete' (the shortcut fans out over the whole selection),
                      the unselected rows untouched.
    D. arrow roving — Qt steps 1/3 (#709). The rows are virtualized, absolutely
                      positioned, non-focusable <div>s, so the SCROLL CONTAINER
                      is the focus target (tabIndex=0, role="tree") and
                      aria-activedescendant names the active row. Asserted by
                      what the user sees: which row carries aria-selected after
                      N presses, that the preview image swaps to that file, that
                      a group header is a stop which does NOT collapse its
                      group but DOES clear the file selection (so 'd'/'k' can't
                      write to the row the cursor left — Qt's
                      set_decision_to_highlighted filters type=="file" out of
                      the current selection, making the desktop a no-op there),
                      and — with the window deliberately shrunk so the
                      5-row fixture no longer fits — that arrowing back up to a
                      row above the viewport scrolls it in BELOW the sticky
                      column header rather than underneath it (#699/#846: the
                      virtualizer needs scrollPaddingStart = the measured
                      scrollMargin, or "align: start" parks the row under it).

Honest omits (no web equivalent / separate item — see the hook header):
  - Shift+arrow range extension, Home/End/PageUp/PageDown, and Left/Right
    expand-collapse on a group header: Qt inherits those from QTreeView; the
    web port ships the bare Up/Down cursor (#709) only, as the issue scoped it.
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
from qa.web.testid_constants import (
    MAIN_RESULT_TREE,
    PREVIEW_SINGLE_IMAGE,
    RESULT_COL_HEADER_ROW,
    row_file_testid,
    row_group_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

# Branch D — the window is shrunk to this so the 5-row fixture (5 * ~72px file
# rows + a ~34px group header + the sticky column header) no longer fits in the
# tree's viewport. Without a scrolling tree every "did it come into view"
# assertion below would pass while doing nothing.
_SHORT_VIEWPORT = {"width": 1280, "height": 340}
# Sub-pixel: browsers report fractional geometry and the virtualizer rounds
# measured row sizes. 1px absorbs that without absorbing a header height.
_GEOM_TOL_PX = 1.0
# The tree stops shrinking with the window at ~420px (its flex row has a
# min-content floor from the preview pane), so the 5-row fixture overflows it by
# only ~40px however short the window is. Branch D therefore never trusts a
# threshold to keep its geometry checks honest: before each "scroll it back into
# view" assertion it MEASURES that the target row is currently hidden under the
# sticky header, and fails as vacuous if it is not. This floor only buys a
# clearer message when the tree does not scroll at all.
_MIN_SCROLLABLE_PX = 10

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


# Branch D reads every quantity in ONE evaluate so they all describe the same
# instant — row boxes, the sticky header box and scrollTop must not be sampled
# across separate scroll frames (the s47 #699 phase learned this the hard way).
_CURSOR_JS = """
() => {
  const tree = document.querySelector('[data-testid="%s"]');
  const header = document.querySelector('[data-testid="%s"]');
  if (!tree) return null;
  const activeId = tree.getAttribute('aria-activedescendant');
  const active = activeId ? document.getElementById(activeId) : null;
  const inner = active
    ? active.querySelector('[data-testid^="row-file-"], [data-testid^="row-group-"]')
    : null;
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, height: r.height };
  };
  const focused = document.activeElement;
  return {
    active_id: activeId,
    active_testid: inner ? inner.getAttribute('data-testid') : null,
    active_role: active ? active.getAttribute('role') : null,
    active_box: box(active),
    header_box: box(header),
    tree_box: box(tree),
    tree_tabindex: tree.getAttribute('tabindex'),
    tree_role: tree.getAttribute('role'),
    scroll_top: tree.scrollTop,
    scrollable: tree.scrollHeight - tree.clientHeight,
    focused_testid: focused ? focused.getAttribute('data-testid') : null,
    focused_tag: focused ? focused.tagName : null,
    selected_testids: Array.from(
      tree.querySelectorAll('[data-testid^="row-file-"][aria-selected="true"]')
    ).map((el) => el.getAttribute('data-testid')),
    row_count: tree.querySelectorAll('[data-testid^="row-file-"]').length,
    // Every rendered row's box, so the branch can prove a row was really
    // hidden BEFORE it asks the cursor to bring it back.
    rows: Object.fromEntries(
      Array.from(
        tree.querySelectorAll('[data-testid^="row-file-"], [data-testid^="row-group-"]')
      ).map((el) => [el.getAttribute('data-testid'), box(el)])
    ),
  };
}
""" % (MAIN_RESULT_TREE, RESULT_COL_HEADER_ROW)


def _cursor(page) -> dict:
    """Read the roving-cursor state + the geometry that decides visibility."""
    state = page.evaluate(_CURSOR_JS)
    if state is None:
        raise AssertionError(
            f"{MAIN_RESULT_TREE} is not in the DOM — the manifest did not render."
        )
    return state


def _preview_src(page) -> str:
    """The single-file preview image's src — carries the previewed file path."""
    return page.get_by_test_id(PREVIEW_SINGLE_IMAGE).get_attribute("src") or ""


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

            # ── Branch D — roving ArrowDown/ArrowUp cursor (#709, Qt 1/3) ────
            # Shrink the window first: with the default 720px viewport the
            # 5-row fixture fits entirely and nothing ever scrolls, so the
            # scroll-into-view assertions would be free.
            page.set_viewport_size(_SHORT_VIEWPORT)
            page.wait_for_timeout(250)

            click_row(page, row_file_testid(gid, _Q95))
            page.wait_for_timeout(120)
            start = _cursor(page)
            print(
                f"probe_status: s26 branch_d focus_after_click="
                f"{start['focused_testid'] or start['focused_tag']!r} "
                f"tabindex={start['tree_tabindex']!r} role={start['tree_role']!r} "
                f"scrollable={start['scrollable']:.1f} rows={start['row_count']}"
            )
            assert start["tree_tabindex"] == "0" and start["tree_role"] == "tree", (
                "branch D: the result tree must be the keyboard focus target "
                f"(tabindex=0, role=tree), got tabindex={start['tree_tabindex']!r} "
                f"role={start['tree_role']!r}"
            )
            assert start["scrollable"] >= _MIN_SCROLLABLE_PX, (
                f"branch D: the tree only has {start['scrollable']:.1f}px of "
                f"scroll range at {_SHORT_VIEWPORT}; the scroll-into-view "
                f"assertions below would pass without scrolling anything."
            )

            # Qt seeds focus with tree.set_focus() before arrowing; do the same
            # so the branch does not depend on click-focus delegation.
            page.get_by_test_id(MAIN_RESULT_TREE).evaluate("el => el.focus()")

            # D1 — two ArrowDowns walk q95 → q88 → q80 in the ON-SCREEN order.
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(150)
            after_down = _cursor(page)
            q80_testid = row_file_testid(gid, _Q80)
            print(
                f"probe_status: s26 branch_d after_down2 "
                f"active={after_down['active_testid']!r} "
                f"selected={after_down['selected_testids']} "
                f"active_id={after_down['active_id']!r}"
            )
            assert after_down["active_testid"] == q80_testid, (
                f"branch D: ArrowDown x2 from {_Q95} must land on {_Q80}; "
                f"aria-activedescendant names {after_down['active_testid']!r}"
            )
            assert after_down["selected_testids"] == [q80_testid], (
                f"branch D: the arrowed-to row must be THE selection (so the "
                f"d/k shortcuts act on it); selected rows are "
                f"{after_down['selected_testids']}"
            )
            assert after_down["active_role"] == "treeitem", (
                f"branch D: aria-activedescendant must name a treeitem, got "
                f"{after_down['active_role']!r}"
            )
            src_after_down = _preview_src(page)
            assert _Q80 in src_after_down, (
                f"branch D: the preview pane must follow the cursor — expected "
                f"{_Q80} in the preview src, got {src_after_down!r}"
            )

            # D2 — ArrowUp walks back one row.
            page.keyboard.press("ArrowUp")
            page.wait_for_timeout(150)
            after_up = _cursor(page)
            assert after_up["active_testid"] == row_file_testid(gid, _Q88), (
                f"branch D: ArrowUp from {_Q80} must land on {_Q88}, got "
                f"{after_up['active_testid']!r}"
            )

            # D3 — run to the last row: it must be brought fully into view.
            for _ in range(5):
                page.keyboard.press("ArrowDown")
            page.wait_for_timeout(250)
            at_last = _cursor(page)
            print(
                f"probe_status: s26 branch_d at_last active={at_last['active_testid']!r} "
                f"scroll_top={at_last['scroll_top']:.1f} "
                f"row_top={at_last['active_box']['top']:.1f} "
                f"tree_bottom={at_last['tree_box']['bottom']:.1f}"
            )
            assert at_last["active_testid"] == row_file_testid(gid, _Q65), (
                f"branch D: ArrowDown past the last row must CLAMP on {_Q65} "
                f"(never wrap), got {at_last['active_testid']!r}"
            )
            assert (
                at_last["active_box"]["bottom"]
                <= at_last["tree_box"]["bottom"] + _GEOM_TOL_PX
            ), (
                f"branch D: the active row's bottom "
                f"({at_last['active_box']['bottom']:.1f}) is below the tree's "
                f"viewport ({at_last['tree_box']['bottom']:.1f}) — arrowing down "
                f"did not keep the cursor on screen."
            )
            assert at_last["scroll_top"] > 0, (
                "branch D: the tree never scrolled while the cursor walked to "
                "the last row, so the on-screen assertions are vacuous."
            )

            # D4 — back up to the FIRST file row, which the scroll above has
            # pushed under the sticky column header. It must be scrolled back in
            # BELOW that header: the virtualizer's "start" alignment parks a row
            # at the container's top edge, i.e. underneath the header
            # (#699/#846), unless scrollPaddingStart carries its measured height.
            q95_testid = row_file_testid(gid, _Q95)
            hidden_by = (
                at_last["header_box"]["bottom"]
                - at_last["rows"][q95_testid]["top"]
            )
            print(
                f"probe_status: s26 branch_d before_up {_Q95} "
                f"top={at_last['rows'][q95_testid]['top']:.1f} "
                f"header_bottom={at_last['header_box']['bottom']:.1f} "
                f"hidden_by={hidden_by:.2f}"
            )
            for _ in range(4):
                page.keyboard.press("ArrowUp")
            page.wait_for_timeout(250)
            at_first = _cursor(page)
            clearance = (
                at_first["active_box"]["top"] - at_first["header_box"]["bottom"]
            )
            print(
                f"probe_status: s26 branch_d at_first active={at_first['active_testid']!r} "
                f"scroll_top={at_first['scroll_top']:.1f} "
                f"row_top={at_first['active_box']['top']:.1f} "
                f"header_bottom={at_first['header_box']['bottom']:.1f} "
                f"clearance={clearance:.2f}"
            )
            assert at_first["active_testid"] == row_file_testid(gid, _Q95), (
                f"branch D: ArrowUp x4 must land back on {_Q95}, got "
                f"{at_first['active_testid']!r}"
            )
            if hidden_by > _GEOM_TOL_PX:
                assert clearance >= -_GEOM_TOL_PX, (
                    f"branch D: the active row's top "
                    f"({at_first['active_box']['top']:.1f}) sits "
                    f"{abs(clearance):.2f}px ABOVE the sticky column header's "
                    f"bottom ({at_first['header_box']['bottom']:.1f}) — the row "
                    f"the user just arrowed to is hidden underneath the header."
                )
            else:
                # This fixture only overflows the tree by ~40px, of which the
                # group-header row eats most, so on a platform whose rows are a
                # few px shorter the first file row can already be clear here.
                # Say so out loud instead of asserting nothing — D5 below pins
                # the same clearance property on a row that IS always hidden at
                # this point (by scroll_top px, ~33 here).
                print(
                    f"probe_status: s26 branch_d at_first clearance NOT asserted "
                    f"— {_Q95} was only {hidden_by:.2f}px under the header before "
                    f"the walk back; D5 carries the clearance check"
                )

            # D5 — one more ArrowUp lands on the GROUP HEADER: a stop, like the
            # Qt tree's top-level rows, but arrowing onto it must not collapse
            # the group or disturb the file selection. It is also where the
            # header-clearance property is pinned unconditionally: after D4 the
            # tree is still scrolled by one group-header + column-header height,
            # so this row is reliably hidden under the sticky header right now —
            # measured, not assumed.
            group_testid = row_group_testid(gid)
            group_hidden_by = (
                at_first["header_box"]["bottom"]
                - at_first["rows"][group_testid]["top"]
            )
            print(
                f"probe_status: s26 branch_d before_up group row "
                f"top={at_first['rows'][group_testid]['top']:.1f} "
                f"hidden_by={group_hidden_by:.2f}"
            )
            assert group_hidden_by > _GEOM_TOL_PX, (
                f"branch D is vacuous: the group header row is already clear of "
                f"the sticky column header ({group_hidden_by:.2f}px) before the "
                f"cursor walks up to it, so 'scrolled in below the header' would "
                f"pass without scrolling anything. The tree must overflow — "
                f"shrink _SHORT_VIEWPORT or grow the fixture."
            )
            page.keyboard.press("ArrowUp")
            page.wait_for_timeout(200)
            at_header = _cursor(page)
            header_clearance = (
                at_header["active_box"]["top"] - at_header["header_box"]["bottom"]
            )
            print(
                f"probe_status: s26 branch_d at_header active={at_header['active_testid']!r} "
                f"rows={at_header['row_count']} selected={at_header['selected_testids']} "
                f"scroll_top={at_header['scroll_top']:.1f} clearance={header_clearance:.2f}"
            )
            assert at_header["active_testid"] == row_group_testid(gid), (
                f"branch D: ArrowUp from the first file row must stop on the "
                f"group header, got {at_header['active_testid']!r}"
            )
            assert header_clearance >= -_GEOM_TOL_PX, (
                f"branch D: the group-header row the cursor just moved to sits "
                f"{abs(header_clearance):.2f}px ABOVE the sticky column header's "
                f"bottom ({at_header['header_box']['bottom']:.1f}) — it was "
                f"scrolled to the container's top edge, i.e. underneath the "
                f"header (#699/#846 scrollPaddingStart)."
            )
            assert at_header["row_count"] == len(_ALL), (
                f"branch D: arrowing onto the group header collapsed it — "
                f"{at_header['row_count']} file rows left of {len(_ALL)}."
            )
            assert at_header["selected_testids"] == [], (
                f"branch D: a group header is not part of the multi-selection, "
                f"so moving the cursor onto one must CLEAR the file selection — "
                f"otherwise 'd' would stage a decision on the row the cursor "
                f"just left (Qt's set_decision_to_highlighted filters "
                f"type=='file' out of the current selection, so the desktop is "
                f"a no-op here). Still selected: {at_header['selected_testids']}"
            )
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
