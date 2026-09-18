"""Web scenario s74 — Daylight theme foundation + results-tree visuals (#878).

WEB-ONLY. The Qt implementation of this design (PR #639) was closed unmerged
when the desktop client was retired by #646, so there is no desktop driver to
port; #878 moved the design to the web client instead.

What this pins, and why a unit test cannot:

  1. **The theme is actually applied.** The tokens live in `index.css` as
     Tailwind v4 `@theme` colours, which only become real utilities after the
     CSS pipeline runs. jsdom never runs it, so every vitest assertion in this
     area can only see CLASS NAMES — `bg-app` asserted in jsdom is green even
     when the token that backs it was deleted. Here the assertion is the
     COMPUTED colour of the app root: rgb(245, 239, 230) = #f5efe6.

  2. **The similarity badge's non-colour cues survive the build.** Tailwind v4
     scans source text for class names, so an interpolated `border-${style}`
     compiles to nothing — the dashed/dotted border that carries the state for
     a colour-blind user would vanish with no test going red. This reads the
     computed `border-style` and `font-weight` off the live badges and asserts
     the rendered (border-style, font-weight) pairs are distinct across the
     states on screen.

  3. **The delete decision is visible.** The UX baseline audit's headline
     finding was that the delete decision was the lowest-salience text on the
     screen. This stages a real delete through the row control and asserts the
     row's computed background is the delete wash and the filename's computed
     `text-decoration-line` is `line-through`.

  4. **The group frame is drawn.** Group rows carry the warm band and the
     4 px accent left strip; the frame's closing line under the group's LAST
     child comes from row data (`isLastInGroup`), not a CSS sibling rule,
     because the tree is virtualised.

  5. **Exactly one decision chip is filled** (slice b). The active segment
     wears its own decision's chip (`dec.delete` red for a staged delete,
     `dec.keep` green for the untouched `''` = keep) and every inactive one is
     transparent. Read as computed colours — the class-level mapping is in
     DecisionControl.test.tsx and would pass with every token deleted.

  6. **The padlock reads faint → solid** (slice b), and clicking it still
     locks the row: `aria-pressed` plus the computed colour, before and after.

  7. **The score mini bar has a real width** (slice b): fill > 0 and ≤ the
     cell, drawn with the `scoreFill` gradient. A NaN width is valid-looking
     markup that CSS silently ignores, and a renamed gradient utility compiles
     to nothing — neither shows up in jsdom.

  8. **The app has a typeface at all** (layout slice T). Nothing declared a
     font family before 2026-09-18, so every surface rendered in whatever the
     browser defaults to and a zh-TW label fell back per glyph. `--font-sans`
     / `--font-mono` are `@theme` tokens: like every other token here they
     exist only after the CSS build, and in jsdom `font-mono` is a string that
     spells correctly with the token deleted.

  9. **The keyboard cursor is visible** (layout slice T, open questions Q7).
     The roving cursor is an `aria-activedescendant`, which a sighted user
     cannot see; its ring is a `group-focus-visible:` variant, i.e. exactly the
     kind of class that compiles to nothing when the token behind it is renamed
     — and the ring is the difference between "what is chosen" (the tint) and
     "where I am" (the stroke). Read as the computed outline of the row the
     cursor names, after a real key press.

 10. **The primary button wears the accent** (layout slice T). `bg-warm` and
     its hover both moved with the Q8 palette fix; the button is the surface
     that fix exists for (white label at 3.88:1 before, 5.04:1 after).

Fixture: qa/sandbox/near-duplicates/ (5 JPEGs, one group), plain scan — the
same fixture s72/s73 use. It yields the Ref winner plus near-match rows, i.e.
two of the five badge states; the five-way mapping itself is pure logic and is
pinned by frontend/src/lib/similarityBadge.test.ts.
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
    dismiss_modal_overlays,
    open_scan_dialog,
    right_click_row,
    run_scan,
)
from qa.web.testid_constants import (
    CONTEXT_MENU,
    CTX_SET_ACTION_KEEP,
    MAIN_RESULT_TREE,
    MAIN_STATUS_BAR,
    PREVIEW_PANE,
    SCAN_DIALOG,
    SCAN_START_BUTTON,
    row_decision_option_testid,
    row_decision_testid,
    row_file_testid,
    row_group_testid,
    row_lock_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

_VIEWPORT = {"width": 1280, "height": 800}

# Daylight tokens, as the browser reports them. Kept as rgb() strings because
# that is what getComputedStyle returns; the hex each one is is in the comment.
_APP_BG = "rgb(245, 239, 230)"  # --color-app        #f5efe6
_DELETE_ROW_BG = "rgb(253, 243, 240)"  # --color-delete-row #fdf3f0
_GROUP_BAND_BG = "rgb(243, 233, 216)"  # --color-group-band #f3e9d8
# Layout slice T (2026-09-18): the accent moved #bd6b39 → #a85a2c so the group
# strip, the ★ Ref border, the Q7 focus ring and the primary button are ONE
# accent rather than two warm oranges a hair apart. The old value failed AA
# under a white label (3.88:1); this one clears it at 5.04:1.
_ACCENT = "rgb(168, 90, 44)"  # --color-warm       #a85a2c

# Slice (b) — decision chips / padlock / score bar (§9.3 dec.* + scoreFill).
_DEC_DELETE_BG = "rgb(196, 80, 63)"  # --color-dec-delete-bg   #c4503f
_DEC_DELETE_INK = "rgb(255, 255, 255)"  # --color-dec-delete-ink  #ffffff
_DEC_KEEP_BG = "rgb(231, 243, 236)"  # --color-dec-keep-bg     #e7f3ec
_DEC_KEEP_INK = "rgb(47, 138, 90)"  # --color-dec-keep-ink    #2f8a5a
_TRANSPARENT = "rgba(0, 0, 0, 0)"  # dec.undecided bg = transparent
_INK_FAINT = "rgb(168, 159, 143)"  # --color-ink-faint       #a89f8f
_EM_DASH = "—"

# Slice (e) — the surfaces OUTSIDE the result tree (preview, status bar,
# context menu, dialogs). Until (e) these were still on Tailwind's stock
# neutral palette, i.e. cold grey panels floating on the warm canvas.
_PANEL_BG = "rgb(255, 253, 249)"  # --color-panel     #fffdf9
_TITLEBAR_BG = "rgb(243, 237, 227)"  # --color-titlebar  #f3ede3
_SUBTLE_BG = "rgb(247, 235, 218)"  # --color-subtle    #f7ebda

_READ_BG = """(testid) => {
  const el = document.querySelector(`[data-testid="${testid}"]`);
  return el ? getComputedStyle(el).backgroundColor : null;
}"""

# The status TEXT is a <p> with no fill of its own; the strip that carries the
# titlebar surface is the <footer> around it.
_READ_STATUS_STRIP_BG = """(testid) => {
  const el = document.querySelector(`[data-testid="${testid}"]`);
  const strip = el ? el.closest('footer') : null;
  return strip ? getComputedStyle(strip).backgroundColor : null;
}"""

# Reads every similarity badge currently mounted, with the two colour-free
# cues plus the leading glyph, so the driver can check them as a set.
_READ_BADGES = """() => {
  const out = [];
  for (const el of document.querySelectorAll('[data-sim-state]')) {
    const cs = getComputedStyle(el);
    out.push({
      state: el.dataset.simState,
      borderStyle: cs.borderTopStyle,
      fontWeight: cs.fontWeight,
      text: (el.textContent || '').trim(),
    });
  }
  return out;
}"""

_READ_ROOT_BG = """() => {
  const root = document.querySelector('[data-testid="main-result-tree"]')
    ?.closest('.h-screen') ?? document.body.firstElementChild;
  return getComputedStyle(root).backgroundColor;
}"""

# Slice T — the two font stacks. `body` is read off a real element; the mono
# stack has no consumer on the review screen yet (slice C's mono cells are the
# first), so it is read through a throwaway element carrying the `font-mono`
# utility. That is the whole assertion worth making: whether the UTILITY
# resolves to the declared stack. The element is removed again immediately.
_READ_TYPOGRAPHY = """() => {
  const first = (ff) =>
    (ff || '').split(',')[0].trim().replace(/^["']|["']$/g, '');
  const probe = document.createElement('span');
  probe.className = 'font-mono';
  probe.textContent = '0';
  document.body.appendChild(probe);
  const mono = getComputedStyle(probe).fontFamily;
  probe.remove();
  const body = getComputedStyle(document.body).fontFamily;
  return { body, bodyFirst: first(body), mono, monoFirst: first(mono) };
}"""

# Slice T / Q7 — the row the roving cursor names, as the browser draws it.
# `[data-cursor]` is on the treeitem wrapper, so this is the cursor's own row
# and not a guess from the selection.
_READ_CURSOR_ROW = """() => {
  const row = document.querySelector('[data-cursor]');
  if (!row) return null;
  const cs = getComputedStyle(row);
  const caret = row.querySelector('[data-cursor-caret]');
  const caretCs = caret ? getComputedStyle(caret) : null;
  return {
    outlineColor: cs.outlineColor,
    outlineWidth: cs.outlineWidth,
    outlineStyle: cs.outlineStyle,
    outlineOffset: cs.outlineOffset,
    borderRadius: cs.borderTopLeftRadius,
    caretText: caret ? (caret.textContent || '').trim() : null,
    caretDisplay: caretCs ? caretCs.display : null,
    caretColor: caretCs ? caretCs.color : null,
  };
}"""


def _read_row_style(page, row_testid: str) -> dict:
    return page.evaluate(
        """(testid) => {
  const row = document.querySelector(`[data-testid="${testid}"]`);
  if (!row) return null;
  const name = row.querySelector('[data-col="name"] span:last-of-type');
  return {
    background: getComputedStyle(row).backgroundColor,
    nameDecoration: name ? getComputedStyle(name).textDecorationLine : null,
  };
}""",
        row_testid,
    )


def _read_decision_chips(page, decision_testid: str) -> dict:
    """Computed fill + ink of each segment of one row's decision control."""
    return page.evaluate(
        """(testid) => {
  const out = {};
  for (const slug of ['none', 'delete', 'ignore']) {
    const el = document.querySelector(`[data-testid="${testid}-${slug}"]`);
    if (!el) { out[slug] = null; continue; }
    const cs = getComputedStyle(el);
    out[slug] = { bg: cs.backgroundColor, ink: cs.color };
  }
  return out;
}""",
        decision_testid,
    )


def _read_lock(page, lock_testid: str) -> dict:
    return page.evaluate(
        """(testid) => {
  const el = document.querySelector(`[data-testid="${testid}"]`);
  if (!el) return null;
  return {
    pressed: el.getAttribute('aria-pressed'),
    color: getComputedStyle(el).color,
  };
}""",
        lock_testid,
    )


def _read_score_bar(page, row_testid: str) -> dict:
    """The score cell's text plus the mini bar's geometry, for one row."""
    return page.evaluate(
        """(testid) => {
  const row = document.querySelector(`[data-testid="${testid}"]`);
  if (!row) return null;
  const cell = row.querySelector('[data-col="score"]');
  if (!cell) return null;
  const fill = cell.querySelector('[data-score-fill]');
  const track = cell.querySelector('[data-score-track]');
  return {
    text: (cell.textContent || '').trim(),
    cellWidth: cell.getBoundingClientRect().width,
    trackWidth: track ? track.getBoundingClientRect().width : null,
    fillWidth: fill ? fill.getBoundingClientRect().width : null,
    fillImage: fill ? getComputedStyle(fill).backgroundImage : null,
  };
}""",
        row_testid,
    )


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def run(*, base_url: str) -> None:
    tmpdir = tempfile.mkdtemp(prefix="qa_s74_")
    db_path = os.path.join(tmpdir, "manifest.db")
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.set_viewport_size(_VIEWPORT)
            page.goto("/")

            run_scan(
                page,
                sources=[_NEAR_DUPS_DIR],
                output_path=db_path,
                scan_timeout=120_000,
            )

            manifest = _get_manifest(base_url, db_path)
            assert manifest["total_groups"] >= 1, "Expected at least one group"
            group = manifest["groups"][0]
            group_id = str(group["group_number"])
            items = group["items"]
            assert len(items) >= 2, "Expected a group with at least two members"

            # ── 1. The theme reached the DOM ──────────────────────────────────
            root_bg = page.evaluate(_READ_ROOT_BG)
            print(f"probe_status: s74 app-root background-color = {root_bg}")
            assert root_bg == _APP_BG, (
                "#878 — the app canvas is not the Daylight warm paper: expected "
                f"{_APP_BG} (#f5efe6), got {root_bg}. Either the @theme token "
                "was dropped from index.css or the root stopped consuming it, "
                "and the whole theme is off with every class name still present."
            )

            # ── 1b. Type (layout slice T · REPLY L3 "Root", T1) ───────────────
            typography = page.evaluate(_READ_TYPOGRAPHY)
            print(f"probe_status: s74 typography = {typography}")
            assert typography["bodyFirst"] == "Segoe UI", (
                "slice T — `body` renders in "
                f"{typography['bodyFirst']!r}, expected 'Segoe UI' first. "
                "Nothing declared a family before this slice, so a dropped "
                "--font-sans is invisible: the app simply goes back to the "
                f"browser default. Full stack: {typography['body']!r}"
            )
            assert "Noto Sans TC" in typography["body"], (
                "slice T — 'Noto Sans TC' is not in the body stack "
                f"({typography['body']!r}). It sits ahead of the generic on "
                "purpose: without it Windows falls back per GLYPH, so a zh-TW "
                "label renders half in one face and half in another."
            )
            assert typography["monoFirst"] == "Cascadia Code", (
                "slice T — the `font-mono` utility resolves to "
                f"{typography['monoFirst']!r}, expected 'Cascadia Code' first "
                f"(full stack {typography['mono']!r}). Slice C's mono metadata "
                "cells consume this utility; with --font-mono undeclared they "
                "would silently render in Tailwind's stock mono stack."
            )

            # ── 2. Badge cues survive the Tailwind build ──────────────────────
            badges = page.evaluate(_READ_BADGES)
            print(f"probe_status: s74 similarity badges rendered = {badges}")
            assert badges, "No similarity badges rendered in the result tree"

            by_state: dict[str, dict] = {}
            for badge in badges:
                by_state.setdefault(badge["state"], badge)
            print(
                "probe_status: s74 badge states on screen = "
                f"{sorted(by_state)}"
            )
            assert len(by_state) >= 2, (
                "Expected at least two similarity states in the near-duplicates "
                f"group (Ref + near match), got {sorted(by_state)}"
            )

            for state, badge in by_state.items():
                assert badge["borderStyle"] in {"solid", "dashed", "dotted"}, (
                    f"#878 — badge state {state!r} rendered with "
                    f"border-style={badge['borderStyle']!r}. Tailwind v4 scans "
                    "source text for class names, so an interpolated "
                    "`border-${...}` compiles to nothing and the shape cue a "
                    "colour-blind user relies on disappears silently."
                )

            cues = {(b["borderStyle"], b["fontWeight"]) for b in by_state.values()}
            assert len(cues) == len(by_state), (
                "#878 — two similarity states render with the SAME "
                f"(border-style, font-weight) pair: {cues}. Colour would then "
                "be the only thing telling them apart, which is the defect the "
                "UX baseline audit filed."
            )

            ref_badge = by_state.get("ref")
            assert ref_badge is not None, (
                f"Expected a Ref badge in the group, saw {sorted(by_state)}"
            )
            print(f"probe_status: s74 ref badge text = {ref_badge['text']!r}")
            assert "★" in ref_badge["text"], (
                "#878 — the Ref badge lost its ★ glyph "
                f"(text={ref_badge['text']!r}); the glyph is the grayscale cue "
                "that separates the keeper from every other state."
            )

            # ── 3. Group framing ──────────────────────────────────────────────
            group_style = page.evaluate(
                """(testid) => {
  const el = document.querySelector(`[data-testid="${testid}"]`);
  if (!el) return null;
  const cs = getComputedStyle(el);
  return {
    background: cs.backgroundColor,
    leftWidth: cs.borderLeftWidth,
    leftColor: cs.borderLeftColor,
  };
}""",
                row_group_testid(group_id),
            )
            print(f"probe_status: s74 group row framing = {group_style}")
            assert group_style["background"] == _GROUP_BAND_BG, (
                "#878 — the group header lost its warm band: expected "
                f"{_GROUP_BAND_BG} (#f3e9d8), got {group_style['background']}."
            )
            assert group_style["leftColor"] == _ACCENT, (
                "#878 — the group header's accent left strip is "
                f"{group_style['leftColor']}, expected {_ACCENT} (#a85a2c)."
            )
            assert group_style["leftWidth"] == "4px", (
                "#878 — the accent left strip is "
                f"{group_style['leftWidth']} wide, expected 4px."
            )

            # ── 4. A staged delete is visible on the row ──────────────────────
            basename = Path(items[0]["file_path"]).name
            row_testid = row_file_testid(group_id, basename)

            before = _read_row_style(page, row_testid)
            print(f"probe_status: s74 row before delete = {before}")
            assert before["background"] != _DELETE_ROW_BG, (
                "An undecided row already carries the delete wash — the tint "
                "cannot mean 'staged for deletion' if every row has it."
            )
            assert "line-through" not in (before["nameDecoration"] or ""), (
                "An undecided row's filename is already struck through."
            )

            page.get_by_test_id(
                row_decision_option_testid(group_id, basename, "delete")
            ).click()
            page.wait_for_timeout(800)

            after = _read_row_style(page, row_testid)
            print(f"probe_status: s74 row after delete = {after}")
            assert after["background"] == _DELETE_ROW_BG, (
                "#878 — a row staged for deletion is not tinted: expected "
                f"{_DELETE_ROW_BG} (#fdf3f0), got {after['background']}. The UX "
                "baseline audit's headline finding was that the delete decision "
                "was the lowest-salience thing on the screen."
            )
            assert "line-through" in (after["nameDecoration"] or ""), (
                "#878 — the filename of a row staged for deletion is not struck "
                f"through (text-decoration-line={after['nameDecoration']!r})."
            )

            # ── 5. Decision chips (slice b) ───────────────────────────────────
            # jsdom can only see the class names (DecisionControl.test.tsx);
            # the tokens behind them only become real colours after the
            # Tailwind build, which is what this reads.
            staged = _read_decision_chips(
                page, row_decision_testid(group_id, basename)
            )
            print(f"probe_status: s74 decision chips (delete staged) = {staged}")
            assert staged["delete"]["bg"] == _DEC_DELETE_BG, (
                "#878 — the active Delete segment is not the delete chip: "
                f"expected {_DEC_DELETE_BG} (#c4503f), got "
                f"{staged['delete']['bg']}. A staged delete has to be the "
                "loudest thing in the cell."
            )
            assert staged["delete"]["ink"] == _DEC_DELETE_INK, (
                "#878 — the active Delete segment's label is "
                f"{staged['delete']['ink']}, expected {_DEC_DELETE_INK} on the "
                "filled red chip."
            )
            for slug in ("none", "ignore"):
                assert staged[slug]["bg"] == _TRANSPARENT, (
                    f"#878 — the inactive {slug!r} segment is filled "
                    f"({staged[slug]['bg']}); dec.undecided is transparent, and "
                    "exactly one filled chip per row is what makes the staged "
                    "decision readable at a glance."
                )

            # A row nobody has touched: "" is KEEP under the #584 model, so its
            # None segment wears the keep chip — not the undecided grey, which
            # would make active and inactive indistinguishable.
            other_basename = Path(items[1]["file_path"]).name
            undecided = _read_decision_chips(
                page, row_decision_testid(group_id, other_basename)
            )
            print(f"probe_status: s74 decision chips (undecided row) = {undecided}")
            assert undecided["none"]["bg"] == _DEC_KEEP_BG, (
                "#878 — an untouched row's None segment is "
                f"{undecided['none']['bg']}, expected the keep chip "
                f"{_DEC_KEEP_BG} (#e7f3ec)."
            )
            assert undecided["none"]["ink"] == _DEC_KEEP_INK, (
                "#878 — an untouched row's None label is "
                f"{undecided['none']['ink']}, expected {_DEC_KEEP_INK} (#2f8a5a)."
            )

            # ── 6. The padlock reads faint → solid (slice b) ──────────────────
            lock_testid = row_lock_testid(group_id, other_basename)
            lock_before = _read_lock(page, lock_testid)
            print(f"probe_status: s74 lock before click = {lock_before}")
            assert lock_before["pressed"] == "false", (
                "#878 — an unlocked row's padlock reports aria-pressed="
                f"{lock_before['pressed']!r}."
            )
            assert lock_before["color"] == _INK_FAINT, (
                "#878 — an unlocked padlock is "
                f"{lock_before['color']}, expected the faint ink {_INK_FAINT} "
                "(#a89f8f). Faint-vs-solid is the whole point: it is what lets "
                "a locked row be spotted without reading every cell."
            )

            page.get_by_test_id(lock_testid).click()
            page.wait_for_timeout(800)

            lock_after = _read_lock(page, lock_testid)
            print(f"probe_status: s74 lock after click = {lock_after}")
            assert lock_after["pressed"] == "true", (
                "#878 — clicking the padlock did not lock the row "
                f"(aria-pressed={lock_after['pressed']!r}); the toggle must "
                "still dispatch the same lock action it always did."
            )
            assert lock_after["color"] == _ACCENT, (
                "#878 — a locked padlock is "
                f"{lock_after['color']}, expected the warm accent {_ACCENT} "
                "(#a85a2c)."
            )

            # ── 7. Score mini bar (slice b) ───────────────────────────────────
            scored = None
            for item in items:
                probe = _read_score_bar(
                    page, row_file_testid(group_id, Path(item["file_path"]).name)
                )
                print(f"probe_status: s74 score cell = {probe}")
                if probe and probe["text"] != _EM_DASH:
                    scored = probe
                    break
            assert scored is not None, (
                "#878 — every row in the near-duplicates group is unscored, so "
                "the score bar was never exercised. Either the scorer stopped "
                "scoring this fixture or the cell's text changed."
            )
            assert scored["trackWidth"], (
                "#878 — a scored row renders no score track; the number is "
                "back to being the only thing in the cell."
            )
            assert 0 < scored["fillWidth"] <= scored["cellWidth"], (
                "#878 — the score bar fill is "
                f"{scored['fillWidth']}px against a {scored['cellWidth']}px "
                "cell. Zero means the ratio never reached the DOM (a NaN width "
                "is silently ignored by CSS); wider than the cell means the "
                "clamp in lib/scoreBar.ts was bypassed."
            )
            assert "gradient" in (scored["fillImage"] or ""), (
                "#878 — the score fill is not the scoreFill gradient "
                f"(background-image={scored['fillImage']!r}). Tailwind's "
                "gradient utilities are the kind of class that compiles to "
                "nothing when renamed, with no test going red."
            )

            # ── 8. The theme reaches the rest of the screen (slice e) ─────────
            # Slices (a)/(b) themed the tree, header, menu strip and root; a UX
            # audit then found the preview pane, every context menu and every
            # dialog still on Tailwind's stock neutral palette — cold grey
            # floating on warm paper. These four reads are the ones no vitest
            # assertion can make: in jsdom `bg-panel` and `bg-neutral-50` are
            # both just strings, and both stay green after the token is gone.

            # 8a. Preview pane — select a row so the pane renders its content.
            page.get_by_test_id(row_testid).locator('[data-col="name"]').click()
            page.wait_for_timeout(500)
            page.get_by_test_id(PREVIEW_PANE).wait_for(
                state="visible", timeout=5_000
            )
            preview_bg = page.evaluate(_READ_BG, PREVIEW_PANE)
            print(f"probe_status: s74 preview pane background = {preview_bg}")
            assert preview_bg == _PANEL_BG, (
                "#878 slice (e) — the preview pane is not the Daylight panel "
                f"surface: expected {_PANEL_BG} (#fffdf9), got {preview_bg}. "
                "The pane is a third of the screen; on the stock neutral-50 it "
                "reads as a cold grey card sitting on warm paper."
            )

            # The pane's mono metadata values are the one REAL consumer of
            # `--font-mono` on the review screen today (PreviewPane MetaRow);
            # 1b proved the utility resolves, this proves something wears it.
            preview_mono = page.evaluate(
                """(testid) => {
  const pane = document.querySelector(`[data-testid="${testid}"]`);
  const el = pane ? pane.querySelector('.font-mono') : null;
  return el ? getComputedStyle(el).fontFamily : null;
}""",
                PREVIEW_PANE,
            )
            print(f"probe_status: s74 preview mono cell font = {preview_mono}")
            assert preview_mono is not None and "Cascadia Code" in preview_mono, (
                "slice T — the preview pane's mono metadata value renders in "
                f"{preview_mono!r}, expected a stack led by 'Cascadia Code'."
            )

            # 8b. Status bar strip (§9.3 'status #f3ede3').
            status_bg = page.evaluate(_READ_STATUS_STRIP_BG, MAIN_STATUS_BAR)
            print(f"probe_status: s74 status bar background = {status_bg}")
            assert status_bg == _TITLEBAR_BG, (
                "#878 — the status bar strip is not the titlebar surface: "
                f"expected {_TITLEBAR_BG} (#f3ede3), got {status_bg}."
            )

            # 8c. Row context menu: its own surface, and the hover fill of an
            # item. Hover is read through a real mouse move — a `hover:` class
            # that lost its token still spells correctly in the DOM.
            right_click_row(page, row_testid)
            menu_bg = page.evaluate(_READ_BG, CONTEXT_MENU)
            print(f"probe_status: s74 context menu background = {menu_bg}")
            assert menu_bg == _PANEL_BG, (
                "#878 slice (e) — the row context menu is not the Daylight "
                f"panel surface: expected {_PANEL_BG} (#fffdf9), got {menu_bg}."
            )

            keep_item = page.get_by_test_id(CTX_SET_ACTION_KEEP)
            keep_item.hover()
            page.wait_for_timeout(300)
            hover_bg = page.evaluate(_READ_BG, CTX_SET_ACTION_KEEP)
            print(f"probe_status: s74 context menu item hover = {hover_bg}")
            assert hover_bg == _SUBTLE_BG, (
                "#878 slice (e) — a hovered context-menu item fills with "
                f"{hover_bg}, expected the warm hover tint {_SUBTLE_BG} "
                "(#f7ebda). A menu whose hover is still neutral-100 is the one "
                "surface the user's pointer is guaranteed to be on."
            )
            page.keyboard.press("Escape")
            page.get_by_test_id(CONTEXT_MENU).wait_for(
                state="hidden", timeout=5_000
            )

            # 8d. One dialog surface — every dialog shares ui/dialog.tsx's
            # DialogContent, so the Scan dialog stands in for all of them.
            open_scan_dialog(page)
            dialog_bg = page.evaluate(_READ_BG, SCAN_DIALOG)
            print(f"probe_status: s74 scan dialog background = {dialog_bg}")
            assert dialog_bg == _PANEL_BG, (
                "#878 slice (e) — the Scan dialog is not the Daylight panel "
                f"surface: expected {_PANEL_BG} (#fffdf9), got {dialog_bg}. "
                "Every dialog in the app inherits this one class, so a miss "
                "here is a miss on all of them."
            )

            # 8e. The primary button (layout slice T). `bg-warm` is the default
            # Button variant, so Start Scan stands in for every primary action
            # in the app — including the Delete-N CTA slice TB adds. Read here
            # rather than on the toolbar because the toolbar has no primary
            # button until that slice.
            start_bg = page.evaluate(_READ_BG, SCAN_START_BUTTON)
            print(f"probe_status: s74 primary button background = {start_bg}")
            assert start_bg == _ACCENT, (
                "slice T — the primary button is not the Daylight accent: "
                f"expected {_ACCENT} (#a85a2c), got {start_bg}. This button is "
                "the surface the Q8 contrast fix exists for — its white label "
                "measured 3.88:1 on the old accent and 5.04:1 on this one."
            )
            dismiss_modal_overlays(page)

            # ── 9. The keyboard cursor is visible (slice T · Q7) ──────────────
            # Focus the tree and press a real arrow: `:focus-visible` is what
            # keeps the ring keyboard-only, and Chrome only grants it once the
            # user has interacted with the keyboard. The row itself never takes
            # DOM focus (the cursor is an aria-activedescendant), so the ring is
            # a `group-focus-visible:` variant read off the container — exactly
            # the class shape that compiles to nothing when a token is renamed.
            page.get_by_test_id(MAIN_RESULT_TREE).focus()
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(300)

            cursor = page.evaluate(_READ_CURSOR_ROW)
            print(f"probe_status: s74 cursor row = {cursor}")
            assert cursor is not None, (
                "slice T — no row carries [data-cursor] after an ArrowDown, so "
                "the roving cursor (#709) never reached the DOM and there is "
                "nothing for the Q7 ring to be drawn on."
            )
            assert cursor["outlineWidth"] == "2px", (
                "slice T — the focus ring is "
                f"{cursor['outlineWidth']} wide, expected 2px. A cursor a "
                "sighted keyboard user cannot see is the defect Q7 filed."
            )
            assert cursor["outlineStyle"] == "solid", (
                f"slice T — the focus ring is {cursor['outlineStyle']!r}, "
                "expected 'solid'."
            )
            assert cursor["outlineColor"] == _ACCENT, (
                "slice T — the focus ring is "
                f"{cursor['outlineColor']}, expected the accent {_ACCENT} "
                "(#a85a2c). The ring, the group strip, the ★ Ref border and "
                "the primary button are one accent doing four jobs."
            )
            assert cursor["outlineOffset"] == "-2px", (
                "slice T — the focus ring's offset is "
                f"{cursor['outlineOffset']}, expected -2px. Inset is what "
                "keeps the ring from shifting the row it lands on."
            )
            assert cursor["caretText"] == "▸", (
                "slice T — the cursor row's caret reads "
                f"{cursor['caretText']!r}, expected '▸'. It is what makes the "
                "cursor survive grayscale and colour-blindness, where the "
                "ring's hue carries nothing."
            )
            assert cursor["caretDisplay"] != "none", (
                "slice T — the ▸ caret is display:none while the tree holds "
                "keyboard focus, so the ring is carrying the cursor alone."
            )
            assert cursor["caretColor"] == _ACCENT, (
                f"slice T — the ▸ caret is {cursor['caretColor']}, expected "
                f"the accent {_ACCENT} (#a85a2c)."
            )
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
