"""Web scenario s77 — the preview pane (#878 layout slice PV; closes #905).

WEB-ONLY.  Layout slice PV gives the pane the REPLY's P1-P7 shape — a 42px
title strip, a 4:3 image frame on the warm-tinted near-black `image-surround`,
the filename as a heading, a five-row metadata table, the decision block #905
asked for, and the keep-worthiness bar.

Five things live here rather than in vitest, each because jsdom cannot decide
it:

  1. **Computed geometry.**  The frame's 4:3 ratio, its 12px radius and the
     `rgb(36, 33, 30)` surround are Tailwind v4 `@theme` utilities that only
     become real after the CSS pipeline runs — a jsdom assertion on
     `bg-image-surround` stays green after the token behind it is deleted.  The
     ASPECT is the one that matters most: it is what removes the #535
     oscillation's source rather than damping it, and `aspect-ratio` is a
     property jsdom resolves to the empty string.
  2. **The pane's own width.**  320px is the slice's default and it is read
     from the real layout, not from the constant — the pane is a flex sibling
     of the tree and the handle, so a wrong `flex-shrink` somewhere else makes
     the constant right and the pane narrow.
  3. **#905 end-to-end.**  A decision set in the PANE has to reach the ROW's
     chip, and that is the whole acceptance of the issue: two surfaces, one
     store action.  A vitest can prove the pane dispatches; only a live run
     proves the row re-renders from the same state.
  4. **Lock gating on a real lock.**  Locking through the row's padlock is a
     server round trip; the pane's buttons must come back disabled from the
     state that write produced, not from a prop a test set by hand.
  5. **zh-TW.**  Every new string is a `web.*` key in both catalogs, and the
     Shot date row is read under BOTH UI locales in one run — `formatDate`'s
     `locale` argument is optional, so a call site that drops it follows the
     BROWSER and prints 「2024年2月1日」 in an English UI with nothing thrown
     and nothing red on an en-* dev machine.

Fixture: qa/sandbox/near-duplicates/ (5 JPEGs, one group) — the same fixture
s72/s73/s74/s75/s76 use, so the keeper split is the one s57 pins.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import click_row, run_scan
from qa.web.testid_constants import (
    MAIN_LANG_TOGGLE,
    PREVIEW_DECISION,
    PREVIEW_INFO,
    PREVIEW_KEEP_WORTHINESS,
    PREVIEW_PANE,
    PREVIEW_SINGLE_IMAGE,
    PREVIEW_TITLE,
    row_decision_testid,
    row_file_testid,
    row_lock_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

# --color-image-surround #24211e — the ONE new hex this slice adds.
_SURROUND = "rgb(36, 33, 30)"

_KEEPER = "neardup_00_q95.jpg"

# An en-GB short date: "1 Feb 2024".  Asserted as a SHAPE plus the absence of
# 「年」 rather than as a literal, because the day/month come from the fixture's
# EXIF and a fixture regeneration must not turn this scenario red for a reason
# that has nothing to do with the pane.
_EN_DATE = re.compile(r"^\d{1,2} [A-Za-z]{3,} \d{4}$")


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
    zh_TW UI.  s75 paid for this; same restore contract as s22 and s76.
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


# The pane's own box and its title strip, read together — the width is a
# property of the LAYOUT (a flex sibling of the tree and the 4px handle), not
# of the constant, so it is measured rather than trusted.
_READ_PANE = """(ids) => {
  const pane = document.querySelector(`[data-testid="${ids.pane}"]`);
  const title = document.querySelector(`[data-testid="${ids.title}"]`);
  if (!pane || !title) return null;
  const tcs = getComputedStyle(title);
  return {
    width: Math.round(pane.getBoundingClientRect().width),
    titleHeight: tcs.height,
    titleTransform: tcs.textTransform,
    titleFontSize: tcs.fontSize,
    titleText: title.textContent.trim(),
  };
}"""

# The frame is the image's PARENT. Its aspect is read as a real ratio from the
# live box rather than from the `aspect-ratio` string, because the string can
# be present while a `min-height` or a stretching flex parent overrides it —
# and an overridden aspect is exactly the #535 shape coming back.
_READ_FRAME = """(imgId) => {
  const img = document.querySelector(`[data-testid="${imgId}"]`);
  if (!img) return null;
  const frame = img.parentElement;
  const r = frame.getBoundingClientRect();
  const cs = getComputedStyle(frame);
  const badge = frame.querySelector('[data-sim-state]');
  return {
    width: r.width,
    height: r.height,
    ratio: r.height > 0 ? r.width / r.height : null,
    backgroundColor: cs.backgroundColor,
    borderRadius: cs.borderTopLeftRadius,
    overflow: cs.overflowX,
    objectFit: getComputedStyle(img).objectFit,
    badgeState: badge ? badge.dataset.simState : null,
    badgeText: badge ? badge.textContent.trim() : null,
    // The badge must sit OVER the surround, not above the frame in flow.
    badgeInside: badge
      ? (() => {
          const b = badge.getBoundingClientRect();
          return b.top >= r.top - 1 && b.left >= r.left - 1 && b.bottom <= r.bottom + 1;
        })()
      : null,
  };
}"""

_READ_META = """(infoId) => {
  const info = document.querySelector(`[data-testid="${infoId}"]`);
  if (!info) return null;
  const infoRight = info.getBoundingClientRect().right;
  const rows = [];
  for (const row of info.children) {
    const label = row.children[0];
    const value = row.querySelector('[data-meta-value]');
    const vr = value ? value.getBoundingClientRect() : null;
    const cs = value ? getComputedStyle(value) : null;
    rows.push({
      label: label ? label.textContent.trim() : null,
      value: value ? value.textContent.trim() : null,
      valueFont: cs ? cs.fontFamily : null,
      valueDir: value ? value.getAttribute('dir') : null,
      valueWhiteSpace: cs ? cs.whiteSpace : null,
      // ONE line: the rendered height against the element's own line box.
      valueHeight: value ? value.clientHeight : null,
      valueLineHeight: cs ? parseFloat(cs.lineHeight) : null,
      // Right-aligned is a GEOMETRIC property here, not `text-align`: the
      // value box is pushed right by `ml-auto` and its text then elides from
      // the LEFT, so the edge is what "right-aligned" means on screen.
      valueRightGap: vr ? infoRight - vr.right : null,
    });
  }
  return rows;
}"""

_READ_DECISIONS = """(blockId) => {
  const block = document.querySelector(`[data-testid="${blockId}"]`);
  if (!block) return null;
  const out = [];
  for (const b of block.querySelectorAll('button')) {
    out.push({
      testid: b.dataset.testid,
      text: b.textContent.trim(),
      pressed: b.getAttribute('aria-pressed'),
      disabled: b.disabled,
      height: getComputedStyle(b).height,
      backgroundColor: getComputedStyle(b).backgroundColor,
      // Stacked, not a grid: every button spans the block's full width.
      width: Math.round(b.getBoundingClientRect().width),
    });
  }
  return { blockWidth: Math.round(block.getBoundingClientRect().width), buttons: out };
}"""

_READ_KEEP_BAR = """(paneId) => {
  const pane = document.querySelector(`[data-testid="${paneId}"]`);
  if (!pane) return null;
  const track = pane.querySelector('[data-keep-track]');
  const fill = pane.querySelector('[data-keep-fill]');
  if (!track || !fill) return null;
  return {
    trackHeight: getComputedStyle(track).height,
    trackWidth: track.getBoundingClientRect().width,
    fillWidth: fill.getBoundingClientRect().width,
    fillColor: getComputedStyle(fill).backgroundColor,
    // A surviving gradient utility paints nothing once its second stop's token
    // is gone — the same trap slice R rewrote s74's assertion 7 for.
    fillImage: getComputedStyle(fill).backgroundImage,
  };
}"""


def _shot_date(page) -> "str | None":
    """The metadata table's LAST row value — Name · Folder · Size · Resolution ·
    Shot date, so the fifth is the date."""
    rows = page.evaluate(_READ_META, PREVIEW_INFO)
    if not rows:
        return None
    return rows[-1]["value"]


def run(*, base_url: str) -> None:
    """Drive the preview pane's layout, its decision block and its locales."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s77_")
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

            # A non-keeper: the keeper is locked by nothing here (plain scan,
            # auto-select off), but picking a duplicate keeps this scenario off
            # the row s57 calls platform-dependent.
            target = next(n for n in names if n != _KEEPER)
            click_row(page, row_file_testid(group_id, target))
            page.wait_for_timeout(400)

            # ── 1. The pane's width and its title strip (P1, P2) ────────────
            pane = page.evaluate(
                _READ_PANE, {"pane": PREVIEW_PANE, "title": PREVIEW_TITLE}
            )
            print(f"probe_status: s77 pane = {pane}")
            assert pane is not None, "The preview pane or its title did not render."
            assert pane["width"] == 320, (
                f"The preview pane is {pane['width']}px wide, expected the "
                "slice's 320px default (REPLY P1). The width is a property of "
                "the flex row, so a constant of 320 and a pane of 288 are both "
                "possible at once."
            )
            assert pane["titleHeight"] == "42px", (
                f"The pane title strip is {pane['titleHeight']}, expected 42px."
            )
            assert pane["titleTransform"] == "uppercase", (
                f"The pane title's text-transform is {pane['titleTransform']!r}."
            )
            assert pane["titleFontSize"] == "11px", (
                f"The pane title is {pane['titleFontSize']}, expected 11px."
            )
            assert "PREVIEW" in pane["titleText"].upper(), (
                f"The pane title reads {pane['titleText']!r}."
            )

            # ── 2. The image frame (P3) ─────────────────────────────────────
            frame = page.evaluate(_READ_FRAME, PREVIEW_SINGLE_IMAGE)
            print(f"probe_status: s77 image frame = {frame}")
            assert frame is not None, "No image rendered in the pane."
            assert frame["ratio"] is not None and abs(frame["ratio"] - 4 / 3) < 0.02, (
                f"The image frame is {frame['width']}x{frame['height']} "
                f"(ratio {frame['ratio']}), expected 4/3. A fixed aspect is not "
                "decoration here: it is what stops the image's OWN aspect from "
                "feeding back into the pane's height, which is the source of "
                "the #535 portrait oscillation the always-on scrollbar only damps."
            )
            assert frame["backgroundColor"] == _SURROUND, (
                f"The image surround is {frame['backgroundColor']}, expected "
                f"{_SURROUND} (#24211e) — «warm-tinted near-black, not #171717»."
            )
            assert frame["borderRadius"] == "12px", (
                f"The frame radius is {frame['borderRadius']}, expected 12px."
            )
            assert frame["overflow"] == "hidden", (
                f"The frame's overflow is {frame['overflow']!r}; a letterboxed "
                "image must be clipped by the frame, not spill past its radius."
            )
            assert frame["objectFit"] == "contain", (
                f"The image's object-fit is {frame['objectFit']!r}, expected "
                "'contain' — 'cover' crops the very difference the user is judging."
            )
            assert frame["badgeState"], (
                "No similarity badge over the image. The pane is where a single "
                "file is judged, and the badge is how it says WHICH duplicate "
                "this is."
            )
            assert frame["badgeInside"] is True, (
                f"The similarity badge is not inside the frame's box: {frame}"
            )

            # ── 3. Five metadata rows, right-aligned mono values (P7) ───────
            meta = page.evaluate(_READ_META, PREVIEW_INFO)
            print(f"probe_status: s77 metadata rows = {meta}")
            assert meta is not None and len(meta) == 5, (
                "The metadata table must be exactly the contract's five fields "
                f"(Name · Folder · Size · Resolution · Shot date); got {meta}."
            )
            assert [r["label"] for r in meta] == [
                "Name",
                "Folder",
                "Size",
                "Resolution",
                "Shot date",
            ], f"Metadata labels are {[r['label'] for r in meta]}."
            # Right-aligned as a GEOMETRIC property — every value box's right
            # edge on the same line, 13px inside the frame (the row padding).
            assert all(
                r["valueRightGap"] is not None and abs(r["valueRightGap"] - 13) <= 1
                for r in meta
            ), (
                "Metadata values are not on a common right edge — «right "
                "alignment is what makes a two-column metadata list read as a "
                "table rather than as ragged prose, and it puts the values on a "
                "common edge for comparison when the user clicks between two "
                f"copies». Got {[r['valueRightGap'] for r in meta]}."
            )
            assert "Cascadia Code" in (meta[0]["valueFont"] or ""), (
                f"Metadata values are not mono: {meta[0]['valueFont']!r}"
            )
            assert meta[0]["value"] == target, (
                f"The Name row reads {meta[0]['value']!r}, expected {target!r}."
            )

            # Every value is ONE line — the Folder row is the one that proves
            # it, because the fixture's folder is a real absolute path and the
            # first draft wrapped it to four lines in this 320px pane.
            folder = meta[1]
            assert "near-duplicates" in (folder["value"] or ""), (
                f"Expected the Folder row second; got {folder}."
            )
            assert folder["valueDir"] == "rtl", (
                "The folder value is not `dir=rtl`, so it elides at its END — "
                "which hides the one segment that tells two copies apart "
                f"(REPLY L2, FileRow.tsx:257-264). Got {folder['valueDir']!r}."
            )
            for row in meta:
                assert row["valueHeight"] <= (row["valueLineHeight"] or 0) + 1, (
                    "A metadata value wrapped to more than one line: "
                    f"{row['label']} is {row['valueHeight']}px tall against a "
                    f"{row['valueLineHeight']}px line. A wrapped path pushes the "
                    "decision block — the reason #905 exists — below the fold."
                )

            # …and the consequence, read directly: the decision block is ON
            # SCREEN at the review viewport. A height assertion on one cell is
            # a proxy; this is the thing the user meets.
            block_top = page.evaluate(
                """(blockId) => {
  const el = document.querySelector(`[data-testid="${blockId}"]`);
  if (!el) return null;
  return { top: el.getBoundingClientRect().top, inner: window.innerHeight };
}""",
                PREVIEW_DECISION,
            )
            print(f"probe_status: s77 decision block position = {block_top}")
            assert block_top is not None and block_top["top"] < block_top["inner"], (
                "The decision block starts below the fold at 1280x800 "
                f"({block_top}) — the pane scrolls, so it is reachable, but the "
                "control #905 exists for is not visible beside the photo it "
                "judges, which is the whole point."
            )

            # The heading says the same basename, at size, above the table.
            heading = page.evaluate(
                """(paneId) => {
  const h = document.querySelector(`[data-testid="${paneId}"] h2`);
  if (!h) return null;
  return {
    text: h.textContent.trim(),
    title: h.getAttribute('title'),
    fontSize: getComputedStyle(h).fontSize,
  };
}""",
                PREVIEW_PANE,
            )
            print(f"probe_status: s77 heading = {heading}")
            assert heading is not None and heading["text"] == target, (
                f"The pane heading reads {heading}, expected {target!r}."
            )
            assert heading["title"] == target, (
                "The heading truncates, so the full name must be in `title`: "
                f"{heading}"
            )
            assert heading["fontSize"] == "15px", (
                f"The heading is {heading['fontSize']}, expected 15px."
            )

            # ── 4. Shot date follows the APP's locale (en half) ─────────────
            date_en = _shot_date(page)
            print(f"probe_status: s77 shot date (en UI) = {date_en!r}")
            assert date_en is not None and "年" not in date_en, (
                f"The pane's Shot date reads {date_en!r} in the ENGLISH UI — "
                "formatDate is following the browser's locale, not the app's."
            )
            assert _EN_DATE.match(date_en), (
                f"The pane's Shot date is {date_en!r}, expected the REPLY's "
                "`1 Feb 2024` shape (day, short month, year — no time)."
            )

            # ── 5. #905: a decision set in the PANE reaches the ROW ─────────
            before = page.evaluate(_READ_DECISIONS, PREVIEW_DECISION)
            print(f"probe_status: s77 decision block (before) = {before}")
            assert before is not None and len(before["buttons"]) == 3, (
                f"The pane decision block must be three buttons: {before}"
            )
            assert all(
                b["height"] == "36px" for b in before["buttons"]
            ), f"Pane decision buttons are not 36px: {before['buttons']}"
            # Stacked full-width, «NOT a 2x2 grid»: three buttons each as wide
            # as the block is how you tell a column from a grid without reading
            # the CSS.
            assert all(
                abs(b["width"] - before["blockWidth"]) <= 1 for b in before["buttons"]
            ), (
                "The pane decision buttons are not full-width — a 2x2 grid "
                f"«leaves a hole» and is what the REPLY ruled out: {before}"
            )
            # `''` IS keep under the #584 model, so an untouched row starts with
            # Keep pressed — the same rule the row control follows.
            assert [b["pressed"] for b in before["buttons"]] == [
                "true",
                "false",
                "false",
            ], f"Pane decision pressed-states start at {before['buttons']}"

            with page.expect_response(
                lambda r: "/api/decision" in r.url and r.request.method == "PATCH",
                timeout=15_000,
            ):
                page.get_by_test_id(f"{PREVIEW_DECISION}-delete").click()
            page.wait_for_timeout(400)

            after = page.evaluate(_READ_DECISIONS, PREVIEW_DECISION)
            print(f"probe_status: s77 decision block (after) = {after}")
            assert [b["pressed"] for b in after["buttons"]] == [
                "false",
                "true",
                "false",
            ], f"The pane's own button did not become selected: {after['buttons']}"

            # …and the ROW's chip is the acceptance of #905: two surfaces, one
            # store action, so the row must re-render from the same state.
            row_chip = page.evaluate(
                """(testid) => {
  const track = document.querySelector(`[data-testid="${testid}"]`);
  if (!track) return null;
  const out = [];
  for (const b of track.querySelectorAll('button')) {
    out.push({ testid: b.dataset.testid, pressed: b.getAttribute('aria-pressed') });
  }
  return out;
}""",
                row_decision_testid(group_id, target),
            )
            print(f"probe_status: s77 row chip after a PANE decision = {row_chip}")
            assert row_chip is not None, "The row's decision control disappeared."
            pressed = [c["testid"] for c in row_chip if c["pressed"] == "true"]
            assert pressed == [f"{row_decision_testid(group_id, target)}-delete"], (
                "A decision set in the PANE did not reach the ROW's chip — this "
                f"is #905's acceptance. Row reports: {row_chip}"
            )
            after_manifest = _decisions(_get_manifest(base_url, db_path))
            print(f"probe_status: s77 manifest after pane decision = {after_manifest}")
            assert after_manifest[target] == "delete", (
                f"{target} is {after_manifest[target]!r} in the manifest after a "
                "pane decision — the pane wrote to the client only."
            )

            # ── 6. Keep-worthiness bar (P6) ─────────────────────────────────
            bar = page.evaluate(_READ_KEEP_BAR, PREVIEW_PANE)
            print(f"probe_status: s77 keep-worthiness bar = {bar}")
            assert bar is not None, "The keep-worthiness bar did not render."
            assert bar["trackHeight"] == "7px", (
                f"The keep-worthiness track is {bar['trackHeight']}, expected 7px."
            )
            assert 0 < bar["fillWidth"] <= bar["trackWidth"] + 1, (
                f"The keep-worthiness fill is {bar['fillWidth']}px in a "
                f"{bar['trackWidth']}px track — a NaN width is valid-looking "
                "markup CSS ignores without complaint."
            )
            assert bar["fillImage"] == "none", (
                f"The fill carries a background-image ({bar['fillImage']!r}); "
                "the REPLY retired the gradient and its second stop's token is "
                "gone, so a surviving gradient utility paints nothing."
            )
            value = page.get_by_test_id(PREVIEW_KEEP_WORTHINESS).inner_text()
            print(f"probe_status: s77 keep-worthiness value = {value!r}")
            assert re.match(r"^\d+\.\d{2}$", value), (
                f"The keep-worthiness value reads {value!r}, expected "
                "formatScore's two decimals."
            )

            # ── 7. A real lock disables the pane's decision buttons ─────────
            page.get_by_test_id(row_lock_testid(group_id, target)).click()
            page.wait_for_timeout(600)
            locked = page.evaluate(_READ_DECISIONS, PREVIEW_DECISION)
            print(f"probe_status: s77 decision block while locked = {locked}")
            assert all(b["disabled"] for b in locked["buttons"]), (
                "The pane's decision buttons are still enabled on a LOCKED row. "
                "The write is refused server-side, so an enabled button is the "
                f"one surface that invites a refused action: {locked['buttons']}"
            )
            page.get_by_test_id(row_lock_testid(group_id, target)).click()
            page.wait_for_timeout(600)
            unlocked = page.evaluate(_READ_DECISIONS, PREVIEW_DECISION)
            print(f"probe_status: s77 decision block after unlock = {unlocked}")
            # The false-positive half: buttons that are disabled for everyone
            # look exactly like a gate that works.
            assert not any(b["disabled"] for b in unlocked["buttons"]), (
                "The pane's decision buttons stayed disabled after the row was "
                f"unlocked — the gate refuses the ordinary case too: {unlocked}"
            )

            # ── 8. The same pane in zh-TW ───────────────────────────────────
            page.get_by_test_id(MAIN_LANG_TOGGLE).click()
            page.wait_for_timeout(800)

            zh_pane = page.evaluate(
                _READ_PANE, {"pane": PREVIEW_PANE, "title": PREVIEW_TITLE}
            )
            zh_meta = page.evaluate(_READ_META, PREVIEW_INFO)
            zh_decisions = page.evaluate(_READ_DECISIONS, PREVIEW_DECISION)
            zh_date = _shot_date(page)
            print(
                "probe_status: s77 zh_TW = "
                f"title={zh_pane['titleText']!r} "
                f"labels={[r['label'] for r in zh_meta]} "
                f"decisions={[b['text'] for b in zh_decisions['buttons']]} "
                f"date={zh_date!r}"
            )
            assert "預覽" in zh_pane["titleText"], (
                f"The zh_TW pane title is still English: {zh_pane['titleText']!r}"
            )
            assert [r["label"] for r in zh_meta] == [
                "名稱",
                "資料夾",
                "大小",
                "解析度",
                "拍攝日期",
            ], f"zh_TW metadata labels are {[r['label'] for r in zh_meta]}"
            assert zh_date is not None and "年" in zh_date, (
                f"The pane's Shot date reads {zh_date!r} after switching to "
                "zh_TW — the language switch must reach the dates too, or the "
                "English assertion above passes on a machine whose browser is "
                "simply English."
            )
            zh_words = [b["text"] for b in zh_decisions["buttons"]]
            assert zh_words == ["保留", "刪除", "略過"], (
                f"zh_TW pane decision labels are {zh_words} — they must be the "
                "row control's words, from the one DECISION_VOCAB table."
            )
    finally:
        # ALWAYS put the server back to English — `ui.locale` lives in the
        # server's settings file, not in the browser context PWContext discards.
        _patch_locale(base_url, "en")
        shutil.rmtree(tmpdir, ignore_errors=True)
