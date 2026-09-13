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
from qa.web._invariants import run_scan
from qa.web.testid_constants import (
    row_decision_option_testid,
    row_file_testid,
    row_group_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

_VIEWPORT = {"width": 1280, "height": 800}

# Daylight tokens, as the browser reports them. Kept as rgb() strings because
# that is what getComputedStyle returns; the hex each one is is in the comment.
_APP_BG = "rgb(245, 239, 230)"  # --color-app        #f5efe6
_DELETE_ROW_BG = "rgb(253, 243, 240)"  # --color-delete-row #fdf3f0
_GROUP_BAND_BG = "rgb(243, 233, 216)"  # --color-group-band #f3e9d8
_ACCENT = "rgb(189, 107, 57)"  # --color-warm       #bd6b39

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
                f"{group_style['leftColor']}, expected {_ACCENT} (#bd6b39)."
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
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
