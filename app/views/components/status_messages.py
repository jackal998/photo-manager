"""Shared status-bar message formatters.

Centralizes the verb / count / pluralization / timeout convention so every
manifest-changing operation reports back to the user the same way.

Convention:
  * Use **past-tense verbs** for completed actions ("Removed", "Executed",
    "Saved"). Reserve present-progressive for in-progress operations
    ("Opening manifest…") which use a 0-timeout (persistent) write.
  * Default timeout for completed actions is 3 seconds. Use 5+ seconds
    only for messages the user must actually read (failures, summaries).
  * Pluralization: ``report_count`` handles the trailing ``s`` so callers
    don't reinvent the ``f"{n} item(s)"`` pattern at every site.
"""
from __future__ import annotations

from typing import Protocol

DEFAULT_TIMEOUT_MS = 3000


class _StatusReporter(Protocol):
    def show_status(self, message: str, timeout: int = DEFAULT_TIMEOUT_MS) -> None: ...


def report_count(
    reporter: _StatusReporter,
    verb: str,
    count: int,
    noun: str,
    timeout: int = DEFAULT_TIMEOUT_MS,
) -> None:
    """Report a completed action with a count. e.g. ``Removed 5 items``.

    Pluralizes *noun* by appending ``s`` when count is not exactly 1.
    """
    suffix = "" if count == 1 else "s"
    reporter.show_status(f"{verb} {count} {noun}{suffix}", timeout)
