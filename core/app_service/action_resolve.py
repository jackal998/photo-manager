"""Qt-free pattern-matcher extracted from the Qt dialog layer.

All functions here are Qt-free — no PySide6, stdlib plus the shared
ranking helper in ``core.services.auto_select`` (#778).  They operate on
duck-typed PhotoGroup / PhotoRecord objects (attribute access) and are
byte-identical in behaviour to their origins in:

  - app/views/dialogs/select_dialog.py  (threshold + top-n + encode/decode)
  - app/views/handlers/file_operations.py  (_FIELD_TO_ATTR, _get_record_field)

The canonical source of truth for the logic is the Qt files above.  This
module is the Qt-free copy that the web-API route uses.  The anti-drift
pin lives in tests/test_action_resolve_parity.py.
"""

from __future__ import annotations

import re as _re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.services.auto_select import top_n_paths


# ---------------------------------------------------------------------------
# Field metadata
# ---------------------------------------------------------------------------

# Fields whose underlying record attribute is numeric (or a datetime that
# maps cleanly to a sortable timestamp).  Mirrors select_dialog._NUMERIC_FIELDS.
_NUMERIC_FIELDS: frozenset[str] = frozenset({
    "Size (Bytes)",
    "Group Count",
    "Similarity",
    "Score",
    "Creation Date",
    "Shot Date",
})

# Mapping from column label to record attribute used by _get_record_field.
# Mirrors file_operations._FIELD_TO_ATTR.
_FIELD_TO_ATTR: dict[str, str] = {
    "File Name":     "file_path",      # basename extracted in _get_record_field
    "Folder":        "folder_path",
    "Action":        "user_decision",
    "Lock":          "is_locked",      # bool → "Locked"/"" in _get_record_field
    "Size (Bytes)":  "file_size_bytes",
    "Creation Date": "creation_date",
    "Shot Date":     "shot_date",
    "Score":         "score",          # float ∈ [0, 1]; None for passenger MOVs
    "Resolution":    "pixel_width",    # placeholder; rendered as "WxH"
}


# ---------------------------------------------------------------------------
# Pattern-string prefixes (mirrors select_dialog constants)
# ---------------------------------------------------------------------------

PATTERN_CMP_PREFIX = "__cmp__:"
PATTERN_TOP_N_PREFIX = "__top_n__:"

# Threshold-comparison operators (label, i18n-key) — order matches the
# Qt dialog dropdown so encode/decode round-trips are predictable.
_CMP_OPS: list[tuple[str, str]] = [
    (">",  "action_dialog.cmp_op_gt"),
    (">=", "action_dialog.cmp_op_ge"),
    ("<",  "action_dialog.cmp_op_lt"),
    ("<=", "action_dialog.cmp_op_le"),
    ("==", "action_dialog.cmp_op_eq"),
    ("!=", "action_dialog.cmp_op_ne"),
]


# ---------------------------------------------------------------------------
# _get_record_field — mirrors file_operations._get_record_field
# ---------------------------------------------------------------------------

def _get_record_field(rec: Any, field: str) -> str | None:
    """Return the string value of a record's field, or None if unavailable.

    The ``Lock`` field maps a boolean ``is_locked`` to the string
    ``"Locked"`` (truthy) or ``""`` (falsy) so users can regex-match
    locked rows with ``^Locked$`` and unlocked rows with ``^$``.

    The ``Resolution`` field formats ``pixel_width × pixel_height``
    using the same ``×`` (U+00D7) glyph the tree's COL_RESOLUTION cell
    uses.  Returns None when either dimension is missing.
    """
    attr = _FIELD_TO_ATTR.get(field)
    if attr is None:
        return None
    if field == "Resolution":
        px_w = getattr(rec, "pixel_width", None)
        px_h = getattr(rec, "pixel_height", None)
        if not px_w or not px_h:
            return None
        return f"{px_w}×{px_h}"
    val = getattr(rec, attr, None)
    if field == "Lock":
        return "Locked" if bool(val) else ""
    if val is None:
        return None
    if field == "File Name":
        return Path(str(val)).name
    return str(val)


# ---------------------------------------------------------------------------
# Numeric helpers — mirrors select_dialog._numeric_value_for / _parse_threshold
# ---------------------------------------------------------------------------

