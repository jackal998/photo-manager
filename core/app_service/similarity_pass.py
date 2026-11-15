"""The scan's last measuring step: how alike each grouped picture and its
group's Ref look.

Runs after grouping, scoring and auto-select, because the Ref the review
screen shows is picked from the columns those steps write
(:func:`core.app_service.review_view.pick_ref_winner`). The number is read
from the review thumbnail the hash pass made (``scanner.thumbnail``), so no
file is opened here.
"""
from __future__ import annotations

import io
from collections import defaultdict
from collections.abc import Callable, Iterable

from PIL import Image

from core.app_service.review_view import pick_ref_winner
from scanner.dedup import HashResult, ManifestRow
from scanner.similarity import Signature, percent, similarity


def _signature(hashed: HashResult) -> Signature | None:
    """The signature read from a row's thumbnail, or ``None`` when the
    thumbnail cannot be read back or decoded. Such a row gets no number."""
    if hashed.thumb is None:
        return None
    try:
        with Image.open(io.BytesIO(hashed.thumb.bytes())) as thumbnail:
            return Signature.from_image(thumbnail)
    except (OSError, ValueError, IndexError, RuntimeError):
        return None


def apply_similarity_to_rows(
    rows: Iterable[ManifestRow],
    hash_results: Iterable[HashResult],
    cancelled: Callable[[], bool] = lambda: False,
) -> int:
    """Set ``row.similarity`` on every grouped row that can be measured
    against its group's Ref; return how many rows got a number.

    The Ref itself, rows without a thumbnail (video, a picture that did not
    decode) and every row of a group whose Ref has no thumbnail keep
    ``None``. A row with the Ref's own bytes is 100 without a measurement.
    Stops early, leaving the remaining rows unmeasured, once ``cancelled()``.
    """
    hashed_by_path = {str(h.record.path): h for h in hash_results}
    groups: dict[str, list[ManifestRow]] = defaultdict(list)
    for row in rows:
        if row.group_id:
            groups[row.group_id].append(row)
    measured = 0
    for members in groups.values():
        if cancelled():
            break
        ref = pick_ref_winner(members)
        ref_hashed = hashed_by_path.get(ref.source_path) if ref is not None else None
        ref_signature = _signature(ref_hashed) if ref_hashed is not None else None
        if ref_signature is None:
            continue
        for row in members:
            hashed = hashed_by_path.get(row.source_path)
            if row is ref or hashed is None:
                continue
            if hashed.sha256 == ref_hashed.sha256:
                row.similarity = percent(1.0, identical=True)
            else:
                signature = _signature(hashed)
                if signature is None:
                    continue
                row.similarity = percent(similarity(ref_signature, signature), identical=False)
            measured += 1
    return measured
