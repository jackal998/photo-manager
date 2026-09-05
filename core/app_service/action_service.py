"""Headless action service: bulk_decide — pattern-matched decision/lock mutations.

All database access is stateless — each function opens its own connection
via ManifestRepository.  SQLite is the only source of truth.

NO PySide6 imports — this module must stay headless for the FastAPI process.

Security note: every ``source_path`` resolved by ``resolve_matched_paths`` is
re-validated against ``allowed_roots`` via ``is_under_roots`` before any DB
write.  This mirrors the 2C1 ship-blocker fix in ``execute_service``: an
in-root manifest carrying an out-of-root ``source_path`` row cannot be used to
mutate records outside the trusted root set.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.app_service.action_resolve import resolve_matched_paths
from core.app_service.path_safety import is_under_roots
from core.app_service.review_view import serialize_groups
from core.services.auto_select import (
    build_auto_select_writes,
    non_keepers_for_aggressive_delete,
    top_score_path_per_group,
)
from infrastructure.manifest_repository import ManifestRepository
from infrastructure.settings import JsonSettings


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


def _resolve_settings_path() -> Path:
    """Return the settings.json path using the same logic as review_service."""
    import os

    home_env = os.environ.get("PHOTO_MANAGER_HOME")
    # core/app_service/action_service.py → core/app_service/ → core/ → repo root
    repo_root = Path(__file__).parent.parent.parent
    if home_env:
        config_home = (repo_root / home_env).resolve()
    else:
        config_home = repo_root
    return config_home / "settings.json"


def _load_vm(manifest_path: str):
    """Load a MainVM from the manifest and return it.

    Mirrors the internal load logic in review_service.load_review so
    action_service can obtain the raw PhotoGroup / PhotoRecord objects
    (not the already-serialised dicts) needed by resolve_matched_paths.
    """
    from app.viewmodels.main_vm import MainVM

    settings = JsonSettings(_resolve_settings_path())
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
    force_locked: bool = False,
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
    force_locked:
        When False (default), a decision action on rows with ``is_locked=True``
        raises ``ValueError("locked_paths", locked, matched_total)`` (three
        positional args; the route matches ``args[0] == "locked_paths"`` → HTTP
        409 and forwards ``matched_total`` so the FE can derive the unlocked
        count without a separate preview round-trip).
        When True, locked rows are mutated anyway.
    skip_locked:
        When True, a decision action applies only to the UNLOCKED subset of the
        matched rows; locked rows are left fully untouched (``is_locked`` and
        ``user_decision`` unchanged) — the "Apply to Unlocked Only" verdict
        (#674, mirrors Qt ``LockedRowsConfirmDialog.APPLY_UNLOCKED_ONLY``). The
        returned ``matched`` / ``affected_paths`` report the actually-applied
        unlocked subset. Mutually exclusive with ``force_locked`` — both True
        raises a plain ``ValueError`` (route → HTTP 422).
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
        Decision action would affect locked rows and neither ``force_locked``
        nor ``skip_locked`` is set.
    ValueError (plain):
        ``force_locked`` and ``skip_locked`` are both True (incoherent request).
    """
    _require_manifest(manifest_path)

    # Mutually-exclusive lock verdicts: "apply to all (unlock)" and "apply to
    # unlocked only" are contradictory write intents. The 3-button FE can never
    # send both, but POST /api/action/bulk-decide is a public HTTP boundary —
    # reject an incoherent body here (route → 422), per the validate-at-system-
    # boundaries rule.
    if force_locked and skip_locked:
        raise ValueError("force_locked and skip_locked are mutually exclusive")

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

    # Locked-rows handling (decision actions only; real writes only — preview
    # returned above). Reads is_locked from the already-loaded record objects,
    # the same source-of-truth discipline as execute_service. Three modes mirror
    # the Qt LockedRowsConfirmDialog verdicts:
    #   force_locked → mutate locked rows too (Unlock & Apply All)
    #   skip_locked  → apply to the unlocked subset only (Apply to Unlocked Only)
    #   neither      → refuse with 409 if any matched row is locked
    locked: list[str] = []
    if kind == "decision":
        path_to_rec: dict[str, Any] = {}
        for group in groups_objs:
            for rec in getattr(group, "items", []):
                path_to_rec[rec.file_path] = rec
        locked = [
            p for p in safe if getattr(path_to_rec.get(p), "is_locked", False)
        ]
        if skip_locked:
            # Narrow `safe` to the unlocked subset IN PLACE so the write set AND
            # the returned matched/affected_paths both report the actually-
            # applied subset (mirrors Qt set_count=len(unlocked_items)). Locked
            # rows keep their is_locked + user_decision untouched — clear
            # `locked` so the force-unlock apply below can never touch them.
            locked_set = set(locked)
            safe = [p for p in safe if p not in locked_set]
            locked = []
        elif locked and not force_locked:
            # 409 carries the FULL matched count (len(safe) BEFORE any narrowing)
            # so the FE derives unlocked = matched_total - len(locked) without a
            # separate preview.
            raise ValueError("locked_paths", locked, len(safe))

    # Apply.
    repo = ManifestRepository()
    if kind in ("lock", "unlock"):
        repo.batch_update_lock_state(manifest_path, {p: lock_value for p in safe})
    elif locked:
        # force_locked path — Unlock & Apply All: the Qt verdict UNLOCKS the
        # locked rows in the same write as the decision (#733; execute_service's
        # force path does the same via batch_update_lock_state). One commit.
        repo.batch_update_decisions_and_lock(
            manifest_path,
            {p: decision_value for p in safe},
            {p: False for p in locked},
        )
    else:
        repo.batch_update_decisions(manifest_path, {p: decision_value for p in safe})

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
    force_locked: bool = False,
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
    matched_total)`` unless ``force_locked`` or ``skip_locked`` is set.
    ``force_locked`` unlocks every previously-locked delete-target in the same
    write (Qt "Unlock & Apply All" parity) — the keeper's own lock write
    (``True``) is unaffected. ``skip_locked`` narrows the write to the
    unlocked subset only (locked rows keep both their decision and lock state
    untouched).

    Args:
        manifest_path: Absolute path to the manifest .sqlite file.
        group_number: The ``group_number`` assigned by the most recent
            ``ManifestRepository.load()`` (matches ``review_view.
            serialize_groups``'s ``group_number`` field the FE already holds).
        force_locked: Write anyway, force-unlocking previously-locked
            delete-targets in the same write.
        skip_locked: Narrow the write to the unlocked subset only.

    Returns:
        dict with keys ``matched`` (int), ``affected_paths`` (list[str]),
        ``action_applied`` (``"apply_best_copy"``), ``groups`` (serialised,
        post-mutation).

    Raises:
        FileNotFoundError: manifest_path does not exist on disk.
        ValueError("group_not_found", group_number): no group with this
            number exists in the current load.
        ValueError: ``force_locked`` and ``skip_locked`` are both True.
        ValueError("locked_paths", [...], matched_total): the write would
            touch locked rows and neither override is set.
    """
    _require_manifest(manifest_path)

    if force_locked and skip_locked:
        raise ValueError("force_locked and skip_locked are mutually exclusive")

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
        # nothing to apply. Benign no-op, mirrors apply_auto_select_decisions.
        return {
            "matched": 0,
            "affected_paths": [],
            "action_applied": "apply_best_copy",
            "groups": serialize_groups(vm.groups),
        }

    non_keepers = non_keepers_for_aggressive_delete(scored, keepers)
    decisions, lock_states = build_auto_select_writes(keepers, non_keepers)

    path_to_rec = {rec.file_path: rec for rec in items}
    locked: list[str] = [
        p for p in decisions if getattr(path_to_rec.get(p), "is_locked", False)
    ]

    if skip_locked:
        locked_set = set(locked)
        decisions = {p: v for p, v in decisions.items() if p not in locked_set}
        lock_states = {p: v for p, v in lock_states.items() if p not in locked_set}
    elif locked and not force_locked:
        # 409 carries the FULL targeted write-set size (before narrowing) —
        # same contract as set_decisions / bulk_decide's 409.
        raise ValueError("locked_paths", locked, len(decisions))
    elif force_locked and locked:
        # Force-unlock every previously-locked row that would otherwise
        # refuse the write. A locked KEEPER already has lock_states[p]=True
        # from build_auto_select_writes — setdefault leaves that alone, so
        # it stays locked. A locked NON-keeper has no entry yet — it gets
        # explicitly unlocked so the 'delete' decision write isn't blocked
        # (mirrors set_decisions' Unlock & Apply All).
        for p in locked:
            lock_states.setdefault(p, False)

    repo = ManifestRepository()
    repo.batch_update_decisions_and_lock(manifest_path, decisions, lock_states)

    vm2 = _load_vm(manifest_path)
    fresh_groups = serialize_groups(vm2.groups)

    return {
        "matched": len(decisions),
        "affected_paths": sorted(decisions),
        "action_applied": "apply_best_copy",
        "groups": fresh_groups,
    }
