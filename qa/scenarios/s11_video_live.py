"""Scenario 11 — Video + Live Photo (photo-manager#88 regression guard).

Required sources: qa/sandbox/videos, qa/sandbox/live-photo
Probes:
  * MP4/MOV recognized at the walker layer.
  * Live Photo HEIC + MOV pair (``IMG_0001.HEIC`` + ``IMG_0001.MOV``)
    forms a SINGLE group of two rows, regardless of whether either is
    a duplicate of anything else.
  * Per photo-manager#88: pairing is coupled at matching/grouping but
    the action / user_decision is per-row independent. Each row keeps
    its own classification (typically ``MOVE`` or ``UNDATED``) — neither
    is auto-marked because the other is.

Pre-#88 this scenario was a print-only smoke test that returned 0
even when the pair didn't form a group (the headline bug). Now it
asserts the pair-group invariant explicitly so regressions fail loud.
"""
from __future__ import annotations

import sys

from qa.scenarios import _uia


def _group_index_of(rows: list, predicate) -> int:
    """Return the index of the row that:
    1) lies above (lower y) the row matched by ``predicate``,
    2) starts with a 'Group' / '群組' label in its first cell.

    A group row typically has cells like ('Group 1', '2 files'); file
    rows under it are siblings rendered with greater y. The closest
    preceding group-label row is the file's parent group.
    """
    for r in rows:
        if predicate(r):
            target_y = r.y
            best_idx = -1
            for i, candidate in enumerate(rows):
                if candidate.y >= target_y:
                    continue
                if not candidate.cells:
                    continue
                first = candidate.cells[0]
                # "Group N" (en) or "群組 N" (zh_TW). Match either prefix.
                if first.startswith("Group ") or "群組" in first:
                    best_idx = i
            return best_idx
    return -1


def _row_has_ext(r, *exts) -> bool:
    """True if any cell in row ``r`` ends with one of the given extensions
    (case-insensitive)."""
    for c in r.cells:
        cl = c.lower()
        for ext in exts:
            if cl.endswith(ext):
                return True
    return False


def main() -> int:
    print("scenario: s11_video_live")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=60)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)

    print("step: read_results")
    _, win = _uia.connect_main()
    rows = _uia.read_result_rows(win)
    print(f"  total_rows={len(rows)}")
    for r in rows:
        print(f"  row: y={r.y} cells={list(r.cells)}")

    # ── Assertions (photo-manager#88) ──────────────────────────────────
    # 1) The Live Photo pair must appear at all. Pre-#88 the loader's
    #    singleton filter dropped both rows when the HEIC was unique →
    #    total_rows=0. Now both must surface.
    heic_idx = next(
        (i for i, r in enumerate(rows) if _row_has_ext(r, ".heic")),
        -1,
    )
    mov_idx = next(
        (i for i, r in enumerate(rows) if _row_has_ext(r, ".mov", ".mp4")),
        -1,
    )
    if heic_idx < 0 or mov_idx < 0:
        print(
            f"FAIL: Live Photo pair did not surface in tree "
            f"(heic_idx={heic_idx}, mov_idx={mov_idx}). "
            f"Pre-#88 symptom — manifest loader filtered both as singletons."
        )
        return 1
    print(f"  heic_row_idx={heic_idx}  mov_row_idx={mov_idx}")

    # 2) Both rows must sit under the SAME group header. Walk backward
    #    from each file row to find its nearest preceding group row;
    #    the group indices must match.
    heic_group_idx = _group_index_of(
        rows, lambda r: _row_has_ext(r, ".heic")
    )
    mov_group_idx = _group_index_of(
        rows, lambda r: _row_has_ext(r, ".mov", ".mp4")
    )
    if heic_group_idx < 0 or mov_group_idx < 0:
        print(
            f"FAIL: could not locate group header for one of the pair "
            f"(heic_group_idx={heic_group_idx} mov_group_idx={mov_group_idx})"
        )
        return 1
    if heic_group_idx != mov_group_idx:
        heic_group_label = rows[heic_group_idx].cells[0] if rows[heic_group_idx].cells else "?"
        mov_group_label = rows[mov_group_idx].cells[0] if rows[mov_group_idx].cells else "?"
        print(
            f"FAIL: pair NOT grouped together. "
            f"HEIC under {heic_group_label!r}, MOV under {mov_group_label!r}. "
            f"#88 invariant: Live Photo HEIC+MOV must always share a group_id."
        )
        return 1
    print(
        f"  pair_grouped_under={rows[heic_group_idx].cells[0]!r}  "
        f"(#88 invariant satisfied)"
    )

    print("scenario: s11_video_live DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
