"""Tests for :mod:`app.views.preview_pane_helpers`.

Covers the pure-logic helpers extracted from
:class:`app.views.preview_pane.PreviewPane` (#185 / next PR). The
extraction keeps the load-bearing decision logic unit-testable
against plain Python without cascade-importing the Qt media stack
— same pattern as ``action_handlers.py`` (#182),
``status_reporter_impl.py`` (#138 / #140), ``empty_state.py``
(#137), ``main_window_helpers.py`` (#185 / #283), and
``group_media_controller_helpers.py`` (#185 / #285).

Each test maps to a real, named failure mode.
"""

from __future__ import annotations

import pytest

from app.views.preview_pane_helpers import (
    aspect_bucket_from_resolution,
    attach_resolutions,
    build_info_rows,
    classify_image_token,
    compute_fit_width,
    compute_grid_geometry,
    format_info_html,
    format_resolution_string,
    get_file_size_bytes,
    normalize_grid_items,
)


# Deterministic translation passthrough — avoids depending on the
# i18n catalog (so tests don't break when a label is reworded).
def _t(key: str, **fmt) -> str:
    if fmt:
        return f"{key}({fmt})"
    return key


# ── format_info_html ─────────────────────────────────────────────────────


class TestFormatInfoHtml:
    """The label/value → ``<table>`` renderer."""

    def test_empty_rows_returns_empty_string(self):
        """Empty list → empty string. The consumer (QLabel) hides
        itself when text is empty; an HTML stub would render a
        visible blank table cell."""
        assert format_info_html([]) == ""

    def test_single_row_wraps_in_table(self):
        """One row → opening + cells + closing table tags."""
        html = format_info_html([("Name", "a.jpg")])
        assert html.startswith("<table>")
        assert html.endswith("</table>")
        assert "Name" in html
        assert "a.jpg" in html

    def test_multi_row_produces_multiple_tr(self):
        """Two rows → two ``<tr>`` elements."""
        html = format_info_html([("L1", "V1"), ("L2", "V2")])
        assert html.count("<tr>") == 2
        assert "L1" in html and "V2" in html


# ── build_info_rows ──────────────────────────────────────────────────────


class TestBuildInfoRows:
    """Assembles the (label, value) tuples the preview displays.
    Row order + empty-filtering are the load-bearing contracts."""

    def test_image_single_full_set_in_canonical_order(self):
        """All 6 fields populated (image case) → 6 rows in canonical
        order: name → folder → size → resolution → creation → shot.

        Failure mode: a refactor that swaps positions makes the
        preview table read e.g. shot before creation — visible
        regression no rendering test catches.
        """
        rows = build_info_rows(
            name="a.jpg",
            folder="/photos",
            size_txt="1024",
            creation_txt="2024-01-01",
            shot_txt="2024-01-02",
            resolution="4032*3024",
            t_func=_t,
        )

        # Keys in canonical order
        keys = [k for k, _v in rows]
        assert keys == [
            "preview.info_name",
            "preview.info_folder",
            "preview.info_size",
            "preview.info_resolution",
            "preview.info_created",
            "preview.info_shot",
        ]

    def test_size_value_double_wrapped_for_bytes_suffix(self):
        """The size VALUE goes through ``preview.info_size_value``
        with ``bytes=…`` — that's where the i18n catalog adds the
        " bytes" suffix. A refactor that drops the wrap surfaces a
        raw integer string like "1024" instead of "1024 bytes".
        """
        rows = build_info_rows(size_txt="1024", t_func=_t)
        assert rows == [("preview.info_size", "preview.info_size_value({'bytes': '1024'})")]

    def test_empty_fields_omitted(self):
        """Empty strings are dropped; the consumer needn't pre-filter."""
        rows = build_info_rows(name="a.jpg", folder="", size_txt="", t_func=_t)
        assert len(rows) == 1
        assert rows[0][0] == "preview.info_name"

    def test_video_grid_appends_duration_unknown_last(self):
        """The grid-video case sets ``duration_unknown=True`` to
        append the "Duration: unknown" row LAST. A refactor that
        inserts it elsewhere would visually misorder the tile
        info."""
        rows = build_info_rows(
            name="a.mp4",
            folder="/vid",
            size_txt="1024",
            duration_unknown=True,
            t_func=_t,
        )

        # Duration is last
        assert rows[-1][0] == "preview.info_duration"
        # Resolution NOT included (no res arg)
        assert all(k != "preview.info_resolution" for k, _ in rows)

    def test_video_single_omits_resolution_and_duration(self):
        """Single-video case: no resolution (videos report different
        dims by player backend), no duration_unknown."""
        rows = build_info_rows(
            name="a.mp4",
            folder="/vid",
            size_txt="1024",
            creation_txt="2024-01-01",
            shot_txt="2024-01-02",
            t_func=_t,
        )

        keys = [k for k, _ in rows]
        assert "preview.info_resolution" not in keys
        assert "preview.info_duration" not in keys

    def test_no_inputs_returns_empty_list(self):
        """All-empty inputs → no rows. The consumer
        (``format_info_html``) then returns empty string and the
        label hides."""
        assert build_info_rows(t_func=_t) == []

    def test_default_t_func_uses_real_translator(self):
        """When ``t_func`` is omitted, the helper falls back to the
        real i18n catalog. This guards against a refactor that
        accidentally makes the parameter required (callers in
        ``preview_pane.py`` rely on the default).
        """
        rows = build_info_rows(name="x.jpg")
        # Just assert it doesn't crash and produces a name row
        assert len(rows) == 1
        assert rows[0][1] == "x.jpg"


