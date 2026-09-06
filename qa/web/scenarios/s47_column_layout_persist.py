"""Web scenario s47 — result-tree column header: width persistence + scroll (#685/#699).

Ported from the Qt s47_column_layout_persist. The web result tree's column
widths are held in the resultView store slice and persisted to localStorage
(frontend/src/lib/columnWidths.ts) — the per-browser analog of the desktop's
per-machine window_state.ini. This scenario does the FULL round-trip end-to-end
through real user gestures (web mouse-drag is reliable in Playwright, so unlike
the Qt s47 we do NOT need to forge a saved-state blob via a sidecar):

  1. Drag the File Name column's resize handle right by a known delta and assert
     the rendered header width grew.
  2. Assert the new width was written to localStorage.
  3. ``page.reload()`` — the web cross-launch boundary (localStorage survives a
     reload but the loaded manifest does not) — then re-load the same manifest.
  4. Assert the File Name header restores to the resized width (the restore path
     reads localStorage at store creation; nothing on manifest load resets it).
  5. Assert the localStorage key survives the reload (the desktop "closeEvent
     must not wipe column_header" check).

Per-scenario isolation is free here: each scenario runs in its own Playwright
browser context, so localStorage starts empty and cannot leak between scenarios
(no server-side reset needed, unlike the shared settings.json in s23a/s23b).

#699 — Phase 6 adds the tall-manifest scroll coverage the web suite never had
(every other fixture fits on screen, so nothing ever scrolled the result tree).
The sticky header is a normal-flow sibling ABOVE the virtualizer's spacer inside
the same scroll container, so the row list starts one header-height into the
container's content. The virtualizer must be told that offset (`scrollMargin`),
or its windowing math is a header-height off — invisible today because
`overscan: 10` buffers ~720px of error, and therefore only catchable by
comparing coordinates, not by looking at the page. Phase 6 loads a synthetic
30-group / 60-row manifest and asserts:

  a. the tree really scrolls (non-vacuity — otherwise every check below is free);
  b. the virtualizer's own coordinate for each rendered row (its `start`,
     recovered as ``translateY + data-scroll-margin``) equals where the row
     actually sits in the scroll container's content — at scrollTop 0 AND
     mid-scroll. This is the #699 bug: on the pre-fix build every row is off by
     exactly the header height;
  c. the first row is not hidden behind the sticky header at scrollTop 0 (the
     opposite failure: a margin subtracted twice slides the list up under it);
  d. the header stays pinned to the top of the container while rows scroll.

Desktop source: qa/scenarios/s47_column_layout_persist.py
Fixture:        qa/sandbox/near-duplicates + a synthetic 30-group manifest
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    add_scan_source,
    load_manifest,
    open_scan_dialog,
    set_output_path,
    start_scan,
    wait_manifest_loaded,
)
from qa.web.testid_constants import (
    MAIN_RESULT_TREE,
    MAIN_STATUS_BAR,
    RESULT_COL_HEADER_ROW,
    col_header_testid,
    col_resize_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_SRC = str(_REPO / "qa" / "sandbox" / "near-duplicates")
_STORAGE_KEY = "pm.result-tree.column-widths.v1"

# Drag the handle this far right; assert the column grew by clearly more than
# noise. Tolerance covers sub-pixel rounding between the drag delta, the
# store's Math.round, and the rendered boundingBox.
_DELTA_PX = 120
_MIN_GROWTH_PX = 50
_TOL_PX = 6

# ── #699 tall-manifest phase ────────────────────────────────────────────────
# 30 two-member groups ≈ 30 * (34px group header + 2 * 72px file row) ≈ 5.3k px
# of content against a viewport in the hundreds — comfortably scrollable on any
# window size the harness uses, and the ~30 groups the issue asks for.
_TALL_GROUPS = 30
_TALL_PER_GROUP = 2
# Sub-pixel: browsers report fractional box geometry, and the virtualizer
# rounds measured row sizes. 1px absorbs that without absorbing the ~28px
# header-height offset the phase exists to catch.
_GEOM_TOL_PX = 1.0
# Below this the manifest is not taller than the viewport and every scroll
# assertion would pass without scrolling anything.
_MIN_SCROLLABLE_PX = 200


def _header_width(page, col_id: str) -> float:
    box = page.get_by_test_id(col_header_testid(col_id)).bounding_box()
    if box is None:
        raise AssertionError(f"header cell {col_id!r} has no bounding box")
    return box["width"]


def _wait_rows(page, n: int = 5) -> None:
    page.wait_for_function(
        "(n) => document.querySelectorAll('[data-testid^=\"row-file-\"]').length === n",
        arg=n,
        timeout=20_000,
    )


def _build_tall_manifest(files_dir: str, db_path: str) -> None:
    """Write a manifest with ``_TALL_GROUPS`` two-member groups.

    Synthesised rather than scanned: the scan pipeline cannot be made to emit
    30 distinct groups from any existing sandbox fixture (they are 5 files at
    most, and copies of one image collapse into a single group), and generating
    30 pHash-distinct images would add a slow, collision-prone fixture for a
    test that only cares about row COUNT. Every row still points at a real JPEG
    copied into ``files_dir``, so thumbnails resolve and the rendered row boxes
    are the production ones — the heights the geometry checks read.

    The manifest is written by the production ``write_manifest``, so the file
    the app opens is a real manifest (the API's own ``ensure_schema`` migration
    adds the is_locked/outcome columns on load), not a hand-rolled table that
    could drift from the schema.
    """
    from scanner.dedup import ManifestRow
    from scanner.manifest import write_manifest

    src = Path(_SRC) / "neardup_00_q95.jpg"
    rows: list[ManifestRow] = []
    for g in range(_TALL_GROUPS):
        # group_id is the canonical root path of the connected component; any
        # stable per-group string works, and ManifestRepository numbers groups
        # by sorted group_id — so g00..g29 render in that order.
        group_id = os.path.join(files_dir, f"g{g:02d}_a.jpg")
        for i in range(_TALL_PER_GROUP):
            name = f"g{g:02d}_{chr(ord('a') + i)}.jpg"
            path = os.path.join(files_dir, name)
            shutil.copy2(src, path)
            rows.append(
                ManifestRow(
                    source_path=path,
                    source_label="tall",
                    # First row of each group is the undecided "Ref" tier, the
                    # rest are its exact duplicates — the shape a real
                    # byte-identical pair produces.
                    action="" if i == 0 else "EXACT",
                    source_hash=f"tallhash{g:04d}",
                    phash=None,
                    hamming_distance=None if i == 0 else 0,
                    duplicate_of=None,
                    reason="qa s47 tall-manifest scroll fixture (#699)",
                    file_size_bytes=os.path.getsize(path),
                    group_id=group_id,
                )
            )
    write_manifest(rows, Path(db_path))


# Reads every quantity the #699 checks need in ONE evaluate, so all of them
# describe the same instant (row boxes, the header box and scrollTop must not
# be sampled across separate scroll frames).
#
# `clientTop` is the container's top border width: getBoundingClientRect().top
# is the BORDER box, while scrollTop is measured from the CONTENT box, so
# without it every offset below is off by the 1px border — right inside the
# tolerance this phase relies on.
_GEOMETRY_JS = """
() => {
  const tree = document.querySelector('[data-testid="%s"]');
  const header = document.querySelector('[data-testid="%s"]');
  if (!tree || !header) return null;
  const treeRect = tree.getBoundingClientRect();
  const headerRect = header.getBoundingClientRect();
  const contentTop = treeRect.top + tree.clientTop;
  const attr = tree.getAttribute('data-scroll-margin');
  const rows = Array.from(tree.querySelectorAll('[data-index]')).map((el) => {
    const m = /translateY\\((-?[0-9.]+)px\\)/.exec(el.style.transform || '');
    const r = el.getBoundingClientRect();
    return {
      index: Number(el.getAttribute('data-index')),
      translate_y: m ? Number(m[1]) : null,
      // where the row really sits, in the scroll container's content coords —
      // the same coordinate space scrollTop is expressed in.
      content_top: r.top - contentTop + tree.scrollTop,
      viewport_top: r.top,
    };
  });
  rows.sort((a, b) => a.index - b.index);
  return {
    scroll_top: tree.scrollTop,
    client_height: tree.clientHeight,
    scroll_height: tree.scrollHeight,
    scrollable: tree.scrollHeight - tree.clientHeight,
    // absent on a build that never passed scrollMargin — that IS the bug, and
    // 0 is exactly what such a build told the virtualizer.
    scroll_margin: attr === null ? 0 : Number(attr),
    header_height: headerRect.height,
    header_viewport_top: headerRect.top,
    header_viewport_bottom: headerRect.bottom,
    content_viewport_top: contentTop,
    rows: rows,
  };
}
""" % (MAIN_RESULT_TREE, RESULT_COL_HEADER_ROW)


def _geometry(page) -> dict:
    geo = page.evaluate(_GEOMETRY_JS)
    if geo is None:
        raise AssertionError(
            "result tree or its sticky column header is not in the DOM — the "
            "tall manifest did not render."
        )
    return geo


def _row_offsets(geo: dict) -> list[tuple[int, float]]:
    """Per row: (index, real content offset − the virtualizer's own coordinate).

    ``translate_y + scroll_margin`` reconstructs ``virtualItem.start`` exactly:
    the component renders each row at ``start - scrollMargin`` inside a spacer
    that itself begins ``scrollMargin`` into the content. So this difference is
    literally "how far the row is from where the virtualizer thinks it is" —
    0 when the coordinate spaces agree, one header height when #699 is present.
    """
    out: list[tuple[int, float]] = []
    for row in geo["rows"]:
        if row["translate_y"] is None:
            continue
        virtual_start = row["translate_y"] + geo["scroll_margin"]
        out.append((row["index"], row["content_top"] - virtual_start))
    return out


def _check_row_coordinates(geo: dict, where: str, failures: list[str]) -> float:
    """Assert every rendered row sits where the virtualizer says. Returns max |offset|."""
    offsets = _row_offsets(geo)
    if len(offsets) < 5:
        failures.append(
            f"{where}: only {len(offsets)} rows carry a translateY — the "
            f"coordinate check would be near-vacuous; expected the virtualizer "
            f"to render at least 5 of the 90 rows."
        )
        return 0.0
    worst_index, worst = max(offsets, key=lambda pair: abs(pair[1]))
    print(
        f"probe_status: s47 scroll {where} scroll_top={geo['scroll_top']:.1f} "
        f"header_h={geo['header_height']:.2f} scroll_margin={geo['scroll_margin']:.2f} "
        f"rows={len(offsets)} max_offset_px={worst:.2f} at_index={worst_index}"
    )
    if abs(worst) > _GEOM_TOL_PX:
        failures.append(
            f"{where}: row {worst_index} sits {worst:.2f}px away from the "
            f"coordinate the virtualizer holds for it (header_height="
            f"{geo['header_height']:.2f}, scrollMargin={geo['scroll_margin']:.2f}) "
            f"— the virtualizer's windowing space is offset from the scroll "
            f"container's; see #699."
        )
    return abs(worst)


def run(*, base_url: str) -> None:
    """Scan → drag-resize File Name → reload → assert width restores → #699 scroll."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s47_")
    db_path = os.path.join(tmpdir, "manifest.db")
    failures: list[str] = []
    try:
        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            open_scan_dialog(page)
            add_scan_source(page, _SRC, idx=0)
            set_output_path(page, db_path)
            start_scan(page)
            wait_manifest_loaded(page, timeout=120_000)
            _wait_rows(page)

            width_before = _header_width(page, "name")
            print(f"probe_status: s47 File Name width before={width_before}")

            # ── Drag the File Name resize handle right by _DELTA_PX ─────────
            handle = page.get_by_test_id(col_resize_testid("name"))
            hb = handle.bounding_box()
            if hb is None:
                raise AssertionError("File Name resize handle has no bounding box")
            cx = hb["x"] + hb["width"] / 2
            cy = hb["y"] + hb["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            page.mouse.move(cx + _DELTA_PX, cy, steps=8)
            page.mouse.up()
            page.wait_for_timeout(150)

            width_after = _header_width(page, "name")
            print(f"probe_status: s47 File Name width after drag={width_after}")
            if width_after < width_before + _MIN_GROWTH_PX:
                failures.append(
                    f"resize drag did not widen File Name: before={width_before} "
                    f"after={width_after} (expected +>{_MIN_GROWTH_PX}px) — the "
                    f"resize-handle drag wiring is broken."
                )

            persisted = page.evaluate(
                "(key) => { const v = localStorage.getItem(key); "
                "return v ? (JSON.parse(v).name ?? null) : null; }",
                _STORAGE_KEY,
            )
            print(f"probe_status: s47 persisted name width={persisted}")
            if persisted is None or abs(persisted - width_after) > _TOL_PX:
                failures.append(
                    f"resized width not persisted to localStorage: "
                    f"persisted={persisted} after={width_after}."
                )

            # ── Reload = cross-launch boundary; re-load the same manifest ──
            page.reload()
            load_manifest(page, db_path)
            _wait_rows(page)
            page.wait_for_timeout(150)

            width_restored = _header_width(page, "name")
            print(f"probe_status: s47 File Name width restored={width_restored}")
            if abs(width_restored - width_after) > _TOL_PX:
                failures.append(
                    f"width not restored after reload: restored={width_restored} "
                    f"!= resized={width_after} (±{_TOL_PX}) — the column-width "
                    f"hydration from localStorage on store creation regressed, "
                    f"or something resets widths on manifest load."
                )

            still = page.evaluate("(key) => localStorage.getItem(key)", _STORAGE_KEY)
            if not still:
                failures.append(
                    "localStorage column-widths key was wiped by the reload."
                )

            # ── #699: tall manifest → the tree actually scrolls ──────────────
            tall_dir = os.path.join(tmpdir, "tall")
            os.makedirs(tall_dir, exist_ok=True)
            tall_db = os.path.join(tmpdir, "tall.db")
            _build_tall_manifest(tall_dir, tall_db)
            load_manifest(page, tall_db, timeout=60_000)
            expected_files = _TALL_GROUPS * _TALL_PER_GROUP
            # load_manifest's wait matches the generic "N groups · M files"
            # status, which the PREVIOUS manifest already satisfies — it returns
            # on the stale text. Wait for this manifest's own counts instead.
            try:
                page.wait_for_function(
                    "(want) => (document.querySelector('[data-testid=\"%s\"]')"
                    "?.textContent || '') === want" % MAIN_STATUS_BAR,
                    arg=f"{_TALL_GROUPS} groups · {expected_files} files",
                    timeout=30_000,
                )
            except Exception:
                pass  # asserted (with the observed text) immediately below
            status = page.get_by_test_id(MAIN_STATUS_BAR).inner_text()
            print(f"probe_status: s47 tall manifest status={status!r}")
            if f"{_TALL_GROUPS} groups" not in status:
                failures.append(
                    f"tall manifest did not load as {_TALL_GROUPS} groups "
                    f"(status={status!r}) — the synthetic fixture or the "
                    f"grouping changed; the scroll checks below need the tall "
                    f"list they assume."
                )
            page.wait_for_function(
                "() => document.querySelectorAll('[data-index]').length > 5",
                timeout=20_000,
            )
            page.wait_for_timeout(250)  # let measureElement settle row heights

            geo = _geometry(page)
            print(
                f"probe_status: s47 scroll fixture groups={_TALL_GROUPS} "
                f"files={expected_files} client_h={geo['client_height']:.0f} "
                f"scroll_h={geo['scroll_height']:.0f} "
                f"scrollable={geo['scrollable']:.0f}"
            )
            if geo["scrollable"] < _MIN_SCROLLABLE_PX:
                failures.append(
                    f"result tree is not scrollable (scrollHeight-clientHeight="
                    f"{geo['scrollable']:.0f}px < {_MIN_SCROLLABLE_PX}) — the "
                    f"whole #699 phase would pass without ever scrolling."
                )

            # (b) at rest: the virtualizer's coordinates == the real layout.
            _check_row_coordinates(geo, "at scrollTop 0", failures)

            # (c) the first row must not be hidden UNDER the sticky header.
            first = next((r for r in geo["rows"] if r["index"] == 0), None)
            if first is None:
                failures.append("row index 0 is not rendered at scrollTop 0.")
            elif first["viewport_top"] < geo["header_viewport_bottom"] - _GEOM_TOL_PX:
                failures.append(
                    f"the first row is occluded by the sticky header at "
                    f"scrollTop 0: row top={first['viewport_top']:.2f} is above "
                    f"the header's bottom={geo['header_viewport_bottom']:.2f} — "
                    f"the list origin was shifted up under the header."
                )

            # ── scroll to the middle: header pinned, rows moved, coords hold ──
            before_top = geo["rows"][0]["viewport_top"] if geo["rows"] else 0.0
            target = geo["scrollable"] / 2
            page.evaluate(
                "(t) => { document.querySelector('[data-testid=\"%s\"]')"
                ".scrollTop = t; }" % MAIN_RESULT_TREE,
                target,
            )
            page.wait_for_timeout(400)  # scroll + re-window + re-measure
            mid = _geometry(page)

            if mid["scroll_top"] < _MIN_SCROLLABLE_PX / 2:
                failures.append(
                    f"scrollTop stayed at {mid['scroll_top']:.0f} after scrolling "
                    f"to {target:.0f} — the tree did not scroll, so the "
                    f"mid-scroll assertions are vacuous."
                )
            # (d) sticky: the header's top stays glued to the container's
            #     content top while the rows underneath move.
            header_drift = mid["header_viewport_top"] - mid["content_viewport_top"]
            print(
                f"probe_status: s47 scroll pinned header_drift_px={header_drift:.2f} "
                f"scroll_top={mid['scroll_top']:.0f}"
            )
            if abs(header_drift) > _GEOM_TOL_PX:
                failures.append(
                    f"the column header is not pinned after scrolling: its top "
                    f"is {header_drift:.2f}px from the container's content top "
                    f"(scrollTop={mid['scroll_top']:.0f}) — sticky positioning "
                    f"regressed."
                )
            mid_first = next((r for r in mid["rows"] if r["index"] == 0), None)
            if mid_first is not None and abs(mid_first["viewport_top"] - before_top) < 1:
                failures.append(
                    "row 0 did not move while the container scrolled — the rows "
                    "are not scrolling with the content."
                )

            # (b) again, mid-scroll: this is where a windowing offset shows up
            #     as late-mounted rows once overscan no longer hides it.
            _check_row_coordinates(mid, "mid-scroll", failures)

            # ── back to the top: no accumulated drift ───────────────────────
            page.evaluate(
                "() => { document.querySelector('[data-testid=\"%s\"]')"
                ".scrollTop = 0; }" % MAIN_RESULT_TREE
            )
            page.wait_for_timeout(400)
            back = _geometry(page)
            _check_row_coordinates(back, "back at scrollTop 0", failures)

        if failures:
            for f in failures:
                print(f"FAIL: {f}")
            raise AssertionError("; ".join(failures))
        print("scenario: s47_column_layout_persist DONE")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
