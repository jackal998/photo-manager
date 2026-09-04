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
    ACTION_DIALOG,
    ACTION_MAIN_BUTTON,
    ACTION_RESIZE_HANDLE,
    ACTION_TITLE_BAR,
    CTX_SET_ACTION_DELETE,
    EXECUTE_DIALOG,
    EXECUTE_RESIZE_HANDLE,
    EXECUTE_TITLE_BAR,
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
    # Radix's open animation is 200ms and the dialogs carry `duration-200`, so
    # a shorter settle samples the box mid-flight — which also moves the resize
    # grip out from under the next gesture's press point. Measured: a 120ms
    # wait read x=-112 for a box that settled at -156.
    page.wait_for_timeout(400)


def _read_key(page, key: str):
    return page.evaluate(
        "(k) => { const v = localStorage.getItem(k); return v ? JSON.parse(v) : null; }",
        key,
    )


def _open_execute(page) -> None:
    open_execute_dialog(page)
    page.get_by_test_id(EXECUTE_DIALOG).wait_for(state="visible", timeout=10_000)
    page.wait_for_timeout(400)  # let the open animation settle before measuring


def _open_action(page) -> None:
    btn = page.get_by_test_id(ACTION_MAIN_BUTTON)
    btn.wait_for(state="visible", timeout=10_000)
    btn.click()
    page.get_by_test_id(ACTION_DIALOG).wait_for(state="visible", timeout=10_000)
    page.wait_for_timeout(400)  # let the open animation settle before measuring


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

        if failures:
            for f in failures:
                print(f"FAIL: {f}")
            raise AssertionError("; ".join(failures))
        print("scenario: s48_dialog_geometry_persist DONE")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