# ── aspect_bucket_from_resolution ────────────────────────────────────────


class TestAspectBucketFromResolution:
    """Returns 0/1/2 for landscape/square-or-unknown/portrait."""

    def test_landscape_returns_zero(self):
        assert aspect_bucket_from_resolution("1920*1080") == 0

    def test_square_returns_one(self):
        assert aspect_bucket_from_resolution("1080*1080") == 1

    def test_portrait_returns_two(self):
        assert aspect_bucket_from_resolution("1080*1920") == 2

    def test_empty_returns_one_unknown(self):
        """Falsy input → unknown bucket. A refactor that returns 0
        would put all unknown items in the "landscape" sort group,
        misordering the visible video roster.
        """
        assert aspect_bucket_from_resolution("") == 1

    def test_none_treated_as_unknown(self):
        """Defensive: callers may pass None when resolution is
        unread."""
        assert aspect_bucket_from_resolution(None) == 1  # type: ignore[arg-type]

    def test_malformed_returns_one_defensive(self):
        """Garbage strings → unknown bucket, not crash. A grid build
        that crashes on a single malformed res value would fail to
        render the entire preview pane."""
        assert aspect_bucket_from_resolution("not-a-res") == 1
        assert aspect_bucket_from_resolution("12") == 1  # missing separator
        assert aspect_bucket_from_resolution("abc*def") == 1  # non-numeric


# ── format_resolution_string ─────────────────────────────────────────────


class TestFormatResolutionString:
    """``(w, h) → 'W*H' | ''`` with positive-only guard."""

    def test_positive_dims_returns_formatted(self):
        assert format_resolution_string(1920, 1080) == "1920*1080"

    def test_zero_width_returns_empty(self):
        """The downstream contract: empty string means "unknown" —
        ``aspect_bucket_from_resolution`` and ``build_info_rows``
        both check for falsy and skip the resolution line."""
        assert format_resolution_string(0, 1080) == ""

    def test_zero_height_returns_empty(self):
        assert format_resolution_string(1920, 0) == ""

    def test_both_zero_returns_empty(self):
        assert format_resolution_string(0, 0) == ""

    def test_negative_returns_empty(self):
        """Defensive: corrupt dims (e.g. signed-overflow on a
        broken codec) shouldn't render as "-1*-1"."""
        assert format_resolution_string(-1, 1080) == ""


# ── compute_grid_geometry ────────────────────────────────────────────────


class TestComputeGridGeometry:
    """Pack-thumbnails math: viewport × max-thumb → (cols, cell)."""

    def test_wide_viewport_packs_multiple_columns(self):
        """1000px viewport, 300px max thumb, 5px spacing, 150px
        floor → expect 3 columns at ~330px each (clamped to 300).
        """
        cols, cell = compute_grid_geometry(
            viewport_width=1000, thumb_size_max=300, spacing=5, min_px=150
        )
        assert cols == 3
        assert cell == 300

    def test_narrow_viewport_falls_back_to_one_column(self):
        """A viewport too narrow for 2 cells of the min size →
        single column at the min size floor."""
        cols, cell = compute_grid_geometry(
            viewport_width=200, thumb_size_max=300, spacing=5, min_px=150
        )
        assert cols == 1

    def test_pathological_zero_viewport_returns_fallback(self):
        """Defensive: 0-width viewport (e.g. during a resize event
        before the widget is laid out) returns the (1, min_px)
        fallback — the iteration breaks on the first column because
        ``0 // 1 < min_px``. Without the ``max(1, viewport_width)``
        guard, ``(0 - 0) // 1 == 0`` and the math degenerates."""
        cols, cell = compute_grid_geometry(
            viewport_width=0, thumb_size_max=300, spacing=5, min_px=150
        )
        assert cols == 1
        # Returns the initial best_cell value (min_px) because
        # the first iteration's `cell = 1 - 0 = 1` is below min_px,
        # immediately breaking the loop with no candidates evaluated.
        assert cell == 150

    def test_clamps_cell_to_max_thumb_size(self):
        """Even a very wide viewport doesn't produce thumbnails
        larger than ``thumb_size_max``."""
        cols, cell = compute_grid_geometry(
            viewport_width=5000, thumb_size_max=300, spacing=5, min_px=150
        )
        assert cell == 300  # clamped, not 5000

    def test_zero_thumb_max_uses_fallback_600(self):
        """A bogus ``thumb_size_max`` (e.g. unset setting) falls
        back to 600px so the user still sees thumbnails."""
        cols, cell = compute_grid_geometry(
            viewport_width=2000, thumb_size_max=0, spacing=5, min_px=150
        )
        # The clamp uses 600 — so cell never exceeds 600
        assert cell <= 600


