"""Web scenario s48 — dialog geometry persists across close-and-reopen (#739).

Re-flip of the desktop-only SKIP. The old scenario_map note read "Web dialogs
are Radix / DOM overlays with no user-resizable geometry and no persistence
contract, so there is nothing to round-trip" — true until #739, whose owner
sign-off (2026-07-17) built one movable/resizable/persisted overlay mechanism
and applied it to all three surfaces. Divergence rows D6 (Execute Action
dialog) and D7 (Set Action dialog) in
``docs/audits/web-divergence-list-2026-07.md`` are exactly what this scenario
now covers.

Qt intent (``qa/scenarios/s48_dialog_geometry_persist.py``): for each
resizable dialog — capture the initial rect, resize it via Win32 MoveWindow,
close, reopen, assert the restored rect matches. The web port keeps the same
shape with real user gestures instead of Win32 calls:

  For the Execute Action dialog and then the Set Action dialog:
    1. Open it; record its default rect.
    2. Drag the TITLE BAR — assert the window moved.
    3. Drag the bottom-right RESIZE GRIP — assert the window grew.
    4. Escape to close, reopen — assert the rect came back (the Qt
       close-and-reopen round-trip, same session).
    5. Assert the rect is in localStorage under that surface's OWN key
       (``pm.overlay-geometry.execute.v1`` / ``…action.v1``).
  Then, once for both:
    6. ``page.reload()`` — the web's cross-launch boundary, which the desktop
       covers in s39 via an app restart — and assert both dialogs still open
       at their saved rects, and that the two keys hold DIFFERENT rects (one
       shared key would move both dialogs together).

Qt -> web divergences:
  - D-a (gesture): Win32 ``MoveWindow`` on a top-level HWND vs a real
    mouse drag on the app's own title bar / resize grip. There is no browser
    API to move a DOM overlay from outside it, and the drag IS the feature.
  - D-b (storage): QSettings INI keys ``geometry/scan_dialog`` /
    ``geometry/action_dialog_splitter`` vs per-surface localStorage keys.
  - D-c (ScanDialog): the desktop scenario also covers the Scan dialog. #739's
    sign-off scoped the web mechanism to the full-res viewer + these two
    dialogs, so the Scan dialog is deliberately NOT covered here — it stays a
    centered, non-movable Radix dialog. Do not "complete" this port by adding
    it; that would assert a feature nobody signed off on.
  - D-d (viewer): the full-res viewer's half of the same mechanism lives in
    s39_layout_persist, next to the preview-panel width it shares a ticket
    with.

Desktop source: qa/scenarios/s48_dialog_geometry_persist.py
Fixture:        qa/sandbox/near-duplicates
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    click_context_item,
    load_manifest,
    open_execute_dialog,
    right_click_row,
    run_scan,
)
from qa.web.testid_constants import (
    ACTION_BTN_APPLY,
    ACTION_DIALOG,
    ACTION_FIELD_COMBO,
    ACTION_MAIN_BUTTON,
    ACTION_REGEX_INPUT,
    ACTION_RESIZE_HANDLE,
    ACTION_TITLE_BAR,
    CTX_SET_ACTION_DELETE,
    EXECUTE_ALL_DELETE_BANNER,
    EXECUTE_BTN_EXECUTE,
    EXECUTE_DIALOG,
    EXECUTE_RESIZE_HANDLE,
    EXECUTE_TITLE_BAR,
    EXECUTE_TREE,
    row_file_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = str(_REPO / "qa" / "sandbox" / "near-duplicates")

_EXECUTE_KEY = "pm.overlay-geometry.execute.v1"
_ACTION_KEY = "pm.overlay-geometry.action.v1"

# Gesture deltas. Geometry is clamped fully inside the 1280x720 default
# Playwright viewport, and both dialogs are narrower than it, so these stay
# inside the clamp for either dialog.
_MOVE_DX = 100
_MOVE_DY = 30
_RESIZE_DX = 90
_RESIZE_DY = 40
# The rendered box must change by clearly more than sub-pixel noise, but the
# clamp may absorb part of a delta, so assert a floor rather than equality.
_MIN_DELTA_PX = 25
_TOL_PX = 6

# Round-2 review checks.
_FILE_NAME_FIELD = "File Name"
_MATCHING_REGEX = "neardup"
# One execute-tree row is ~56px; below this the tree is unusable and the fixed
# chrome has eaten the pinned box.
_MIN_TREE_HEIGHT_PX = 60


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _box(page, testid: str) -> dict:
    box = page.get_by_test_id(testid).bounding_box()
    if box is None:
        raise AssertionError(f"{testid} has no bounding box")
    return box


def _drag(page, testid: str, dx: float, dy: float) -> None:
    """Press in the middle of `testid` and drag by (dx, dy)."""
    handle = _box(page, testid)
    cx = handle["x"] + handle["width"] / 2
    cy = handle["y"] + handle["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx + dx, cy + dy, steps=8)
    page.mouse.up()
    # A short settle so the next gesture measures a stable box. The round-1
    # workaround here was 400ms, justified by a 120ms read of x=-112 for a box
    # that settled at -156 — that reading was the uncancelled Tailwind-v4
    # `translate` centering, fixed in the mechanism, not an entry animation:
    # probed live, the dialog reports 0 running animations 50ms after open.
    page.wait_for_timeout(200)


def _read_key(page, key: str):
    return page.evaluate(
        "(k) => { const v = localStorage.getItem(k); return v ? JSON.parse(v) : null; }",
        key,
    )


def _open_execute(page) -> None:
    open_execute_dialog(page)
    page.get_by_test_id(EXECUTE_DIALOG).wait_for(state="visible", timeout=10_000)
    page.wait_for_timeout(200)  # brief settle before measuring


def _open_action(page) -> None:
    btn = page.get_by_test_id(ACTION_MAIN_BUTTON)
    btn.wait_for(state="visible", timeout=10_000)
    btn.click()
    page.get_by_test_id(ACTION_DIALOG).wait_for(state="visible", timeout=10_000)
    page.wait_for_timeout(200)  # brief settle before measuring


def _close(page, dialog_testid: str) -> None:
    page.keyboard.press("Escape")
    page.get_by_test_id(dialog_testid).wait_for(state="hidden", timeout=5_000)


def _exercise(
    page,
    *,
    label: str,
    dialog_testid: str,
    title_testid: str,
    grip_testid: str,
    storage_key: str,
    reopen,
    failures: list[str],
) -> dict:
    """Open, move + resize one dialog, close, reopen, assert the rect came back.

    Returns the rect the dialog was left at, for the later reload assertions.
    """
    reopen(page)
    before = _box(page, dialog_testid)
    print(
        f"probe_status: s48 {label} default="
        f"{before['x']},{before['y']},{before['width']},{before['height']}"
    )

    _drag(page, title_testid, _MOVE_DX, _MOVE_DY)
    moved = _box(page, dialog_testid)
    print(
        f"probe_status: s48 {label} after drag="
        f"{moved['x']},{moved['y']},{moved['width']},{moved['height']}"
    )
    if abs(moved["x"] - before["x"]) < _MIN_DELTA_PX:
        failures.append(
            f"{label}: title-bar drag did not move the dialog "
            f"(x {before['x']} -> {moved['x']}) — the useOverlayGeometry move "
            f"gesture is not wired to {title_testid}."
        )

    _drag(page, grip_testid, _RESIZE_DX, _RESIZE_DY)
    resized = _box(page, dialog_testid)
    print(
        f"probe_status: s48 {label} after resize="
        f"{resized['x']},{resized['y']},{resized['width']},{resized['height']}"
    )
    if resized["width"] - moved["width"] < _MIN_DELTA_PX:
        failures.append(
            f"{label}: corner-grip drag did not resize the dialog "
            f"(width {moved['width']} -> {resized['width']}) — the resize "
            f"gesture is not wired to {grip_testid}."
        )

    stored = _read_key(page, storage_key)
    print(f"probe_status: s48 {label} persisted={stored}")
    if stored is None:
        failures.append(
            f"{label}: nothing written to localStorage['{storage_key}'] after "
            f"the gestures."
        )
    elif abs(stored["w"] - resized["width"]) > _TOL_PX:
        failures.append(
            f"{label}: persisted geometry {stored} does not match the rendered "
            f"box {resized}."
        )

    # Close-and-reopen WITHIN the session — the desktop scenario's contract.
    _close(page, dialog_testid)
    reopen(page)
    reopened = _box(page, dialog_testid)
    print(
        f"probe_status: s48 {label} reopened="
        f"{reopened['x']},{reopened['y']},{reopened['width']},{reopened['height']}"
    )
    for axis in ("x", "y", "width", "height"):
        if abs(reopened[axis] - resized[axis]) > _TOL_PX:
            failures.append(
                f"{label}: {axis} not restored on reopen: {reopened[axis]} != "
                f"{resized[axis]} (+-{_TOL_PX}) — geometry hydration on open "
                f"regressed."
            )
    _close(page, dialog_testid)
    return resized


def _assert_chrome_reachable(
    page, *, label: str, dialog_testid: str, button_testid: str, failures: list[str]
) -> None:
    """The dialog's primary button must be inside the box AND on screen.

    The failure this encodes (round-2 review finding 1/2): a pinned height with
    no scroll container pushes the footer out of the dialog and off the bottom
    of the viewport, so Apply / Execute cannot be clicked — and because the
    geometry persists, it recurs on every open.
    """
    dialog = _box(page, dialog_testid)
    button = _box(page, button_testid)
    viewport = page.viewport_size or {"width": 1280, "height": 720}
    d_bottom = dialog["y"] + dialog["height"]
    b_bottom = button["y"] + button["height"]
    print(
        f"probe_status: s48 {label} chrome dialog_bottom={d_bottom} "
        f"button_bottom={b_bottom} viewport_h={viewport['height']}"
    )
    if b_bottom > d_bottom + _TOL_PX:
        failures.append(
            f"{label}: the primary button's bottom ({b_bottom}) is BELOW the "
            f"dialog's own bottom ({d_bottom}) — content is spilling out of the "
            f"pinned box instead of the body scrolling."
        )
    if b_bottom > viewport["height"] + _TOL_PX:
        failures.append(
            f"{label}: the primary button's bottom ({b_bottom}) is off the "
            f"bottom of the {viewport['height']}px viewport — it cannot be "
            f"clicked."
        )


def _check_move_keeps_auto_height(page, *, base_url: str, failures: list[str]) -> None:
    """A MOVE must reposition the Set Action dialog, never pin its size.

    Round-2 finding 1. Open, drag the title bar, then type a matching pattern
    so the Preview block appears. On a mechanism that pins height at move time
    the dialog cannot grow, so the Preview pushes Cancel/Apply out of the box
    and off-screen. Asserts (a) the drag did not change the height, (b) the
    dialog GREW once the preview appeared, (c) Apply is still reachable.
    """
    _open_action(page)
    before = _box(page, ACTION_DIALOG)
    _drag(page, ACTION_TITLE_BAR, _MOVE_DX, _MOVE_DY)
    moved = _box(page, ACTION_DIALOG)
    print(
        f"probe_status: s48 action move-keeps-auto before_h={before['height']} "
        f"after_h={moved['height']}"
    )
    if abs(moved["height"] - before["height"]) > _TOL_PX:
        failures.append(
            f"action: a MOVE changed the dialog height "
            f"({before['height']} -> {moved['height']}). Moving a window must "
            f"not resize it; only the corner grip may pin a size."
        )

    # Make the Preview block appear — the content growth the pinned height
    # cannot absorb.
    page.get_by_test_id(ACTION_FIELD_COMBO).select_option(label=_FILE_NAME_FIELD)
    regex_input = page.get_by_test_id(ACTION_REGEX_INPUT)
    regex_input.wait_for(state="visible", timeout=5_000)
    regex_input.fill(_MATCHING_REGEX)
    page.wait_for_timeout(900)  # 300ms debounce + preview round-trip
    grown = _box(page, ACTION_DIALOG)
    print(
        f"probe_status: s48 action move-keeps-auto grown_h={grown['height']} "
        f"(preview visible)"
    )
    if grown["height"] <= moved["height"] + _TOL_PX:
        failures.append(
            f"action: the dialog did not grow when the Preview block appeared "
            f"({moved['height']} -> {grown['height']}) — its height is pinned "
            f"by the move gesture, so the new content has nowhere to go."
        )
    _assert_chrome_reachable(
        page,
        label="action after move+preview",
        dialog_testid=ACTION_DIALOG,
        button_testid=ACTION_BTN_APPLY,
        failures=failures,
    )

    # Outcome (b): now SHRINK it hard with the corner grip, so the preview
    # content is far taller than the box. The body must absorb that by
    # scrolling; without a scroll container the same overflow pushes Apply
    # straight out of the dialog.
    _drag(page, ACTION_RESIZE_HANDLE, -600, -600)
    shrunk = _box(page, ACTION_DIALOG)
    scroll = page.evaluate(
        "() => { const b = document.querySelector('[data-testid=\"action-body\"]');"
        " return b ? { s: b.scrollHeight, c: b.clientHeight } : null; }"
    )
    print(
        f"probe_status: s48 action shrunk-to-floor h={shrunk['height']} "
        f"body={scroll}"
    )
    if scroll is None:
        failures.append(
            "action: no scrollable body element (action-body) — overflow has "
            "nowhere to go at a pinned height."
        )
    elif scroll["s"] <= scroll["c"]:
        failures.append(
            f"action: the body is not scrolling at the minimum height "
            f"(scrollHeight={scroll['s']} <= clientHeight={scroll['c']}) — the "
            f"preview content should overflow a floor-height box."
        )
    _assert_chrome_reachable(
        page,
        label="action shrunk-to-floor",
        dialog_testid=ACTION_DIALOG,
        button_testid=ACTION_BTN_APPLY,
        failures=failures,
    )
    _close(page, ACTION_DIALOG)


def _check_pinned_height_scrolls(
    page, *, group_id: str, basenames: list[str], failures: list[str]
) -> None:
    """A too-small PINNED height must scroll the body, not evict the chrome.

    Round-2 finding 2. Seeds the execute key with a deliberately short height
    (the natural height measured on this fixture) and opens the dialog with the
    all-delete banner present, so the fixed chrome alone would exceed it.
    Asserts the tree keeps a usable height and Execute stays reachable.
    """
    # Stage the WHOLE group as delete so the all-delete banner is present —
    # the reviewer's condition: the banners are what exhaust a short box.
    for basename in basenames:
        right_click_row(page, row_file_testid(group_id, basename))
        click_context_item(page, CTX_SET_ACTION_DELETE)
    # Baseline: what the dialog looks like with NO stored geometry. The pinned
    # case must not be worse than this.
    _open_execute(page)
    base_dialog = _box(page, EXECUTE_DIALOG)
    base_tree = _box(page, EXECUTE_TREE)
    print(
        f"probe_status: s48 execute unpinned-baseline dialog_h="
        f"{base_dialog['height']} tree_h={base_tree['height']}"
    )
    _close(page, EXECUTE_DIALOG)

    page.evaluate(
        "(v) => localStorage.setItem('pm.overlay-geometry.execute.v1', v)",
        json.dumps({"x": 192, "y": 242, "w": 896, "h": 236}),
    )
    _open_execute(page)
    tree = _box(page, EXECUTE_TREE)
    dialog = _box(page, EXECUTE_DIALOG)
    banner = page.get_by_test_id(EXECUTE_ALL_DELETE_BANNER).count()
    diag = page.evaluate(
        "() => { const d = document.querySelector('[data-testid=\"execute-dialog\"]');"
        " const b = document.querySelector('[data-testid=\"execute-body\"]');"
        " return { styleH: d ? d.style.height : null,"
        " display: d ? d.style.display : null,"
        " bodyScroll: b ? b.scrollHeight : null,"
        " bodyClient: b ? b.clientHeight : null,"
        " stored: localStorage.getItem('pm.overlay-geometry.execute.v1') }; }"
    )
    print(f"probe_status: s48 execute pinned-short diag={diag}")
    # The body element must EXIST for overflow to have anywhere to go. Whether
    # it actually scrolls here depends on the fixture's content: at the 280px
    # floor this dialog's content happens to fit exactly (166/166 measured), so
    # the scrolling assertion lives on the Action dialog, whose preview block
    # genuinely overflows a floor-height box.
    if diag["bodyScroll"] is None or diag["bodyClient"] is None:
        failures.append(
            "execute: no scrollable body element (execute-body) at a pinned "
            "height — content has nowhere to overflow to."
        )
    print(
        f"probe_status: s48 execute pinned-short dialog_h={dialog['height']} "
        f"tree_h={tree['height']} all_delete_banner={banner}"
    )
    if tree["height"] < _MIN_TREE_HEIGHT_PX:
        failures.append(
            f"execute: at a pinned height of 236px the file tree collapsed to "
            f"{tree['height']}px (< {_MIN_TREE_HEIGHT_PX}px, about one row) — "
            f"the fixed chrome ate the whole box instead of the body scrolling."
        )
    _assert_chrome_reachable(
        page,
        label="execute pinned-short",
        dialog_testid=EXECUTE_DIALOG,
        button_testid=EXECUTE_BTN_EXECUTE,
        failures=failures,
    )
    _close(page, EXECUTE_DIALOG)
    page.evaluate("() => localStorage.removeItem('pm.overlay-geometry.execute.v1')")


def _check_drag_during_open_animation(page, *, failures: list[str]) -> None:
    """A drag started mid-open must seed from the SETTLED layout.

    Round-2 finding 3. Radix's 200ms zoom-in-95 entry animation makes
    getBoundingClientRect return a scaled, offset rect; a user who grabs the
    title bar 50ms after open would otherwise persist that wrong size/position.
    """
    btn = page.get_by_test_id(ACTION_MAIN_BUTTON)
    btn.wait_for(state="visible", timeout=10_000)
    btn.click()
    page.get_by_test_id(ACTION_DIALOG).wait_for(state="visible", timeout=10_000)
    page.wait_for_timeout(50)  # deliberately INSIDE the 200ms entry animation
    mid = _box(page, ACTION_DIALOG)
    running = page.evaluate(
        "(tid) => { const el = document.querySelector('[data-testid=\"'+tid+'\"]');"
        " return el ? el.getAnimations().length : -1; }",
        ACTION_DIALOG,
    )
    print(
        f"probe_status: s48 action mid-animation rect={mid['x']},{mid['y']},"
        f"{mid['width']},{mid['height']} running_animations={running}"
    )
    _drag(page, ACTION_TITLE_BAR, _MOVE_DX, _MOVE_DY)
    stored = _read_key(page, _ACTION_KEY)
    rendered = _box(page, ACTION_DIALOG)
    print(
        f"probe_status: s48 action mid-animation-drag stored={stored} "
        f"rendered_w={rendered['width']} rendered_h={rendered['height']}"
    )
    if stored is None:
        failures.append("action: a mid-animation drag persisted nothing.")
    else:
        # Whatever the gesture seeded from must match what the user now sees.
        if abs(rendered["x"] - stored["x"]) > _TOL_PX:
            failures.append(
                f"action: a drag started 50ms after open seeded from the "
                f"mid-animation rect — stored x={stored['x']} but the settled "
                f"dialog renders at x={rendered['x']}."
            )
    _close(page, ACTION_DIALOG)
    page.evaluate("() => localStorage.removeItem('pm.overlay-geometry.action.v1')")


def run(*, base_url: str) -> int:
    """Move+resize both dialogs, close/reopen, reload, assert both round-trip."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s48_")
    db_path = os.path.join(tmpdir, "manifest.db")
    failures: list[str] = []
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

            # The Execute button is gated on at least one staged decision.
            # Staging only — this scenario never clicks Execute, so the
            # read-only fixture is never touched.
            manifest = _get_manifest(base_url, db_path)
            group = manifest["groups"][0]
            group_id = str(group["group_number"])
            basename = Path(group["items"][0]["file_path"]).name
            right_click_row(page, row_file_testid(group_id, basename))
            click_context_item(page, CTX_SET_ACTION_DELETE)

            execute_rect = _exercise(
                page,
                label="execute",
                dialog_testid=EXECUTE_DIALOG,
                title_testid=EXECUTE_TITLE_BAR,
                grip_testid=EXECUTE_RESIZE_HANDLE,
                storage_key=_EXECUTE_KEY,
                reopen=_open_execute,
                failures=failures,
            )
            action_rect = _exercise(
                page,
                label="action",
                dialog_testid=ACTION_DIALOG,
                title_testid=ACTION_TITLE_BAR,
                grip_testid=ACTION_RESIZE_HANDLE,
                storage_key=_ACTION_KEY,
                reopen=_open_action,
                failures=failures,
            )

            # One shared key would move both dialogs together; the two rects
            # were dragged from different defaults, so they must differ.
            if (
                abs(execute_rect["x"] - action_rect["x"]) <= _TOL_PX
                and abs(execute_rect["width"] - action_rect["width"]) <= _TOL_PX
            ):
                failures.append(
                    f"execute and action ended at the same rect "
                    f"({execute_rect} vs {action_rect}) — the two surfaces may "
                    f"be sharing one storage key."
                )

            # ── Reload = the web cross-launch boundary ───────────────────────
            page.reload()
            load_manifest(page, db_path)

            _open_execute(page)
            after_reload_exec = _box(page, EXECUTE_DIALOG)
            print(
                f"probe_status: s48 execute after reload="
                f"{after_reload_exec['x']},{after_reload_exec['y']},"
                f"{after_reload_exec['width']},{after_reload_exec['height']}"
            )
            for axis in ("x", "y", "width", "height"):
                if abs(after_reload_exec[axis] - execute_rect[axis]) > _TOL_PX:
                    failures.append(
                        f"execute: {axis} not restored after reload: "
                        f"{after_reload_exec[axis]} != {execute_rect[axis]}."
                    )
            _close(page, EXECUTE_DIALOG)

            _open_action(page)
            after_reload_action = _box(page, ACTION_DIALOG)
            print(
                f"probe_status: s48 action after reload="
                f"{after_reload_action['x']},{after_reload_action['y']},"
                f"{after_reload_action['width']},{after_reload_action['height']}"
            )
            for axis in ("x", "y", "width", "height"):
                if abs(after_reload_action[axis] - action_rect[axis]) > _TOL_PX:
                    failures.append(
                        f"action: {axis} not restored after reload: "
                        f"{after_reload_action[axis]} != {action_rect[axis]}."
                    )
            _close(page, ACTION_DIALOG)

            # ── Round-2 review checks: move vs resize, pinned-height scroll,
            #    and seeding from the settled (not mid-animation) layout ──────
            page.evaluate(
                "() => { localStorage.removeItem('pm.overlay-geometry.action.v1');"
                " localStorage.removeItem('pm.overlay-geometry.execute.v1'); }"
            )
            _check_move_keeps_auto_height(page, base_url=base_url, failures=failures)
            page.evaluate(
                "() => localStorage.removeItem('pm.overlay-geometry.action.v1')"
            )
            _check_pinned_height_scrolls(
                page,
                group_id=group_id,
                basenames=[Path(f["file_path"]).name for f in group["items"]],
                failures=failures,
            )
            _check_drag_during_open_animation(page, failures=failures)

        if failures:
            for f in failures:
                print(f"FAIL: {f}")
            raise AssertionError("; ".join(failures))
        print("scenario: s48_dialog_geometry_persist DONE")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
