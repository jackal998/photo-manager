"""Headless action service: bulk_decide — pattern-matched decision/lock mutations.

All database access is stateless — each function opens its own connection
via ManifestRepository.  SQLite is the only source of truth.

No UI imports — this module must stay headless for the FastAPI process.

Security note: every ``source_path`` resolved by ``resolve_matched_paths`` is
re-validated against ``allowed_roots`` via ``is_under_roots`` before any DB
write.  This mirrors the 2C1 ship-blocker fix in ``execute_service``: an
in-root manifest carrying an out-of-root ``source_path`` row cannot be used to
mutate records outside the trusted root set.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.app_service.action_resolve import resolve_matched_paths
from core.app_service.lock_gate import apply_gated_writes, verdict_from_named
from core.app_service.path_safety import is_under_roots
from core.app_service.review_view import serialize_groups
from core.services.auto_select import (
    build_auto_select_writes,
    non_keepers_for_aggressive_delete,
    top_score_path_per_group,
)
from infrastructure.manifest_repository import DecisionWrite, ManifestRepository
from infrastructure.settings import load_settings


# ---------------------------------------------------------------------------
# Valid action sentinels — wire constants
# ---------------------------------------------------------------------------

_LOCK_ACTIONS: frozenset[str] = frozenset({"__lock__", "__unlock__"})
_DECISION_MAP: dict[str, str] = {
    "":           "",        # undecided
    "delete":     "delete",
    "__ignore__": "ignore",
}


def _require_manifest(manifest_path: str) -> None:
    """Raise FileNotFoundError when manifest_path does not exist on disk."""
    if not Path(manifest_path).is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_path!r}")



def _load_vm(manifest_path: str):
    """Load a MainVM from the manifest and return it.

    Mirrors the internal load logic in review_service.load_review so
    action_service can obtain the raw PhotoGroup / PhotoRecord objects
    (not the already-serialised dicts) needed by resolve_matched_paths.
    """
    from app.viewmodels.main_vm import MainVM

    settings = load_settings()
    default_sort = settings.get("sorting.defaults", [])
    vm = MainVM(default_sort=default_sort)
    vm.load_from_repo(ManifestRepository(), manifest_path)
    return vm


def bulk_decide(
    manifest_path: str,
    field: str,
    pattern: str,
    action: str,
    *,
    allowed_roots: list[str] | None = None,
    confirmed_locked: Collection[str] = (),
    skip_locked: bool = False,
    preview: bool = False,
) -> dict[str, Any]:
    """Apply a decision or lock mutation to all rows matching ``pattern`` on ``field``.

    Parameters
    ----------
    manifest_path:
        Absolute path to the manifest .sqlite file.
    field:
        Column label string (e.g. ``"Score"``, ``"File Name"``).
    pattern:
        Pattern string accepted by ``resolve_matched_paths``:
        ``""`` → empty (no-op), ``__cmp__:op:value``,
        ``__top_n__:n:order``, or a plain case-insensitive regex.
    action:
        One of: ``"__lock__"``, ``"__unlock__"``, ``""`` (undecided),
        ``"delete"``, ``"__ignore__"`` (→ ``"ignore"``).
        Any other value raises ``ValueError``.
    allowed_roots:
        When truthy, every resolved path is re-validated against this list
        before any mutation.  Paths outside all roots are silently excluded
        from the affected set (same semantics as execute_service's filter).
    confirmed_locked:
        The locked paths the user confirmed by name (A8). A decision action
        on a locked row that is not named raises
        ``ValueError("locked_paths", locked, matched_total)`` (three
        positional args; the route matches ``args[0] == "locked_paths"`` → HTTP
        409 and forwards ``matched_total`` so the FE can derive the unlocked
        count without a separate preview round-trip). Named rows are mutated
        and unlocked in the same write. There is no boolean force.
    skip_locked:
        When True, a decision action applies only to the UNLOCKED subset of the
        matched rows; locked rows are left fully untouched (``is_locked`` and
        ``user_decision`` unchanged) — the "Apply to Unlocked Only" verdict
        (#674, mirrors Qt ``LockedRowsConfirmDialog.APPLY_UNLOCKED_ONLY``). The
        returned ``matched`` / ``affected_paths`` report the actually-applied
        unlocked subset. Mutually exclusive with ``confirmed_locked`` — both
        set raises a plain ``ValueError`` (route → HTTP 422).
    preview:
        When True, compute ``affected_paths`` but do not write any DB changes.
        Returns the same shape dict with an unchanged manifest.

    Returns
    -------
    dict with keys:
        - ``matched``: int — number of paths in the affected set.
        - ``affected_paths``: list[str] — the path strings.
        - ``action_applied``: ``"decision"`` | ``"lock"`` | ``"unlock"``.
        - ``groups``: serialised group list (pre-mutation on preview, post-mutation otherwise).

    Raises
    ------
    FileNotFoundError:
        Manifest does not exist on disk.
    re.error:
        ``pattern`` is an invalid regex (plain-regex branch only).
    ValueError:
        ``action`` is not a recognized sentinel.
    ValueError("locked_paths", [...], matched_total):
        Decision action would affect a locked row that is not named in
        ``confirmed_locked`` and ``skip_locked`` is not set.
    ValueError (plain):
        ``confirmed_locked`` and ``skip_locked`` are both set (incoherent request).
    """
    _require_manifest(manifest_path)

    # Mutually-exclusive lock verdicts: "apply to all (confirmed)" and "apply to
    # unlocked only" are contradictory write intents. POST /api/action/bulk-decide
    # is a public HTTP boundary, so an incoherent body is refused here (route → 422).
    verdict, named = verdict_from_named(confirmed_locked, skip_locked)

    vm = _load_vm(manifest_path)
    groups_objs = vm.groups

    # Resolve matching paths — re.error propagates (route → 400).
    resolved: list[str] = resolve_matched_paths(groups_objs, field, pattern)

    # Root-safety filter — mirrors execute_service's per-row check.
    if allowed_roots:
        safe: list[str] = [p for p in resolved if is_under_roots(p, allowed_roots)]
    else:
        safe = resolved

    # Classify action.
    if action == "__lock__":
        kind = "lock"
        lock_value = True
    elif action == "__unlock__":
        kind = "unlock"
        lock_value = False
    elif action in _DECISION_MAP:
        kind = "decision"
        decision_value = _DECISION_MAP[action]
        lock_value = None  # not used for decision actions
    else:
        raise ValueError(
            f"Unknown action {action!r}. "
            "Accepted: '__lock__', '__unlock__', '', 'delete', '__ignore__'."
        )

    action_applied = "lock" if kind == "lock" else ("unlock" if kind == "unlock" else "decision")

    # Preview — dry-run: compute the affected set, write NOTHING, and DO NOT
    # apply the locked-rows gate. A preview must always return the full resolved
    # set (even when it includes locked rows) so the FE can show the user
    # exactly what a real apply would touch; the gate fires on real writes only.
    if preview:
        return {
            "matched": len(safe),
            "affected_paths": safe,
            "action_applied": action_applied,
            "groups": serialize_groups(groups_objs),
        }

    # Apply. Lock verbs stay unguarded (BE-10). A decision write runs the lock
    # gate inside one IMMEDIATE transaction: the locked set is read on the
    # write's own connection, so it covers hidden rows and a lock set after the
    # VM was loaded. The three modes: a non-empty confirmed_locked unlocks the
    # named rows, skip_locked narrows to the unlocked subset, and neither
    # refuses with 409 (the primitive counts matched_total = len(safe)).
    repo = ManifestRepository()
    if kind in ("lock", "unlock"):
        repo.batch_update_lock_state(manifest_path, {p: lock_value for p in safe})
    else:
        writes = [DecisionWrite(path=p, decision=decision_value) for p in safe]
        with repo.write_txn(manifest_path) as tx:
            safe = apply_gated_writes(tx, writes, verdict, named=named)

    # Reload for the fresh post-mutation serialisation.
    vm2 = _load_vm(manifest_path)
    fresh_groups = serialize_groups(vm2.groups)

    return {
        "matched": len(safe),
        "affected_paths": safe,
        "action_applied": action_applied,
        "groups": fresh_groups,
    }


def apply_best_copy(
    manifest_path: str,
    group_number: int,
    *,
    confirmed_locked: Collection[str] = (),
    skip_locked: bool = False,
) -> dict[str, Any]:
    """Apply best-copy decisions to ONE duplicate group (#744).

    The review-time twin of scan-time auto-select
    (:mod:`core.services.auto_select`): within the target group, the
    highest-scoring row becomes the keeper (``user_decision=""`` +
    ``is_locked=True``); every other row the classifier positively identified
    as a duplicate (``EXACT`` / ``REVIEW_DUPLICATE``) receives
    ``user_decision="delete"``. A Ref-tier row pulled into the group by the
    ungated filename pair edge (RAW+JPG / Live-Photo MOV passenger) is left
    untouched — same allowlist :func:`core.services.auto_select.
    non_keepers_for_aggressive_delete` uses for the scan-time aggressive path.

    Deliberately NO ``match_confidence`` filtering: that field lives only on
    the scanner's transient ``ManifestRow`` shape and is never persisted to
    the manifest DB, so ``core.models.PhotoRecord`` (what this function reads)
    has no such attribute — the shared helper's ``getattr(..., None) != "low"``
    check is naturally always-True here. This is intentional, not a gap (#744
    scope decision).

    Locked-rows gate mirrors :func:`set_decisions` / ``bulk_decide``: if any
    row that would be WRITTEN (keeper or delete-target) is currently locked,
    the write is refused with ``ValueError("locked_paths", locked,
    matched_total)`` unless every such locked row is named in
    ``confirmed_locked`` or ``skip_locked`` is set. A named previously-locked
    delete-target is unlocked in the same write; a locked row that is not
    named is refused, so a lock set after the dialog opened is confirmed
    again (A8, D7). The keeper's own lock write (``True``) is unaffected.
    ``skip_locked`` narrows the write to the unlocked subset only (locked rows
    keep both their decision and lock state untouched).

    Args:
        manifest_path: Absolute path to the manifest .sqlite file.
        group_number: The ``group_number`` assigned by the most recent
            ``ManifestRepository.load()`` (matches ``review_view.
            serialize_groups``'s ``group_number`` field the FE already holds).
        confirmed_locked: Locked paths the user confirmed by name; each is
            unlocked in the same write. Naming and ``skip_locked`` are exclusive.
        skip_locked: Narrow the write to the unlocked subset only.

    Returns:
        dict with keys ``matched`` (int), ``affected_paths`` (list[str]),
        ``action_applied`` (``"apply_best_copy"``), ``groups`` (serialised,
        post-mutation).

    Raises:
        FileNotFoundError: manifest_path does not exist on disk.
        ValueError("group_not_found", group_number): no group with this
            number exists in the current load.
        ValueError: ``confirmed_locked`` and ``skip_locked`` are both set.
        ValueError("locked_paths", [...], matched_total): the write would
            touch a locked row that is not named and ``skip_locked`` is not set.
    """
    _require_manifest(manifest_path)
    verdict, named = verdict_from_named(confirmed_locked, skip_locked)

    vm = _load_vm(manifest_path)
    group = next(
        (g for g in vm.groups if getattr(g, "group_number", None) == group_number),
        None,
    )
    if group is None:
        raise ValueError("group_not_found", group_number)

    items = list(getattr(group, "items", []))

    # Duck-typed adapter — top_score_path_per_group / non_keepers_for_
    # aggressive_delete read .group_id/.source_path/.score/.action (the
    # scanner.dedup.ManifestRow shape); PhotoRecord carries the same data
    # under different names (.group_number/.file_path) and has no
    # .match_confidence attribute at all (never persisted — see docstring).
    # A single constant group_id is fine: every adapted row belongs to the
    # one already-resolved group, so the helpers' internal grouping is a
    # no-op bucket of one.
    scored = [
        SimpleNamespace(
            group_id="g",
            source_path=rec.file_path,
            score=rec.score,
            action=getattr(rec, "action", ""),
        )
        for rec in items
    ]

    keepers = top_score_path_per_group(scored)
    if not keepers:
        # No scored rows in this group (e.g. every member is a passenger) —
        # nothing to apply. Benign no-op, mirrors the post-scan auto-select write.
        return {
            "matched": 0,
            "affected_paths": [],
            "action_applied": "apply_best_copy",
            "groups": serialize_groups(vm.groups),
        }

    non_keepers = non_keepers_for_aggressive_delete(scored, keepers)
    decisions, lock_states = build_auto_select_writes(keepers, non_keepers)

    # The keeper is written with lock_after=True and every other row with None:
    # a locked non-keeper is unlocked only when the user named it (FORCE),
    # and a locked keeper is refused like any other locked row in the write set.
    writes = [
        DecisionWrite(path=p, decision=v, lock_after=lock_states.get(p))
        for p, v in decisions.items()
    ]
    repo = ManifestRepository()
    with repo.write_txn(manifest_path) as tx:
        written = apply_gated_writes(tx, writes, verdict, named=named)

    vm2 = _load_vm(manifest_path)
    fresh_groups = serialize_groups(vm2.groups)

    return {
        "matched": len(written),
        "affected_paths": sorted(written),
        "action_applied": "apply_best_copy",
        "groups": fresh_groups,
    }
