"""Web scenario s76 — toolbar, status bar and density switch (#878 slice TB).

WEB-ONLY. Layout slice TB gives the 52px toolbar and the 30px status strip the
REPLY's spec, and adds three controls the app has never had: a filter box, the
counted bulk verbs L5 asked for, and the "⊗ Delete N files…" CTA that closes
#906 together with the status strip's two figures.

Five things live here rather than in vitest, each because jsdom cannot decide
it:

  1. **Computed geometry.** The REPLY fixes the toolbar at 52px, the strip at
     30px/11.5px, and — the assertion with teeth — «exactly one filled warm
     button in the toolbar, so "where do I start" has exactly one answer». A
     jsdom test can only see class names; Tailwind v4 resolves `bg-warm` to a
     colour after the CSS pipeline runs, so a jsdom assertion on it stays green
     after the token behind it is deleted.
  2. **The filter against the VIRTUALISED tree.** Rows are windowed, so
     "hidden" is not a style — the row is not in the DOM at all, and the group
     header has to go with it. jsdom never runs the measurement path, so a
     jsdom filter test proves a predicate ran and nothing more.
  3. **The bulk verbs end-to-end.** Ctrl-click two rows, press Delete, and the
     MANIFEST has to carry both decisions — the store, the PATCH, and the undo
     toast slice G built, now reached from a selection that is not a group.
  4. **The density switch and the virtualiser.** Compact re-renders rows at
     52px, but the virtualiser caches a size per index; if nothing re-measures,
     the total scroll height stays 72px-based and a scroll to the last row
     lands short. `overscan: 10` hides that near the top, so BOTH halves are
     read: the row's computed height AND the tree's total size, then a scroll
     to the last row.
  5. **zh-TW.** Every new string is a `web.*` key in both catalogs; a toast or
     a CTA that silently falls back to English is the copy-audit R8 defect.

Fixture: qa/sandbox/near-duplicates/ (5 JPEGs, one group) — the same fixture
s72/s73/s74/s75 use, so the keeper split is the one s57 pins.
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
from qa.web._invariants import click_row, ctrl_click_row, run_scan
from qa.web.testid_constants import (
    EXECUTE_DIALOG,
    MAIN_BULK_LABEL,
    MAIN_MANIFEST_INPUT,
    MAIN_MANIFEST_OPEN,
    group_keep_best_testid,
    MAIN_BULK_VERB_DELETE,
    MAIN_DELETE_CTA,
    MAIN_DENSITY_COMFORTABLE,
    MAIN_DENSITY_COMPACT,
    MAIN_DENSITY_TOGGLE,
    MAIN_FILTER_INPUT,
    MAIN_LANG_TOGGLE,
    MAIN_RESULT_TREE,
    MAIN_SCAN_SUMMARY,
    MAIN_STATUS_DELETE_COUNT,
    MAIN_STATUS_RECLAIM,
    MAIN_STATUS_STRIP,
    MAIN_TOAST,
    MAIN_TOAST_UNDO,
    MAIN_TOOLBAR,
    row_file_testid,
    row_group_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

# --color-warm #a85a2c — the one accent, and the primary button's fill.
_ACCENT = "rgb(168, 90, 44)"
# --color-titlebar #f3ede3 — the status strip's surface.
_TITLEBAR = "rgb(243, 237, 227)"

_KEEPER = "neardup_00_q95.jpg"


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _decisions(manifest: dict) -> dict[str, str]:
    """{basename: user_decision} across every group."""
    out: dict[str, str] = {}
    for group in manifest.get("groups", []):
        for item in group.get("items", []):
            out[Path(item["file_path"]).name] = item.get("user_decision", "") or ""
    return out


def _patch_locale(base_url: str, locale: str) -> None:
    """PATCH /api/settings to set ui.locale — the restore step.

    `ui.locale` is persisted SERVER-side, so a scenario that toggles the
    language leaves every scenario after it in the batch running against a
    zh_TW UI. s75 paid for this: its first live run left the server in zh_TW
    and s74 then timed out on an English status-bar regex that could never
    match 「1 個群組 · 5 個檔案」. Same restore contract as s22.
    """
    url = f"{base_url.rstrip('/')}/api/settings"
    body = json.dumps({"updates": {"ui.locale": locale}}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"PATCH /api/settings failed ({exc.code}): {exc.read().decode()}"
        ) from exc


# Height + the count of warm-filled controls, read together: the REPLY's
# "exactly one primary" is a property of the STRIP, not of any one button, and
# reading it as a count is what makes a second accent button fail here.
_READ_TOOLBAR = """(testid) => {
  const bar = document.querySelector(`[data-testid="${testid}"]`);
  if (!bar) return null;
  const accents = [];
  for (const el of bar.querySelectorAll('button')) {
    const bg = getComputedStyle(el).backgroundColor;
    if (bg === 'rgb(168, 90, 44)') accents.push(el.dataset.testid || el.textContent.trim());
  }
  const cs = getComputedStyle(bar);
  // The overflow is reported as a NUMBER, with the per-child widths and the
  // resolved font behind it, because the first CI run of this scenario failed
  // here on a runner with no Segoe UI and the boolean said only "too wide" —
  // which is a prompt to guess at a floor rather than to set one. The widths
  // are the measurement the next reader needs.
  const kids = [];
  for (const el of bar.children) {
    kids.push({
      id: el.dataset.testid || el.tagName,
      w: Math.round(el.getBoundingClientRect().width),
    });
  }
  return {
    height: cs.height,
    backgroundColor: cs.backgroundColor,
    accents,
    // A strip that does not fit is a strip whose right end — where the only
    // destructive control lives — is off screen (or, since it is
    // `overflow-x: auto`, only reachable by scrolling a toolbar).
    overflowPx: bar.scrollWidth - bar.clientWidth,
    font: getComputedStyle(document.body).fontFamily,
    kids,
  };
}"""

_READ_STRIP = """(testid) => {
  const el = document.querySelector(`[data-testid="${testid}"]`);
  if (!el) return null;
  const cs = getComputedStyle(el);
  return {
    height: cs.height,
    fontSize: cs.fontSize,
    backgroundColor: getComputedStyle(el.closest('footer')).backgroundColor,
  };
}"""

_READ_DENSITY_SELECTED = """(toggleId) => {
  const box = document.querySelector(`[data-testid="${toggleId}"]`);
  if (!box) return null;
  const out = [];
  for (const b of box.querySelectorAll('button')) {
    out.push({
      testid: b.dataset.testid,
      pressed: b.getAttribute('aria-pressed'),
      backgroundColor: getComputedStyle(b).backgroundColor,
    });
  }
  return out;
}"""

# The virtualiser's own total size, not the rendered row count: with
# `overscan: 10` a stale cache renders exactly the same rows near the top, so
# the spacer's height is the only place the staleness is visible.
_READ_TREE_METRICS = """(treeId) => {
  const tree = document.querySelector(`[data-testid="${treeId}"]`);
  if (!tree) return null;
  const spacer = tree.querySelector('[style*="height"]');
  const row = document.querySelector('[data-testid^="row-file-"]');
  return {
    totalSize: spacer ? Math.round(spacer.getBoundingClientRect().height) : null,
    rowHeight: row ? getComputedStyle(row).height : null,
  };
}"""


def _visible_rows(page) -> list[str]:
    """Basenames of the file rows currently mounted in the tree."""
    return page.evaluate(
        """() => Array.from(
             document.querySelectorAll('[data-testid^="row-file-"]')
           ).map((el) => el.dataset.testid)"""
    )


def run(*, base_url: str) -> None:
    """Drive the toolbar, the status strip and the density switch."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s76_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            page.goto("/")

            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            manifest_data = _get_manifest(base_url, db_path)
            assert manifest_data["total_groups"] >= 1, "Expected at least one group"
            group_number = manifest_data["groups"][0]["group_number"]
            group_id = str(group_number)
            names = sorted(_decisions(manifest_data))
            assert len(names) == 5, f"Expected 5 near-dup rows, got {names}"

            # ── 1. Toolbar geometry + the single primary ────────────────────
            toolbar = page.evaluate(_READ_TOOLBAR, MAIN_TOOLBAR)
            print(f"probe_status: s76 toolbar = {toolbar}")
            assert toolbar is not None, "The toolbar did not render."
            assert toolbar["height"] == "52px", (
                f"The toolbar computes to {toolbar['height']}, expected 52px "
                "(REPLY §Toolbar). box-sizing must include its bottom rule."
            )
            assert toolbar["accents"] == ["main-scan-button"], (
                "The REPLY allows EXACTLY ONE warm-filled control in the "
                f"toolbar, and it must be the primary. Found: {toolbar['accents']}"
            )
            assert toolbar["overflowPx"] <= 1, (
                f"The toolbar overflows its own width by {toolbar['overflowPx']}px "
                "at 1280x800, so its right end — where the Delete CTA lives — is "
                "only reachable by scrolling a toolbar. The two text inputs are "
                "the shrinkable members (filter floor 104px, manifest floor "
                "56px); widen the shrink, do not relax this assertion. Resolved "
                f"font: {toolbar['font']}. Child widths: {toolbar['kids']}"
            )

            # ── 2. Status strip geometry + #906's two figures at zero ───────
            strip = page.evaluate(_READ_STRIP, MAIN_STATUS_STRIP)
            print(f"probe_status: s76 status strip = {strip}")
            assert strip["height"] == "30px", (
                f"The status strip computes to {strip['height']}, expected 30px."
            )
            assert strip["fontSize"] == "11.5px", (
                f"The status strip is {strip['fontSize']}, expected 11.5px — "
                "«F11's font is the gap, the colours already match»."
            )
            assert strip["backgroundColor"] == _TITLEBAR, (
                f"The footer surface is {strip['backgroundColor']}, expected "
                f"{_TITLEBAR} (#f3ede3)."
            )
            zero_count = page.get_by_test_id(MAIN_STATUS_DELETE_COUNT).inner_text()
            zero_cta = page.get_by_test_id(MAIN_DELETE_CTA).inner_text()
            print(f"probe_status: s76 at zero = {zero_count!r} / {zero_cta!r}")
            assert "0" in zero_count, (
                f"The delete counter reads {zero_count!r} with nothing marked."
            )
            assert "Delete 0 files" in zero_cta, (
                "«Zero marked → disabled, label reads 'Delete 0 files…' rather "
                f"than disappearing, so the button never moves». Got {zero_cta!r}."
            )
            assert page.get_by_test_id(MAIN_DELETE_CTA).is_disabled(), (
                "The danger CTA is enabled with nothing marked for deletion."
            )

            # ── 3. Menu-bar scan summary (F3/F4) ───────────────────────────
            summary = page.get_by_test_id(MAIN_SCAN_SUMMARY).inner_text()
            print(f"probe_status: s76 menu-bar scan summary = {summary!r}")
            assert "5" in summary and "1" in summary, (
                f"The scan summary does not carry the counts: {summary!r}"
            )
            assert "near-duplicates" in summary, (
                "The scan summary does not name the source folder every row "
                f"shares: {summary!r}"
            )
            no_wrap = page.evaluate(
                """(testid) => {
  const el = document.querySelector(`[data-testid="${testid}"]`);
  return el ? getComputedStyle(el).whiteSpace : null;
}""",
                MAIN_SCAN_SUMMARY,
            )
            assert no_wrap == "nowrap", (
                f"The scan summary's white-space is {no_wrap!r} — «truncate the "
                "source name first; never wrap»."
            )

            # ── 4. Filter narrows the VIRTUALISED tree ─────────────────────
            before_filter = _visible_rows(page)
            assert len(before_filter) == 5, (
                f"Expected 5 rows mounted before filtering, got {before_filter}"
            )
            filter_box = page.get_by_test_id(MAIN_FILTER_INPUT)
            filter_box.fill("q95")
            page.wait_for_timeout(300)
            filtered = _visible_rows(page)
            print(f"probe_status: s76 rows after filter 'q95' = {filtered}")
            assert filtered == [row_file_testid(group_id, _KEEPER)], (
                f"Filtering on 'q95' left {filtered}, expected only the one "
                f"matching row ({_KEEPER})."
            )
            # The group header survives because ONE of its rows matched; a group
            # with no match must disappear entirely, which the folder query
            # below cannot show on a single-group fixture — so assert the
            # surviving header here and the empty-result case next.
            assert page.get_by_test_id(row_group_testid(group_id)).is_visible(), (
                "The group header vanished even though one of its rows matches."
            )
            filter_box.fill("no-such-file-anywhere")
            page.wait_for_timeout(300)
            empty_rows = _visible_rows(page)
            empty_headers = page.evaluate(
                """() => document.querySelectorAll('[data-testid^="row-group-"]').length"""
            )
            print(
                f"probe_status: s76 no-match rows={empty_rows} headers={empty_headers}"
            )
            assert empty_rows == [] and empty_headers == 0, (
                "A filter that matches nothing must leave no rows AND no group "
                f"headers — an empty header promises rows that are not there. "
                f"Got rows={empty_rows}, headers={empty_headers}."
            )
            # The filter is VIEW-ONLY: nothing about the manifest changed.
            assert _decisions(_get_manifest(base_url, db_path)) == _decisions(
                manifest_data
            ), "The filter wrote to the manifest — it must be view-only."

            # ── 4b. A filter NARROWS what the bulk verbs act on ─────────────
            # The selection survives the filter (typing must not destroy the
            # user's picks), so it can name rows nobody can see. The label and
            # the write read one selector, and this is where that is checked
            # end-to-end: select every row, filter down to one, and the label
            # must say 1 — a jsdom test cannot settle it because the hidden
            # rows are not merely styled away, they are out of the DOM.
            filter_box.fill("")
            page.wait_for_timeout(300)
            click_row(page, row_file_testid(group_id, sorted(names)[0]))
            for name in sorted(names)[1:]:
                ctrl_click_row(page, row_file_testid(group_id, name))
            page.wait_for_timeout(200)
            label_all = page.get_by_test_id(MAIN_BULK_LABEL).inner_text()
            filter_box.fill("q95")
            page.wait_for_timeout(300)
            label_filtered = page.get_by_test_id(MAIN_BULK_LABEL).inner_text()
            print(
                "probe_status: s76 bulk label across a filter = "
                f"{label_all!r} → {label_filtered!r}"
            )
            assert "5" in label_all, (
                f"Expected all five rows selected, label read {label_all!r}."
            )
            assert "1" in label_filtered and "5" not in label_filtered, (
                "The bulk label still counts rows the filter is hiding: "
                f"{label_filtered!r}. The verb would then write to rows the "
                "user cannot see, with a count that disagrees with the label "
                "it was pressed under."
            )

            # ── 4c. Keep-best is GATED while the filter is active ───────────
            # It is a server-side write scoped by group_number, so under a
            # filter it marks rows the header is not showing while the header
            # shows the filtered count beside the button.
            keep_best = page.get_by_test_id(group_keep_best_testid(group_id))
            filtered_disabled = keep_best.is_disabled()
            filtered_title = keep_best.get_attribute("title")
            print(
                "probe_status: s76 keep-best while filtered = "
                f"disabled={filtered_disabled} title={filtered_title!r}"
            )
            assert filtered_disabled is True, (
                "'Keep best · delete rest' is still clickable under an active "
                "filter — it would mark rows the filter is hiding."
            )
            assert filtered_title == "Clear the filter to use Keep best", (
                f"The disabled button gives no reason: title={filtered_title!r}"
            )

            filter_box.fill("")
            page.wait_for_timeout(300)
            cleared_disabled = keep_best.is_disabled()
            print(
                f"probe_status: s76 keep-best after clearing = disabled={cleared_disabled}"
            )
            # The false-positive half: a gate that refuses everything looks
            # exactly like one that works.
            assert cleared_disabled is False, (
                "'Keep best · delete rest' stayed disabled after the filter was "
                "cleared — the gate is refusing the ordinary case too."
            )
            page.get_by_test_id(row_file_testid(group_id, sorted(names)[0])).click()
            page.wait_for_timeout(200)

            restored = _visible_rows(page)
            assert sorted(restored) == sorted(before_filter), (
                f"Clearing the filter restored {restored}, expected the original "
                f"{before_filter}."
            )

            # ── 5. Counted bulk verbs over a ctrl-click selection ───────────
            others = [n for n in names if n != _KEEPER]
            pick = sorted(others)[:2]
            # Through the shared helpers, not a raw `.click()`: slice R centres
            # the row's cells, which puts the decision control under the row
            # box's centre point — a centre click stages a decision and selects
            # nothing (_invariants.click_row carries the measurement).
            click_row(page, row_file_testid(group_id, pick[0]))
            ctrl_click_row(page, row_file_testid(group_id, pick[1]))
            page.wait_for_timeout(200)
            bulk_label = page.get_by_test_id(MAIN_BULK_LABEL).inner_text()
            print(f"probe_status: s76 bulk label = {bulk_label!r}")
            assert "2" in bulk_label, (
                f"The bulk label does not carry the selection count: {bulk_label!r}"
            )

            with page.expect_response(
                lambda r: "/api/decision" in r.url and r.request.method == "PATCH",
                timeout=15_000,
            ):
                page.get_by_test_id(MAIN_BULK_VERB_DELETE).click()
            page.wait_for_timeout(400)

            after_bulk = _decisions(_get_manifest(base_url, db_path))
            print(f"probe_status: s76 decisions after bulk delete = {after_bulk}")
            for name in pick:
                assert after_bulk[name] == "delete", (
                    f"{name} is {after_bulk[name]!r} after the bulk Delete verb, "
                    "expected 'delete'."
                )
            assert after_bulk[_KEEPER] == "", (
                "The bulk verb reached a row that was never selected."
            )

            # ── 6. #906's figures and the CTA, now non-zero ─────────────────
            marked = page.get_by_test_id(MAIN_STATUS_DELETE_COUNT).inner_text()
            reclaim = page.get_by_test_id(MAIN_STATUS_RECLAIM).inner_text()
            cta = page.get_by_test_id(MAIN_DELETE_CTA).inner_text()
            print(
                f"probe_status: s76 marked={marked!r} reclaim={reclaim!r} cta={cta!r}"
            )
            assert "2" in marked, f"The delete counter reads {marked!r}, expected 2."
            assert any(unit in reclaim for unit in ("B", "KB", "MB", "GB")), (
                f"The reclaim figure carries no size: {reclaim!r}"
            )
            assert "Delete 2 files" in cta, (
                f"The CTA label does not carry the count: {cta!r}"
            )
            page.get_by_test_id(MAIN_DELETE_CTA).click()
            page.get_by_test_id(EXECUTE_DIALOG).wait_for(
                state="visible", timeout=10_000
            )
            print("probe_status: s76 delete CTA opened the Execute dialog")
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

            # ── 7. Undo puts both rows back ─────────────────────────────────
            toast = page.get_by_test_id(MAIN_TOAST)
            toast.wait_for(state="visible", timeout=5_000)
            toast_en = toast.inner_text()
            print(f"probe_status: s76 toast (en) = {toast_en!r}")
            assert "2" in toast_en and "Delete" in toast_en, (
                f"The toast does not say what the verb did: {toast_en!r}"
            )
            with page.expect_response(
                lambda r: "/api/lock" in r.url and r.request.method == "PATCH",
                timeout=15_000,
            ):
                page.get_by_test_id(MAIN_TOAST_UNDO).click()
            page.wait_for_timeout(500)
            undone = _decisions(_get_manifest(base_url, db_path))
            print(f"probe_status: s76 decisions after undo = {undone}")
            assert undone == _decisions(manifest_data), (
                f"Undo did not restore the pre-verb state: {undone}"
            )

            # ── 8. Density: the row AND the virtualiser's total size ────────
            comfortable = page.evaluate(_READ_TREE_METRICS, MAIN_RESULT_TREE)
            print(f"probe_status: s76 comfortable metrics = {comfortable}")
            assert comfortable["rowHeight"] == "72px", (
                f"A comfortable row computes to {comfortable['rowHeight']}, "
                "expected 72px."
            )

            density = page.evaluate(_READ_DENSITY_SELECTED, MAIN_DENSITY_TOGGLE)
            print(f"probe_status: s76 density control = {density}")
            selected = [d for d in density if d["pressed"] == "true"]
            assert len(selected) == 1, f"Expected one selected segment: {density}"
            assert selected[0]["testid"] == MAIN_DENSITY_COMFORTABLE
            assert selected[0]["backgroundColor"] == _ACCENT, (
                f"The selected density segment is {selected[0]['backgroundColor']}, "
                f"expected the accent {_ACCENT}."
            )

            page.get_by_test_id(MAIN_DENSITY_COMPACT).click()
            page.wait_for_timeout(400)
            compact = page.evaluate(_READ_TREE_METRICS, MAIN_RESULT_TREE)
            print(f"probe_status: s76 compact metrics = {compact}")
            assert compact["rowHeight"] == "52px", (
                f"A compact row computes to {compact['rowHeight']}, expected 52px."
            )
            # The half that is invisible under `overscan: 10`: if nothing
            # re-measures, the rows shrink and the spacer does not.
            assert compact["totalSize"] is not None and comfortable["totalSize"], (
                "Could not read the virtualiser's spacer height."
            )
            assert compact["totalSize"] < comfortable["totalSize"], (
                "The virtualiser's total size did not shrink with the rows "
                f"({comfortable['totalSize']} → {compact['totalSize']}). The "
                "size cache survived the density change, so every offset past "
                "the first screen is still 72px-based."
            )
            # And the consequence a user meets: scrolling to the last row lands
            # ON it rather than short of it.
            last = sorted(names)[-1]
            page.get_by_test_id(row_file_testid(group_id, last)).scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            assert page.get_by_test_id(row_file_testid(group_id, last)).is_visible(), (
                f"Scrolling to {last} at compact density did not bring it into "
                "view — the virtualiser's coordinates are stale."
            )
            page.get_by_test_id(MAIN_DENSITY_COMFORTABLE).click()
            page.wait_for_timeout(300)

            # ── 9. The same surfaces in zh-TW ───────────────────────────────
            page.get_by_test_id(MAIN_LANG_TOGGLE).click()
            page.wait_for_timeout(800)

            zh_cta = page.get_by_test_id(MAIN_DELETE_CTA).inner_text()
            zh_marked = page.get_by_test_id(MAIN_STATUS_DELETE_COUNT).inner_text()
            zh_placeholder = page.get_by_test_id(MAIN_FILTER_INPUT).get_attribute(
                "placeholder"
            )
            zh_density = page.get_by_test_id(MAIN_DENSITY_COMPACT).inner_text()
            print(
                "probe_status: s76 zh_TW = "
                f"cta={zh_cta!r} marked={zh_marked!r} "
                f"placeholder={zh_placeholder!r} density={zh_density!r}"
            )
            assert "刪除" in zh_cta, f"zh_TW CTA is still English: {zh_cta!r}"
            assert "待刪除" in zh_marked, (
                f"zh_TW delete counter is still English: {zh_marked!r}"
            )
            assert zh_placeholder == "篩選照片…", (
                f"zh_TW filter placeholder is {zh_placeholder!r}"
            )
            assert zh_density == "緊湊", f"zh_TW density segment is {zh_density!r}"
            for leaked in ("Delete", "marked to delete", "Filter photos"):
                assert leaked not in (
                    zh_cta + zh_marked + (zh_placeholder or "") + zh_density
                ), f"English {leaked!r} leaked into the zh_TW toolbar/status bar."

            # ── 10. Narrow viewport: the manifest pair sheds, the CTA stays ──
            # Below ~1100px the strip's compression runs out, and the control
            # at the scrolled-off end would be the Delete CTA — the only
            # destructive thing on the screen. So the manifest path input and
            # its Open button shed instead: they are the one group with an
            # exact duplicate (File → Open Manifest…, which opens the picker),
            # so dropping them costs a redundant path, not a capability.
            page.get_by_test_id(MAIN_LANG_TOGGLE).click()
            page.wait_for_timeout(600)
            page.set_viewport_size({"width": 1000, "height": 800})
            page.wait_for_timeout(500)
            narrow = page.evaluate(_READ_TOOLBAR, MAIN_TOOLBAR)
            manifest_present = page.get_by_test_id(MAIN_MANIFEST_INPUT).count()
            open_present = page.get_by_test_id(MAIN_MANIFEST_OPEN).count()
            cta_right = page.evaluate(
                """(testid) => {
  const el = document.querySelector(`[data-testid="${testid}"]`);
  if (!el) return null;
  return { right: el.getBoundingClientRect().right, inner: window.innerWidth };
}""",
                MAIN_DELETE_CTA,
            )
            print(
                f"probe_status: s76 at 1000px toolbar={narrow['overflowPx']}px over, "
                f"manifest_input={manifest_present} open={open_present} cta={cta_right} "
                f"font={narrow['font']}"
            )
            assert manifest_present == 0 and open_present == 0, (
                "The manifest path input / Open button did not shed at 1000px "
                f"(input={manifest_present}, open={open_present}), so the strip "
                "has to scroll to reach the Delete CTA."
            )
            # 1000px is BELOW the 1280 design width, so the contract here is
            # weaker than the zero-overflow one asserted above, and
            # deliberately so. Shedding the manifest pair is as far as this can
            # go: the only other controls with a menu-bar duplicate are the
            # language toggle and Set Action…, and both are pinned — the owner
            # kept the language and Settings buttons in the toolbar, and
            # ACTION_MAIN_BUTTON is on the slice's must-not-change list. So
            # below ~1050px on a wide (non-Segoe) stack the strip genuinely
            # scrolls, which is what `overflow-x: auto` is the net for.
            #
            # What must still hold at this width is the thing the shed exists
            # to protect: the one destructive control is REACHABLE — scrollable
            # into view, fully inside the viewport once there, and hit-testing
            # to itself rather than to something covering it. A CTA you cannot
            # reach and a CTA you must scroll to are different failures, and
            # only the first is a bug.
            page.get_by_test_id(MAIN_DELETE_CTA).scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            cta_reachable = page.evaluate(
                """(testid) => {
  const el = document.querySelector(`[data-testid="${testid}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  return {
    left: Math.round(r.left),
    right: Math.round(r.right),
    inner: window.innerWidth,
    hits: hit === el || el.contains(hit),
  };
}""",
                MAIN_DELETE_CTA,
            )
            print(f"probe_status: s76 CTA after scroll at 1000px = {cta_reachable}")
            assert cta_reachable is not None, "The Delete CTA left the DOM at 1000px."
            # 1px of slack, not taste: scrolling a flex row to its end pins the
            # last child's right edge to the viewport edge (the container's own
            # right padding is not rendered past the content at scroll end), so
            # the measurement lands exactly ON `innerWidth` and sub-pixel
            # rounding can carry it one past. Reproduced under a forced wide
            # fallback: left 841, right 1000, inner 1000.
            assert (
                cta_reachable["left"] >= 0
                and cta_reachable["right"] <= cta_reachable["inner"] + 1
            ), (
                "The Delete CTA is not fully inside the viewport at 1000px even "
                f"after scrolling the strip to it ({cta_reachable}) — the one "
                "destructive control is unreachable, not merely off-origin."
            )
            assert cta_reachable["hits"] is True, (
                "The Delete CTA's own centre does not resolve to the CTA at "
                f"1000px ({cta_reachable}) — something is covering it."
            )
            page.set_viewport_size({"width": 1280, "height": 800})
            page.wait_for_timeout(400)
            assert page.get_by_test_id(MAIN_MANIFEST_INPUT).count() == 1, (
                "The manifest input did not come BACK at 1280px — a shed that "
                "never restores is a removal."
            )
    finally:
        # ALWAYS put the server back to English — `ui.locale` lives in the
        # server's settings file, not in the browser context PWContext discards.
        _patch_locale(base_url, "en")
        shutil.rmtree(tmpdir, ignore_errors=True)
