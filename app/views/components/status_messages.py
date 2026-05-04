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


def plural_form(n: int, singular: str, plural: str | None = None) -> str:
    """Return the noun form (singular vs plural) without the count.

    Use this when you need custom number formatting (thousands separator,
    locale, etc.) at the call site::

        f"{n:,} {plural_form(n, 'isolated file')}"   # "10,000 isolated files"

    For the common case of plain ``"<n> <noun>"``, use ``pluralize`` instead.

    The default plural is ``f"{singular}s"``. Pass an explicit ``plural``
    only for irregular forms (geese, mice, children).
    """
    if n == 1:
        return singular
    return plural or singular + "s"


def pluralize(n: int, singular: str, plural: str | None = None) -> str:
    """Return ``"<n> <singular>"`` when n == 1, else ``"<n> <plural>"``.

    Use this for status-bar messages that contain multiple count+noun
    phrases (where ``report_count`` doesn't fit) or for irregular plurals
    where appending ``s`` is wrong.

    Examples:
        ``pluralize(1, "pair")`` → ``"1 pair"``
        ``pluralize(5, "pair")`` → ``"5 pairs"``
        ``pluralize(3, "isolated file")`` → ``"3 isolated files"``
        ``pluralize(2, "child", "children")`` → ``"2 children"``
    """
    return f"{n} {plural_form(n, singular, plural)}"


def report_count(
    reporter: _StatusReporter,
    verb: str,
    count: int,
    noun: str,
    plural: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_MS,
) -> None:
    """Report a completed action with a count. e.g. ``Removed 5 items``.

    Default pluralization appends ``s`` to *noun* when count is not
    exactly 1. For irregular plurals (children) or multi-word phrases
    where the bare suffix rule lands on the wrong word
    ("item from list" → "item from lists"), pass *plural* explicitly:

        report_count(reporter, "Removed", n, "item from list",
                     plural="items from list")
    """
    form = noun if count == 1 else (plural or noun + "s")
    reporter.show_status(f"{verb} {count} {form}", timeout)
