"""Headless execute service: execute, remove, prune, save.

All database access is stateless — each function opens its own connection
via ManifestRepository.  SQLite is the only source of truth.

No UI imports — this module must stay headless for the FastAPI process.

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
from collections.abc import Collection
from typing import Any

from loguru import logger

from core.app_service.lock_gate import (
    LockVerdict,
    apply_gated_transition,
    refuse_gated_outcome,
    verdict_from_named,
)
from core.app_service.path_safety import is_under_roots
from infrastructure.delete_service import DeleteService
from infrastructure.logging import write_delete_log
from infrastructure.manifest_repository import DeleteSession, ManifestRepository

# A9 (D6): reasons for a delete row left alone by the pre-call re-check. They
# go in the existing ``failed`` list, which already carries per-row reasons for
# rows that were not deleted (e.g. outside_allowed_roots).
ROW_LOCKED_AFTER_GATE = "locked after the gate: not deleted, lock kept"
ROW_OUTCOME_CHANGED = "dismissed or handled after the gate: not deleted"


def _require_manifest(manifest_path: str) -> None:
    """Reject a manifest path that does not point at an existing file."""
    if not Path(manifest_path).is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_path!r}")


def _split_by_roots(
    paths: list[str], allowed_roots: list[str], verb: str
) -> tuple[list[str], list[str]]:
    """Split ``paths`` into (inside allowed_roots, outside). Logs each outside path."""
    inside: list[str] = []
    outside: list[str] = []
    for path in paths:
        if is_under_roots(path, allowed_roots):
            inside.append(path)
        else:
            logger.warning("Refusing {} of out-of-root source_path: {}", verb, path)
            outside.append(path)
    return inside, outside


def _load_decided_rows(
    manifest_path: str,
    scope_paths: list[str] | None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return (delete_paths, ignore_paths, locked_delete_paths, locked_ignore_paths).

    Reads user_decision and is_locked directly from SQLite — the service
    never trusts client-supplied lock state.  Only rows with outcome=''
    (in-review) are considered; already-executed rows are skipped.

    Only rows the review SHOWS are considered (#941, "visible = committed"):
    a row counts only if its group still has >= 2 in-review members. That is
    the same group-survivor rule as ManifestRepository.load()'s orphan-skip,
    and the complement of _fetch_singleton_rows. A kept actioned singleton,
    left alone by a partial execute, is hidden from the review, so no tree,
    count or #733 confirm ever shows it. It must not be deleted here. It keeps
    its decision and stays a prune candidate.

    Args:
        manifest_path: Absolute path to the manifest sqlite.
        scope_paths: Optional whitelist of paths to restrict the query to.
            None means all decided rows.

    Returns:
        Four lists:
        - delete_paths: paths decided 'delete' (unlocked)
        - ignore_paths: paths decided 'ignore' (unlocked)
        - locked_delete_paths: paths decided 'delete' that are locked
        - locked_ignore_paths: paths decided 'ignore' that are locked (#972:
          a locked Skip is gated exactly like a locked Delete)
    """
    conn = _connect(manifest_path)
    try:
        rows = conn.execute(
            """
            SELECT m.source_path, m.user_decision, m.is_locked
            FROM migration_manifest m
            JOIN (
                SELECT group_id
                FROM migration_manifest
                WHERE outcome = '' AND group_id IS NOT NULL AND group_id != ''
                GROUP BY group_id
                HAVING COUNT(*) >= 2
            ) reviewed ON m.group_id = reviewed.group_id
            WHERE m.outcome = '' AND m.user_decision IN ('delete', 'ignore')
            """
        ).fetchall()
    finally:
        conn.close()

    scope_set: set[str] | None = set(scope_paths) if scope_paths is not None else None

    delete_paths: list[str] = []
    ignore_paths: list[str] = []
    locked_delete_paths: list[str] = []
    locked_ignore_paths: list[str] = []

    for source_path, user_decision, is_locked_val in rows:
        if scope_set is not None and source_path not in scope_set:
            continue
        if user_decision == "delete":
            if is_locked_val:
                locked_delete_paths.append(source_path)
            else:
                delete_paths.append(source_path)
        elif user_decision == "ignore":
            if is_locked_val:
                locked_ignore_paths.append(source_path)
            else:
                ignore_paths.append(source_path)

    return delete_paths, ignore_paths, locked_delete_paths, locked_ignore_paths


