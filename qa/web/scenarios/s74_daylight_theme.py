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

 12. **The group header is the REPLY's, and does not compress** (layout slice
     G). 44px at BOTH densities, a 14px/700 title, a derived byte total the
     manifest never sends, and the Q5 "Keep best · delete rest" button as a
     SECONDARY control at 28px. One of these is not a class at all: the gap
     between the button and the caret's hit area is the arithmetic of two
     boxes, so only a live layout can evaluate it — and Q5 made that gap a
     requirement because «a misclick from collapse into a bulk decision is the
     expensive misclick on this screen».

  5. **Exactly one decision chip is filled** (slice b). The active segment
     wears its own decision's chip (`dec.delete` red for a staged delete,
     `dec.keep` green for the untouched `''` = keep) and every inactive one is
     transparent. Read as computed colours — the class-level mapping is in
     DecisionControl.test.tsx and would pass with every token deleted.

  6. **The padlock reads faint → solid** (slice b), and clicking it still
     locks the row: `aria-pressed` plus the computed colour, before and after.

  7. **The score mini bar has a real width** (slice b): fill > 0 and ≤ the
     cell. A NaN width is valid-looking markup that CSS silently ignores, and
     a renamed utility compiles to nothing — neither shows up in jsdom. Layout
     slice R makes the fill SOLID (the REPLY retires the two-stop gradient:
     "at 96px … invisible at best and a banding artefact at worst"), so this
     now asserts `background-image: none` and the fill's computed COLOUR —
     the token that backed the second stop is deleted in that slice, and a
     surviving gradient utility would silently paint nothing at all.

 11. **The row's geometry is the REPLY's, at BOTH densities** (layout slice R).
     Every number in §"Row heights" / §"Thumbnail" / §"Decision control" /
     §"Padlock" / §"Score bar" is an absolute px value that exists only after
     the CSS build: in jsdom `h-[72px]` is a string that spells correctly with
     Tailwind removed entirely. Read as computed boxes — row height (and the
     6px taller last row of a group, which is a second number in
     `estimateSize`), the thumbnail's box/radius/border, the decision control's
     inset track, the padlock's hit area, the score track and its solid fill,
     the folder sub-line's left-truncation `direction`, and the mono family on
     a machine-value cell. Then the density preference is flipped in
     localStorage and the whole set is re-read at compact — a density-keyed
     class table is exactly the shape that renders one density correctly and
     the other not at all.

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
    load_manifest,
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
    RESULT_COL_HEADER_ROW,
    SCAN_DIALOG,
    SCAN_START_BUTTON,
    col_resize_testid,
    group_keep_best_testid,
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
# Layout slice C — the column header is CHROME (the toolbar surface), the rule
# under it is the group line, and the separator between rows inside a group is
# the new, lighter row line.
_TOOLBAR_BG = "rgb(250, 246, 238)"  # --color-toolbar   #faf6ee
_GROUP_LINE = "rgb(228, 214, 189)"  # --color-group-line #e4d6bd
_ROW_LINE = "rgb(244, 238, 227)"  # --color-row-line  #f4eee3
_HEADER_HEIGHT = "28px"  # REPLY L1: 28px INCLUDING the bottom rule
# The zh-agnostic English defaults `classificationLabel()` falls back to, so the
# badge tooltip can be checked against the row's own manifest `action`.
_CLASSIFICATION_EN = {
    "EXACT": "Exact copy",
    "REVIEW_DUPLICATE": "Near-duplicate",
    "KEEP": "Best in group",
    "UNDATED": "No date",
}
# Narrow enough that the row cannot pay for either dims or date — the tree is
# narrower than the window by the preview pane, so this leaves ~600px of table
# against a full-set requirement near 1000px.
_NARROW_VIEWPORT = {"width": 900, "height": 800}
# Empty row allowed to the right of the padlock. The fill column should leave
# only the row's own right padding (16px) plus sub-pixel rounding; anything
# larger means the filename is not taking the slack.
_MAX_GUTTER_PX = 24
# Far more than the row can pay for at 1280×800 — the drag has to be clamped,
# not merely survived.
_DATE_DRAG_PX = 200
_WIDTHS_KEY = "pm.result-tree.column-widths.v1"

# Layout slice R — the row's own geometry, per density (REPLY 2026-09-18).
_HAIRLINE = "rgb(231, 221, 205)"  # --color-hairline   #e7ddcd
_SCORE_FILL = "rgb(184, 148, 106)"  # --color-score-fill #b8946a (now SOLID)
_DENSITY_KEY = "density"

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