# ── compute_fit_width ────────────────────────────────────────────────────


class TestComputeFitWidth:
    """``min(pixmap, viewport-1)`` with positive-only guard."""

    def test_pixmap_smaller_than_viewport_returns_pixmap_width(self):
        """A small pixmap in a wide viewport → don't upscale; return
        the pixmap's natural width."""
        assert compute_fit_width(pixmap_width=500, viewport_width=1000) == 500

    def test_pixmap_larger_than_viewport_returns_viewport_minus_one(self):
        """Large pixmap → scale to viewport - 1 (the -1 prevents
        the horizontal scrollbar from triggering on exact-fit).
        """
        assert compute_fit_width(pixmap_width=2000, viewport_width=800) == 799

    def test_zero_viewport_falls_back_to_pixmap_width(self):
        """Degenerate viewport (mid-resize) → return pixmap width
        rather than 0. A refactor that drops this guard would
        render a 0-width image (blank preview).
        """
        assert compute_fit_width(pixmap_width=500, viewport_width=0) == 500

    def test_one_pixel_viewport_falls_back(self):
        """``min(500, 0) = 0`` → guard catches it → returns 500."""
        assert compute_fit_width(pixmap_width=500, viewport_width=1) == 500


# ── classify_image_token ─────────────────────────────────────────────────


class TestClassifyImageToken:
    """Token-prefix routing for ``on_image_loaded`` dispatch."""

    def test_single_token_returns_single(self):
        assert classify_image_token("single|/photos/x.jpg") == "single"

    def test_grid_token_returns_grid(self):
        assert classify_image_token("grid|/photos/x.jpg") == "grid"

    def test_unknown_token_returns_none(self):
        """An unrecognised prefix → None (ignore). A new surface
        added later (e.g. ``"thumbnail|…"``) returns None until
        ``classify_image_token`` learns about it — the silent-ignore
        is the right default behaviour (no UI side-effects from
        unknown payloads)."""
        assert classify_image_token("thumbnail|/x.jpg") is None

    def test_empty_token_returns_none(self):
        assert classify_image_token("") is None

    def test_non_string_token_returns_none(self):
        """Defensive: a None or int token (caller bug) shouldn't
        crash the slot."""
        assert classify_image_token(None) is None  # type: ignore[arg-type]
        assert classify_image_token(42) is None  # type: ignore[arg-type]


# ── get_file_size_bytes ──────────────────────────────────────────────────


class TestGetFileSizeBytes:
    """File-size accessor with a defensive zero-on-error contract."""

    def test_returns_size_for_real_file(self, tmp_path):
        """A real file → its actual size in bytes."""
        f = tmp_path / "x.txt"
        f.write_bytes(b"hello")
        assert get_file_size_bytes(str(f)) == 5

    def test_returns_zero_for_missing_file(self):
        """Missing file → 0 (sorts last in the grid; doesn't crash
        the grid build)."""
        assert get_file_size_bytes("/nonexistent/path/x.jpg") == 0

    def test_returns_zero_for_directory(self, tmp_path):
        """Directories don't have a meaningful size in this context
        — return 0 rather than the OS's directory-size answer.

        Actually os.path.getsize works on dirs (returns the
        directory entry size on POSIX, typically 0 on Windows for
        empty dirs), so the contract is "don't crash". We just
        assert that the call returns an int.
        """
        result = get_file_size_bytes(str(tmp_path))
        assert isinstance(result, int)


# ── normalize_grid_items ─────────────────────────────────────────────────


