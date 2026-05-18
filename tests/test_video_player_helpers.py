"""Tests for :mod:`app.views.widgets.video_player_helpers`.

Covers the pure-logic helpers extracted from
:class:`app.views.widgets.video_player.VideoPlayerWidget` (#293).
The extraction keeps the load-bearing decision logic (URL routing,
glyph resolution, volume scaling) unit-testable against plain
Python without cascade-importing the ``QMediaPlayer`` /
``QVideoWidget`` stack.

Each test maps to a real, named failure mode.
"""

from __future__ import annotations

import pytest

from app.views.widgets.video_player_helpers import (
    play_button_glyph,
    should_use_file_protocol,
    volume_button_glyph,
    volume_float_to_slider_int,
    volume_int_to_float,
)


# ── should_use_file_protocol ────────────────────────────────────────────


class TestShouldUseFileProtocol:
    """The URL-routing decision: does this path need
    ``QUrl.fromLocalFile`` (True) or is it already a URL we should
    parse directly (False)?"""

    def test_bare_windows_path_needs_wrapping(self):
        """Failure mode: feeding ``C:\\photos\\a.mp4`` to ``QUrl(...)``
        on Windows parses ``c`` as the scheme — the video silently
        fails to load."""
        assert should_use_file_protocol("C:/photos/a.mp4") is True

    def test_bare_unix_path_needs_wrapping(self):
        """Posix-style paths from a network share or a Linux
        env."""
        assert should_use_file_protocol("/home/user/v.mp4") is True

    def test_file_url_does_not_need_wrapping(self):
        """A path that's already a file:// URL must NOT be
        re-wrapped, else ``QUrl.fromLocalFile("file://...")``
        builds a doubly-encoded URL pointing at a path that
        contains the scheme string."""
        assert should_use_file_protocol("file:///c:/v.mp4") is False

    def test_uppercase_file_scheme_recognised(self):
        """``lower()`` makes the check case-insensitive — some
        callers normalise the scheme to uppercase."""
        assert should_use_file_protocol("FILE:///c:/v.mp4") is False

    def test_mixed_case_file_scheme_recognised(self):
        """File:// or fIlE:// — same outcome."""
        assert should_use_file_protocol("File:///c:/v.mp4") is False

    def test_empty_string_routes_through_fromLocalFile(self):
        """Empty path is not a file:// URL → True. Caller's
        downstream ``setSource`` will fail or set to nothing,
        which is fine — this helper just decides the construction
        path, not whether it's valid."""
        assert should_use_file_protocol("") is True


# ── play_button_glyph ────────────────────────────────────────────────────


class TestPlayButtonGlyph:
    """Boolean ``is_playing`` → glyph."""

    def test_playing_shows_pause_glyph(self):
        """Failure mode: a refactor that swapped the two glyphs
        would show ``▶`` while the video plays and ``⏸`` while
        paused — every user would press the "wrong" button. Test
        on the exact glyph because the symbol matters (the button
        width is sized for these specific characters)."""
        assert play_button_glyph(True) == "⏸"

    def test_paused_shows_play_glyph(self):
        assert play_button_glyph(False) == "▶"

    def test_glyphs_are_distinct(self):
        """Drift guard: if a refactor ever set both branches to
        the same glyph, this would fire even if the constants
        moved."""
        assert play_button_glyph(True) != play_button_glyph(False)


# ── volume_button_glyph ──────────────────────────────────────────────────


class TestVolumeButtonGlyph:
    """Boolean ``is_muted`` → volume icon glyph."""

    def test_muted_shows_mute_glyph(self):
        assert volume_button_glyph(True) == "🔇"

    def test_unmuted_shows_speaker_glyph(self):
        assert volume_button_glyph(False) == "🔊"

    def test_glyphs_are_distinct(self):
        assert volume_button_glyph(True) != volume_button_glyph(False)


# ── volume_float_to_slider_int ───────────────────────────────────────────


class TestVolumeFloatToSliderInt:
    """The ``0.0..1.0`` → ``0..100`` mapping."""

    def test_zero_volume_zero_slider(self):
        assert volume_float_to_slider_int(0.0) == 0

    def test_full_volume_hundred_slider(self):
        assert volume_float_to_slider_int(1.0) == 100

    def test_half_volume_fifty_slider(self):
        assert volume_float_to_slider_int(0.5) == 50

    def test_returns_int_not_float(self):
        """Failure mode: dropping the ``int()`` cast would feed
        a float to ``QSlider.setValue``. Qt would silently
        truncate but a slider position lookup elsewhere could
        compare against ``int`` and miss."""
        result = volume_float_to_slider_int(0.337)
        assert isinstance(result, int)
        assert result == 33  # int(0.337 * 100) == 33

    def test_over_unity_scales_proportionally(self):
        """Values outside ``0..1.0`` aren't clamped here — the
        caller is responsible. This test just pins that there's
        no surprise clamping."""
        assert volume_float_to_slider_int(1.5) == 150


# ── volume_int_to_float ──────────────────────────────────────────────────


class TestVolumeIntToFloat:
    """The ``0..100`` → ``0.0..1.0`` mapping (inverse direction)."""

    def test_zero_slider_zero_volume(self):
        assert volume_int_to_float(0) == 0.0

    def test_hundred_slider_full_volume(self):
        assert volume_int_to_float(100) == 1.0

    def test_fifty_slider_half_volume(self):
        assert volume_int_to_float(50) == 0.5

    def test_returns_float_not_int(self):
        """Failure mode: using integer division (``// 100``) would
        return 0 for every value below 100 — audio output would
        be silent at every slider position except max."""
        result = volume_int_to_float(33)
        assert isinstance(result, float)
        assert result == 0.33

    @pytest.mark.parametrize("value", [0, 1, 25, 50, 75, 100])
    def test_round_trip_through_int_slider_steps(self, value):
        """When the slider value IS already an integer in
        ``0..100``, the round-trip through float and back is
        lossless. The cross-boundary lossiness only happens for
        intermediate float volumes (tested above)."""
        assert volume_float_to_slider_int(volume_int_to_float(value)) == value
