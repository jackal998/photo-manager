"""Headless review service: load, set-decisions, set-locks.

All database access is stateless — each function opens its own
connection via ManifestRepository. SQLite is the only source of truth.

NO PySide6 imports — this module must stay headless for the FastAPI process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from infrastructure.manifest_repository import ManifestRepository
from infrastructure.settings import JsonSettings
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


def _resolve_settings_path() -> Path:
    """Resolve settings.json the same way scan.py does.

    Resolution order (mirrors app/web/routes/scan.py _load_settings):
    1. PHOTO_MANAGER_HOME env var (relative to repo root)
    2. Repository root (two levels above core/app_service/)
    """
    import os

    home_env = os.environ.get("PHOTO_MANAGER_HOME")
    # core/app_service/review_service.py → core/app_service/ → core/ → repo root
    repo_root = Path(__file__).parent.parent.parent
    if home_env:
        config_home = (repo_root / home_env).resolve()
    else:
        config_home = repo_root
    return config_home / "settings.json"


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
        settings: Optional JsonSettings instance. If None, a JsonSettings
            is constructed from the same path the scan route uses.

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
        settings = JsonSettings(_resolve_settings_path())

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
    force_locked: bool = False,
    skip_locked: bool = False,
) -> int:
    """Persist user decisions to the manifest, gated on locked rows.

    Mirrors the Qt desktop's ``set_decision_with_lock_check`` /
    ``LockedRowsConfirmDialog`` gate (#182, #733): a decision write that
    would touch a locked row is refused unless the caller opts into one
    of ``force_locked`` (write anyway, unlocking the row in the same
    write) or ``skip_locked`` (drop locked rows from the write set,
    leaving both ``is_locked`` and ``user_decision`` untouched). The gate
    applies to every decision value, including ``""`` (clearing a locked
    row's decision is also gated — Qt does not special-case it).

    The returned count is the number of rows ACTUALLY submitted for write
    (narrowed by ``skip_locked``), not necessarily the number matched in
    the DB (executemany does not report per-row matches).

    Args:
        manifest_path: Absolute path to the manifest.
        decisions: Mapping of file_path → decision value.
        force_locked: When True, locked rows are written anyway and
            unlocked (``is_locked`` set to False) in the same write.
        skip_locked: When True, locked rows are dropped from the write
            set; only the unlocked subset is written.

    Returns:
        Number of decision entries actually submitted.

    Raises:
        FileNotFoundError: If manifest_path does not exist on disk.
        ValueError: If any decision value is not in VALID_DECISIONS.
        ValueError("locked_paths", [...], matched_total): The decision
            write targets locked rows and neither ``force_locked`` nor
            ``skip_locked`` is set.
    """
    _require_manifest(manifest_path)
    for fp, dec in decisions.items():
        if dec not in VALID_DECISIONS:
            raise ValueError(
                f"Invalid decision {dec!r} for {fp!r}. "
                f"Allowed values: {sorted(VALID_DECISIONS)}"
            )

    # Locked-rows gate — RE-READ is_locked from the DB (never trust client
    # state). Reuses ManifestRepository.load(), the same repository call
    # action_service.bulk_decide's path_to_rec build is ultimately backed by.
    repo = ManifestRepository()
    path_to_rec = {rec.file_path: rec for rec in repo.load(manifest_path)}
    locked: list[str] = [
        p for p in decisions if getattr(path_to_rec.get(p), "is_locked", False)
    ]

    if skip_locked:
        # Apply to Unlocked Only — narrow the write set IN PLACE so both the
        # write and the returned count reflect the actually-applied subset.
        # Locked rows keep their is_locked + user_decision untouched.
        locked_set = set(locked)
        decisions = {p: v for p, v in decisions.items() if p not in locked_set}
    elif locked and not force_locked:
        # 409 carries the FULL targeted count (before any narrowing) so the
        # caller can derive the unlocked count without a separate preview.
        raise ValueError("locked_paths", locked, len(decisions))
    elif force_locked and locked:
        # Unlock & Apply All — unlock the locked rows in the same write as
        # the decision (mirrors Qt's batch_update_decisions_and_lock call).
        repo.batch_update_decisions_and_lock(
            manifest_path, decisions, {p: False for p in locked}
        )
        return len(decisions)

    repo.batch_update_decisions(manifest_path, decisions)
    return len(decisions)


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
