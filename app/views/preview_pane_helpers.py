"""Pure-logic helpers extracted from
:class:`app.views.preview_pane.PreviewPane`.

Extracted so the load-bearing decision logic (info-row build,
grid geometry, fit-to-window math, aspect-bucket sorting, token
routing, HTML formatting) is unit-testable against plain Python
without cascade-importing the Qt media stack.

Same extraction pattern previously used by ``action_handlers.py``
(#182), ``status_reporter_impl.py`` (#138, #140), ``empty_state.py``
(#137), ``main_window_helpers.py`` (#185 / #283), and
``group_media_controller_helpers.py`` (#185 / #285).

What lives here:

* :func:`format_info_html` — render ``(label, value)`` pairs as an
  HTML two-column table for ``QLabel`` (info-row display).
* :func:`build_info_rows` — assemble the list of label/value tuples
  the preview header / grid tiles display. Replaces the four
  near-duplicate inline loops in ``show_single`` / ``show_grid``.
* :func:`aspect_bucket_from_resolution` — landscape / square /
  portrait classifier; used by ``show_grid`` to sort videos by
  aspect before display.
* :func:`format_resolution_string` — ``(w, h) → "W*H" | ""`` with
  positive-only guard.
* :func:`compute_grid_geometry` — viewport-width × max-thumb-size
  → ``(cols, cell_size)`` packing math; the core layout decision
  for the grid view.
* :func:`compute_fit_width` — pixmap × viewport → scaled-target-width
  with positive-only guard. Used by ``_apply_single_pixmap_fit``
  to fit-on-width without distorting.
* :func:`classify_image_token` — ``"single|…" → "single"``,
  ``"grid|…" → "grid"``, anything else → ``None``. Used by
  ``on_image_loaded`` to route decoded thumbnails to the right
  display surface.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Iterable


# RAW formats whose dims must be read via rawpy (Qt's QImageReader
# has no DNG decoder; PIL returns embedded-thumbnail dims instead of
# sensor dims). Kept here so callers can share one source of truth.
RAW_EXTENSIONS: frozenset[str] = frozenset(
    (".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".rw2")
)


def format_info_html(rows: list[tuple[str, str]]) -> str:
    """Render (label, value) pairs as an HTML two-column table for
    ``QLabel``. Empty input returns an empty string (no ``<table>``
    wrapping so the label can suppress itself).

    Failure mode: a refactor that changes the empty-rows return
    from ``""`` to a stub HTML table would render an empty
    1x1 cell on every "no-info" preview — a visible UX regression
    that no rendering test catches without pixel diff.
    """
    if not rows:
        return ""
    cells = "".join(
        f"<tr><td style='color:#888;padding-right:8px;white-space:nowrap'>{lbl}</td>"
        f"<td>{val}</td></tr>"
        for lbl, val in rows
    )
    return f"<table>{cells}</table>"


def build_info_rows(
    name: str = "",
    folder: str = "",
    size_txt: str = "",
    creation_txt: str = "",
    shot_txt: str = "",
    resolution: str = "",
    duration_unknown: bool = False,
    t_func: Callable[..., str] | None = None,
) -> list[tuple[str, str]]:
    """Assemble the (label, value) tuples for a single-image / grid
    tile / single-video info display.

    Empty-string inputs are omitted from the output — the caller
    needn't pre-filter. The canonical ROW ORDER is:
    ``name`` → ``folder`` → ``size`` → ``resolution`` → ``creation``
    → ``shot`` → ``duration``. This order is what users see; a
    refactor that reorders silently swaps positions in the preview
    table.

    ``t_func`` defaults to ``infrastructure.i18n.t`` when ``None`` —
    accept the override so unit tests can supply a deterministic
    no-translation passthrough (i18n catalog dependence is the kind
    of thing that makes assertions brittle).

    Failure modes:

    * A refactor that swaps row order makes the preview table read
      e.g. ``shot`` before ``creation`` — visible regression.
    * A refactor that includes empty rows clutters the table with
      ``"Folder:" → ""`` entries — visible regression.
    * The size value is double-wrapped through ``preview.info_size_value``
      (which adds a "bytes" suffix); a refactor that drops the
      double-wrap would surface raw byte counts as "1024" instead
      of "1024 bytes" → user-visible.
    """
    if t_func is None:
        from infrastructure.i18n import t as _t
        t_func = _t

    rows: list[tuple[str, str]] = []
    if name:
        rows.append((t_func("preview.info_name"), name))
    if folder:
        rows.append((t_func("preview.info_folder"), folder))
    if size_txt:
        rows.append(
            (t_func("preview.info_size"), t_func("preview.info_size_value", bytes=size_txt))
        )
    if resolution:
        rows.append((t_func("preview.info_resolution"), resolution))
    if creation_txt:
        rows.append((t_func("preview.info_created"), creation_txt))
    if shot_txt:
        rows.append((t_func("preview.info_shot"), shot_txt))
    if duration_unknown:
        rows.append(
            (t_func("preview.info_duration"), t_func("preview.info_duration_unknown"))
        )
    return rows


def aspect_bucket_from_resolution(res: str) -> int:
    """Classify a resolution string ``"W*H"`` as landscape (0),
    square / unknown (1), or portrait (2).

    Used by :meth:`PreviewPane.show_grid` to sort video tiles by
    aspect-ratio bucket before display so landscape videos cluster
    first, then square, then portrait. Pure parsing + comparison.

    Falsy ``res`` → 1 (square/unknown). Malformed input → 1
    (defensive: a corrupt resolution shouldn't crash the grid build).

    Failure mode: a refactor that inverts ``w > h`` vs ``w < h``
    would put portrait first and landscape last — the sort still
    works (groups stay together) but the visual order swaps,
    breaking the "wide images first" reading pattern.
    """
    if not res:
        return 1
    try:
        w_str, h_str = res.split("*")
        w, h = int(w_str), int(h_str)
        if w > h:
            return 0
        if w == h:
            return 1
        return 2
    except (ValueError, AttributeError):
        return 1


def format_resolution_string(width: int, height: int) -> str:
    """Return ``"{w}*{h}"`` when both dims are positive, else ``""``.

    Used by :meth:`PreviewPane.show_grid` after ``_image_dims``
    returns. The empty-string contract is load-bearing — the
    consumer ``aspect_bucket_from_resolution`` treats empty as
    "unknown" and the ``build_info_rows`` consumer suppresses the
    resolution line entirely.
    """
    if width > 0 and height > 0:
        return f"{width}*{height}"
    return ""


def compute_grid_geometry(
    viewport_width: int,
    thumb_size_max: int,
    spacing: int,
    min_px: int,
) -> tuple[int, int]:
    """Pack the largest possible square thumbnails into ``viewport_width``
    given ``spacing`` between columns, a per-cell ``min_px`` floor,
    and a per-cell ``thumb_size_max`` ceiling. Returns ``(cols,
    cell_size)``.

    Iterates from 1 column upward; stops when the per-cell size
    would drop below ``min_px``. Cell size is clamped to
    ``thumb_size_max``. The starting state is ``(1, min_px)`` so a
    pathologically narrow viewport still returns something usable.

    Pulled from :meth:`PreviewPane._compute_grid_geometry`. Pure
    math — the original method just reads ``viewport.width()`` and
    ``self._thumb_size`` and delegates to this shape.

    Failure modes:

    * A refactor that drops the ``< min_px`` break condition would
      compute cells smaller than the legibility floor, producing
      unreadable thumbnails on narrow viewports.
    * A refactor that drops the ``min(cell, max_px)`` clamp would
      let a wide viewport produce ENORMOUS single-column thumbnails
      that overflow the scroll area.
    * The "non-strict ``>=``" comparison on ``cand >= best_cell``
      is intentional — it lets MORE columns win on a tie. A refactor
      to strict ``>`` would prefer the FIRST tied option (1 column)
      over later options (2, 3, ...) — visibly wrong on viewports
      that pack evenly into multiple columns at the max cell size.
    """
    width = max(1, viewport_width)
    max_px = thumb_size_max if thumb_size_max > 0 else 600
    best_cols = 1
    best_cell = min_px
    for cols in range(1, 64):
        total_spacing = spacing * (cols - 1)
        cell = (width - total_spacing) // cols
        if cell < min_px:
            break
        cand = min(cell, max_px)
        if cand >= best_cell:
            best_cell = cand
            best_cols = cols
    return best_cols, best_cell


def compute_fit_width(pixmap_width: int, viewport_width: int) -> int:
    """Return the target width to scale a pixmap to so it fits the
    viewport on width without exceeding its natural size.

    Contract: ``min(pixmap_width, viewport_width - 1)`` with a
    positive-only guard. The ``- 1`` is to avoid the horizontal
    scrollbar appearing on an exact-fit (Qt's scroll-area treats
    "equal" as overflow on some platforms). When the computation
    underflows to ≤ 0 (e.g. viewport of 1px), fall back to the
    pixmap's natural width so we don't render a zero-width image.

    Pulled from :meth:`PreviewPane._apply_single_pixmap_fit`.

    Failure mode: a refactor that drops the positive-only guard
    would scale to 0 on a degenerate viewport, producing a blank
    preview the user can't recover without resizing the window.
    """
    target = min(pixmap_width, viewport_width - 1)
    if target <= 0:
        return pixmap_width
    return target


def get_file_size_bytes(path: str) -> int:
    """Return file size in bytes, or ``0`` if the file can't be read.

    Pulled from the ``_size_key`` closure inside
    :meth:`PreviewPane.show_grid`. Used as a sort key for grid tile
    ordering (larger files first). On any OS error (missing file,
    permission denied), returns 0 so the file sorts last — better
    than crashing the entire grid build on a transient FS issue.
    """
    try:
        return int(os.path.getsize(path))
    except (OSError, ValueError):
        return 0


def normalize_grid_items(
    items: Iterable[Any],
    path_normalizer: Callable[[str], str],
) -> list[tuple[str, str, str, str, str, str, str]]:
    """Convert mixed-arity input tuples (4 or 6 elements) into the
    canonical 7-tuple shape
    ``(path, name, folder, size, creation, shot, resolution)``.

    The 4-element form is the legacy grid-item shape (path, name,
    folder, size); the 6-element form added creation + shot dates;
    the 7-element form (this output) reserves the last slot for
    the resolution string attached downstream by
    :func:`attach_resolutions`.

    ``path_normalizer`` is the OS-specific path canonicaliser
    (``app.views.media_utils.normalize_windows_path``). Injected so
    the helper stays testable without importing that module.

    Failure mode: a refactor that drops the ``c or ""`` /
    ``sh or ""`` falsy-coalescing would leak ``None`` into the
    downstream display chain, where it'd render as the literal
    string "None" in the info table.
    """
    out: list[tuple[str, str, str, str, str, str, str]] = []
    for it in items:
        if len(it) == 4:
            p, n, f, s = it
            out.append((path_normalizer(p), n, f, s, "", "", ""))
        else:
            p, n, f, s, c, sh = it
            out.append((path_normalizer(p), n, f, s, c or "", sh or "", ""))
    return out


def attach_resolutions(
    items: Iterable[tuple[str, str, str, str, str, str, str]],
    dim_reader: Callable[[str], tuple[int, int]],
    is_video_predicate: Callable[[str], bool],
) -> list[tuple[str, str, str, str, str, str, str]]:
    """Walk ``items`` and replace the empty resolution slot
    (last element) with the formatted ``"W*H"`` string for image
    files, or ``""`` for videos.

    ``dim_reader`` reads (width, height) for an image path —
    in production this is ``read_image_dimensions``; in tests
    a deterministic stub. Returns 7-tuples with the resolution
    slot populated.

    Failure mode: a refactor that calls ``dim_reader`` on video
    paths (no early skip) would block the grid build on real
    media I/O — videos report 0×0 from the image reader, so the
    result is the same, but the latency hit on a 4K MP4 is
    measurable.
    """
    out: list[tuple[str, str, str, str, str, str, str]] = []
    for it in items:
        p = it[0]
        if not is_video_predicate(p):
            w, h = dim_reader(p)
            res = format_resolution_string(w, h)
        else:
            res = ""
        out.append((p, it[1], it[2], it[3], it[4], it[5], res))
    return out


def classify_image_token(token: str) -> str | None:
    """Route a decoded-thumbnail token to the right display surface.

    Tokens follow the convention ``"<surface>|<payload>"``:

    * ``"single|<path>"`` → returns ``"single"`` — display in the
      large single-preview area.
    * ``"grid|<path>"`` → returns ``"grid"`` — display in the
      corresponding grid-tile label (looked up by full token).
    * Anything else → ``None`` (ignore — defensive against
      malformed payloads from a future runner change).

    Pulled from :meth:`PreviewPane.on_image_loaded`. The owner's
    2026-05-16 comment specifically called out the
    "token-mismatch race" failure mode (an in-flight thumbnail
    arriving after the user clicked away to a different file). The
    classification step is the front edge of the routing — without
    it, a renamed token prefix silently drops every load.
    """
    if not isinstance(token, str):
        return None
    if token.startswith("single|"):
        return "single"
    if token.startswith("grid|"):
        return "grid"
    return None
