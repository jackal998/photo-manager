"""Live Photo pairing for auto-select rule B6 (PR-4b, PRD v2 section 6).

A Live Photo is a still (HEIC or JPG) and a motion clip (MOV or MP4) that
share a ContentIdentifier. This module finds, for each Live video, the
stills it belongs to. It is pure: it reads only ``group_id``,
``source_path`` and ``content_identifier`` from each row.

The identifier says which capture a file belongs to; the folder says which
copy of that capture. A library often holds one capture twice (a camera
roll and a backup of it), and each copy's video follows its own still. File
names take no part: a renamed or re-exported video is still the video of
its capture (GROUPING 18).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from scanner.media import is_video


def content_identifier(row: object) -> str | None:
    """Return the row's ContentIdentifier, or None when it is absent or empty."""
    return getattr(row, "content_identifier", None) or None


def _pairing_key(row: object) -> tuple[str, str, str] | None:
    """Return ``(group, folder, CID)`` for a row that may pair.

    None when the row has no group or no CID.
    """
    cid = content_identifier(row)
    if row.group_id is None or cid is None:
        return None
    return (row.group_id, str(Path(row.source_path).parent), cid)


def live_photo_stills(rows: Iterable) -> dict[str, tuple[str, ...]]:
    """Map each Live video's source_path to all of its qualifying stills.

    A still qualifies for a video when it shares the video's group, folder
    and ContentIdentifier. The file name is not part of the key, so a renamed
    video pairs with its still, and an edited copy that kept the identifier
    is one of the stills the video waits for. Videos with no qualifying still
    are absent from the map. Paths in each tuple are sorted.
    """
    still_paths: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    video_keys: list[tuple[str, tuple[str, str, str]]] = []
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
