"""Row-density state for the results tree (Comfortable / Compact).

A single process-wide setting the tree delegates read from their
``sizeHint`` to size each row. Kept as a tiny module rather than threaded
through every delegate constructor because all delegates must agree on
one height, and the View → Density menu flips it globally.

Comfortable (default) gives a calm, low-fatigue row for long review
sessions; Compact fits more groups on screen for power users with large
libraries — the trade the design's density toggle is for. The delegates
cap their painted pills at ``row_height - 6`` so they shrink cleanly in
Compact without code changes here.
"""

from __future__ import annotations

# code → row height in px
_ROW_HEIGHTS: dict[str, int] = {
    "comfortable": 30,
    "compact": 22,
}
DEFAULT: str = "comfortable"

_current: str = DEFAULT


def available() -> list[tuple[str, str]]:
    """(code, i18n key) for each density, in menu display order."""
    return [
        ("comfortable", "menu.view.density.comfortable"),
        ("compact", "menu.view.density.compact"),
    ]


def set_density(name: str) -> None:
    """Set the active density, falling back to the default for unknown names."""
    global _current
    _current = name if name in _ROW_HEIGHTS else DEFAULT


def current() -> str:
    return _current


def row_height() -> int:
    return _ROW_HEIGHTS.get(_current, _ROW_HEIGHTS[DEFAULT])
