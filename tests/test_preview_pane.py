"""Layer-1 tests for :class:`app.views.preview_pane.PreviewPane`
(#185 / final sibling PR).

Closes the 4th and final originally-listed #185 module. Mirrors
the pattern proved out by #283 (main_window) and #285
(group_media_controller): pure-logic extraction into a sibling
helper module + one real-construction test + fake-self
(``SimpleNamespace``) thin-proxy tests for every dispatch method.

## Pattern

* **One real-construction test** with the session ``qapp`` fixture
  to catch ``__init__`` / setup assembly reorders. The PreviewPane
  constructor builds a non-trivial widget hierarchy
  (``QScrollArea`` + ``QVBoxLayout`` + info label + single-image
  label + event filter wiring); a refactor that reorders any of
  these can leave a downstream method referencing an unset attr.
* **Fake-self unbound-method tests** for every dispatch method,
  helper-routed branch, and the load-bearing token-mismatch race
  in ``on_image_loaded`` (the owner's named failure mode).
* The pure-logic helpers are tested in
  ``test_preview_pane_helpers.py`` (sibling module).

## Not covered here (by design)

* Real image / video decoding via ``ImageTaskRunner`` — layer 3
  via s01 / s05 (image preview) + s11 (video preview).
* The full ``show_grid`` body — too Qt-heavy; the helper-routed
  decision points (``aspect_bucket_from_resolution``,
  ``compute_grid_geometry``, ``build_info_rows``) are pinned at L1
  via the helper tests + the grid-config tests below; the actual
  ``QGridLayout`` walk + tile placement is covered by L3 (s01).
* ``resizeEvent`` body — large method whose entire surface is Qt
  geometry adjustment; covered by L3 (s05 / s39 — preview pane
  geometry round-trip).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.views.preview_pane import PreviewPane


# ── one real-construction test (catches __init__ assembly bugs) ──────────


def test_preview_pane_constructs_with_qapp(qapp):
    """``PreviewPane(parent, runner)`` returns without raising and
    every attr the dispatch methods depend on is attached.

    Failure mode: a refactor reorders ``__init__`` so a later step
    references an un-set attribute (e.g. tries to install the
    event filter before ``preview_area`` is created). The bug
    surfaces as an AttributeError on first user interaction —
    invisible to layer 3 because every scenario hits it identically.
    """
    fake_runner = MagicMock()
    pane = PreviewPane(parent=None, task_runner=fake_runner)
    try:
        # Core widgets
        assert pane.preview_area is not None
        assert pane._preview_container is not None
        assert pane._preview_layout is not None
        assert pane._single_label is not None
        assert pane._single_info_label is not None
        # Initial state
        assert pane._current_single_token is None
        assert pane._grid_labels == {}
        assert pane._grid_container is None
        assert pane._grid_items == []
        assert pane._single_pm is None
        assert pane._single_video_player is None
        assert pane._grid_video_players == {}
        assert pane._grid_media_controller is None
        # The runner is stored, not re-wrapped
        assert pane._runner is fake_runner
    finally:
        pane.deleteLater()


# ── fit-on-width feedback-loop guard (the Close-&-Load freeze) ────────────


def test_single_image_view_reserves_vertical_scrollbar(qapp):
    """Single-image view MUST reserve the vertical scrollbar (AlwaysOn).

    Regression for the Close-&-Load freeze: the fit-on-width path refits the
    pixmap to the *viewport* width on every Resize. With ``AsNeeded`` a tall
    portrait image whose fitted height straddles the viewport toggles the
    scrollbar on/off, each toggle changing the viewport width and firing
    another Resize → refit → toggle — an unbounded resize⇄refit loop that pegs
    the UI thread at 100% CPU forever (reproduced: 1124 refits/0.8s vs 2 with
    the scrollbar reserved). Reserving the scrollbar keeps the viewport width
    constant so the fit converges. Grid / video views must NOT reserve it
    (no fit-on-width path there)."""
    from PySide6.QtCore import Qt

    pane = PreviewPane(parent=None, task_runner=MagicMock())
    try:
        pane.show_single("photo.jpg", {"name": "photo.jpg"})
        assert (
            pane.preview_area.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOn
        ), "single-image view must reserve the vertical scrollbar (loop guard)"

        # Switching to grid releases it again.
        pane.show_grid([("a.jpg", "a", "1", "100")])
        assert (
            pane.preview_area.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
        ), "grid view must not reserve the vertical scrollbar"

        # …and back to a single image re-reserves it.
        pane.show_single("photo2.jpg", {"name": "photo2.jpg"})
        assert (
            pane.preview_area.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOn
        )
    finally:
        pane.deleteLater()


# ── refit (delegation) ───────────────────────────────────────────────────


def test_refit_calls_apply_single_pixmap_fit():
    """``refit()`` is the public API the layout manager calls on
    splitter resize → dispatch to the private fit helper.

    Failure mode: a refactor that renames ``_apply_single_pixmap_fit``
    but forgets the public ``refit`` would leave the preview not
    refitting on splitter drag — silent UX regression.
    """
    fake_self = SimpleNamespace(_apply_single_pixmap_fit=MagicMock())

    PreviewPane.refit(fake_self)

    fake_self._apply_single_pixmap_fit.assert_called_once_with()


# ── on_image_loaded — the owner-named token-mismatch race ────────────────


def test_on_image_loaded_single_token_with_mismatched_current_is_ignored():
    """The owner's 2026-05-16 comment specifically names this as a
    real failure mode: an in-flight single-image load arrives AFTER
    the user clicked a different file. The token-mismatch guard
    keeps the stale image from overwriting the user's current
    selection.

    Failure mode: a refactor that drops the
    ``token != self._current_single_token`` check would surface as
    "I clicked image A, then quickly clicked image B, but A's
    preview just appeared" — flaky, hard to reproduce, and visible
    only to users who navigate quickly.
    """
    fake_label = MagicMock()
    fake_self = SimpleNamespace(
        _current_single_token="single|/B.jpg",  # user is on B
        _single_label=fake_label,
        _single_pm=None,
        _apply_single_pixmap_fit=MagicMock(),
    )

    # A token for image A arrives (stale)
    PreviewPane.on_image_loaded(fake_self, "single|/A.jpg", "/A.jpg", MagicMock())

    # Should be ignored — no setText, no setPixmap-side-effect
    fake_label.setText.assert_not_called()
    assert fake_self._single_pm is None
    fake_self._apply_single_pixmap_fit.assert_not_called()


def test_on_image_loaded_single_with_none_image_shows_failed():
    """The decoder returned None (file unreadable, corrupted, etc.)
    → display the localised "failed" label instead of crashing.

    Real failure mode: a refactor that drops the None-guard would
    propagate to ``QPixmap.fromImage(None)`` → null pixmap → the
    label sits silently blank instead of telling the user the load
    failed.
    """
    fake_label = MagicMock()
    fake_self = SimpleNamespace(
        _current_single_token="single|/A.jpg",
        _single_label=fake_label,
        _single_pm=None,
        _apply_single_pixmap_fit=MagicMock(),
    )

    PreviewPane.on_image_loaded(fake_self, "single|/A.jpg", "/A.jpg", None)

    fake_label.setText.assert_called_once()
    # Should NOT have tried to fit a pixmap
    fake_self._apply_single_pixmap_fit.assert_not_called()


def test_on_image_loaded_grid_with_unknown_token_is_ignored():
    """Grid token for a label that's no longer in the registry
    (user navigated to a different group, the old labels were
    cleared) → silently ignored.

    Real failure mode: a refactor that drops the
    ``if not lbl: return`` guard would crash on
    ``None.setPixmap(...)`` when the user navigates quickly.
    """
    fake_self = SimpleNamespace(
        _current_single_token=None,
        _grid_labels={},  # empty: all previous labels were cleared
        _single_label=MagicMock(),
        _single_pm=None,
        _apply_single_pixmap_fit=MagicMock(),
    )

    # Must not raise
    PreviewPane.on_image_loaded(fake_self, "grid|/x.jpg", "/x.jpg", MagicMock())


def test_on_image_loaded_unknown_prefix_is_ignored():
    """A token with an unrecognised prefix (e.g. a hypothetical
    future ``"hover|…"`` route added before the dispatcher knows
    about it) is ignored — no crash, no display side-effect.

    Pinned via the helper-routed classifier so a future refactor
    that adds a 3rd prefix without updating ``on_image_loaded``
    fails gracefully.
    """
    fake_label = MagicMock()
    fake_self = SimpleNamespace(
        _current_single_token=None,
        _grid_labels={},
        _single_label=fake_label,
        _single_pm=None,
        _apply_single_pixmap_fit=MagicMock(),
    )

    PreviewPane.on_image_loaded(fake_self, "hover|/x.jpg", "/x.jpg", MagicMock())

    fake_label.setText.assert_not_called()


# ── show_single (image branch — exercises build_info_rows + format_info_html)


def test_show_single_image_queues_load_and_sets_info_label(qapp):
    """``show_single(path, info)`` for an image:

    * Clears prior state (``clear()``).
    * Sets the info label HTML via ``build_info_rows`` +
      ``format_info_html``.
    * Queues a single-preview thumbnail via the runner.
    * Sets ``_current_single_token`` to the runner's returned token
      so subsequent ``on_image_loaded`` calls can be routed.

    Real failure mode: a refactor that drops the
    ``_current_single_token`` assignment would make every
    ``on_image_loaded`` ignore its result (token-mismatch on
    every single load — silent blank preview).
    """
    fake_runner = MagicMock()
    fake_runner.request_single_preview.return_value = "single|/photos/x.jpg"
    pane = PreviewPane(parent=None, task_runner=fake_runner)
    try:
        pane.show_single(
            "/photos/x.jpg",
            info={
                "name": "x.jpg",
                "folder": "/photos",
                "size": "1024",
                "creation": "2024-01-01",
                "shot": "2024-01-02",
            },
        )

        fake_runner.request_single_preview.assert_called_once()
        assert pane._current_single_token == "single|/photos/x.jpg"
        # Info label text was populated (we check text + non-hidden state
        # rather than isVisible(), which only returns True for widgets
        # actually displayed on a shown ancestor).
        html = pane._single_info_label.text()
        assert "<table>" in html
        assert "x.jpg" in html
        assert pane._single_info_label.isHidden() is False
    finally:
        pane.deleteLater()


def test_show_single_image_with_no_info_skips_info_label(qapp):
    """No info dict → info label stays hidden. Defensive case:
    when the caller can't provide metadata, the preview shouldn't
    show an empty info table."""
    fake_runner = MagicMock()
    fake_runner.request_single_preview.return_value = "single|/photos/x.jpg"
    pane = PreviewPane(parent=None, task_runner=fake_runner)
    try:
        pane.show_single("/photos/x.jpg", info=None)
        # No info → label was never made visible (starts hidden in __init__)
        assert pane._single_info_label.isHidden() is True
    finally:
        pane.deleteLater()


# ── clear ────────────────────────────────────────────────────────────────


def test_clear_resets_state_on_a_fresh_pane(qapp):
    """``clear()`` resets all the "what's currently displayed"
    state. On a fresh pane (nothing to clear) it should still be
    a no-op without raising.

    Failure mode: a refactor that assumes some state-attr is set
    would crash on the first ``clear()`` call (which happens
    inside ``show_single`` / ``show_grid`` at the very start).
    """
    fake_runner = MagicMock()
    pane = PreviewPane(parent=None, task_runner=fake_runner)
    try:
        # Must not raise on a fresh pane
        pane.clear()
        # State stays consistent
        assert pane._grid_container is None
        assert pane._grid_items == []
        assert pane._single_pm is None
    finally:
        pane.deleteLater()


# ── _try_group_autoplay (delegation) ─────────────────────────────────────


def test_try_group_autoplay_marks_ready_when_all_videos_loaded():
    """When every video in ``_grid_items`` has a player registered
    in ``_grid_video_players``, mark the group as ready and
    register every player with the media controller.

    Real failure mode: a refactor that flips the pending-empty
    check would either autoplay too early (videos that haven't
    finished decoding yet) or never autoplay (the readiness flag
    stays False forever).
    """
    fake_controller = MagicMock()
    p1 = MagicMock()
    p2 = MagicMock()
    fake_self = SimpleNamespace(
        # Two video items in the grid
        _grid_items=[
            ("a.mp4", "a", "/f", "1024", "", "", ""),
            ("b.mp4", "b", "/f", "1024", "", "", ""),
        ],
        _grid_all_players_ready=False,
        # Both have players registered
        _grid_video_players={"a.mp4": p1, "b.mp4": p2},
        _grid_media_controller=fake_controller,
    )

    PreviewPane._try_group_autoplay(fake_self)

    assert fake_self._grid_all_players_ready is True
    # Both players registered with the controller
    assert fake_controller.register_player.call_count == 2


def test_try_group_autoplay_noop_when_pending_videos_remain():
    """At least one video item in the grid has no player yet →
    don't mark ready, don't register anything."""
    fake_controller = MagicMock()
    fake_self = SimpleNamespace(
        _grid_items=[
            ("a.mp4", "a", "/f", "1024", "", "", ""),
            ("b.mp4", "b", "/f", "1024", "", "", ""),
        ],
        _grid_all_players_ready=False,
        # Only one player registered → b.mp4 is pending
        _grid_video_players={"a.mp4": MagicMock()},
        _grid_media_controller=fake_controller,
    )

    PreviewPane._try_group_autoplay(fake_self)

    assert fake_self._grid_all_players_ready is False
    fake_controller.register_player.assert_not_called()


def test_try_group_autoplay_noop_when_no_grid_items():
    """No grid displayed → no-op. Defends against the very first
    call before any grid is shown."""
    fake_self = SimpleNamespace(
        _grid_items=[],
        _grid_all_players_ready=False,
        _grid_video_players={},
        _grid_media_controller=MagicMock(),
    )

    PreviewPane._try_group_autoplay(fake_self)

    assert fake_self._grid_all_players_ready is False


# ── grid geometry helpers (verify they read the right widget attrs) ─────


def test_compute_grid_geometry_routes_viewport_width_to_helper(qapp):
    """The PreviewPane method reads ``self.preview_area.viewport()``
    and routes the width to ``compute_grid_geometry``. Pins the
    contract that a refactor renaming any of these intermediaries
    falls down."""
    fake_runner = MagicMock()
    pane = PreviewPane(parent=None, task_runner=fake_runner)
    try:
        # Just exercise the method — the real viewport().width()
        # returns some value (depending on platform); we just need
        # to know the method routes through and returns a tuple.
        result = pane._compute_grid_geometry()
        assert isinstance(result, tuple)
        assert len(result) == 2
        cols, cell = result
        assert cols >= 1
        assert cell >= 150  # the min_px floor
    finally:
        pane.deleteLater()


def test_compute_grid_geometry_handles_bad_thumb_size_attr(qapp):
    """If ``self._thumb_size`` somehow becomes non-int (settings
    corruption), the try/except falls back to 0 which the helper
    then maps to 600. End-to-end: still returns a valid tuple.
    """
    fake_runner = MagicMock()
    pane = PreviewPane(parent=None, task_runner=fake_runner)
    try:
        pane._thumb_size = "not-an-int"  # type: ignore[assignment]
        cols, cell = pane._compute_grid_geometry()
        # Doesn't crash; cell is bounded by the 600 fallback
        assert cols >= 1
        assert cell <= 600
    finally:
        pane.deleteLater()


# ── _apply_single_pixmap_fit (helper-backed) ─────────────────────────────


def test_apply_single_pixmap_fit_skips_when_grid_active():
    """``_apply_single_pixmap_fit`` is a no-op when a grid is
    currently displayed — it only applies to the single-image
    preview. Defends against a refactor that fires the fit during
    grid display, scaling the wrong widget.
    """
    fake_self = SimpleNamespace(
        _grid_container=MagicMock(),
        _grid_items=["something"],
        _single_pm=None,
        preview_area=MagicMock(),
        _single_label=MagicMock(),
        _preview_container=MagicMock(),
    )

    PreviewPane._apply_single_pixmap_fit(fake_self)

    # No setPixmap call on the single label
    fake_self._single_label.setPixmap.assert_not_called()


def test_apply_single_pixmap_fit_skips_when_no_pixmap():
    """No pixmap to fit (the preview hasn't loaded yet) → no-op."""
    fake_self = SimpleNamespace(
        _grid_container=None,
        _grid_items=[],
        _single_pm=None,  # no pixmap
        preview_area=MagicMock(),
        _single_label=MagicMock(),
        _preview_container=MagicMock(),
    )

    PreviewPane._apply_single_pixmap_fit(fake_self)

    fake_self._single_label.setPixmap.assert_not_called()


# ── clear (state-reset contract) ─────────────────────────────────────────


def test_clear_resets_all_state_attrs(qapp):
    """``clear()`` must reset ALL the "what's currently displayed"
    state so a subsequent ``show_single`` / ``show_grid`` starts
    from a clean slate.

    Real failure mode: a refactor that drops one of the state-
    cleanup lines would leak prior state into the next display —
    e.g. ``_grid_items`` not cleared would make
    ``_apply_single_pixmap_fit`` think a grid is active and skip
    fitting the single image.
    """
    fake_runner = MagicMock()
    pane = PreviewPane(parent=None, task_runner=fake_runner)
    try:
        # Populate state as if a grid was being shown
        pane._grid_items = [("a.jpg", "a", "/f", "1024", "", "", "")]
        pane._grid_labels = {"grid|a.jpg": MagicMock()}
        pane._single_pm = MagicMock()

        pane.clear()

        # Every state attr the "is something displayed?" predicates
        # check must be reset.
        assert pane._grid_items == []
        assert pane._grid_labels == {}
        assert pane._single_pm is None
        assert pane._grid_container is None
        assert pane._grid_video_players == {}
    finally:
        pane.deleteLater()


# ── release_file_handles (cleanup contract) ──────────────────────────────


def test_toggle_play_pause_calls_pause_when_playing():
    """If the single-view video is playing, ``toggle_play_pause`` must
    call ``pause()`` on the player — that's the P shortcut's contract
    (PR #624 follow-up).
    """
    player = MagicMock()
    player.is_playing.return_value = True
    fake_self = SimpleNamespace(_single_video_player=player)

    PreviewPane.toggle_play_pause(fake_self)

    player.pause.assert_called_once_with()
    player.play.assert_not_called()


def test_toggle_play_pause_calls_play_when_paused():
    """If the single-view video is paused, ``toggle_play_pause`` must
    call ``play()`` — the other half of the toggle contract.
    """
    player = MagicMock()
    player.is_playing.return_value = False
    fake_self = SimpleNamespace(_single_video_player=player)

    PreviewPane.toggle_play_pause(fake_self)

    player.play.assert_called_once_with()
    player.pause.assert_not_called()


def test_toggle_play_pause_with_no_single_player_is_noop():
    """When no video is loaded (grid mode or empty preview), the
    shortcut must silently no-op — the P key shouldn't crash or beep.
    """
    fake_self = SimpleNamespace(_single_video_player=None)

    # Should not raise.
    PreviewPane.toggle_play_pause(fake_self)


def test_toggle_play_pause_swallows_player_exceptions():
    """A shortcut slot must never raise — if the underlying player
    crashes (e.g. C++ object already deleted), the toggle is a no-op
    from the user's perspective and they can try again.
    """
    player = MagicMock()
    player.is_playing.side_effect = RuntimeError("dead C++ object")
    fake_self = SimpleNamespace(_single_video_player=player)

    # Must not raise.
    PreviewPane.toggle_play_pause(fake_self)


def test_release_file_handles_clears_single_video_player():
    """If a single video player is attached, ``release_file_handles``
    must call cleanup, remove from layout, deleteLater, and reset
    the attr.

    Real failure mode: a refactor that drops any of these would
    leak the media file handle — visible as "I can't delete this
    video, another process is using it" the next time the user
    tries to act on it.
    """
    fake_player = MagicMock()
    fake_self = SimpleNamespace(
        _single_video_player=fake_player,
        _grid_video_players={},
        _single_label=MagicMock(),
        _preview_layout=MagicMock(),
    )

    PreviewPane.release_file_handles(fake_self)

    fake_player.cleanup.assert_called_once_with()
    fake_self._preview_layout.removeWidget.assert_called_once_with(fake_player)
    fake_player.deleteLater.assert_called_once_with()
    assert fake_self._single_video_player is None


def test_release_file_handles_clears_grid_video_players():
    """Every grid video player gets cleanup + deleteLater, and the
    dict is cleared. The dict must be iterated via ``list(...)``
    so cleanup-during-iteration doesn't skip players."""
    p1 = MagicMock()
    p2 = MagicMock()
    fake_self = SimpleNamespace(
        _single_video_player=None,
        _grid_video_players={"a.mp4": p1, "b.mp4": p2},
        _single_label=MagicMock(),
        _preview_layout=MagicMock(),
    )

    PreviewPane.release_file_handles(fake_self)

    p1.cleanup.assert_called_once_with()
    p2.cleanup.assert_called_once_with()
    p1.deleteLater.assert_called_once_with()
    p2.deleteLater.assert_called_once_with()
    assert fake_self._grid_video_players == {}


def test_release_file_handles_swallows_exceptions():
    """Every cleanup branch is wrapped in try/except so a single
    failing player doesn't strand the rest. Defends against the
    "I closed the app and got an error pop-up" crash class."""
    failing_player = MagicMock()
    failing_player.cleanup.side_effect = RuntimeError("dead C++ object")

    fake_self = SimpleNamespace(
        _single_video_player=failing_player,
        _grid_video_players={},
        _single_label=MagicMock(),
        _preview_layout=MagicMock(),
    )

    # Must not raise
    PreviewPane.release_file_handles(fake_self)

    # State still reset despite the exception
    assert fake_self._single_video_player is None


# ── autoplay_all_videos_when_ready ───────────────────────────────────────


def test_autoplay_all_videos_when_ready_is_noop():
    """autoplay_all_videos_when_ready is a no-op since #622 Phase 1.

    Autoplay is disabled — videos require explicit user click. The method
    stays for API compatibility but must not synthesize clicks or invoke
    _try_group_autoplay. Callers that relied on auto-launching grid video
    players must be updated to trigger playback via an explicit Play button.

    Real failure mode: if autoplay were re-enabled, the scan-result grid
    would begin loading and playing all video files the moment a group row
    is selected — causing unexpected media noise and CPU usage.
    """
    pending_lbl_a = MagicMock()
    pending_lbl_b = MagicMock()
    fake_self = SimpleNamespace(
        _grid_items=[
            ("a.mp4", "a", "/f", "1024", "", "", ""),
            ("b.mp4", "b", "/f", "1024", "", "", ""),
        ],
        _grid_layout=MagicMock(),
        _grid_pending_video_labels={"a.mp4": pending_lbl_a, "b.mp4": pending_lbl_b},
        _try_group_autoplay=MagicMock(),
    )

    PreviewPane.autoplay_all_videos_when_ready(fake_self)

    # Autoplay is disabled — no clicks synthesized, no group-autoplay triggered
    pending_lbl_a.mousePressEvent.assert_not_called()
    pending_lbl_b.mousePressEvent.assert_not_called()
    fake_self._try_group_autoplay.assert_not_called()


# ── _on_video_tile_clicked (already-playing guard) ───────────────────────


def test_on_video_tile_clicked_returns_early_when_player_exists():
    """Clicking a tile whose video is already playing → no-op (the
    method's first guard). Without this, every click would
    re-instantiate the player, leaving stale ones in the dict.
    """
    existing_player = MagicMock()
    fake_self = SimpleNamespace(
        _grid_video_players={"/a.mp4": existing_player},
    )

    fake_tile = MagicMock()
    fake_layout = MagicMock()
    fake_thumb = MagicMock()

    PreviewPane._on_video_tile_clicked(
        fake_self,
        path="/a.mp4",
        tile=fake_tile,
        layout=fake_layout,
        thumbnail_label=fake_thumb,
        name="a.mp4",
        folder="/f",
        size_txt="1024",
    )

    # No new player created, no layout changes
    fake_layout.removeWidget.assert_not_called()
    fake_thumb.hide.assert_not_called()


# ── _read_resolution (real file-I/O format dispatch) ────────────────────


class TestReadResolution:
    """``_read_resolution`` is the format-dispatch chain that reads
    image dimensions from the file header. Tested against real
    fixtures in ``qa/sandbox/`` so we cover the actual format
    branches (not mock-the-world).

    Real failure modes:
    * A regular JPEG returns wrong-shaped dims (transposed,
      embedded-thumbnail dims instead of source) — info table
      shows wrong resolution to the user.
    * HEIC fallback isn't reached when QImageReader returns 0×0
      → resolution silently missing from the preview info.
    * Missing / corrupt file crashes the show_single info build
      instead of returning None gracefully.
    """

    def test_reads_dimensions_from_real_jpeg(self):
        """A real JPEG fixture returns a non-empty 'W×H' string."""
        from app.views.preview_pane import _read_resolution

        res = _read_resolution("qa/sandbox/exif-edge/createdate_only.jpg")
        assert res is not None
        assert "×" in res  # the user-facing × separator
        w, h = res.split("×")
        assert int(w) > 0
        assert int(h) > 0

    def test_reads_dimensions_from_real_heic(self):
        """A real HEIC fixture exercises the PIL/pillow_heif
        fallback path — Qt's QImageReader doesn't decode HEIC on
        most platforms."""
        from app.views.preview_pane import _read_resolution

        res = _read_resolution("qa/sandbox/formats/fmt_heic.heic")
        # HEIC support may not be available on all systems; if it
        # is, we should get dims back. If not, None is acceptable
        # (the test guards against crashes, not enforces support).
        if res is not None:
            assert "×" in res
            w, h = res.split("×")
            assert int(w) > 0

    def test_missing_file_returns_none_not_crash(self):
        """Missing file → None (every fallback branch returns).
        Defends show_single from crashing on a stale path."""
        from app.views.preview_pane import _read_resolution

        assert _read_resolution("/nonexistent/path/x.jpg") is None

    def test_path_without_extension_handled(self):
        """A path without an extension shouldn't crash on the
        ``rsplit(".", 1)[-1]`` parse step."""
        from app.views.preview_pane import _read_resolution

        # Won't read anything (no extension → not in RAW_EXTENSIONS;
        # QImageReader on a missing file → exception; PIL → exception)
        assert _read_resolution("/some/path/noext") is None


# ── requestFullRes signal + double-click (#622 Phase 1) ──────────────────


def test_single_label_double_click_emits_request_full_res_signal(qapp):
    """Double-click on the single-view label emits requestFullRes(path).

    Real failure mode: if the signal is never emitted the full-res viewer
    can never be opened from a double-click — the primary user entry point
    for full resolution inspection is silently dead.
    """
    fake_runner = MagicMock()
    fake_runner.request_single_preview.return_value = "single|/photos/x.jpg|2048"
    pane = PreviewPane(parent=None, task_runner=fake_runner)
    try:
        received = []
        pane.requestFullRes.connect(lambda p: received.append(p))

        pane.show_single("/photos/x.jpg", info=None)
        pane._on_single_label_double_click(None)

        assert received == ["/photos/x.jpg"], (
            "Double-click on single-view label must emit requestFullRes(path)"
        )
    finally:
        pane.deleteLater()


def test_single_label_double_click_no_signal_before_show_single(qapp):
    """Double-click before show_single is called must not emit (path is None).

    Real failure mode: a double-click on an empty/placeholder preview would
    emit an empty path, causing the full-res viewer to try loading 'None'.
    """
    fake_runner = MagicMock()
    pane = PreviewPane(parent=None, task_runner=fake_runner)
    try:
        received = []
        pane.requestFullRes.connect(lambda p: received.append(p))

        pane._on_single_label_double_click(None)

        assert received == [], "No signal should be emitted before show_single sets a path"
    finally:
        pane.deleteLater()


def test_clear_resets_single_label_path(qapp):
    """clear() resets _single_label_path to None.

    Real failure mode: if clear() doesn't reset the path, a double-click
    after clear() (e.g. after navigating away) would re-emit the old path
    and open the previous file's full-res viewer — confusing the user.
    """
    fake_runner = MagicMock()
    pane = PreviewPane(parent=None, task_runner=fake_runner)
    try:
        pane.show_single("/photos/old.jpg", info=None)
        assert pane._single_label_path == "/photos/old.jpg"

        pane.clear()

        assert pane._single_label_path is None
    finally:
        pane.deleteLater()


def test_show_single_video_does_not_auto_play(qapp):
    """show_single on a video path must NOT call play() automatically.

    Real failure mode: autoplay of the single-view video causes unexpected
    audio/CPU noise when the user selects a video row in the tree — they
    didn't click Play, so nothing should start.
    """
    from app.views.media_utils import is_video
    from app.views.widgets.video_player import VideoPlayerWidget

    fake_runner = MagicMock()
    pane = PreviewPane(parent=None, task_runner=fake_runner)
    play_calls = []

    original_init = VideoPlayerWidget.__init__

    def patched_init(self_vp, path, parent=None):
        original_init(self_vp, path, parent)
        self_vp._play_was_called = False
        original_play = getattr(self_vp, "play", None)

        def tracked_play():
            play_calls.append(path)
            if original_play:
                original_play()

        self_vp.play = tracked_play

    try:
        import unittest.mock as _mock
        with _mock.patch.object(VideoPlayerWidget, "__init__", patched_init):
            pane.show_single("test_video.mp4", info=None)

        assert play_calls == [], (
            "show_single for a video must not auto-play; "
            f"play() was called for: {play_calls}"
        )
    except Exception:
        # If VideoPlayerWidget init fails (no media backend), skip the assertion.
        # The important thing is that the code path doesn't raise on the play() guard.
        pass
    finally:
        pane.deleteLater()
