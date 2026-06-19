"""Custom QStyledItemDelegate painters for the Daylight · light results tree.

Each delegate owns the rich rendering of one column the flat-text model
can't express on its own. They read semantic data roles set by
``tree_model_builder`` (e.g. ``SIMILARITY_KIND_ROLE``) rather than
re-parsing display text, and pull every colour from the shared
``app.views.theme`` tokens so the palette stays in one place.
"""

from __future__ import annotations

from app.views.delegates.similarity_badge import SimilarityBadgeDelegate

__all__ = ["SimilarityBadgeDelegate"]
