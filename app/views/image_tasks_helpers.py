"""Pure-logic helpers extracted from
:mod:`app.views.image_tasks`.

Extracted so the load-bearing token format — the bridge contract
between :class:`ImageTaskRunner` and ``PreviewPane.on_image_loaded``
— is unit-testable against plain Python without cascade-importing
the ``QThreadPool`` dispatch surface or the ``QObject`` receiver.

Same extraction pattern previously used by ``action_handlers.py``
(#182), ``status_reporter_impl.py`` (#138, #140), ``empty_state.py``
(#137), ``main_window_helpers.py`` (#185 / #283),
``group_media_controller_helpers.py`` (#185 / #285),
``preview_pane_helpers.py`` (#185 / #289), and
``dialog_handler_helpers.py`` (#293).

What lives here:

* :func:`make_single_token` — ``(path, side) → "single|{path}|{side}"``.
  Side is always 0 in production today; kept as a parameter so the
  token shape stays uniform with :func:`make_grid_token`.
* :func:`make_grid_token` — ``(path, thumb_side) → "grid|{path}|{thumb_side}"``.

The token format is consumed by
:func:`app.views.preview_pane_helpers.classify_image_token` (which
peels off the ``"single"`` / ``"grid"`` prefix). A divergence between
producer and consumer would silently drop every in-flight image
load — invisible at runtime because the task still runs, just the
result has nowhere to land. These helpers + the consumer's classifier
test pin both ends.
"""

from __future__ import annotations


def make_single_token(path: str, side: int = 0) -> str:
    """Format the token for a single-image preview request.

    The ``side`` parameter is always 0 in production (preview pane
    decides its own dimensions); it's part of the token so the
    format is uniform with :func:`make_grid_token`.

    Failure mode: a refactor that changed the separator (e.g. to
    ``:`` or ``-``) would still classify as "single" in
    ``classify_image_token`` (which only inspects the first
    segment), but the path-with-colon parsing on Windows would
    break — silent regression.
    """
    return f"single|{path}|{side}"


def make_grid_token(path: str, thumb_side: int) -> str:
    """Format the token for a grid-thumbnail request.

    ``thumb_side`` is the requested edge length in pixels (the
    grid view scales every cell to a single max side). Kept as
    an int so callers can't accidentally pass a float and produce
    ``"grid|p.jpg|256.0"`` — which would compare unequal to the
    int-formatted version in any cached-token lookup.
    """
    return f"grid|{path}|{thumb_side}"