def _numeric_value_for(field: str, rec: Any, group: Any) -> float | None:
    """Return the comparable numeric value of ``field`` for ``rec``.

    For date fields the datetime is converted to a POSIX timestamp so
    threshold comparisons stay in floats.  Returns ``None`` when the
    attribute is missing or unset — caller skips such records (same
    semantics as ``_get_record_field`` returning ``None``).
    """
    if field == "Size (Bytes)":
        val = getattr(rec, "file_size_bytes", None)
        return float(val) if val is not None else None
    if field == "Group Count":
        items = getattr(group, "items", None)
        return float(len(items)) if items is not None else None
    if field == "Similarity":
        val = getattr(rec, "hamming_distance", None)
        return float(val) if val is not None else None
    if field == "Score":
        val = getattr(rec, "score", None)
        return float(val) if val is not None else None
    if field == "Creation Date":
        d = getattr(rec, "creation_date", None)
        try:
            return d.timestamp() if d is not None else None
        except Exception:
            return None
    if field == "Shot Date":
        d = getattr(rec, "shot_date", None)
        try:
            return d.timestamp() if d is not None else None
        except Exception:
            return None
    return None


def _parse_threshold(field: str, text: str) -> float | None:
    """Parse the user's threshold text into a numeric value to compare against.

    Pure numeric fields accept a float.  Date fields accept ISO ``YYYY-MM-DD``
    (or ``YYYY-MM-DD HH:MM:SS``) and we convert to a timestamp so the
    threshold matches ``_numeric_value_for``'s float-of-timestamp form.
    Returns ``None`` for unparseable input — the caller treats this as
    a zero-match condition.
    """
    text = (text or "").strip()
    if not text:
        return None
    if field in ("Creation Date", "Shot Date"):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).timestamp()
            except ValueError:
                continue
        # Fall through to bare-float — lets power users paste a timestamp.
    try:
        return float(text)
    except ValueError:
        return None


def _cmp_apply(value: float, op: str, threshold: float) -> bool:
    """Evaluate ``value <op> threshold``.  Unknown op → False (defensive)."""
    if op == ">":  return value > threshold
    if op == ">=": return value >= threshold
    if op == "<":  return value < threshold
    if op == "<=": return value <= threshold
    if op == "==": return value == threshold
    if op == "!=": return value != threshold
    return False


# ---------------------------------------------------------------------------
# Public selection helpers — mirrors select_dialog public functions
# ---------------------------------------------------------------------------

def select_paths_by_threshold(
    groups: list, field: str, op: str, threshold_text: str
) -> list[str]:
    """Return file_paths from ``groups`` whose ``field`` value passes ``op threshold``.

    Records whose numeric value is missing (None) are skipped.  Order is
    group-then-record so the caller's truncated lock-confirm list reads as
    user tree order.
    """
    threshold = _parse_threshold(field, threshold_text)
    if threshold is None:
        return []
    matched: list[str] = []
    for group in groups:
        for rec in getattr(group, "items", []):
            val = _numeric_value_for(field, rec, group)
            if val is None:
                continue
            if _cmp_apply(val, op, threshold):
                matched.append(rec.file_path)
    return matched


def select_paths_top_n(
    groups: list, field: str, n: int, order: str
) -> list[str]:
    """Return file_paths ranked top (or bottom) ``n`` within each group.

    ``order='desc'`` selects the N records with the LARGEST values —
    "top by score" picks the keepers.  ``order='asc'`` selects the N
    records with the SMALLEST values — "bottom by score" picks the
    deletables.  Ties break by file_path so the selection is stable
    across re-runs.  Records with no numeric value (None) are excluded
    from ranking entirely.

    When a group has fewer than N rankable records, all of its rankable
    records are selected.

    #778 — the ranking itself lives in
    :func:`core.services.auto_select.top_n_paths`, the single home of the
    top-N-by-score rule. This function owns only the part that is specific
    to this surface: turning a group's records into ``(value, path)`` pairs
    via :func:`_numeric_value_for`. ``top_score_path_per_group`` (the
    apply-best-copy / post-scan auto-select surface) calls the same helper,
    so the two can no longer drift apart on tie-breaks or score tiers —
    pinned by ``tests/test_topn_keeper_parity.py``.
    """
    if n <= 0 or order not in ("asc", "desc"):
        return []
    matched: list[str] = []
    for group in groups:
        ranked: list[tuple[float, str]] = []
        for rec in getattr(group, "items", []):
            val = _numeric_value_for(field, rec, group)
            if val is None:
                continue
            ranked.append((val, rec.file_path))
        matched.extend(top_n_paths(ranked, n, order))
    return matched


# ---------------------------------------------------------------------------
# Encode / decode helpers — mirrors select_dialog encode/decode functions
# ---------------------------------------------------------------------------

def encode_cmp_pattern(op: str, value_text: str) -> str:
    """Encode a threshold condition as a single pattern string.

    Format: ``__cmp__:<op>:<value_text>``.  Value text is the user's raw
    input so the receiver can re-parse it with the field's own rules
    (numeric vs. ISO date).  Value is the last segment so user input
    containing ``:`` (e.g. ``2026-01-01 12:00:00``) still round-trips.
    """
    return f"{PATTERN_CMP_PREFIX}{op}:{value_text}"


