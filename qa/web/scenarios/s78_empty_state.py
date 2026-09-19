"""Web scenario s78 — the empty state, at size, in both locales (#878 slice E).

WEB-ONLY. The desktop client was retired by #646, and its empty state was a
two-line QLabel with two ordinary buttons; there is no driver to port.

Why this is layer 3 and not a vitest file. `App.test.tsx` covers the
COMPOSITION — a heading, the body, two CTAs, the handlers each fires, and the
two things that are deliberately absent. Every remaining claim in layout slice
E's empty state is a number that exists only after the Tailwind build:

  1. **The primary CTA is primary.** `h-12`, `rounded-[12px]` and `bg-warm` are
     strings in jsdom that spell correctly with the token deleted — the whole
     point of E5 is that ONE of the two buttons is filled and at size (h48),
     and the screen the audit describes had two identical 32px neutral buttons.
     Read as computed height, radius and background colour.

  2. **Exactly one of them is filled.** Asserted as a COUNT over the empty
     state's buttons, the same shape s76 uses on the toolbar: a second accent
     button is not a class regression on any one element, it is a property of
     the screen.

  3. **The frame and the type.** E1's 520px column and E3's 25px/700 heading —
     a heading that renders at the body's 14px is the defect the audit filed
     («none — one 14px muted sentence»), and it is invisible to a class check.

  4. **Two owner decisions are visible as ABSENCE.** Q10's safety pill is
     dropped everywhere ("Nothing is deleted until you review and confirm"／
     「不會刪除」) and E7's recent-sources list is not built. Absence is
     indistinguishable from "not shipped yet" unless something asserts it, and
     the prototype draws both.

  5. **The CTA still opens what it opened.** The composition changed; the two
     handlers did not. Clicking the primary opens the real Scan dialog.

  6. **zh_TW.** The heading and body are new keys, and a new key is exactly
     what gets added to en.yml alone — which renders English inside an
     otherwise Chinese session, the copy-audit R8 defect. `ui.locale` is a
     SERVER-side setting, so it is restored to `en` in a `finally` (the s75
     lesson: leaking it times out every English-reading scenario after this one).

No scan, no manifest, no fixture: the empty state IS the app with nothing
loaded, which is what makes this the cheapest scenario in the batch.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from qa.web._pw import PWContext
from qa.web._invariants import dismiss_modal_overlays
from qa.web.testid_constants import (
    MAIN_EMPTY_OPEN,
    MAIN_EMPTY_SCAN,
    MAIN_EMPTY_STATE,
    MAIN_LANG_TOGGLE,
    SCAN_DIALOG,
)

_VIEWPORT = {"width": 1280, "height": 800}

# --color-warm #a85a2c, as the browser reports it. The accent moved from
# #bd6b39 in layout slice T (white label 3.88:1 → 5.04:1), and this CTA is one
# of the two surfaces that fix exists for.
_ACCENT = "rgb(168, 90, 44)"
_WHITE = "rgb(255, 255, 255)"

# Q10 (dropped) and E7 (dropped by design). Matched as substrings against the
# empty state's whole text in both locales.
_FORBIDDEN_EN = ["Nothing is deleted", "Recent sources"]
_FORBIDDEN_ZH = ["不會刪除", "最近的來源"]


def _patch_locale(base_url: str, locale: str) -> None:
    """PATCH /api/settings to set ui.locale — the restore step (s75 lesson)."""
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


# The empty state read as one object: the frame's own width cap, the heading's
# type, and every button in it with the three properties that decide whether it
# is the primary or the secondary.
_READ_EMPTY = """(ids) => {
  const root = document.querySelector(`[data-testid="${ids.state}"]`);
  if (!root) return null;
  const heading = root.querySelector('h2');
  const hcs = heading ? getComputedStyle(heading) : null;
  // The column that carries the 520px cap — the heading's own parent.
  const column = heading ? heading.parentElement : null;
  const btn = (testid) => {
    const el = root.querySelector(`[data-testid="${testid}"]`);
    if (!el) return null;
    const cs = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    return {
      height: Math.round(box.height),
      backgroundColor: cs.backgroundColor,
      color: cs.color,
      borderRadius: cs.borderTopLeftRadius,
      fontSize: cs.fontSize,
      fontWeight: cs.fontWeight,
      text: (el.textContent || '').trim(),
    };
  };
  const filled = [];
  for (const el of root.querySelectorAll('button')) {
    if (getComputedStyle(el).backgroundColor === 'rgb(168, 90, 44)') {
      filled.push(el.dataset.testid || '(unnamed)');
    }
  }
  return {
    text: (root.textContent || '').trim(),
    headingText: heading ? heading.textContent.trim() : null,
    headingFontSize: hcs ? hcs.fontSize : null,
    headingFontWeight: hcs ? hcs.fontWeight : null,
    columnWidth: column ? Math.round(column.getBoundingClientRect().width) : null,
    buttonCount: root.querySelectorAll('button').length,
    filled,
    scan: btn(ids.scan),
    open: btn(ids.open),
  };
}"""


def run(*, base_url: str) -> None:
    ids = {
        "state": MAIN_EMPTY_STATE,
        "scan": MAIN_EMPTY_SCAN,
        "open": MAIN_EMPTY_OPEN,
    }
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.set_viewport_size(_VIEWPORT)
            page.goto("/")
            page.get_by_test_id(MAIN_EMPTY_STATE).wait_for(
                state="visible", timeout=10_000
            )

            # ── 1. The composition, in English ───────────────────────────────
            empty = page.evaluate(_READ_EMPTY, ids)
            print(f"probe_status: s78 empty state (en) = {empty}")
            assert empty is not None, "The empty state did not render."
            assert empty["headingText"], (
                "The empty state has no heading element. E3 is MUST-MATCH: the "
                "screen was one 14px muted sentence, and a heading is what "
                "turns 'which state the app is in' into 'what this app does'."
            )
            assert "duplicate" in empty["headingText"].lower(), (
                f"The empty-state heading reads {empty['headingText']!r}."
            )
            assert empty["headingFontSize"] == "25px", (
                f"The heading is {empty['headingFontSize']}, expected 25px "
                "(audit E3). A heading rendered at the body's 14px is exactly "
                "the defect this slice was filed against."
            )
            assert empty["headingFontWeight"] in {"700", "bold"}, (
                f"The heading's weight is {empty['headingFontWeight']!r}, "
                "expected 700."
            )
            assert empty["columnWidth"] is not None and empty["columnWidth"] <= 520, (
                f"The empty state's column is {empty['columnWidth']}px wide at "
                "1280×800, expected E1's 520px cap. Uncapped, the body copy "
                "runs the full width of the tree pane and stops being readable."
            )

            # ── 2. The primary CTA is primary (E5) ───────────────────────────
            scan, open_btn = empty["scan"], empty["open"]
            assert scan is not None and open_btn is not None, (
                f"One of the two CTAs is missing: scan={scan}, open={open_btn}"
            )
            assert scan["height"] == 48, (
                f"The primary CTA is {scan['height']}px tall, expected 48 "
                "(E5 MUST-MATCH; it shipped at 32)."
            )
            assert scan["backgroundColor"] == _ACCENT, (
                f"The primary CTA's background is {scan['backgroundColor']}, "
                f"expected the accent {_ACCENT} (#a85a2c). Without a filled "
                "primary the first screen offers two identical neutral buttons "
                "and no answer to «where do I start»."
            )
            assert scan["color"] == _WHITE, (
                f"The primary CTA's label is {scan['color']}, expected white."
            )
            assert scan["borderRadius"] == "12px", (
                f"The primary CTA's radius is {scan['borderRadius']}, "
                "expected 12px."
            )
            assert scan["fontSize"] == "15px", (
                f"The primary CTA is {scan['fontSize']}, expected 15px."
            )
            assert open_btn["height"] == 48, (
                f"The secondary CTA is {open_btn['height']}px tall, expected 48 "
                "— the two buttons sit on one row and must share a height."
            )
            assert open_btn["borderRadius"] == "12px", (
                f"The secondary CTA's radius is {open_btn['borderRadius']}."
            )
            assert open_btn["backgroundColor"] != _ACCENT, (
                "BOTH CTAs are filled with the accent. One primary is the whole "
                "point of the pair."
            )
            assert empty["filled"] == [MAIN_EMPTY_SCAN], (
                f"The empty state has {empty['filled']} filled with the accent, "
                f"expected exactly [{MAIN_EMPTY_SCAN!r}]. This is a property of "
                "the SCREEN, not of either button."
            )

            # ── 3. Two owner decisions, visible only as absence ──────────────
            for phrase in _FORBIDDEN_EN:
                assert phrase not in empty["text"], (
                    f"The empty state contains {phrase!r}. Both are dropped by "
                    "owner decision — Q10's safety line is drawn nowhere in the "
                    "app, and E7's recent sources is not built (it needs a "
                    "scan-history store whose stale paths would fail on the "
                    "app's FIRST screen)."
                )

            # ── 4. The primary still opens the Scan dialog ───────────────────
            page.get_by_test_id(MAIN_EMPTY_SCAN).click()
            page.get_by_test_id(SCAN_DIALOG).wait_for(state="visible", timeout=5_000)
            print("probe_status: s78 primary CTA opened the scan dialog")
            page.keyboard.press("Escape")
            dismiss_modal_overlays(page)
            page.get_by_test_id(MAIN_EMPTY_STATE).wait_for(
                state="visible", timeout=5_000
            )

            # ── 5. The same screen in zh-TW ─────────────────────────────────
            page.get_by_test_id(MAIN_LANG_TOGGLE).click()
            page.wait_for_timeout(800)
            zh = page.evaluate(_READ_EMPTY, ids)
            print(f"probe_status: s78 empty state (zh_TW) = {zh}")
            assert zh is not None, "The empty state vanished after the switch."
            assert zh["headingText"] and "照片" in zh["headingText"], (
                f"The zh_TW heading reads {zh['headingText']!r} — a key added "
                "to en.yml alone renders English inside a Chinese session, "
                "which is the copy-audit R8 defect."
            )
            assert "duplicate" not in zh["text"].lower(), (
                f"English leaked into the zh_TW empty state: {zh['text']!r}"
            )
            for phrase in _FORBIDDEN_ZH:
                assert phrase not in zh["text"], (
                    f"The zh_TW empty state contains {phrase!r} — the dropped "
                    "safety line and recent-sources list must be absent in "
                    "BOTH locales, not only the one the author reads."
                )
            assert zh["scan"] is not None and zh["scan"]["height"] == 48, (
                f"The zh_TW primary CTA is {zh['scan']}, expected the same 48px "
                "box — a longer or shorter label must not resize the control."
            )
            assert zh["scan"]["backgroundColor"] == _ACCENT, (
                f"The zh_TW primary CTA's background is "
                f"{zh['scan']['backgroundColor']}."
            )
    finally:
        # ALWAYS put the server back to English — `ui.locale` is persisted
        # server-side, not in the browser context PWContext discards.
        _patch_locale(base_url, "en")
