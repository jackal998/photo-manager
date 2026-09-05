"""Headless display derivation for the duplicate-review manifest.

Ports the pure logic from app/views/tree_model_builder.py verbatim,
minus Qt, minus the t() localisation — returns structured kinds instead
of translated strings so the web client renders its own labels.

NO PySide6 imports here — this module must stay headless so it runs in
the FastAPI process without a display.
"""

from __future__ import annotations

import math
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

from scanner.media import is_video as _is_video
from scanner.phash_distance import hamming_distance as _phash_hamming

# ---------------------------------------------------------------------------
# Action-sort priorities — verbatim from tree_model_builder._ACTION_SORT.
# Ref tier == 1; missing key → 1 (treated as Ref tier).
# ---------------------------------------------------------------------------
_ACTION_SORT: dict[str, int] = {
    "KEEP": 1,
    "UNDATED": 1,
    "": 1,
    "EXACT": 2,
    "REVIEW_DUPLICATE": 3,
}


def pick_ref_winner(items_list: list[Any]) -> Any | None:
    """Return the item that should carry the 'Ref' label within its group.

    Verbatim port of tree_model_builder._pick_ref_winner:
    - Only considers Ref-tier items (priority == 1).
    - Tie-break key: (priority, -(score or -inf), str(file_path)).
    - Returns None if no Ref-tier item exists.
    """
    best_item: Any | None = None
    best_key: tuple | None = None
    for it in items_list:
        action = getattr(it, "action", "") or ""
        priority = _ACTION_SORT.get(action, 1)
        if priority != 1:
            continue  # not Ref-tier — skip
        score = getattr(it, "score", None)
        score_key = -(score if score is not None else -math.inf)
        path_key = str(getattr(it, "file_path", ""))
        key = (priority, score_key, path_key)
        if best_key is None or key < best_key:
            best_key = key
            best_item = it
    return best_item


def compute_similarity(
    action: str,
    record: Any,
    is_ref_winner: bool,
    ref_phash: str | None,
) -> dict[str, Any]:
    """Return structured similarity for a file row.

    Verbatim port of tree_model_builder._file_similarity, returning
    {"kind": str, "percent": int|None} instead of a localised string.

    Kinds:
      "percent"    — EXACT (100%) or REVIEW_DUPLICATE (computed %)
      "ref"        — this row is the Ref winner
      "passenger"  — Ref-tier non-winner with a computable pHash distance (starred)
      "near_dup"   — REVIEW_DUPLICATE with no usable pHash
      "none"       — Ref-tier non-winner with no usable pHash (bare "—")
    """
    if action == "EXACT":
        return {"kind": "percent", "percent": 100}

    if action == "REVIEW_DUPLICATE":
        record_phash = getattr(record, "phash", None)
        recomputed = _phash_hamming(ref_phash, record_phash)
        if recomputed is not None:
            return {"kind": "percent", "percent": round((64 - recomputed) / 64 * 100)}
        stored = getattr(record, "hamming_distance", None)
        if stored is not None:
            return {"kind": "percent", "percent": round((64 - stored) / 64 * 100)}
        return {"kind": "near_dup", "percent": None}

    if is_ref_winner:
        return {"kind": "ref", "percent": None}

    # Ref-tier passenger: show similarity to the displayed Ref with a star,
    # matching tree_model_builder's #536 Direction A behaviour.
    record_phash = getattr(record, "phash", None)
    vs_ref = _phash_hamming(ref_phash, record_phash)
    if vs_ref is not None:
        return {"kind": "passenger", "percent": round((64 - vs_ref) / 64 * 100)}
    return {"kind": "none", "percent": None}


def _normalize_decision(user_decision: str) -> str:
    """Map legacy 'keep' → '' (canonical undecided); pass all other values through."""
    if user_decision == "keep":
        return ""
    return user_decision


def _datetime_to_iso(dt: datetime | None) -> str | None:
    """Return ISO 8601 string or None."""
    if dt is None:
        return None
    return dt.isoformat()


def _thumbnail_url(file_path: str) -> str:
    """Build the /api/image thumbnail URL for a file path."""
    return "/api/image?path=" + urllib.parse.quote(file_path, safe="") + "&size=512"


def _build_file_row(
    record: Any,
    is_ref_winner: bool,
    ref_phash: str | None,
) -> dict[str, Any]:
    """Build a FileRow dict for one PhotoRecord."""
    action = getattr(record, "action", "") or ""
    raw_decision = getattr(record, "user_decision", "") or ""
    file_path = str(getattr(record, "file_path", ""))

    return {
        "file_path": file_path,
        "basename": Path(file_path).name,
        "folder": getattr(record, "folder_path", ""),
        "action": action,
        "user_decision": _normalize_decision(raw_decision),
        "is_locked": bool(getattr(record, "is_locked", False)),
        "is_ref_winner": is_ref_winner,
        "similarity": compute_similarity(action, record, is_ref_winner, ref_phash),
        "score": getattr(record, "score", None),
        # #680 — the per-dimension signals the composite ``score`` is built
        # from, so the web QA harness can assert extraction→scoring→storage
        # wiring per dimension instead of only through the aggregate.
        # Nullability mirrors the manifest columns (core.models.PhotoRecord):
        # exif_tag_count may be null (census pass did not run); the other two
        # are NOT NULL in SQLite and so are always a bool here. They are
        # populated by extraction, not by scoring, so a row with score=null
        # still carries real values. Passed through unconverted, like every
        # other field above: SQLite's INTEGER 0/1 becomes a real bool at the
        # single construction site, manifest_repository._photo_record, which
        # test_scoring_signals_load_from_db_into_photo_record pins.
        "exif_tag_count": getattr(record, "exif_tag_count", None),
        "gps_present": getattr(record, "gps_present", False),
        "xmp_derived": getattr(record, "xmp_derived", False),
        "file_size_bytes": int(getattr(record, "file_size_bytes", 0) or 0),
        "pixel_width": getattr(record, "pixel_width", None),
        "pixel_height": getattr(record, "pixel_height", None),
        "shot_date": _datetime_to_iso(getattr(record, "shot_date", None)),
        "creation_date": _datetime_to_iso(getattr(record, "creation_date", None)),
        "phash": getattr(record, "phash", None),
        "hamming_distance": getattr(record, "hamming_distance", None),
        "thumbnail_url": _thumbnail_url(file_path),
        "media_type": "video" if _is_video(file_path) else "image",
    }


def serialize_groups(groups: list[Any]) -> list[dict[str, Any]]:
    """Serialize a list of PhotoGroup objects into API dicts.

    Preserves item order as given — MainVM already produces correctly
    ordered groups (score-DESC within each group).
    """
    result: list[dict[str, Any]] = []
    for group in groups:
        group_number = int(getattr(group, "group_number", 0) or 0)
        items_list = getattr(group, "items", []) or []

        ref = pick_ref_winner(items_list)
        ref_phash = getattr(ref, "phash", None) if ref is not None else None

        file_rows = [
            _build_file_row(record, record is ref, ref_phash)
            for record in items_list
        ]

        result.append({
            "group_number": group_number,
            "member_count": len(items_list),
            "items": file_rows,
        })

    return result
