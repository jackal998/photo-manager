"""Web scenario s73 — Set-Action row seed (#893) + page-vs-tree scroll (#894).

WEB-ONLY. Both halves are defects the owner hit on the first trial of the web
client after the #646 cutover; neither has a desktop driver (the Qt client was
removed, and #894 is a CSS-layout property that has no Qt analog at all).

Half 1 — #894, the page must never be the scroller
--------------------------------------------------
The root div carried ``min-h-screen`` (a MINIMUM height), which leaves the
column flex container's main size indefinite: ``<main>``'s ``flex-1``
(``flex-basis: 0%``) then resolves against an indefinite size and falls back to
its CONTENT height, and ``ResultTree``'s ``h-full`` falls back to ``auto``. The
document grows past the viewport and the MenuBar / toolbar scroll away.

Measured on the base commit at 620x400 with a row selected: root 571 px against
a 400 px viewport; ``window.scrollTo(0, 1e5)`` then put the header at y=-138.
After the fix (``h-screen overflow-hidden``) the same steps give 400/400 and
the header stays at y=33.

Two details this scenario encodes deliberately:

* **A row must be SELECTED.** The result tree is ``contain: strict``
  (size-contained), so even 4918 px of virtualised rows contribute zero to
  layout — the tree alone can never trigger this. Only the preview pane's
  min-content height can, and the preview pane is empty until a row is picked.
  A scenario that only loads a manifest passes on the broken build.
* **A small viewport.** At 1280x720 the preview pane's ~424 px min-content
  height still fits inside ``main``, so the broken build looks fine. 620x400 is
  the smallest ordinary window where the defect is visible; it is the condition,
  not a trick.

Half 2 — #893, the Set-Action dialog seeds from the highlighted row
------------------------------------------------------------------
Qt seeded the matcher with the highlighted row's value for the selected field
(``_get_highlighted_row_values`` → ``ActionDialog(row_values=…)`` →
``_apply_exact_regex_for_current_field``, which stamps ``re.escape(value)`` so
the Simple row reverse-parses to ("contains", value)). The web port carried the
field half (#735) but not the value half, so every pattern started from an
empty box. This asserts the Simple text equals the right-clicked row's basename
and the regex line equals its escaped form.

It also fills the regex line afterwards and reads it back: both inputs are
CONTROLLED by the Zustand pattern, so a value that survives the round trip
proves the seed did not pin the control (a jsdom test cannot settle this for a
Radix dialog — project memory, "jsdom not proof for Radix").

Fixture: qa/sandbox/near-duplicates/ (5 JPEGs, one group), plain scan.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    click_context_item,
    right_click_row,
    run_scan,
)
from qa.web.testid_constants import (
    ACTION_DIALOG,
    ACTION_REGEX_INPUT,
    ACTION_SIMPLE_TEXT,
    CTX_SET_ACTION_BY_FIELD,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

# The window the #894 defect is visible in. See the module docstring: the
# preview pane's min-content height (~424 px on the base commit) has to exceed
# the height <main> is allotted, which it does not at 1280x720.
_VIEWPORT = {"width": 620, "height": 400}

# Mirrors frontend/src/lib/regexEscape.ts::escapeRegex — the set of characters
# the seed escapes. Kept as its own expression (not re.escape) because Python's
# re.escape and the JS helper do NOT agree on which characters need escaping
# (re.escape leaves '/' alone but escapes '-'; the JS set is the one shipped).
_JS_ESCAPE_RE = re.compile(r"[.*+?^${}()|\[\]\\]")


def _js_escape(text: str) -> str:
    return _JS_ESCAPE_RE.sub(lambda m: "\\" + m.group(0), text)


_MEASURE = """() => {
  const tree = document.querySelector('[data-testid="main-result-tree"]');
  const header = document.querySelector('header');
  return {
    docScrollHeight: document.documentElement.scrollHeight,
    innerHeight: window.innerHeight,
    treeScrollHeight: tree ? tree.scrollHeight : null,
    treeClientHeight: tree ? tree.clientHeight : null,
    headerTop: header ? Math.round(header.getBoundingClientRect().top) : null,
    scrollY: window.scrollY,
  };
}"""


def _get_manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def run(*, base_url: str) -> None:
    tmpdir = tempfile.mkdtemp(prefix="qa_s73_")
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
            item = group["items"][0]
            basename = Path(item["file_path"]).name
            row_testid = row_file_testid(group_id, basename)

            # ── #894 ──────────────────────────────────────────────────────────
            # Select a row so the preview pane renders — the only content that
            # can push the document past the viewport (see module docstring).
            page.get_by_test_id(row_testid).click()
            page.wait_for_timeout(1_500)

            before = page.evaluate(_MEASURE)
            print(f"probe_status: s73 #894 geometry after row select: {before}")

            assert before["docScrollHeight"] <= before["innerHeight"], (
                "#894 — the document is taller than the viewport "
                f"({before['docScrollHeight']} > {before['innerHeight']}), so the "
                "page itself scrolls and the MenuBar / toolbar leave the "
                "viewport. The root element must carry a DEFINITE height "
                "(h-screen), not min-h-screen."
            )
            assert (
                before["treeScrollHeight"] is not None
                and before["treeScrollHeight"] > before["treeClientHeight"]
            ), (
                "#894 — the result tree is not the scroller "
                f"(scrollHeight={before['treeScrollHeight']}, "
                f"clientHeight={before['treeClientHeight']}). The rows must "
                "overflow INSIDE the tree; otherwise the sticky column header "
                "(#685) has nothing to stay pinned to."
            )

            # The user-visible symptom, asserted directly: the page cannot be
            # scrolled at all, so the toolbar cannot be scrolled out of reach.
            page.evaluate("() => window.scrollTo(0, 100000)")
            page.wait_for_timeout(300)
            after_scroll = page.evaluate(_MEASURE)
            assert after_scroll["scrollY"] == 0, (
                "#894 — the document scrolled to "
                f"y={after_scroll['scrollY']}; the page must not be scrollable."
            )
            assert after_scroll["headerTop"] == before["headerTop"], (
                "#894 — the toolbar moved from "
                f"y={before['headerTop']} to y={after_scroll['headerTop']} when "
                "the page was scrolled. It must stay reachable while browsing."
            )

            # ── #893 ──────────────────────────────────────────────────────────
            # Back to an ordinary window first. #893 has nothing to do with the
            # viewport, and the row context menu is taller than 400 px, so at
            # the #894 window its lower items sit outside the viewport and
            # Playwright cannot click them. (That clipping is real — a context
            # menu opened near the bottom of a short window has unreachable
            # items — but it is a separate defect, not this scenario's subject.)
            page.set_viewport_size({"width": 1280, "height": 800})
            page.wait_for_timeout(300)

            # Right-click the row → "Set Action by Field…". No column is
            # threaded, so the dialog opens on its default field, File Name —
            # and the matcher must arrive pre-filled with THIS row's name.
            right_click_row(page, row_testid)
            click_context_item(page, CTX_SET_ACTION_BY_FIELD)
            page.get_by_test_id(ACTION_DIALOG).wait_for(state="visible", timeout=10_000)

            simple_text = page.get_by_test_id(ACTION_SIMPLE_TEXT).input_value()
            regex_text = page.get_by_test_id(ACTION_REGEX_INPUT).input_value()
            print(
                f"probe_status: s73 #893 seed for {basename!r}: "
                f"simple={simple_text!r} regex={regex_text!r}"
            )

            assert simple_text == basename, (
                "#893 — the Simple text box should open seeded with the "
                f"highlighted row's File Name {basename!r}, got {simple_text!r}. "
                "An empty box means the row values never reached the dialog."
            )
            assert regex_text == _js_escape(basename), (
                "#893 — the regex line should hold the ESCAPED row value "
                f"{_js_escape(basename)!r}, got {regex_text!r}. An unescaped "
                "seed would silently match more rows than the one the user "
                "right-clicked."
            )

            # The seed must not pin the control: a hand-typed pattern has to
            # survive the controlled round trip through the store.
            page.get_by_test_id(ACTION_REGEX_INPUT).fill("neardup_0")
            page.wait_for_timeout(300)
            assert page.get_by_test_id(ACTION_REGEX_INPUT).input_value() == "neardup_0", (
                "#893 — editing the regex line did not stick; the seed must be "
                "a starting value, not a lock."
            )
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
