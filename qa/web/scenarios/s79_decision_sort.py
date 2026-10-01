"""Web scenario s79 — sort by decision (#923, design F3 + owner ruling 2026-09-29).

WEB-ONLY. The Action column header becomes the canonical decision-sort control
and the View menu mirrors it; both write ONE sort state. The unit tests
(lib/decisionSort.test.ts, store/decisionSort.store.test.ts,
components/ResultTree.decisionSort.test.tsx) pin the order, the tie rule and
the transitions. What lives here is what only a real browser can settle:

  1. **The cycle reorders the VIRTUALISED rows.** Keep first (Keep → Skip →
     Delete) → Delete first → cleared, read as the mounted row order after
     real decisions were staged through the row controls (PATCH /api/decision),
     and "cleared" returns exactly the server order.
  2. **The order is spelled, in the rendered case.** The header renders
     `ACTION · KEEP FIRST` through CSS `uppercase`, which jsdom never applies,
     and the field name must not be the part that truncates (F3).
  3. **A decision change never moves a row.** Staging Delete on the TOP Keep
     row leaves every row where it was; the 4px accent stale dot appears
     (computed box and colour — `h-1 w-1 bg-warm` are class strings in jsdom)
     with its tooltip, and the next header click re-sorts in place and clears
     it without advancing the cycle.
  4. **The View-menu mirror is the same state.** Picking "Delete first" in the
     menu shows in the header, the menu ticks what the header set, and
     "Clear sort" clears it.
  5. **zh-TW.** 「動作 · 保留優先」 / 「動作 · 刪除優先」 — new `web.*` keys, the
     kind that ships in en.yml alone (copy audit R8). `ui.locale` is a SERVER
     setting, so it is restored to `en` in a finally (the s75 lesson).

Fixture: qa/sandbox/near-duplicates/ (5 JPEGs, one group) — the fixture
s72-s76 use.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import run_scan, set_row_decision
from qa.web.testid_constants import (
    COL_SORT_STALE_DOT,
    COL_SORT_SUBLABEL,
    MENU_VIEW,
    MENU_VIEW_LANG_ZH,
    MENU_VIEW_SORT_CLEAR,
    MENU_VIEW_SORT_DELETE_FIRST,
    MENU_VIEW_SORT_KEEP_FIRST,
    col_header_testid,
    row_decision_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

_ACTION_HEADER = col_header_testid("action")
_ACCENT = "rgb(168, 90, 44)"  # --color-warm #a85a2c
_STALE_TOOLTIP = "Order is out of date — click to re-sort"


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _patch_locale(base_url: str, locale: str) -> None:
    """PATCH /api/settings ui.locale — the restore step (same contract as s76)."""
    url = f"{base_url.rstrip('/')}/api/settings"
    body = json.dumps({"updates": {"ui.locale": locale}}).encode()
    req = urllib.request.Request(
        url, data=body, method="PATCH", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"PATCH /api/settings failed ({exc.code}): {exc.read().decode()}"
        ) from exc


def _row_order(page, group_id: str) -> list[str]:
    """Basenames of the group's mounted file rows, in on-screen order."""
    prefix = f"row-file-{group_id}-"
    return page.evaluate(
        """(prefix) => Array.from(
             document.querySelectorAll(`[data-testid^="${prefix}"]`)
           ).map((el) => el.dataset.testid.slice(prefix.length))""",
        prefix,
    )