# Slice C — the column header as chrome, and one body cell's typography. Both
# are computed-style reads a vitest assertion cannot make: in jsdom `uppercase`,
# `bg-toolbar` and `font-mono` are all just class strings that survive the token
# being deleted.
_READ_COLUMN_HEADER = """(testid) => {
  const head = document.querySelector(`[data-testid="${testid}"]`);
  if (!head) return null;
  const cs = getComputedStyle(head);
  const action = head.querySelector('[data-testid="col-header-action"]');
  const handle = head.querySelector('[data-testid="col-resize-name"]');
  return {
    height: cs.height,
    backgroundColor: cs.backgroundColor,
    fontSize: cs.fontSize,
    fontWeight: cs.fontWeight,
    textTransform: cs.textTransform,
    letterSpacing: cs.letterSpacing,
    borderBottomWidth: cs.borderBottomWidth,
    borderBottomColor: cs.borderBottomColor,
    actionText: action ? (action.textContent || '').trim() : null,
    classificationHeads:
      head.querySelectorAll('[data-testid="col-header-classification"]').length,
    resizeHitWidth: handle ? handle.getBoundingClientRect().width : null,
  };
}"""

# Slice C — the shed set, keyed off the width the tree itself measured rather
# than off the viewport, so a failure says WHY the column is missing.
_READ_SHED = """() => {
  const tree = document.querySelector('[data-testid="main-result-tree"]');
  const row = document.querySelector('[data-testid^="row-file-"]');
  const head = document.querySelector('[data-testid="col-header-name"]');
  const nameCell = row ? row.querySelector('[data-col="name"]') : null;
  const lock = row ? row.querySelector('[data-testid^="row-lock-"]') : null;
  const treeBox = tree ? tree.getBoundingClientRect() : null;
  const lockBox = lock ? lock.getBoundingClientRect() : null;
  return {
    tableWidth: tree ? tree.getAttribute('data-table-width') : null,
    dims: document.querySelectorAll('[data-col="dims"]').length,
    date: document.querySelectorAll('[data-col="date"]').length,
    size: document.querySelectorAll('[data-col="size"]').length,
    action: document.querySelectorAll('[data-col="action"]').length,
    nameBasis: head ? Number(head.getAttribute('data-col-basis')) : null,
    nameWidth: nameCell ? Math.round(nameCell.getBoundingClientRect().width) : null,
    // How much empty row is left to the RIGHT of the last thing on it. The
    // whole point of the fill column is that this stays small.
    gutter: treeBox && lockBox ? Math.round(treeBox.right - lockBox.right) : null,
    rowPresent: Boolean(row),
  };
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


def _read_cell_type(page, row_testid: str, col: str) -> dict:
    """One body cell's computed alignment + font family, for one row."""
    return page.evaluate(
        """([testid, col]) => {
  const row = document.querySelector(`[data-testid="${testid}"]`);
  if (!row) return null;
  const cell = row.querySelector(`[data-col="${col}"]`);
  if (!cell) return null;
  const cs = getComputedStyle(cell);
  return {
    textAlign: cs.textAlign,
    fontFamily: cs.fontFamily,
    text: (cell.textContent || '').trim(),
  };
}""",
        [row_testid, col],
    )


