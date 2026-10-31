"""Headless review service: load, set-decisions, set-locks.

All database access is stateless — each function opens its own
connection via ManifestRepository. SQLite is the only source of truth.

No UI imports — this module must stay headless for the FastAPI process.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Any

from core.app_service.lock_gate import (
    LockFlagsConflictError,
    LockVerdict,
    apply_gated_writes,
    verdict_from_flags,
)
from infrastructure.manifest_repository import DecisionWrite, ManifestRepository
from infrastructure.settings import load_settings
from app.viewmodels.main_vm import MainVM
from core.app_service.review_view import serialize_groups

# Valid stored user_decision values.
# Cross-reference: core.constants.IGNORE_DECISION == "ignore". Kept as an
# explicit literal here (not imported) for a self-contained frozenset; the
# drift-check tests in tests/test_review_service.py and tests/test_web_qt_free.py
# pin the two in sync.
VALID_DECISIONS: frozenset[str] = frozenset({"", "delete", "ignore"})


def _require_manifest(manifest_path: str) -> None:
    """Reject a manifest path that does not point at an existing file.

    sqlite3.connect() silently CREATES a file if it is absent, so without
    this guard a PATCH with a bogus manifest_path would litter empty sqlite
    files anywhere the server can write. Mirrors GET /api/manifest's check.
    """
    if not Path(manifest_path).is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_path!r}")


def load_review(
    manifest_path: str,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Load a manifest and return its groups serialised for the API.

    The load is STATELESS per-request — SQLite is the single source of truth;
    the client caches the result in its own store. GET is called infrequently
    (after scan, after execute, on initial load — not per-poll), so the
    per-request open overhead is acceptable. An ETag/304 skip is a possible
    future optimization if the payload becomes large.

    Args:
        manifest_path: Absolute path to a .sqlite manifest file.
        settings: Optional JsonSettings instance. If None, one is loaded
            through ``infrastructure.settings.load_settings`` — the single
            resolver every caller shares (#882).

    Returns:
        {
            "manifest_path": str,
            "groups": [...],
            "total_groups": int,
            "total_files": int,
            "roots": [sorted distinct folder_path strings],
        }

    Raises:
        FileNotFoundError: If manifest_path does not exist on disk.
    """
    _require_manifest(manifest_path)
    if settings is None:
        settings = load_settings()

    default_sort = settings.get("sorting.defaults", [])
    vm = MainVM(default_sort=default_sort)
    vm.load_from_repo(ManifestRepository(), manifest_path)

    groups = serialize_groups(vm.groups)

    total_files = sum(g["member_count"] for g in groups)

    # Collect distinct folder_path values from ALL items for allowed_roots.
    roots: list[str] = sorted(
        {
            item["folder"]
            for g in groups
            for item in g["items"]
            if item["folder"]
        }
    )

    return {
        "manifest_path": manifest_path,
        "groups": groups,
        "total_groups": len(groups),
        "total_files": total_files,
        "roots": roots,
    }


def set_decisions(
    manifest_path: str,
    decisions: dict[str, str],
    *,
    confirmed_locked: Collection[str] = (),
    skip_locked: bool = False,
) -> int:
    """Persist user decisions to the manifest, gated on locked rows.

    Mirrors the Qt desktop's ``set_decision_with_lock_check`` /
    ``LockedRowsConfirmDialog`` gate (#182, #733): a decision write that
    would touch a locked row is refused unless the caller opts into one
    of ``confirmed_locked`` (write the named locked rows, unlocking each in
    the same write) or ``skip_locked`` (drop locked rows from the write set,
    leaving both ``is_locked`` and ``user_decision`` untouched). The gate
    applies to every decision value, including ``""`` (clearing a locked
    row's decision is also gated — Qt does not special-case it).

    A locked row in the write set that is not named in ``confirmed_locked``
    is refused, even when others are named (A6, D7): a row locked after the
    user's dialog opened must be confirmed again. The refusal is raised by
    the write primitive inside the same transaction, so nothing is written.

    The returned count is the number of rows ACTUALLY submitted for write
    (narrowed by ``skip_locked``), not necessarily the number matched in
    the DB (executemany does not report per-row matches).

    Args:
        manifest_path: Absolute path to the manifest.
        decisions: Mapping of file_path → decision value.
        confirmed_locked: Locked paths the user confirmed. Each is written
            and unlocked in the same statement. Empty means no confirmation.
        skip_locked: When True, locked rows are dropped from the write
            set; only the unlocked subset is written.

    Returns:
        Number of decision entries actually submitted.

    Raises:
        FileNotFoundError: If manifest_path does not exist on disk.
        ValueError: If any decision value is not in VALID_DECISIONS.
        LockFlagsConflictError: If ``confirmed_locked`` and ``skip_locked``
            are both set (422 at the route).
        ValueError("locked_paths", [...], matched_total): The decision
            write targets locked rows that are not confirmed.
    """
    _require_manifest(manifest_path)
    for fp, dec in decisions.items():
        if dec not in VALID_DECISIONS:
            raise ValueError(
                f"Invalid decision {dec!r} for {fp!r}. "
                f"Allowed values: {sorted(VALID_DECISIONS)}"
            )

    # The lock gate runs inside one IMMEDIATE transaction: the locked set is
    # read from the same connection that writes, and the primitive refuses
    # any unconfirmed locked row (hidden rows included, B-5). No load() here.
    verdict, named = _decision_verdict(confirmed_locked, skip_locked)
    writes = [DecisionWrite(path=p, decision=v) for p, v in decisions.items()]
    with ManifestRepository().write_txn(manifest_path) as tx:
        return len(apply_gated_writes(tx, writes, verdict, named=named))


def _decision_verdict(
    confirmed_locked: Collection[str], skip_locked: bool
) -> tuple[LockVerdict, list[str] | None]:
    """Map the decision request to a verdict; a non-empty list means FORCE (A6)."""
    named = list(confirmed_locked)
    if named and skip_locked:
        raise LockFlagsConflictError(
            "confirmed_locked and skip_locked are mutually exclusive"
        )
    if named:
        return LockVerdict.FORCE, named
    return verdict_from_flags(False, skip_locked), None


def set_locks(manifest_path: str, locks: dict[str, bool]) -> int:
    """Persist lock state to the manifest.

    The returned count is the number of rows REQUESTED, not necessarily
    the number matched in the DB (executemany does not report per-row matches).

    Args:
        manifest_path: Absolute path to the manifest.
        locks: Mapping of file_path → is_locked bool.

    Returns:
        Number of lock entries submitted.

    Raises:
        FileNotFoundError: If manifest_path does not exist on disk.
    """
    _require_manifest(manifest_path)
    ManifestRepository().batch_update_lock_state(manifest_path, locks)
    return len(locks)