# The header's rendered text, half by half: innerText applies `uppercase`, and
# the field name's own overflow says whether it — the part F3 forbids
# truncating — is being clipped.
_READ_ACTION_HEADER = """([headerId, subId, dotId]) => {
  const head = document.querySelector(`[data-testid="${headerId}"]`);
  if (!head) return null;
  const label = head.querySelector('[data-col-label]');
  const sub = head.querySelector(`[data-testid="${subId}"]`);
  const dot = head.querySelector(`[data-testid="${dotId}"]`);
  const dotCs = dot ? getComputedStyle(dot) : null;
  return {
    label: label ? label.innerText.trim() : null,
    labelClipped: label ? label.scrollWidth > label.clientWidth : null,
    sub: sub ? sub.innerText.trim() : null,
    ariaSort: head.getAttribute('aria-sort'),
    dot: dot
      ? {
          width: dotCs.width,
          height: dotCs.height,
          backgroundColor: dotCs.backgroundColor,
          title: dot.getAttribute('title'),
        }
      : null,
  };
}"""


def _header(page) -> dict:
    return page.evaluate(
        _READ_ACTION_HEADER, [_ACTION_HEADER, COL_SORT_SUBLABEL, COL_SORT_STALE_DOT]
    )


def _click_header(page) -> dict:
    page.get_by_test_id(_ACTION_HEADER).click()
    page.wait_for_timeout(400)  # past the 120ms re-sort slide
    return _header(page)


def _expected(order: list[str], decisions: dict[str, str], direction: str) -> list[str]:
    """The ruled order: Keep → Skip → Delete (reversed for desc), ties in server order."""
    rank = {"": 0, "ignore": 1, "delete": 2}
    sign = -1 if direction == "desc" else 1
    return sorted(order, key=lambda name: sign * rank[decisions[name]])


def _menu_ticks(page) -> dict[str, bool]:
    page.get_by_test_id(MENU_VIEW).click()
    page.wait_for_timeout(300)
    ticks = {
        "keep_first": "✓" in page.get_by_test_id(MENU_VIEW_SORT_KEEP_FIRST).inner_text(),
        "delete_first": "✓" in page.get_by_test_id(MENU_VIEW_SORT_DELETE_FIRST).inner_text(),
    }
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    return ticks