def _read_action_cell(page, row_testid: str, decision_testid: str) -> dict:
    """What the Action column holds — the decision control, not an enum (Q1)."""
    return page.evaluate(
        """([testid, decTestid]) => {
  const row = document.querySelector(`[data-testid="${testid}"]`);
  if (!row) return null;
  const cell = row.querySelector('[data-col="action"]');
  if (!cell) return null;
  const badge = row.querySelector('[data-sim-state]');
  return {
    holdsDecision: Boolean(cell.querySelector(`[data-testid="${decTestid}"]`)),
    classificationCells:
      row.querySelectorAll('[data-col="classification"]').length,
    badgeTitle: badge ? badge.getAttribute('title') : null,
    rowBorderColor: getComputedStyle(row).borderBottomColor,
  };
}""",
        [row_testid, decision_testid],
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
  // Layout slice R put a "keep" label in this cell beside the number, so the
  // number's exact handle moved from the cell's textContent to the value
  // element. Reading the whole cell here would make every row look scored
  // (`keep—` is not the em dash) and pick an unscored one for the bar checks.
  const value = cell.querySelector('[data-score-value]');
  return {
    text: value ? (value.textContent || '').trim() : (cell.textContent || '').trim(),
    cellWidth: cell.getBoundingClientRect().width,
    trackWidth: track ? track.getBoundingClientRect().width : null,
    fillWidth: fill ? fill.getBoundingClientRect().width : null,
    fillImage: fill ? getComputedStyle(fill).backgroundImage : null,
    fillColor: fill ? getComputedStyle(fill).backgroundColor : null,
  };
}""",
        row_testid,
    )


# Layout slice R — every box on the row, as the browser computes it. Read as
# `getBoundingClientRect()` rather than `getComputedStyle().height`: the CSSOM
# resolves `height` to the CONTENT box, so a border-box row declared 72px tall
# reports "49px" there and an assertion written against it would be wrong in a
# way that looks like a layout bug.
_READ_ROW_GEOMETRY = """([testid, decTestid, lockTestid]) => {
  const row = document.querySelector(`[data-testid="${testid}"]`);
  if (!row) return null;
  const cs = getComputedStyle(row);
  const thumb = row.querySelector('[data-thumb]');
  const ts = thumb ? getComputedStyle(thumb) : null;
  const track = document.querySelector(`[data-testid="${decTestid}"]`);
  const trackCs = track ? getComputedStyle(track) : null;
  const selected = track ? track.querySelector('[aria-pressed="true"]') : null;
  const lock = document.querySelector(`[data-testid="${lockTestid}"]`);
  const scoreCell = row.querySelector('[data-col="score"]');
  const scoreTrack = row.querySelector('[data-score-track]');
  const scoreFill = row.querySelector('[data-score-fill]');
  const scoreLabel = row.querySelector('[data-score-label]');
  const folder = row.querySelector('[data-col-folder]');
  const size = row.querySelector('[data-col="size"]');
  const h = (el) => el ? Math.round(el.getBoundingClientRect().height) : null;
  const w = (el) => el ? Math.round(el.getBoundingClientRect().width) : null;
  return {
    density: row.dataset.density,
    rowHeight: h(row),
    alignItems: cs.alignItems,
    rowBorderColor: cs.borderBottomColor,
    thumbWidth: w(thumb),
    thumbHeight: h(thumb),
    thumbRadius: ts ? ts.borderTopLeftRadius : null,
    thumbBorderWidth: ts ? ts.borderTopWidth : null,
    thumbBorderColor: ts ? ts.borderTopColor : null,
    decisionHeight: h(track),
    decisionBg: trackCs ? trackCs.backgroundColor : null,
    decisionRadius: trackCs ? trackCs.borderTopLeftRadius : null,
    selectedLabel: selected ? (selected.textContent || '').trim() : null,
    selectedBg: selected ? getComputedStyle(selected).backgroundColor : null,
    lockWidth: w(lock),
    lockHeight: h(lock),
    scoreCellWidth: w(scoreCell),
    scoreLabel: scoreLabel ? (scoreLabel.textContent || '').trim() : null,
    scoreTrackHeight: h(scoreTrack),
    scoreFillBg: scoreFill ? getComputedStyle(scoreFill).backgroundColor : null,
    scoreFillImage: scoreFill ? getComputedStyle(scoreFill).backgroundImage : null,
    folderDirection: folder ? getComputedStyle(folder).direction : null,
    sizeFont: size ? getComputedStyle(size).fontFamily : null,
  };
}"""

_READ_ROW_HEIGHT = """(testid) => {
  const row = document.querySelector(`[data-testid="${testid}"]`);
  return row ? Math.round(row.getBoundingClientRect().height) : null;
}"""

# Layout slice G — the group header's geometry, its title type, the derived
# size total and the Q5 button, read in ONE pass so the caret/button gap is
# measured against the same layout the rest of the numbers came from.
# The caret is the row's first element child; the button is addressed by its
# own testid, so a renamed caret wrapper fails loudly rather than silently
# measuring the gap from the row's left edge.
_READ_GROUP_HEADER_GEOMETRY = """([rowTestid, buttonTestid]) => {
  const row = document.querySelector(`[data-testid="${rowTestid}"]`);
  if (!row) return null;
  const caret = row.firstElementChild;
  const button = document.querySelector(`[data-testid="${buttonTestid}"]`);
  const title = row.querySelector('.font-bold');
  const mono = [...row.querySelectorAll('.font-mono')];
  const cs = getComputedStyle(row);
  const titleCs = title ? getComputedStyle(title) : null;
  const buttonCs = button ? getComputedStyle(button) : null;
  return {
    rowHeight: Math.round(row.getBoundingClientRect().height),
    paddingLeft: cs.paddingLeft,
    paddingRight: cs.paddingRight,
    titleSize: titleCs ? titleCs.fontSize : null,
    titleWeight: titleCs ? titleCs.fontWeight : null,
    sizeText: mono.length > 0 ? mono[0].textContent : null,
    suffixText: mono.length > 1 ? mono[1].textContent : null,
    buttonHeight: button ? Math.round(button.getBoundingClientRect().height) : null,
    buttonBg: buttonCs ? buttonCs.backgroundColor : null,
    buttonRadius: buttonCs ? buttonCs.borderRadius : null,
    caretToButtonGap:
      caret && button
        ? button.getBoundingClientRect().left - caret.getBoundingClientRect().right
        : null,
  };
}"""


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

            # 3a-ii. Layout slice G — the header's own geometry and the Q5
            # button. Every number is an absolute px that exists only after the
            # CSS build: `h-[44px]` in jsdom is a string that spells correctly
            # with Tailwind removed entirely, and the caret-to-button GAP is not
            # a class at all — it is the arithmetic of two boxes, which nothing
            # but a live layout can evaluate.
            header_geo = page.evaluate(
                _READ_GROUP_HEADER_GEOMETRY,
                [row_group_testid(group_id), group_keep_best_testid(group_id)],
            )
            print(f"probe_status: s74 group header geometry = {header_geo}")
            assert header_geo is not None, (
                "slice G — the group header row was not found."
            )
            assert header_geo["rowHeight"] == 44, (
                f"slice G — the group header measures {header_geo['rowHeight']}px, "
                "expected 44. That number is ALSO the virtualiser's "
                "GROUP_HEADER_HEIGHT, so a mismatch drifts the whole list "
                "without turning a unit test red."
            )
            assert header_geo["titleSize"] == "14px", (
                f"slice G — the group title is {header_geo['titleSize']}, "
                "expected 14px (REPLY §'Group header': «'Group 12' 14px/700»)."
            )
            assert header_geo["titleWeight"] in {"700", "bold"}, (
                f"slice G — the group title's weight is "
                f"{header_geo['titleWeight']!r}, expected 700. The title is what "
                "makes this row read as a heading rather than a row."
            )
            # H7: the derived byte total sits beside the id, not off to the
            # right. The manifest never sends it, so a broken sum renders a
            # plausible wrong figure — the presence of a unit is the cheapest
            # live check that something was summed at all.
            assert header_geo["sizeText"] is not None and any(
                unit in header_geo["sizeText"] for unit in (" B", "KB", "MB", "GB")
            ), (
                "slice G — the group header shows no derived size total "
                f"(read {header_geo['sizeText']!r})."
            )
            assert header_geo["buttonHeight"] == 28, (
                f"slice G — the Q5 button is {header_geo['buttonHeight']}px tall, "
                "expected 28."
            )
            assert header_geo["buttonBg"] == _PANEL_BG, (
                "slice G — the Q5 button's fill is "
                f"{header_geo['buttonBg']}, expected {_PANEL_BG} (#fffdf9). It is "
                "a SECONDARY button: the one accent-filled control on this "
                "screen is 'Scan folder'."
            )
            assert header_geo["caretToButtonGap"] >= 16, (
                "slice G — only "
                f"{header_geo['caretToButtonGap']:.1f}px separate the keep-best "
                "button from the caret's hit area; Q5 requires >= 16px, because "
                "«a misclick from collapse into a bulk decision is the expensive "
                "misclick on this screen»."
            )

            # 3b. The keeper row's 2px strip is the SAME accent (Q2). It used
            # to be the Ref badge's own ink #a85f2e, which sits 5/255 from the
            # group strip directly above it in one vertical line — close
            # enough to read as a rendering fault rather than as two cues.
            # Read off the Ref-badged row rather than guessing which item wins.
            keeper_strip = page.evaluate(
                """() => {
  const badge = document.querySelector('[data-sim-state="ref"]');
  const row = badge ? badge.closest('[data-testid^="row-file-"]') : null;
  if (!row) return null;
  const cs = getComputedStyle(row);
  return { width: cs.borderLeftWidth, color: cs.borderLeftColor };
}"""
            )
            print(f"probe_status: s74 keeper row strip = {keeper_strip}")
            assert keeper_strip is not None, (
                "No Ref-badged row on screen, so the keeper strip was never "
                "exercised."
            )
            assert keeper_strip["width"] == "2px", (
                "#878 — the keeper's left strip is "
                f"{keeper_strip['width']} wide, expected 2px."
            )
            assert keeper_strip["color"] == _ACCENT, (
                "slice T / Q2 — the keeper's left strip is "
                f"{keeper_strip['color']}, expected the accent {_ACCENT} "
                "(#a85a2c) — the same token the group strip above it uses."
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
            # Layout slice R retires the gradient — REPLY §"Score bar (R15)":
            # «Solid fill, not the prototype's gradient: at 96px a two-stop
            # gradient is three or four distinguishable pixels of variation,
            # which is invisible at best and a banding artefact at worst.»
            # `--color-score-fill-end` is deleted with it, so the assertion
            # that used to look for "gradient" is now its inverse: a surviving
            # `bg-linear-to-r from-… to-…` would reference a token that no
            # longer exists and paint NOTHING, which is exactly the class of
            # failure this scenario exists to catch.
            assert scored["fillImage"] == "none", (
                "slice R — the score fill still carries a background-image "
                f"({scored['fillImage']!r}); the REPLY retires the two-stop "
                "gradient for a solid fill, and the token behind its second "
                "stop is gone."
            )
            assert scored["fillColor"] == _SCORE_FILL, (
                "slice R — the score fill is "
                f"{scored['fillColor']}, expected the solid {_SCORE_FILL} "
                "(#b8946a). A Tailwind colour utility naming a deleted token "
                "compiles to nothing, with no test going red."
            )

            # ── 7c. Row geometry, comfortable (layout slice R) ────────────────
            # Every number below is an absolute px value that exists only after
            # the CSS build. `h-[72px]` in jsdom is a correctly-spelled string
            # with Tailwind removed entirely, so the only place these can be
            # checked is here, against the browser's own boxes.
            geo = page.evaluate(
                _READ_ROW_GEOMETRY,
                [
                    row_testid,
                    row_decision_testid(group_id, basename),
                    row_lock_testid(group_id, basename),
                ],
            )
            print(f"probe_status: s74 row geometry (comfortable) = {geo}")
            assert geo is not None, (
                "slice R — the file row is not in the DOM; every geometry "
                "assertion below would pass for the wrong reason."
            )
            assert geo["rowHeight"] == 72, (
                f"slice R — the row measures {geo['rowHeight']}px, not the "
                "REPLY's 72px total. This is the number ResultTree hands the "
                "virtualiser as `estimateSize`: when the two disagree, "
                "arrow-key scroll-into-view lands a row under the sticky "
                "header and `overscan: 10` hides it until the list is long."
            )
            assert geo["alignItems"] == "center", (
                f"slice R — the row aligns its cells {geo['alignItems']!r}. R4: "
                "top-aligning six short cells against a 48px thumbnail «is the "
                "single biggest reason the shipped row reads as a spreadsheet "
                "rather than a photo row»."
            )
            assert geo["rowBorderColor"] == _ROW_LINE, (
                f"slice R — the in-group separator is {geo['rowBorderColor']}, "
                f"expected the light row line {_ROW_LINE} (#f4eee3)."
            )
            assert geo["thumbWidth"] == 48 and geo["thumbHeight"] == 48, (
                f"slice R — the thumbnail is {geo['thumbWidth']}×"
                f"{geo['thumbHeight']}, expected 48×48."
            )
            assert geo["thumbRadius"] == "8px", (
                f"slice R — the thumbnail radius is {geo['thumbRadius']!r}, "
                "expected 8px."
            )
            assert geo["thumbBorderWidth"] == "1px", (
                f"slice R — the thumbnail border is {geo['thumbBorderWidth']!r}; "
                "without the 1px hairline a pale photo bleeds into the panel."
            )
            assert geo["thumbBorderColor"] == _HAIRLINE, (
                f"slice R — the thumbnail border is {geo['thumbBorderColor']}, "
                f"expected the hairline {_HAIRLINE} (#e7ddcd)."
            )
            assert geo["decisionHeight"] == 32, (
                f"slice R — the decision control is {geo['decisionHeight']}px "
                "tall, expected the REPLY's 32px inset track."
            )
            assert geo["decisionBg"] == _TITLEBAR_BG, (
                f"slice R — the decision track is {geo['decisionBg']}, expected "
                f"{_TITLEBAR_BG} (#f3ede3). «The track is what makes three "
                "mutually exclusive options read as one control with a current "
                "value rather than three adjacent buttons.»"
            )
            assert geo["decisionRadius"] == "9px", (
                f"slice R — the track radius is {geo['decisionRadius']!r}, "
                "expected 9px."
            )
            # Section 4 staged a delete on this row, so its Delete segment is
            # the selected one — the only solid-dark-fill-with-a-light-label in
            # the control, which is the cue that survives grayscale.
            assert geo["selectedBg"] == _DEC_DELETE_BG, (
                f"slice R — the selected segment fills {geo['selectedBg']}, "
                f"expected the delete chip {_DEC_DELETE_BG} (#c4503f)."
            )
            assert geo["lockWidth"] == 28 and geo["lockHeight"] == 28, (
                f"slice R — the padlock hit target is {geo['lockWidth']}×"
                f"{geo['lockHeight']}, expected 28×28. «A 16px target is below "
                "a comfortable pointer target and this is the control that "
                "protects a file from bulk operations.»"
            )
            assert geo["scoreTrackHeight"] == 6, (
                f"slice R — the score track is {geo['scoreTrackHeight']}px "
                "high, expected 6px. «A 4px full-width hairline track reads as "
                "a progress indicator; 6px with a visible empty remainder "
                "reads as a score out of one.»"
            )
            assert geo["scoreLabel"] == "keep", (
                f"slice R — the score cell's label is {geo['scoreLabel']!r}, "
                "expected 'keep'. «Without it a bare bar and a number in a "
                "96px cell is an unlabelled quantity, and this number is the "
                "one the \"keep best\" button acts on.»"
            )
            assert geo["folderDirection"] == "rtl", (
                "slice R — the folder sub-line's direction is "
                f"{geo['folderDirection']!r}, so it truncates from the RIGHT "
                "and elides the leaf — the one segment that distinguishes two "
                "copies of the same file (REPLY L2)."
            )
            assert "Cascadia" in (geo["sizeFont"] or ""), (
                f"slice R — the Size cell renders in {geo['sizeFont']!r}; the "
                "machine-value columns are mono so their digits line up down "
                "the column (REPLY L2)."
            )

            # The group's LAST child is 6px taller — a second number in
            # `estimateSize`, and the one the REPLY calls "one number" (plan
            # §Contradictions item 3). It is also the only place the closing
            # breath is visible at all.
            last_testid = row_file_testid(
                group_id, Path(items[-1]["file_path"]).name
            )
            last_height = page.evaluate(_READ_ROW_HEIGHT, last_testid)
            print(f"probe_status: s74 last-in-group row height = {last_height}")
            assert last_height == 78, (
                f"slice R — the group's last row measures {last_height}px, "
                "expected 78 (72 + the 6px closing breath). «The group then "
                "ends with a breath instead of a butt-joint.»"
            )

            # ── 7b. Columns + header chrome (layout slice C) ──────────────────
            # Q1 retired the classification column: it printed a value the
            # similarity badge beside it already implied, in the one vocabulary
            # the user never chose, while the REAL decision control sat
            # unlabelled to its right looking like nothing. "Action" now heads
            # the decision, and the classification moved into the badge's
            # tooltip. Every read below is a computed style — in jsdom
            # `uppercase`, `bg-toolbar` and `font-mono` are class strings that
            # stay green after the token they name is deleted.
            header = page.evaluate(_READ_COLUMN_HEADER, RESULT_COL_HEADER_ROW)
            print(f"probe_status: s74 column header = {header}")
            assert header is not None, (
                "slice C — no column header row in the DOM; every assertion "
                "below is about a header that never rendered."
            )
            assert header["height"] == _HEADER_HEIGHT, (
                f"slice C — the column header is {header['height']} tall, "
                f"expected {_HEADER_HEIGHT} INCLUDING its bottom rule (REPLY "
                "L1). ResultTree measures this height and hands it to the "
                "virtualizer as scrollMargin (#699), so a drift here is a "
                "windowing bug as well as a visual one."
            )
            assert header["backgroundColor"] == _TOOLBAR_BG, (
                f"slice C — the header is {header['backgroundColor']}, expected "
                f"the toolbar chrome {_TOOLBAR_BG} (#faf6ee). The header is "
                "chrome, so it takes the toolbar's warmth, not the panel's."
            )
            assert header["fontSize"] == "11px", (
                f"slice C — the header is {header['fontSize']}, expected 11px."
            )
            assert header["textTransform"] == "uppercase", (
                "slice C — the header's text-transform is "
                f"{header['textTransform']!r}, expected 'uppercase'. Uppercase "
                "plus tracking at 11px is the whole difference between a header "
                "and a bold first row of data (audit C1)."
            )
            assert header["letterSpacing"] == "0.66px", (
                f"slice C — header letter-spacing is {header['letterSpacing']}, "
                "expected 0.66px (.06em at 11px)."
            )
            assert header["borderBottomColor"] == _GROUP_LINE, (
                "slice C — the header's bottom rule is "
                f"{header['borderBottomColor']}, expected {_GROUP_LINE} "
                "(#e4d6bd)."
            )
            assert header["classificationHeads"] == 0, (
                "slice C — a classification column header is still rendered "
                f"({header['classificationHeads']} of them). Q1 removed that "
                "column outright."
            )
            assert header["actionText"] == "Action", (
                f"slice C — the decision column reads {header['actionText']!r}, "
                "expected 'Action' (web.column.action — 動作 in zh_TW). That "
                "header is the label the decision control never had."
            )
            assert header["resizeHitWidth"] == 8, (
                "slice C — the File Name resize handle's hit area is "
                f"{header['resizeHitWidth']}px, expected 8 (REPLY L1). A 6px "
                "target is the affordance users report as 'it never grabs'."
            )

            # 7b-ii. The Action cell holds the DECISION, and the classification
            # it replaced is reachable on the badge.
            first = items[0]
            first_name = Path(first["file_path"]).name
            action_cell = _read_action_cell(
                page,
                row_file_testid(group_id, first_name),
                row_decision_testid(group_id, first_name),
            )
            print(f"probe_status: s74 action cell = {action_cell}")
            assert action_cell["holdsDecision"], (
                "slice C — the decision control is not inside "
                '[data-col="action"]. Q1 moved it into the column that finally '
                "names it; leaving it in row-chrome puts the label and the "
                "control back in different places."
            )
            assert action_cell["classificationCells"] == 0, (
                "slice C — a classification cell is still rendered on the row."
            )
            expected_title = _CLASSIFICATION_EN.get(first["action"], "—")
            assert action_cell["badgeTitle"] == expected_title, (
                "slice C — the similarity badge's tooltip is "
                f"{action_cell['badgeTitle']!r}, expected {expected_title!r} "
                f"for action={first['action']!r}. The tooltip is the ONLY place "
                "the classification survives now that its column is gone — if "
                "it is empty, Q1 deleted information instead of moving it."
            )
            assert action_cell["rowBorderColor"] == _ROW_LINE, (
                "slice C — the separator between rows inside a group is "
                f"{action_cell['rowBorderColor']}, expected {_ROW_LINE} "
                "(#f4eee3). It is deliberately lighter than the group rule: "
                "inside a group the rows are alternatives to each other."
            )

            # 7b-iii. Mono, right-aligned machine values (REPLY L2).
            size_cell = _read_cell_type(
                page, row_file_testid(group_id, first_name), "size"
            )
            print(f"probe_status: s74 size cell type = {size_cell}")
            assert size_cell["textAlign"] == "right", (
                f"slice C — the Size cell is {size_cell['textAlign']}-aligned, "
                "expected right. Right-aligning magnitudes is what makes a "
                "column of numbers scannable."
            )
            assert "Cascadia Code" in size_cell["fontFamily"], (
                "slice C — the Size cell renders in "
                f"{size_cell['fontFamily']!r}; the mono stack is not applied, "
                "so the digits do not line up down the column."
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
            assert cursor["borderRadius"] == "6px", (
                "slice T — the focus ring's radius is "
                f"{cursor['borderRadius']}, expected 6px (Q7: it matches the "
                "row)."
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

            # ── 10. Need-based shedding + the fill column (slice C · L3) ──────
            # LAST on purpose: it narrows the window, and every section above
            # reads geometry at 1280×800. Shedding is need-based — a column goes
            # only while what is left still does not fit — and the width it
            # frees goes to the FILENAME, not to a gutter. Keyed off the width
            # the tree MEASURED (`data-table-width`), so a failure here says
            # whether the arithmetic fired or the measurement did.
            wide = page.evaluate(_READ_SHED)
            print(f"probe_status: s74 columns at 1280 = {wide}")
            assert wide["rowPresent"], (
                "slice C — no file row on screen; the shed probe would pass "
                "vacuously (zero dims cells because there are zero rows)."
            )
            assert wide["nameWidth"] > wide["nameBasis"], (
                "slice C — File Name renders at "
                f"{wide['nameWidth']}px against a stored basis of "
                f"{wide['nameBasis']}px, i.e. it is NOT filling the row. The "
                "width the shed columns freed is sitting in a gutter on the "
                "right instead of in the one string the user reads."
            )
            assert wide["gutter"] is not None and wide["gutter"] <= _MAX_GUTTER_PX, (
                "slice C — "
                f"{wide['gutter']}px of empty row is left to the right of the "
                f"padlock at 1280×800 (max {_MAX_GUTTER_PX}). That hole is what "
                "the fill column exists to close."
            )
            # 10b. A drag on the LAST column that fits must not delete it.
            # Shot Date is that column at this width, and shedding is need-based
            # — so before the clamp, dragging its own handle right pushed it past
            # the budget, unmounted it mid-drag, and mouseup persisted the
            # oversized width. With no header cell there is no handle and no
            # double-click target: the column stayed gone at this window size.
            handle = page.get_by_test_id(col_resize_testid("date"))
            hb = handle.bounding_box()
            assert hb is not None, "slice C — the Shot Date resize handle has no box"
            cx = hb["x"] + hb["width"] / 2
            cy = hb["y"] + hb["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            page.mouse.move(cx + _DATE_DRAG_PX, cy, steps=8)
            page.mouse.up()
            page.wait_for_timeout(200)
            dragged = page.evaluate(_READ_SHED)
            stored_date = page.evaluate(
                "(key) => { const v = localStorage.getItem(key); "
                "return v ? (JSON.parse(v).date ?? null) : null; }",
                _WIDTHS_KEY,
            )
            print(
                f"probe_status: s74 after +{_DATE_DRAG_PX}px Shot Date drag = "
                f"{dragged} stored_date={stored_date}"
            )
            assert dragged["date"] > 0, (
                "slice C — Shot Date is gone after a drag on its OWN handle. "
                "The drag shed the column it was on, and there is now no handle "
                "left to undo it with."
            )
            assert stored_date is not None and stored_date < 112 + _DATE_DRAG_PX, (
                f"slice C — the drag persisted date={stored_date}, the full "
                f"requested width. It was not clamped to what the row can pay "
                f"for, so the next launch hydrates a width that hides the column."
            )
            assert stored_date > 112, (
                f"slice C — the drag persisted date={stored_date}, no wider than "
                "the default: the clamp swallowed the resize entirely instead of "
                "capping it."
            )

            # …and the clamped width survives the cross-launch boundary without
            # taking the column with it.
            page.reload()
            load_manifest(page, db_path)
            page.wait_for_timeout(400)
            reloaded = page.evaluate(_READ_SHED)
            stored_after = page.evaluate(
                "(key) => { const v = localStorage.getItem(key); "
                "return v ? (JSON.parse(v).date ?? null) : null; }",
                _WIDTHS_KEY,
            )
            print(
                f"probe_status: s74 after reload = {reloaded} "
                f"stored_date={stored_after}"
            )
            assert reloaded["date"] > 0, (
                "slice C — Shot Date is missing after a reload that hydrated "
                f"date={stored_after}. A stored width is hiding its own column, "
                "which is the state the self-heal exists to prevent."
            )
            assert stored_after == stored_date, (
                f"slice C — the stored Shot Date width changed across the "
                f"reload ({stored_date} → {stored_after}); either it was not "
                "persisted or the heal is firing on a width that fits."
            )

            page.set_viewport_size(_NARROW_VIEWPORT)
            page.wait_for_timeout(400)
            narrow = page.evaluate(_READ_SHED)
            print(f"probe_status: s74 columns at 900 = {narrow}")
            page.set_viewport_size(_VIEWPORT)
            assert narrow["rowPresent"], (
                "slice C — the rows unmounted at a 900px viewport; the shed "
                "assertions below would pass for the wrong reason."
            )
            assert narrow["tableWidth"] and float(narrow["tableWidth"]) < float(
                wide["tableWidth"]
            ), (
                "slice C — the tree measured "
                f"{narrow['tableWidth']!r}px at a 900px viewport, no narrower "
                f"than the {wide['tableWidth']!r}px it reported at 1280. Either "
                "the ResizeObserver never re-measured or the tree is not the "
                "element being sized."
            )
            assert narrow["dims"] == 0 and narrow["date"] == 0, (
                f"slice C — Resolution ({narrow['dims']}) or Shot Date "
                f"({narrow['date']}) cells survive a {narrow['tableWidth']}px "
                "table, which cannot fit them; the filename is being squeezed "
                "instead of a column being given up."
            )
            assert narrow["size"] > 0 and narrow["action"] > 0, (
                "slice C — shedding took Size or Action with it "
                f"(size={narrow['size']}, action={narrow['action']}). Only dims "
                "and date are sheddable; Size is SORTABLE and a shed must never "
                "strand an active sort."
            )

            # ── 11. The COMPACT density (layout slice R) ──────────────────────
            # The preference is set the way the store hydrates it — its own
            # localStorage key, read once at store creation — so this exercises
            # the real cross-launch path rather than poking React state. The
            # toggle UI that will set it belongs to slice TB; the preference
            # and its effect are this slice's.
            page.evaluate(
                "(key) => localStorage.setItem(key, 'compact')", _DENSITY_KEY
            )
            page.reload()
            load_manifest(page, db_path)
            page.wait_for_timeout(600)

            compact = page.evaluate(
                _READ_ROW_GEOMETRY,
                [
                    row_testid,
                    row_decision_testid(group_id, basename),
                    row_lock_testid(group_id, basename),
                ],
            )
            print(f"probe_status: s74 row geometry (compact) = {compact}")
            assert compact is not None, (
                "slice R — no file row after re-hydrating at the compact "
                "density. A density value the store cannot map indexes the "
                "metrics table with `undefined` and every row renders with no "
                "height at all — a blank tree."
            )
            assert compact["density"] == "compact", (
                "slice R — the row reports density="
                f"{compact['density']!r} after the preference was persisted as "
                "'compact'; the store is not hydrating from localStorage."
            )
            assert compact["rowHeight"] == 52, (
                f"slice R — the compact row measures {compact['rowHeight']}px, "
                "expected 52. The virtualiser estimates 52 here, so a row that "
                "renders taller drifts the whole list."
            )
            assert compact["thumbWidth"] == 36 and compact["thumbRadius"] == "6px", (
                f"slice R — the compact thumbnail is {compact['thumbWidth']}px "
                f"at radius {compact['thumbRadius']!r}, expected 36px / 6px."
            )
            assert compact["decisionHeight"] == 28, (
                "slice R — the compact decision control is "
                f"{compact['decisionHeight']}px tall, expected 28."
            )
            assert compact["lockWidth"] == 24, (
                f"slice R — the compact padlock is {compact['lockWidth']}px "
                "wide, expected 24."
            )
            assert compact["scoreCellWidth"] == 72, (
                f"slice R — the compact score cell is "
                f"{compact['scoreCellWidth']}px, expected 72."
            )
            assert compact["scoreLabel"] is None, (
                "slice R — the compact score cell still renders its "
                f"{compact['scoreLabel']!r} label. «In compact, drop the "
                "'keep' label and keep the number» — at 72px the label and the "
                "number cannot share a line."
            )

            compact_last = page.evaluate(_READ_ROW_HEIGHT, last_testid)
            print(f"probe_status: s74 compact last-in-group row = {compact_last}")
            assert compact_last == 56, (
                f"slice R — the compact group's last row measures "
                f"{compact_last}px, expected 56 (52 + the 4px closing breath)."
            )

            # Slice G — the band is the ONE row that does not compress: «44px
            # (comfortable and compact — the group band does not compress)».
            # The density-keyed class table is exactly the shape that picks up
            # a neighbour's density by accident, and the virtualiser's
            # group-header branch is a single constant that would then be wrong.
            compact_group = page.evaluate(
                _READ_ROW_HEIGHT, row_group_testid(group_id)
            )
            print(f"probe_status: s74 compact group header = {compact_group}")
            assert compact_group == 44, (
                f"slice G — the group header measures {compact_group}px at the "
                "compact density, expected 44 at BOTH densities."
            )
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
