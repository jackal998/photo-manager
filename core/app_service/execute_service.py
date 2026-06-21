"""Headless execute service: execute, remove, prune, save.

All database access is stateless — each function opens its own connection
via ManifestRepository.  SQLite is the only source of truth.

NO PySide6 imports — this module must stay headless for the FastAPI process.

The D7 per-file consistency guarantee: for delete-decided paths, outcome is
written to SQLite PER FILE immediately after each successful trash, not in a
batch at the end.  A mid-batch crash therefore leaves the DB consistent —
every completed deletion is recorded.

Path-safety invariant: every candidate source_path from SQLite is validated
against allowed_roots via core.app_service.path_safety.is_under_roots before
any delete or ignore operation.  A manifest can reference arbitrary paths;
we never act on one that escapes the allowed roots.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from loguru import logger

from core.app_service.path_safety import is_under_roots
from infrastructure.delete_service import DeleteService
from infrastructure.logging import write_delete_log
from infrastructure.manifest_repository import ManifestRepository


def _require_manifest(manifest_path: str) -> None:
    """Reject a manifest path that does not point at an existing file."""
    if not Path(manifest_path).is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_path!r}")


def _load_decided_rows(
    manifest_path: str,
    scope_paths: list[str] | None,
) -> tuple[list[str], list[str], list[str]]:
    """Return (delete_paths, ignore_paths, locked_delete_paths) from the manifest.

    Reads user_decision and is_locked directly from SQLite — the service
    never trusts client-supplied lock state.  Only rows with outcome=''
    (in-review) are considered; already-executed rows are skipped.

    Args:
        manifest_path: Absolute path to the manifest sqlite.
        scope_paths: Optional whitelist of paths to restrict the query to.
            None means all decided rows.

    Returns:
        Three lists:
        - delete_paths: paths decided 'delete' (unlocked)
        - ignore_paths: paths decided 'ignore'
        - locked_delete_paths: paths decided 'delete' that are locked
    """
    conn = _connect(manifest_path)
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision, is_locked "
            "FROM migration_manifest "
            "WHERE outcome = '' AND user_decision IN ('delete', 'ignore')"
        ).fetchall()
    finally:
        conn.close()

    scope_set: set[str] | None = set(scope_paths) if scope_paths is not None else None

    delete_paths: list[str] = []
    ignore_paths: list[str] = []
    locked_delete_paths: list[str] = []

    for source_path, user_decision, is_locked_val in rows:
        if scope_set is not None and source_path not in scope_set:
            continue
        if user_decision == "delete":
            if is_locked_val:
                locked_delete_paths.append(source_path)
            else:
                delete_paths.append(source_path)
        elif user_decision == "ignore":
            ignore_paths.append(source_path)

    return delete_paths, ignore_paths, locked_delete_paths


def _connect(manifest_path: str) -> sqlite3.Connection:
    """Open a SQLite connection (mirrors ManifestRepository._connect)."""
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(manifest_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -32000")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def execute_decisions(
    manifest_path: str,
    scope_paths: list[str] | None = None,
    recycle: bool = True,
    force_locked: bool = False,
    allowed_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Execute all decided rows in the manifest.

    Args:
        manifest_path: Absolute path to the manifest sqlite.
        scope_paths: Optional subset of paths to execute (None = all).
        recycle: True = send2trash; False = os.unlink.
        force_locked: If True, unlock-then-delete locked delete-decided rows.
            If False and any such rows exist, raises ValueError with locked paths.
        allowed_roots: Trusted root directories.  Every candidate source_path
            is checked against this list before any deletion or ignore.  Rows
            whose resolved path escapes all roots are collected into the
            returned ``failed`` list with reason 'outside_allowed_roots' and
            are never deleted or ignored.  Pass None (or []) to skip the check
            (legacy / internal callers that already validated upstream).

    Returns:
        Dict with keys: success_paths, failed, ignored, missing, log_path,
        groups, db_write_failed.

    Raises:
        FileNotFoundError: If manifest_path does not exist.
        ValueError: If locked_delete_paths is non-empty and force_locked is False.
            The exception's args[1] carries the list of locked paths.
    """
    _require_manifest(manifest_path)
    repo = ManifestRepository()
    repo.ensure_schema(manifest_path)

    delete_paths, ignore_paths, locked_delete_paths = _load_decided_rows(
        manifest_path, scope_paths
    )

    # Locked gate — RE-READ from DB (never trust client state).
    if locked_delete_paths and not force_locked:
        raise ValueError("locked_paths", locked_delete_paths)

    if locked_delete_paths and force_locked:
        # Unlock the locked rows before proceeding.
        repo.batch_update_lock_state(
            manifest_path, {p: False for p in locked_delete_paths}
        )
        delete_paths = delete_paths + locked_delete_paths

    # --- Path-safety filter ------------------------------------------------
    # Check every candidate source_path against allowed_roots BEFORE any
    # filesystem operation.  This prevents a crafted manifest from directing
    # the service to delete files outside the trusted root set.
    refused: list[tuple[str, str]] = []
    safe_delete_paths: list[str] = []
    safe_ignore_paths: list[str] = []

    if allowed_roots:
        for path in delete_paths:
            if is_under_roots(path, allowed_roots):
                safe_delete_paths.append(path)
            else:
                logger.warning(
                    "Refusing delete of out-of-root source_path: {}", path
                )
                refused.append((path, "outside_allowed_roots"))
        for path in ignore_paths:
            if is_under_roots(path, allowed_roots):
                safe_ignore_paths.append(path)
            else:
                logger.warning(
                    "Refusing ignore of out-of-root source_path: {}", path
                )
                # Out-of-root ignore rows are silently skipped (not added to refused).
    else:
        safe_delete_paths = delete_paths
        safe_ignore_paths = ignore_paths

    # --- Per-file delete with D7 consistency ----------------------------
    svc = DeleteService()
    success_paths: list[str] = []
    failed: list[tuple[str, str]] = list(refused)  # seed with refused rows
    missing: list[str] = []
    db_write_failed: list[str] = []

    for path in safe_delete_paths:
        if not os.path.exists(path):
            missing.append(path)
            logger.warning("File not found, skipping delete: {}", path)
            # Do NOT write outcome='deleted' for missing files — they
            # never appeared in the manifest as deleted from disk.
            continue

        if recycle:
            result = svc.delete_to_recycle([path])
            if result.success_paths:
                success_paths.append(path)
                # D7: write outcome PER FILE immediately after success.
                try:
                    repo.finalize_outcome(manifest_path, [path], "deleted")
                except Exception as exc:
                    # FIX 5: file IS gone from disk — keep it in success_paths,
                    # record the DB write failure separately.
                    logger.warning("Failed to write deleted outcome for {}: {}", path, exc)
                    db_write_failed.append(path)
            else:
                reason = result.failed[0][1] if result.failed else "unknown"
                failed.append((path, reason))
        else:
            # recycle=False: direct unlink (for tests / explicit user opt-out).
            try:
                os.unlink(path)
                success_paths.append(path)
                try:
                    repo.finalize_outcome(manifest_path, [path], "deleted")
                except Exception as exc:
                    # FIX 5: file IS gone from disk — keep it in success_paths,
                    # record the DB write failure separately.
                    logger.warning("Failed to write deleted outcome for {}: {}", path, exc)
                    db_write_failed.append(path)
            except OSError as exc:
                failed.append((path, str(exc)))

    # --- Batch ignore ---------------------------------------------------
    if safe_ignore_paths:
        try:
            repo.finalize_outcome(manifest_path, safe_ignore_paths, "ignored")
        except Exception as exc:
            logger.warning("Failed to write ignored outcomes: {}", exc)

    # --- Audit CSV ------------------------------------------------------
    # FIX 5: db_write_failed rows count as success=True (file deleted) with
    # a Reason indicating the DB write failed, so the audit trail is accurate.
    audit_rows: list[tuple[int, str, bool, str]] = (
        [(0, p, True, "") for p in success_paths]
        + [(0, p, False, r) for p, r in failed]
        + [(0, p, True, "outcome write failed") for p in db_write_failed]
    )
    log_path: str | None = None
    if audit_rows:
        try:
            log_path = write_delete_log(audit_rows)
        except Exception as exc:
            logger.warning("Failed to write delete audit log: {}", exc)

    # --- Reload post-execute groups -------------------------------------
    from core.app_service.review_service import load_review
    try:
        review = load_review(manifest_path)
        groups = review["groups"]
    except Exception as exc:
        logger.warning("Failed to reload groups after execute: {}", exc)
        groups = []

    return {
        "success_paths": success_paths,
        "failed": [[p, r] for p, r in failed],
        "ignored": safe_ignore_paths,
        "missing": missing,
        "log_path": log_path,
        "groups": groups,
        "db_write_failed": db_write_failed,
    }


