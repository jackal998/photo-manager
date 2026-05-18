"""Tests for :mod:`app.views.image_tasks_helpers`.

Covers the token-format helpers extracted from
:mod:`app.views.image_tasks` (#293). The token is the bridge
contract between :class:`ImageTaskRunner` (producer) and
:func:`app.views.preview_pane_helpers.classify_image_token`
(consumer); both ends must agree on the ``"single|"`` / ``"grid|"``
prefix or every in-flight image load silently drops.
"""

from __future__ import annotations

from app.views.image_tasks_helpers import make_grid_token, make_single_token


# ── make_single_token ────────────────────────────────────────────────────


class TestMakeSingleToken:
    """The single-preview token format."""

    def test_default_side_is_zero(self):
        """Production callers always pass side=0; the default keeps
        the contract explicit in the signature so a caller that
        forgets to pass it still produces a parseable token."""
        assert make_single_token("C:/a.jpg") == "single|C:/a.jpg|0"

    def test_explicit_side_appears_in_token(self):
        """When side is non-zero (future caller or test), it
        appears in the trailing segment."""
        assert make_single_token("p.jpg", side=512) == "single|p.jpg|512"

    def test_path_with_pipe_passes_through(self):
        """Pipe characters in paths are unusual but not impossible
        (some NAS shares). The token format is what it is — the
        classifier on the consumer side only checks the first
        segment, so an embedded pipe is parsed correctly enough
        for routing. This test pins the verbatim behaviour."""
        assert make_single_token("a|b.jpg", side=0) == "single|a|b.jpg|0"

    def test_starts_with_single_prefix_for_classifier(self):
        """The consumer (``classify_image_token``) splits on the
        first ``|`` and matches the first segment. Failure mode: a
        refactor that changed the prefix to ``"sgl"`` would break
        the classifier and every single-preview load would silently
        drop on arrival."""
        assert make_single_token("any.jpg").startswith("single|")


# ── make_grid_token ──────────────────────────────────────────────────────


class TestMakeGridToken:
    """The grid-thumbnail token format."""

    def test_format(self):
        """Three pipe-separated segments: ``grid``, path, side."""
        assert make_grid_token("photos/a.jpg", 256) == "grid|photos/a.jpg|256"

    def test_starts_with_grid_prefix_for_classifier(self):
        """Same producer/consumer pairing as the single case —
        ``"grid|"`` is the prefix ``classify_image_token`` matches
        on."""
        assert make_grid_token("a.jpg", 128).startswith("grid|")

    def test_token_differs_from_single_for_same_path(self):
        """The single and grid tokens for the same path-and-side
        must be distinct — otherwise the receiver couldn't tell
        which view requested the load. The prefix is the entire
        disambiguator."""
        assert make_single_token("a.jpg", 256) != make_grid_token("a.jpg", 256)
