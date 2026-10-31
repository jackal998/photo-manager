"""Lock gate for guarded writes: the refusal signal, the verdicts and the planner.

The refusal itself lives in the write primitive
(``ManifestTxn.refuse_unconfirmed`` in ``infrastructure/manifest_repository.py``),
so a caller that forgets the gate still gets a 409. This module only decides
which rows a write may touch: it narrows the scope (SKIP), names the locked
rows the user confirmed (FORCE), and passes the rest to the primitive (ASK).

Design: ``docs/audits/2026-10-17-write-chokepoint/design.md`` section 2.2.
No I/O here; the module imports nothing from the infrastructure layer at
runtime, so the repository can import ``LockedRowsError`` without a cycle.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from infrastructure.manifest_repository import DecisionWrite, ManifestTxn


class LockedRowsError(ValueError):
    """A guarded write targets locked rows that were not confirmed.

    Subclasses ``ValueError`` and keeps ``args == ("locked_paths", paths)`` or
    ``("locked_paths", paths, matched_total)``, so the route branches that
    match ``args[0] == "locked_paths"`` keep answering 409 unchanged.
    """

    def __init__(self, paths: Iterable[str], matched_total: int | None = None) -> None:
        self.locked_paths: list[str] = list(paths)
        self.matched_total = matched_total
        args: tuple[Any, ...] = ("locked_paths", self.locked_paths)
        if matched_total is not None:
            args = (*args, matched_total)
        super().__init__(*args)


class LockFlagsConflictError(ValueError):
    """``confirmed_locked`` and ``skip_locked`` were both set (B-6).

    Plain ``ValueError`` for every verb; the routes map it to 422.
    """


class LockVerdict(Enum):
    ASK = "ask"      # refuse locked rows unless confirmed (the primitive refuses)
    FORCE = "force"  # write the locked rows the user confirmed, unlocking them
    SKIP = "skip"    # drop the locked rows from the write


def verdict_from_named(
    confirmed_locked: Collection[str], skip_locked: bool
) -> tuple[LockVerdict, list[str] | None]:
    """Map a request's named confirmation to one verdict (A6, A8).

    A non-empty ``confirmed_locked`` is FORCE with those paths as the named
    set; nothing is confirmed by a boolean. Naming and ``skip_locked`` together
    is refused for every verb (B-6). Returns ``(verdict, named)``, where
    ``named`` is None unless the verdict is FORCE.
    """
    named = list(confirmed_locked)
    if named and skip_locked:
        raise LockFlagsConflictError(
            "confirmed_locked and skip_locked are mutually exclusive"
        )
    if named:
        return LockVerdict.FORCE, named
    if skip_locked:
        return LockVerdict.SKIP, None
    return LockVerdict.ASK, None


def plan_locked_write(
    scope: Iterable[str],
    locked_now: Collection[str],
    verdict: LockVerdict,
    *,
    skip_allowed: bool,
    named: Collection[str] | None = None,
) -> tuple[list[str], frozenset[str]]:
    """Return ``(paths to write, confirmed locked paths)``. It does NOT refuse.

    ASK   -> (scope, {})                          the primitive refuses any locked path
    FORCE -> (scope, named)                       only the NAMED locked paths are confirmed
    SKIP  -> (scope - locked_now, {})             only where ``skip_allowed``
    """
    scope_list = list(scope)
    locked_in_scope = frozenset(p for p in scope_list if p in locked_now)
    if verdict is LockVerdict.SKIP:
        if not skip_allowed:
            raise ValueError("skip_locked is not allowed for this write")
        return [p for p in scope_list if p not in locked_in_scope], frozenset()
    if verdict is LockVerdict.FORCE:
        return scope_list, frozenset(named or ())
    return scope_list, frozenset()


def apply_gated_writes(
    tx: ManifestTxn,
    writes: list[DecisionWrite],
    verdict: LockVerdict,
    *,
    skip_allowed: bool = True,
    named: Collection[str] | None = None,
) -> list[str]:
    """Plan inside the open transaction, then write. Returns the paths written.

    ``locked_now`` comes from the same transaction as the write, so the plan
    and the primitive's refusal see one state of the manifest.
    """
    scope = [w.path for w in writes]
    paths, confirmed = plan_locked_write(
        scope,
        tx.locked_paths(),
        verdict,
        skip_allowed=skip_allowed,
        named=named,
    )
    keep = set(paths)
    kept = [w for w in writes if w.path in keep]
    tx.write_decisions(kept, confirmed=confirmed, matched_total=len(scope))
    return [w.path for w in kept]


def _confirmed_for(
    tx: ManifestTxn,
    paths: list[str],
    verdict: LockVerdict,
    outcome: str,
    named: Collection[str] | None,
) -> frozenset[str]:
    """The locked paths the verdict confirms, read inside the open transaction."""
    _, confirmed = plan_locked_write(
        paths,
        tx.locked_paths(outcome=outcome),
        verdict,
        skip_allowed=False,
        named=named,
    )
    return confirmed


def refuse_gated_outcome(
    tx: ManifestTxn,
    paths: Iterable[str],
    verdict: LockVerdict,
    *,
    outcome: str,
    named: Collection[str] | None = None,
) -> None:
    """Refuse locked rows in ``outcome`` that the verdict does not confirm (A4).

    Used by Execute's up-front gate, before any file is touched. The primitive
    reads the locks itself, so a stale plan cannot switch the refusal off.
    """
    scope = list(paths)
    confirmed = _confirmed_for(tx, scope, verdict, outcome, named)
    tx.refuse_unconfirmed(scope, confirmed, outcome=outcome)


def apply_gated_transition(
    tx: ManifestTxn,
    paths: Iterable[str],
    verdict: LockVerdict,
    *,
    from_outcome: str,
    to_outcome: str,
    lock_after: bool = False,
    named: Collection[str] | None = None,
) -> int:
    """Plan inside the open transaction, then transition. Returns rows changed.

    Outcome writes have no SKIP here: a verb that skips locked rows narrows
    its scope before calling this. ``transition`` refuses what the verdict
    does not confirm (A4, design 2.2); under FORCE only the ``named`` rows
    are confirmed (A8).
    """
    scope = list(paths)
    confirmed = _confirmed_for(tx, scope, verdict, from_outcome, named)
    return tx.transition(
        scope,
        from_outcome=from_outcome,
        to_outcome=to_outcome,
        confirmed=confirmed,
        lock_after=lock_after,
    )