def encode_top_n_pattern(n: int, order: str) -> str:
    """Encode a Top-N condition as a pattern string.

    Format: ``__top_n__:<n>:<order>`` where order ∈ {asc, desc}.
    """
    return f"{PATTERN_TOP_N_PREFIX}{n}:{order}"


def decode_cmp_pattern(pattern: str) -> tuple[str, str] | None:
    """Reverse of :func:`encode_cmp_pattern`.  Returns (op, value_text)
    or ``None`` if the pattern isn't a cmp-encoded string.
    """
    if not pattern.startswith(PATTERN_CMP_PREFIX):
        return None
    rest = pattern[len(PATTERN_CMP_PREFIX):]
    # op is first segment, value is everything after — value may contain `:`
    if ":" not in rest:
        return None
    op, value_text = rest.split(":", 1)
    return op, value_text


def decode_top_n_pattern(pattern: str) -> tuple[int, str] | None:
    """Reverse of :func:`encode_top_n_pattern`.  Returns (n, order) or
    ``None`` if pattern isn't a top-n-encoded string.
    """
    if not pattern.startswith(PATTERN_TOP_N_PREFIX):
        return None
    rest = pattern[len(PATTERN_TOP_N_PREFIX):]
    parts = rest.split(":", 1)
    if len(parts) != 2:
        return None
    try:
        n = int(parts[0])
    except ValueError:
        return None
    order = parts[1]
    if order not in ("asc", "desc"):
        return None
    return n, order


# ---------------------------------------------------------------------------
# Unified dispatcher — the public API consumed by the web-API route
# ---------------------------------------------------------------------------

def resolve_matched_paths(groups: list, field: str, pattern: str) -> list[str]:
    """Return file_paths from ``groups`` that match ``pattern`` on ``field``.

    Dispatch order:
      1. Empty-pattern guard — returns [] immediately if ``not pattern``.
         Load-bearing: ``re.search("", x)`` is truthy for every string so
         an empty pattern would match every row (pinned by
         test_file_operations.py:1523).
      2. ``__cmp__:`` prefix → select_paths_by_threshold.
      3. ``__top_n__:`` prefix → select_paths_top_n.
      4. Plain regex (case-insensitive) over _get_record_field in
         group-then-record order, mirroring file_operations.py:1387-1408.
         ``re.error`` propagates — the route maps it to 400.

    ``field`` must be a key present in ``_FIELD_TO_ATTR`` or a numeric-only
    field from ``_NUMERIC_FIELDS``; unknown fields yield [] (same as the Qt
    handler when _FIELD_TO_ATTR lookup returns None).

    Parameters
    ----------
    groups:
        Iterable of duck-typed group objects with a ``.items`` attribute
        (list of record objects with ``.file_path``, ``.score``, etc.).
    field:
        Column label string, e.g. ``"File Name"``, ``"Score"``,
        ``"Size (Bytes)"``, etc.
    pattern:
        Raw pattern string — empty, ``__cmp__:…``, ``__top_n__:…``,
        or a plain regex.
    """
    if not pattern:
        return []

    # Mirror file_operations.py:1387-1408 EXACTLY: a `__cmp__:`/`__top_n__:`
    # PREFIX commits to that branch — a malformed payload RAISES ValueError, it
    # does NOT fall through to plain regex. Falling through would treat
    # `__cmp__:weird` as a literal regex and could mutate a file whose path
    # contains that substring; the Qt path rejects it. The route maps this
    # ValueError to HTTP 422. (Parity-pinned by test_action_resolve_parity.py.)
    if pattern.startswith(PATTERN_CMP_PREFIX):
        decoded_cmp = decode_cmp_pattern(pattern)
        if decoded_cmp is None:
            raise ValueError(pattern)
        op, value_text = decoded_cmp
        return select_paths_by_threshold(groups, field, op, value_text)

    if pattern.startswith(PATTERN_TOP_N_PREFIX):
        decoded_top_n = decode_top_n_pattern(pattern)
        if decoded_top_n is None:
            raise ValueError(pattern)
        n, order = decoded_top_n
        return select_paths_top_n(groups, field, n, order)

    # Plain regex — re.error propagates intentionally.
    rx = _re.compile(pattern, _re.IGNORECASE)
    out: list[str] = []
    for group in groups:
        for rec in getattr(group, "items", []):
            value = _get_record_field(rec, field)
            if value is not None and rx.search(value):
                out.append(rec.file_path)
    return out