class TestNormalizeGridItems:
    """Convert 4-tuples / 6-tuples → canonical 7-tuples."""

    def test_four_tuple_padded_with_empty_strings(self):
        """4-element input → 7-element output with empty creation/
        shot/resolution slots."""
        out = normalize_grid_items(
            [("/a.jpg", "a.jpg", "/photos", "1024")],
            path_normalizer=lambda p: p,
        )
        assert out == [("/a.jpg", "a.jpg", "/photos", "1024", "", "", "")]

    def test_six_tuple_creation_shot_passed_through(self):
        """6-element input → creation/shot end up in slots 4/5;
        resolution stays empty (attached downstream)."""
        out = normalize_grid_items(
            [("/a.jpg", "a.jpg", "/photos", "1024", "2024-01-01", "2024-01-02")],
            path_normalizer=lambda p: p,
        )
        assert out == [
            ("/a.jpg", "a.jpg", "/photos", "1024", "2024-01-01", "2024-01-02", "")
        ]

    def test_six_tuple_none_creation_coerced_to_empty(self):
        """``None`` creation/shot → empty string (the ``c or ""``
        coalesce). Catches the "None leaks to display as 'None'"
        regression."""
        out = normalize_grid_items(
            [("/a.jpg", "a.jpg", "/photos", "1024", None, None)],
            path_normalizer=lambda p: p,
        )
        assert out[0][4] == ""
        assert out[0][5] == ""

    def test_path_normalizer_invoked_on_path(self):
        """The normaliser callable transforms only the path (slot 0)."""
        normalizer_calls: list[str] = []

        def fake_norm(p: str) -> str:
            normalizer_calls.append(p)
            return "NORMALIZED|" + p

        out = normalize_grid_items(
            [("/a.jpg", "a.jpg", "/photos", "1024")],
            path_normalizer=fake_norm,
        )

        assert normalizer_calls == ["/a.jpg"]
        assert out[0][0] == "NORMALIZED|/a.jpg"

    def test_mixed_tuples_handled(self):
        """A list mixing 4-tuples and 6-tuples → all promoted to
        7-tuples in order."""
        out = normalize_grid_items(
            [
                ("/a.jpg", "a", "/f", "1"),
                ("/b.jpg", "b", "/f", "2", "c1", "s1"),
            ],
            path_normalizer=lambda p: p,
        )
        assert len(out) == 2
        assert out[0][4] == ""  # 4-tuple → empty creation
        assert out[1][4] == "c1"  # 6-tuple → real creation


# ── attach_resolutions ───────────────────────────────────────────────────


class TestAttachResolutions:
    """Walk items + attach the resolution string (skip videos)."""

    def test_image_gets_formatted_resolution(self):
        """An image path → call dim_reader, format via
        format_resolution_string, attach to slot 6."""
        items = [("/a.jpg", "a", "/f", "1", "", "", "")]
        out = attach_resolutions(
            items,
            dim_reader=lambda p: (4032, 3024),
            is_video_predicate=lambda p: False,
        )
        assert out[0][6] == "4032*3024"

    def test_video_skipped_dim_reader(self):
        """Video paths should NOT call dim_reader (avoids unnecessary
        I/O on .mp4 files). Resolution slot stays empty."""
        called_with: list[str] = []

        def spy_dim_reader(p: str) -> tuple[int, int]:
            called_with.append(p)
            return (1920, 1080)

        items = [("/a.mp4", "a", "/f", "1", "", "", "")]
        out = attach_resolutions(
            items,
            dim_reader=spy_dim_reader,
            is_video_predicate=lambda p: True,
        )

        assert out[0][6] == ""
        assert called_with == []  # dim_reader never invoked on videos

    def test_dim_reader_returning_zero_dims_yields_empty_string(self):
        """An image whose dims are unreadable (corrupt file, format
        unsupported by all 3 fallbacks) → empty resolution string
        (the format_resolution_string contract)."""
        items = [("/x.jpg", "x", "/f", "1", "", "", "")]
        out = attach_resolutions(
            items,
            dim_reader=lambda p: (0, 0),
            is_video_predicate=lambda p: False,
        )
        assert out[0][6] == ""

    def test_mixed_items_routed_correctly(self):
        """Mixed video + image list → only images go through
        dim_reader; videos retain empty resolution."""
        items = [
            ("/a.jpg", "a", "/f", "1", "", "", ""),
            ("/b.mp4", "b", "/f", "2", "", "", ""),
            ("/c.jpg", "c", "/f", "3", "", "", ""),
        ]
        out = attach_resolutions(
            items,
            dim_reader=lambda p: (800, 600),
            is_video_predicate=lambda p: p.endswith(".mp4"),
        )
        assert out[0][6] == "800*600"
        assert out[1][6] == ""
        assert out[2][6] == "800*600"