def _connect(manifest_path: str) -> sqlite3.Connection:
    """Open a SQLite connection (mirrors ManifestRepository._connect)."""
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(manifest_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -32000")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def _verdict(confirmed_locked: Collection[str]) -> tuple[LockVerdict, list[str] | None]:
    """The verdict for a verb with no skip option (A8): name rows, or refuse.

    A non-empty ``confirmed_locked`` confirms exactly those locked rows; empty
    refuses every locked row in scope (ASK).
    """
    verdict, named = verdict_from_named(confirmed_locked, skip_locked=False)
    return verdict, named


def _transition_outcome(
    repo: ManifestRepository,
    manifest_path: str,
    paths: list[str],
    verdict: LockVerdict,
    *,
    from_outcome: str,
    to_outcome: str,
    named: Collection[str] | None = None,
) -> int:
    """Plan, refuse and write one outcome transition in one IMMEDIATE txn (A4)."""
    if not paths:
        return 0
    with repo.write_txn(manifest_path) as tx:
        return apply_gated_transition(
            tx,
            paths,
            verdict,
            from_outcome=from_outcome,
            to_outcome=to_outcome,
            named=named,
        )


def _recheck_reason(
    session: DeleteSession, path: str, confirmed: frozenset[str]
) -> str | None:
    """Why ``path`` must not be deleted now, or None when it may be (A9, D6).

    A row locked after the gate is kept unless the user named it at the gate.
    A row whose outcome is no longer '' (dismissed or handled) is never deleted.
    """
    for is_locked, outcome in session.row_states(path):
        if outcome != "":
            return ROW_OUTCOME_CHANGED
        if is_locked and path not in confirmed:
            return ROW_LOCKED_AFTER_GATE
    return None


def _gate_execute(
    repo: ManifestRepository,
    manifest_path: str,
    paths: list[str],
    verdict: LockVerdict,
    named: Collection[str] | None,
) -> None:
    """Execute's up-front gate: refuse locked rows before any file is touched (A4)."""
    if not paths:
        return
    with repo.write_txn(manifest_path) as tx:
        refuse_gated_outcome(tx, paths, verdict, outcome="", named=named)


def execute_decisions(
    manifest_path: str,
    scope_paths: list[str] | None = None,
    recycle: bool = True,
    confirmed_locked: Collection[str] = (),
    allowed_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Execute all decided rows in the manifest.

    Args:
        manifest_path: Absolute path to the manifest sqlite.
        scope_paths: Optional subset of paths to execute (None = every
            decided row the review shows; see _load_decided_rows).
        recycle: True = send2trash; False = os.unlink.
        confirmed_locked: Locked rows decided delete or ignore (#972) that the
            user confirmed by name (A8). Each named row is unlocked in the same
            write as its outcome, so a row whose action does not apply keeps its
            lock (#985). A locked row in scope that is not named raises
            ValueError with the locked paths, and nothing is written.
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
        ValueError: If any locked row (decided delete or ignore) is in scope
            and not named in confirmed_locked.
            The exception's args[1] carries the list of locked paths.
    """
    _require_manifest(manifest_path)
    repo = ManifestRepository()
    repo.ensure_schema(manifest_path)

    (
        delete_paths,
        ignore_paths,
        locked_delete_paths,
        locked_ignore_paths,
    ) = _load_decided_rows(manifest_path, scope_paths)

    # --- Path-safety filter (#985: BEFORE the lock gate) -----------------
    # Check every candidate source_path against allowed_roots BEFORE the lock
    # gate and any filesystem operation. This prevents a crafted manifest from
    # directing the service to delete files outside the trusted root set. A
    # locked out-of-root row is filtered exactly like an unlocked one: it is
    # not gated, not offered for unlock, and keeps its lock.
    refused: list[tuple[str, str]] = []
    if allowed_roots:
        delete_paths, out_delete = _split_by_roots(delete_paths, allowed_roots, "delete")
        locked_delete_paths, out_locked = _split_by_roots(
            locked_delete_paths, allowed_roots, "delete"
        )
        refused = [(p, "outside_allowed_roots") for p in out_delete + out_locked]
        # Out-of-root ignore rows are silently skipped (not added to refused).
        ignore_paths, _ = _split_by_roots(ignore_paths, allowed_roots, "ignore")
        locked_ignore_paths, _ = _split_by_roots(
            locked_ignore_paths, allowed_roots, "ignore"
        )

    # #985: nothing is unlocked up front. A locked row joins the batch and
    # keeps its lock until its outcome is written, so a row whose action does
    # not apply stays locked and in review. #972: a locked Skip ('ignore') row
    # is gated like a locked Delete, so one 409 covers both.
    safe_delete_paths = delete_paths + locked_delete_paths
    safe_ignore_paths = ignore_paths + locked_ignore_paths

    # Up-front gate (A4): the locks are re-read inside the primitive's own
    # transaction, so a row locked after the plan is refused (409) before any
    # file is touched.
    verdict, named = _verdict(confirmed_locked)
    _gate_execute(repo, manifest_path, safe_delete_paths + safe_ignore_paths, verdict, named)

    # --- Per-file delete with D7 consistency ----------------------------
    svc = DeleteService()
    success_paths: list[str] = []
    failed: list[tuple[str, str]] = list(refused)  # seed with refused rows
    missing: list[str] = []
    db_write_failed: list[str] = []

    confirmed = frozenset(named or ())
    with repo.delete_session(manifest_path) as session:
        for path in safe_delete_paths:
            if not os.path.exists(path):
                missing.append(path)
                logger.warning("File not found, skipping delete: {}", path)
                # Do NOT write outcome='deleted' for missing files — they
                # never appeared in the manifest as deleted from disk.
                continue

            # A9 (D6): re-check the row's lock and outcome right before its
            # file call. A row changed after the gate is left alone and reported.
            skip_reason = _recheck_reason(session, path, confirmed)
            if skip_reason is not None:
                failed.append((path, skip_reason))
                logger.warning("Skipped delete of {}: {}", path, skip_reason)
                continue

            if recycle:
                result = svc.delete_to_recycle([path])
                if result.success_paths:
                    success_paths.append(path)
                    # D7: write outcome PER FILE immediately after success.
                    try:
                        session.record_deleted(path)
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
                        session.record_deleted(path)
                    except Exception as exc:
                        # FIX 5: file IS gone from disk — keep it in success_paths,
                        # record the DB write failure separately.
                        logger.warning("Failed to write deleted outcome for {}: {}", path, exc)
                        db_write_failed.append(path)
                except OSError as exc:
                    failed.append((path, str(exc)))

    # --- Batch ignore ---------------------------------------------------
    ignored: list[str] = []
    if safe_ignore_paths:
        try:
            _transition_outcome(
                repo, manifest_path, safe_ignore_paths, verdict,
                from_outcome="", to_outcome="ignored", named=named,
            )
            ignored = safe_ignore_paths
        except Exception as exc:
            # #985: the rows keep their lock and stay in review; report them
            # as failed instead of returning them as ignored.
            logger.warning("Failed to write ignored outcomes: {}", exc)
            failed.extend(
                (p, f"outcome write failed: {exc}") for p in safe_ignore_paths
            )

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
        "ignored": ignored,
        "missing": missing,
        "log_path": log_path,
        "groups": groups,
        "db_write_failed": db_write_failed,
    }


def remove_from_review(
    manifest_path: str,
    file_paths: list[str],
    confirmed_locked: Collection[str] = (),
    allowed_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Remove files from the review manifest (outcome='ignored', files untouched).

    Args:
        manifest_path: Absolute path to the manifest sqlite.
        file_paths: Paths to mark as ignored. Only rows still in review are
            written; deleted or already-dismissed rows are left as they are.
        confirmed_locked: Locked rows the user confirmed by name; each is
            unlocked in the same write as its ignored outcome (#985). A locked
            in-root row that is not named raises ValueError with the locked paths.
        allowed_roots: Trusted root directories.  Out-of-root rows in
            ``file_paths`` are silently skipped (not marked ignored).

    Returns:
        Dict with keys: removed, groups.
    """
    _require_manifest(manifest_path)
    repo = ManifestRepository()
    repo.ensure_schema(manifest_path)

    # --- Path-safety filter (#985: BEFORE the lock gate) -----------------
    # An out-of-root locked row is not gated, not unlocked, and keeps its lock.
    if allowed_roots:
        safe_paths, _ = _split_by_roots(file_paths, allowed_roots, "remove_from_review")
    else:
        safe_paths = file_paths

    # A4: plan, refuse and write in one IMMEDIATE txn. The lock is re-read there,
    # so a row locked after any earlier read is refused (409). Only rows still in
    # review are written (#A2); the rest are left as they are and not counted.
    verdict, named = _verdict(confirmed_locked)
    written = _transition_outcome(
        repo, manifest_path, safe_paths, verdict,
        from_outcome="", to_outcome="ignored", named=named,
    )

    from core.app_service.review_service import load_review
    try:
        review = load_review(manifest_path)
        groups = review["groups"]
    except Exception as exc:
        logger.warning("Failed to reload groups after remove: {}", exc)
        groups = []

    return {"removed": written, "groups": groups}


def restore_to_review(
    manifest_path: str,
    file_paths: list[str],
    allowed_roots: list[str] | None = None,
    confirmed_locked: Collection[str] = (),
) -> dict[str, Any]:
    """Undo an immediate Skip: put dismissed rows back in review (#909).

    ``remove_from_review`` (and the singleton prune that can follow it) writes
    nothing but ``outcome='ignored'`` — no file is touched, no delete CSV is
    written — so resetting ``outcome`` to ``''`` reverses it exactly. Only rows
    currently 'ignored' change; a 'deleted' row stays deleted.

    A locked dismissed row is gated like every other locked action (#981): it
    is refused with ``ValueError("locked_paths", ...)`` and nothing is written,
    unless it is named in ``confirmed_locked`` — then it is unlocked and
    restored in one write.

    Args:
        manifest_path: Absolute path to the manifest sqlite.
        file_paths: Paths to return to review.
        allowed_roots: Trusted root directories.  Out-of-root rows in
            ``file_paths`` are skipped (not restored), as in remove.
        confirmed_locked: Locked dismissed rows the user confirmed by name.
            A locked dismissed row that is not named raises ValueError.

    Returns:
        Dict with keys: restored (rows changed), groups.

    Raises:
        ValueError: ``("locked_paths", [...])`` when a locked dismissed row is
            in scope and not named in ``confirmed_locked``.
    """
    _require_manifest(manifest_path)
    if allowed_roots:
        safe_paths = [p for p in file_paths if is_under_roots(p, allowed_roots)]
        for p in file_paths:
            if not is_under_roots(p, allowed_roots):
                logger.warning(
                    "Refusing restore_to_review of out-of-root source_path: {}", p
                )
    else:
        safe_paths = file_paths

    repo = ManifestRepository()
    repo.ensure_schema(manifest_path)
    # A4: only 'ignored' rows are gated (BE-7). A locked row that is still in
    # review is not something this write touches, and a row locked after any
    # earlier read is refused inside the transaction.
    verdict, named = _verdict(confirmed_locked)
    restored = _transition_outcome(
        repo, manifest_path, safe_paths, verdict,
        from_outcome="ignored", to_outcome="", named=named,
    )

    from core.app_service.review_service import load_review
    try:
        groups = load_review(manifest_path)["groups"]
    except Exception as exc:
        logger.warning("Failed to reload groups after restore: {}", exc)
        groups = []

    return {"restored": restored, "groups": groups}


def _fetch_singleton_rows(conn) -> list:
    """Return (source_path, user_decision, is_locked, group_id) for every row
    whose group has exactly ONE remaining in-review (outcome='') member.

    Shared by classify_singletons (offer) and prune_singletons (apply) so both
    agree on what a singleton is.
    """
    return conn.execute(
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


def classify_singletons(
    manifest_path: str,
    allowed_roots: list[str] | None = None,
) -> dict[str, list[str]]:
    """Classify the manifest's current singletons into plain / actioned / locked.

    The web review view (load_review → MainVM) drops single-member groups
    (manifest_repository orphan-skip), so the frontend never *sees* singletons in
    its groups — unlike the Qt desktop's in-memory vm, which keeps them. This is
    the backend's authoritative classification the web prune flow offers from,
    mirroring the Qt ``_maybe_offer_singleton_prune`` bucket loop:

      * locked  — is_locked (regardless of decision); gated via the lock dialog.
      * actioned — unlocked, user_decision in {'delete', 'ignore'}.
      * plain   — unlocked, no pending decision.

    Out-of-root singletons are excluded (they can't be pruned, so they aren't
    offered).

    Returns:
        {"plain": [...], "actioned": [...], "locked": [...]}.
    """
    _require_manifest(manifest_path)
    repo = ManifestRepository()
    repo.ensure_schema(manifest_path)

    conn = _connect(manifest_path)
    try:
        rows = _fetch_singleton_rows(conn)
    finally:
        conn.close()

    plain: list[str] = []
    actioned: list[str] = []
    locked: list[str] = []
    for source_path, user_decision, is_locked_val, _group_id in rows:
        if allowed_roots and not is_under_roots(source_path, allowed_roots):
            continue
        if is_locked_val:
            locked.append(source_path)
            continue
        decision = user_decision or ""
        if decision in ("delete", "ignore"):
            actioned.append(source_path)
        else:
            plain.append(source_path)

    return {"plain": plain, "actioned": actioned, "locked": locked}


def prune_singletons(
    manifest_path: str,
    include_actioned: bool = False,
    paths: list[str] | None = None,
    allowed_roots: list[str] | None = None,
    confirmed_locked: Collection[str] = (),
) -> dict[str, Any]:
    """Remove singleton groups from the review manifest.

    A singleton is a group whose sole remaining outcome='' member (with all
    partner rows now at outcome='deleted'/'ignored') would otherwise orphan.
    We detect them differently from the Qt path: we look for group_id values
    that have exactly ONE row with outcome=''.

    Two modes:
      * **Category** (``paths is None``) — prune all plain singletons, plus all
        actioned ones when ``include_actioned`` is True.
      * **Explicit** (``paths`` provided, #686) — prune EXACTLY the singletons
        named in ``paths``, ignoring ``include_actioned``. The caller (the web
        prune dialog / lock-gate verdict) computes the precise set, mirroring
        the Qt desktop's ``_apply_singleton_prune(to_prune)``. The requested
        paths are still intersected with the *actual* current singletons and
        filtered by the locked / under-roots guards below — a stray or
        non-singleton path is silently dropped, never pruned.

    Args:
        manifest_path: Absolute path to the manifest sqlite.
        include_actioned: If True, also prune singletons whose remaining row
            has user_decision in {'delete', 'ignore'}.  If False, only prune
            plain singletons (user_decision == '').  Ignored in explicit mode.
        paths: Explicit-mode prune set (#686).  None = category mode.
        allowed_roots: Trusted root directories.  Out-of-root singleton rows
            are silently skipped (not pruned) and never lock-gated.
        confirmed_locked: Locked singletons in scope that the user confirmed by
            name (A8, B-2/B-3). Each is pruned and unlocked in the same write as
            its ignored outcome (#983). A locked singleton in scope that is not
            named raises ``ValueError("locked_paths", [...])`` and nothing is written.

    Returns:
        Dict with keys: pruned, locked_skipped (always empty since #983), groups.

    Raises:
        ValueError: ``("locked_paths", [...])`` when a locked singleton is in
            scope and not named in ``confirmed_locked``.
    """
    requested: set[str] | None = set(paths) if paths is not None else None
    _require_manifest(manifest_path)
    repo = ManifestRepository()
    repo.ensure_schema(manifest_path)

    conn = _connect(manifest_path)
    try:
        # Find group_ids with exactly one remaining in-review row.
        rows = _fetch_singleton_rows(conn)
    finally:
        conn.close()

    # Scope first, then the lock gate (the #985 order): out-of-root rows are
    # dropped before the gate, so a locked out-of-root row is neither gated nor
    # pruned.
    selected: list[str] = []
    for source_path, user_decision, _is_locked, _group_id in rows:
        # Explicit mode (#686): consider only the caller-named singletons. A
        # requested path that is no longer a singleton simply never appears in
        # `rows`, so it is dropped — never pruned.
        if requested is not None and source_path not in requested:
            continue

        # Category mode: plain singletons, plus actioned ones when asked.
        decision = user_decision or ""
        if requested is None and not (
            decision == "" or (include_actioned and decision in ("delete", "ignore"))
        ):
            continue

        # --- Path-safety check ------------------------------------------------
        if allowed_roots and not is_under_roots(source_path, allowed_roots):
            logger.warning(
                "Refusing prune of out-of-root source_path: {}", source_path
            )
            continue

        selected.append(source_path)

    # #983: a locked singleton in scope is refused like every other write, and
    # the whole call writes nothing (A4). Each confirmed locked row is unlocked
    # in the same statement as its ignored outcome (#985).
    to_prune = selected
    verdict, named = _verdict(confirmed_locked)
    _transition_outcome(
        repo, manifest_path, to_prune, verdict,
        from_outcome="", to_outcome="ignored", named=named,
    )

    from core.app_service.review_service import load_review
    try:
        review = load_review(manifest_path)
        groups = review["groups"]
    except Exception as exc:
        logger.warning("Failed to reload groups after prune: {}", exc)
        groups = []

    # locked_skipped is always empty since #983: a locked singleton in scope is
    # either refused (409) or pruned and unlocked. Kept so PruneResult's shape
    # does not change for existing callers.
    return {"pruned": to_prune, "locked_skipped": [], "groups": groups}


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