def run(*, base_url: str) -> None:
    """Drive the Action-header decision sort, its stale dot and its menu mirror."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s79_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            page.goto("/")
            run_scan(page, sources=[_NEAR_DUPS_DIR], output_path=db_path, scan_timeout=120_000)

            manifest = _get_manifest(base_url, db_path)
            assert manifest["total_groups"] >= 1, "Expected at least one group"
            group_id = str(manifest["groups"][0]["group_number"])
            server = _row_order(page, group_id)
            assert len(server) == 5, f"Expected 5 near-dup rows on screen, got {server}"
            print(f"probe_status: s79 server order = {server}")

            # Interleave the three buckets through the REAL row controls so every
            # sort has to move rows: D K S D K down the server order.
            decisions = dict.fromkeys(server, "")
            for name, label, value in (
                (server[0], "Delete", "delete"),
                (server[2], "Skip", "ignore"),
                (server[3], "Delete", "delete"),
            ):
                set_row_decision(page, row_decision_testid(group_id, name), label)
                decisions[name] = value

            # ── 1+2. Keep first: rows reorder and the order is spelled ─────────
            head = _click_header(page)
            keep_first = _row_order(page, group_id)
            print(f"probe_status: s79 keep-first order = {keep_first} header = {head}")
            assert keep_first == _expected(server, decisions, "asc"), (
                f"#923 — Keep first must order Keep → Skip → Delete with ties in "
                f"server order; expected {_expected(server, decisions, 'asc')}, "
                f"got {keep_first}."
            )
            assert (head["label"], head["sub"]) == ("ACTION", "· KEEP FIRST"), (
                f"#923 — the active header must read 'ACTION · KEEP FIRST', got {head}."
            )
            assert head["labelClipped"] is False, (
                f"#923 — F3: truncate the sub-label, never the field name ({head})."
            )
            assert head["dot"] is None, f"A fresh sort must not show the stale dot: {head}"

            # ── 3. A decision change does NOT move a row ──────────────────────
            top = keep_first[0]
            set_row_decision(page, row_decision_testid(group_id, top), "Delete")
            decisions[top] = "delete"
            page.wait_for_timeout(300)
            after_change = _row_order(page, group_id)
            head = _header(page)
            print(f"probe_status: s79 after staging Delete on {top} = {after_change} header = {head}")
            assert after_change == keep_first, (
                "#923 — staging a decision MOVED rows under the pointer "
                f"({keep_first} → {after_change}); F3 defers the re-sort."
            )
            assert head["dot"] == {
                "width": "4px",
                "height": "4px",
                "backgroundColor": _ACCENT,
                "title": _STALE_TOOLTIP,
            }, f"#923 — the stale dot must be a 4px accent dot with its tooltip: {head['dot']}"

            head = _click_header(page)
            resorted = _row_order(page, group_id)
            print(f"probe_status: s79 re-sorted = {resorted} header = {head}")
            assert resorted == _expected(server, decisions, "asc"), (
                f"#923 — the stale click must re-sort in place; got {resorted}."
            )
            assert head["sub"] == "· KEEP FIRST" and head["dot"] is None, (
                f"#923 — a stale click re-sorts WITHOUT advancing the cycle and clears the dot: {head}"
            )

            # ── 1. Delete first, then cleared = the server order ──────────────
            head = _click_header(page)
            delete_first = _row_order(page, group_id)
            assert delete_first == _expected(server, decisions, "desc"), (
                f"#923 — Delete first reverses the buckets; got {delete_first}."
            )
            assert head["sub"] == "· DELETE FIRST", f"Header after the 2nd click: {head}"
            head = _click_header(page)
            cleared = _row_order(page, group_id)
            print(f"probe_status: s79 delete-first = {delete_first} cleared = {cleared}")
            assert cleared == server and head["sub"] is None, (
                f"#923 — the 3rd click must clear back to the server order: {cleared} / {head}"
            )

            # ── 4. The View-menu mirror drives the same single state ──────────
            page.get_by_test_id(MENU_VIEW).click()
            page.wait_for_timeout(300)
            page.get_by_test_id(MENU_VIEW_SORT_DELETE_FIRST).click()
            page.wait_for_timeout(300)
            head = _header(page)
            ticks = _menu_ticks(page)
            print(f"probe_status: s79 menu → header = {head['sub']!r} ticks = {ticks}")
            assert head["sub"] == "· DELETE FIRST", f"Menu 'Delete first' did not reach the header: {head}"
            assert ticks == {"keep_first": False, "delete_first": True}, f"Menu ticks: {ticks}"
            page.get_by_test_id(MENU_VIEW).click()
            page.wait_for_timeout(300)
            page.get_by_test_id(MENU_VIEW_SORT_CLEAR).click()
            page.wait_for_timeout(300)
            assert _header(page)["sub"] is None, "Menu 'Clear sort' did not clear the header."
            assert _row_order(page, group_id) == server, "'Clear sort' did not restore server order."

            # ── 5. zh-TW ──────────────────────────────────────────────────────
            page.get_by_test_id(MENU_VIEW).click()
            page.wait_for_timeout(300)
            page.get_by_test_id(MENU_VIEW_LANG_ZH).click()
            page.wait_for_timeout(800)
            zh_keep = _click_header(page)
            zh_delete = _click_header(page)
            _click_header(page)  # back to cleared
            print(f"probe_status: s79 zh = {zh_keep['label']} {zh_keep['sub']} / {zh_delete['sub']}")
            assert f"{zh_keep['label']} {zh_keep['sub']}" == "動作 · 保留優先", f"zh Keep first: {zh_keep}"
            assert f"{zh_delete['label']} {zh_delete['sub']}" == "動作 · 刪除優先", f"zh Delete first: {zh_delete}"
    finally:
        # ALWAYS put the server back to English — `ui.locale` lives in the
        # server's settings file, not in the browser context PWContext discards.
        _patch_locale(base_url, "en")
        shutil.rmtree(tmpdir, ignore_errors=True)
