"""Pure pHash Hamming-distance helper.

Extracted so the tree renderer can recompute distances at render time
(#253) without importing scanner internals like ``scanner.dedup``.
Scanner-side near-duplicate classification still uses its own inline
``imagehash.hex_to_hash`` calls — sharing this helper would force
``dedup`` to import a sibling for one call.
"""
from __future__ import annotations

try:
    import imagehash
    _IMAGEHASH_AVAILABLE = True
except ImportError:
    _IMAGEHASH_AVAILABLE = False


def hamming_distance(phash_a: str | None, phash_b: str | None) -> int | None:
    """Return pHash Hamming distance between two hex strings.

    Returns ``None`` when either input is missing/empty, when imagehash
    is not installed (CI without optional deps), or when a hex string is
    malformed. ``None`` signals "fall back to whatever the caller stored
    elsewhere" rather than raising — the renderer must never crash on a
    bad row.
    """
    if not phash_a or not phash_b or not _IMAGEHASH_AVAILABLE:
        return None
    try:
        return imagehash.hex_to_hash(phash_a) - imagehash.hex_to_hash(phash_b)
    except (ValueError, TypeError):
        return None
