"""Pure-logic helpers extracted from
:class:`app.views.widgets.video_player.VideoPlayerWidget`.

Extracted so the load-bearing decision logic — URL routing,
play/volume button glyph resolution, volume scaling — is
unit-testable against plain Python without cascade-importing the
``QMediaPlayer`` / ``QVideoWidget`` stack.

Same extraction pattern previously used by ``action_handlers.py``
(#182), ``status_reporter_impl.py`` (#138, #140), ``empty_state.py``
(#137), ``main_window_helpers.py`` (#185 / #283),
``group_media_controller_helpers.py`` (#185 / #285),
``preview_pane_helpers.py`` (#185 / #289),
``dialog_handler_helpers.py`` (#293), and
``image_tasks_helpers.py`` (#293).

What lives here:

* :func:`should_use_file_protocol` — True if the path should be
  wrapped in ``QUrl.fromLocalFile`` rather than parsed as a URL
  directly. The decision rests on whether the string already has
  a ``file://`` scheme.
* :func:`play_button_glyph` — boolean ``is_playing`` → ``"⏸"`` or
  ``"▶"``. Decoupled from ``QMediaPlayer.PlaybackState`` so the
  glyph table can be tested without a Qt enum.
* :func:`volume_button_glyph` — boolean ``is_muted`` → ``"🔇"``
  or ``"🔊"``.
* :func:`volume_float_to_slider_int` — ``0.0..1.0`` → ``0..100``
  for the slider control.
* :func:`volume_int_to_float` — ``0..100`` → ``0.0..1.0`` for the
  audio output.
"""

from __future__ import annotations


# Glyph constants live here so the test can assert on the exact
# strings; a refactor that swapped them would render the play/
# pause button with the wrong icon and the test catches it
# regardless of which state path it was reached through.
_PLAY_GLYPH = "▶"
_PAUSE_GLYPH = "⏸"
_VOLUME_ON_GLYPH = "🔊"
_VOLUME_MUTED_GLYPH = "🔇"


def should_use_file_protocol(path: str) -> bool:
    """Return True iff ``path`` lacks a ``file://`` scheme and so
    must be wrapped in ``QUrl.fromLocalFile`` rather than parsed
    directly.

    Failure mode: a refactor that inverted the boolean would feed
    bare paths to ``QUrl(path)`` (which interprets ``C:\\...`` as
    a scheme of ``c`` on Windows) — every video would fail to
    load with no clear error message.
    """
    return not path.lower().startswith("file://")


def play_button_glyph(is_playing: bool) -> str:
    """Resolve the play/pause button glyph from the boolean state.

    The caller derives ``is_playing`` from
    ``QMediaPlayer.PlaybackState.PlayingState`` so this helper
    stays Qt-free.

    Failure mode: a refactor that swapped the two glyphs would
    show ``"⏸"`` when stopped and ``"▶"`` when playing — every
    user would click the "wrong" button to toggle.
    """
    return _PAUSE_GLYPH if is_playing else _PLAY_GLYPH


def volume_button_glyph(is_muted: bool) -> str:
    """Resolve the volume-icon glyph from the boolean mute state.

    Same shape as :func:`play_button_glyph` — Qt-free callable
    over a boolean, used by ``_update_volume_button``.
    """
    return _VOLUME_MUTED_GLYPH if is_muted else _VOLUME_ON_GLYPH


def volume_float_to_slider_int(volume: float) -> int:
    """Map an audio-output volume (``0.0..1.0``) to a slider value
    (``0..100``).

    Failure mode: a refactor that dropped the ``int()`` cast would
    pass a float to ``QSlider.setValue``, which is silently
    truncated — but a refactor that swapped the multiplier from
    100 to 10 would set the slider to a near-zero position on
    every volume restore.
    """
    return int(volume * 100)


def volume_int_to_float(slider_value: int) -> float:
    """Map a slider value (``0..100``) to an audio-output volume
    (``0.0..1.0``).

    Inverse of :func:`volume_float_to_slider_int`. The pair is
    not perfectly round-trip because of int truncation — a slider
    at 33 → 0.33 → back to slider 33 → exact; but volume 0.337 →
    slider 33 → back to volume 0.33 (lost the trailing digits).
    Production accepts the precision loss because the audio
    output API takes a float and the slider can only display
    integers.
    """
    return slider_value / 100.0
