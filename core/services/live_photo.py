"""Live Photo pairing for auto-select rule B6 (PR-4b, PRD v2 section 6).

A Live Photo is a still (HEIC or JPG) and a motion clip (MOV or MP4) that
share a ContentIdentifier. This module finds, for each Live video, the
stills it belongs to. It is pure: it reads only ``group_id``,
``source_path`` and ``content_identifier`` from each row, and it reuses the
walker's filename parser so the clean-stem rule has one definition.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from scanner.media import is_video, parse_media_filename


def content_identifier(row: object) -> str | None:
    """Return the row's ContentIdentifier, or None when it is absent or empty."""
    return getattr(row, "content_identifier", None) or None


def _pairing_key(row: object) -> tuple[str, str, str, str] | None:
    """Return ``(group, folder, clean stem, CID)`` for a row that may pair.

    None when the row has no group, no CID, or an edited name. The walker
    never clusters edited files, so an edited copy must not pair either.
    """
    cid = content_identifier(row)
    if row.group_id is None or cid is None:
        return None
    path = Path(row.source_path)
    mf = parse_media_filename(path)
    if mf.is_edited:
        return None
    return (row.group_id, str(path.parent), mf.clean_stem, cid)


def live_photo_stills(rows: Iterable) -> dict[str, tuple[str, ...]]:
    """Map each Live video's source_path to all of its qualifying stills.

    A still qualifies for a video when it shares the video's group, folder,
    clean stem and ContentIdentifier, and it is not edited. The number
    marker ``(N)`` is not part of the key, so ``IMG_1(1).MOV`` pairs with
    ``IMG_1.HEIC``. Videos with no qualifying still are absent from the map.
    Paths in each tuple are sorted.
    """
    still_paths: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    video_keys: list[tuple[str, tuple[str, str, str, str]]] = []
    for row in rows:
        key = _pairing_key(row)
        if key is None:
            continue
        if is_video(row.source_path):
            video_keys.append((row.source_path, key))
        else:
            still_paths[key].append(row.source_path)
    return {
        video: tuple(sorted(still_paths[key]))
        for video, key in video_keys
        if still_paths.get(key)
    }