def remove_from_review(
    manifest_path: str,
    file_paths: list[str],
    force_locked: bool = False,
    allowed_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Remove files from the review manifest (outcome='ignored', files untouched).

    Args:
        manifest_path: Absolute path to the manifest sqlite.
        file_paths: Paths to mark as ignored.
        force_locked: If True, remove locked rows anyway. If False and any
            rows are locked, raises ValueError with the locked paths.
        allowed_roots: Trusted root directories.  Out-of-root rows in
            ``file_paths`` are silently skipped (not marked ignored).

    Returns:
        Dict with keys: removed, groups.
    """
    _require_manifest(manifest_path)
    repo = ManifestRepository()
    repo.ensure_schema(manifest_path)

    # RE-READ lock state from DB — never trust client.
    conn = _connect(manifest_path)
    try:
        placeholders = ",".join("?" for _ in file_paths)
        rows = conn.execute(
            f"SELECT source_path, is_locked FROM migration_manifest "
            f"WHERE source_path IN ({placeholders}) AND outcome = ''",
            file_paths,
        ).fetchall()
    finally:
        conn.close()

    locked_paths = [p for p, locked in rows if locked]

    if locked_paths and not force_locked:
        raise ValueError("locked_paths", locked_paths)

    if locked_paths and force_locked:
        repo.batch_update_lock_state(manifest_path, {p: False for p in locked_paths})

    # --- Path-safety filter ------------------------------------------------
    if allowed_roots:
        safe_paths = [p for p in file_paths if is_under_roots(p, allowed_roots)]
        for p in file_paths:
            if not is_under_roots(p, allowed_roots):
                logger.warning(
                    "Refusing remove_from_review of out-of-root source_path: {}", p
                )
    else:
        safe_paths = file_paths

    repo.remove_from_review(manifest_path, safe_paths)

    from core.app_service.review_service import load_review
    try:
        review = load_review(manifest_path)
        groups = review["groups"]
    except Exception as exc:
        logger.warning("Failed to reload groups after remove: {}", exc)
        groups = []

    return {"removed": len(safe_paths), "groups": groups}


def prune_singletons(
    manifest_path: str,
    include_actioned: bool = False,
    allowed_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Remove singleton groups from the review manifest.

    A singleton is a group whose sole remaining outcome='' member (with all
    partner rows now at outcome='deleted'/'ignored') would otherwise orphan.
    We detect them differently from the Qt path: we look for group_id values
    that have exactly ONE row with outcome=''.

    Args:
        manifest_path: Absolute path to the manifest sqlite.
        include_actioned: If True, also prune singletons whose remaining row
            has user_decision in {'delete', 'ignore'}.  If False, only prune
            plain singletons (user_decision == '').
        allowed_roots: Trusted root directories.  Out-of-root singleton rows
            are silently skipped (not pruned).

    Returns:
        Dict with keys: pruned, locked_skipped, groups.
    """
    _require_manifest(manifest_path)
    repo = ManifestRepository()
    repo.ensure_schema(manifest_path)

    conn = _connect(manifest_path)
    try:
        # Find group_ids with exactly one remaining in-review row.
        rows = conn.execute(
            """
            SELECT m.source_path, m.user_decision, m.is_locked, m.group_id
            FROM migration_manifest m
            JOIN (
                SELECT group_id, COUNT(*) as cnt
                FROM migration_manifest
                WHERE outcome = '' AND group_id IS NOT NULL
                GROUP BY group_id
                HAVING cnt = 1
            ) singletons ON m.group_id = singletons.group_id
            WHERE m.outcome = ''
            """
        ).fetchall()
    finally:
        conn.close()

    to_prune: list[str] = []
    locked_skipped: list[str] = []

    for source_path, user_decision, is_locked_val, _group_id in rows:
        if is_locked_val:
            locked_skipped.append(source_path)
            continue

        # --- Path-safety check ------------------------------------------------
        if allowed_roots and not is_under_roots(source_path, allowed_roots):
            logger.warning(
                "Refusing prune of out-of-root source_path: {}", source_path
            )
            continue

        decision = user_decision or ""
        if decision == "":
            to_prune.append(source_path)
        elif include_actioned and decision in ("delete", "ignore"):
            to_prune.append(source_path)
        # Actioned singletons when include_actioned=False: silently skip.

    if to_prune:
        repo.finalize_outcome(manifest_path, to_prune, "ignored")

    from core.app_service.review_service import load_review
    try:
        review = load_review(manifest_path)
        groups = review["groups"]
    except Exception as exc:
        logger.warning("Failed to reload groups after prune: {}", exc)
        groups = []

    return {"pruned": to_prune, "locked_skipped": locked_skipped, "groups": groups}


def save_manifest(
    manifest_path: str,
    target_path: str | None = None,
) -> dict[str, Any]:
    """Save the manifest decisions in-place or to a new path.

    Args:
        manifest_path: Source manifest path (must exist).
        target_path: If provided, checkpoint the WAL and copy to this path.
            If None, save in-place.

    Returns:
        Dict with keys: saved_to, updated.

    Implementation notes (FIX 4 — dead code removed):
    - In-place: run a WAL checkpoint (PRAGMA wal_checkpoint(FULL)) to flush
      the -wal file into the main db, making it durable/copy-safe, then
      return a count of in-review rows (outcome='').  The dead load_review +
      MainVM dance is gone — decisions are already persisted per-PATCH.
    - Save-as: WAL checkpoint THEN shutil.copy2.  Because the checkpoint
      flushed decisions into the main db file, copy2 already carries them —
      the redundant MainVM load + repo.save into the copy is removed.
    """
    _require_manifest(manifest_path)

    # Count in-review rows (outcome='') — used as the `updated` return value.
    conn = sqlite3.connect(manifest_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        row = conn.execute(
            "SELECT COUNT(*) FROM migration_manifest WHERE outcome = ''"
        ).fetchone()
        in_review_count: int = row[0] if row else 0
    finally:
        conn.close()

    if target_path is None:
        # In-place: WAL already checkpointed above; decisions already persisted.
        return {"saved_to": manifest_path, "updated": in_review_count}
    else:
        # Save-as: copy the (now-checkpointed) main db file.
        shutil.copy2(manifest_path, target_path)
        return {"saved_to": target_path, "updated": in_review_count}
