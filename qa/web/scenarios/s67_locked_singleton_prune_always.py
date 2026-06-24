"""Web scenario s67 — D6 regression guard: locked singleton under prune="always"
(#686, web port of qa/scenarios/s67_locked_singleton_prune_always.py).

Qt intent
---------
Under ``ui.prune_singletons="always"`` a destructive op that leaves a LOCKED
singleton must STILL fire the lock-confirm gate before pruning it — the
"always" standing instruction does not silently sweep locked rows (the D6
regression the scenario exists to guard). The SingletonPruneConfirmDialog must
NEVER appear on the "always" path.

Web slice
---------
One generated near-dup cluster (KEEP, DROP). KEEP is locked; DROP is finalized
out via the EXECUTE-dialog "Remove from list" (store.removeFromList → POST
/api/remove → outcome='ignored', the same finalizing path s54 pins). That
collapses the group to the locked KEEP singleton, which fires maybeOfferPrune
under "always" → the prune-context LockConfirmDialog (op="prune").

Two variants:
  * V1 — Cancel on the lock gate → KEEP is held (still in the manifest, locked);
    nothing is pruned (the locked subset was the only content).
  * V2 — Unlock & Apply → KEEP is unlocked then pruned (gone from the manifest).

In BOTH the PruneConfirmDialog must NEVER appear (the "always" path skips it) —
asserted by a short-timeout absence check after the lock gate is dismissed.

Web divergences vs Qt
  D1. Finalizing remove is driven via the EXECUTE-dialog menu (single-row +
      confirm); DROP is staged 'delete' first to appear in the execute tree,
      then removed there. (Since #694 the result-tree "Remove from list" also
      finalizes, but this scenario keeps the single-row execute-dialog path.)
  D2. The held/pruned outcome is asserted via a DIRECT sqlite read of the
      ``outcome`` column, NOT GET /api/manifest. A HELD locked singleton has
      outcome='' but is a single-member group, which the web review view
      (load_review → MainVM orphan-skip, len<2) FILTERS OUT — so the manifest
      API cannot distinguish "held" from "pruned" for a singleton (both are
      absent from the view). sqlite is the source of truth here, mirroring the
      desktop driver. The copied file staying on disk corroborates outcome=
      'ignored' (removed from review, not deleted).

Desktop source: qa/scenarios/s67_locked_singleton_prune_always.py
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._prune_fixtures import write_cluster
from qa.web._invariants import (
    click_context_item,
    open_execute_dialog,
    right_click_row,
    run_scan,
    set_prune_pref,
)
from qa.web.testid_constants import (
    CTX_LOCK,
    CTX_SET_ACTION_DELETE,
    CTX_SET_ACTION_REMOVE,
    EXECUTE_REMOVE_CONFIRM,
    EXECUTE_REMOVE_CONFIRM_YES,
    LOCK_CONFIRM_BTN_CANCEL,
    LOCK_CONFIRM_BTN_UNLOCK_APPLY,
    LOCK_CONFIRM_DIALOG,
    PRUNE_CONFIRM_DIALOG,
    execute_row_testid,
    row_file_testid,
)

_KEEP = "s67_keep_q95.jpg"
_DROP = "s67_drop_q65.jpg"
_PRUNE_ABSENCE_MS = 1200


# ---------------------------------------------------------------------------
# Manifest helpers (s54 shape)
# ---------------------------------------------------------------------------


def _manifest(base_url: str, db_path: str) -> dict:
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _by_name(manifest: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for group in manifest.get("groups", []):
        for it in group.get("items", []):
            out[Path(it["file_path"]).name] = it
    return out


def _first_group_id(base_url: str, db_path: str) -> str:
    data = _manifest(base_url, db_path)
    assert data["total_groups"] >= 1, "expected at least one group in the manifest"
    return str(data["groups"][0]["group_number"])


def _sqlite_row(db_path: str, basename: str) -> tuple[str, int]:
    """Return (outcome, is_locked) for ``basename`` straight from sqlite.

    The review-view API hides singletons, so we read the manifest db directly to
    distinguish a HELD locked singleton (outcome='') from a pruned one
    (outcome='ignored'). Read-only — never mutates."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COALESCE(outcome, ''), COALESCE(is_locked, 0) "
            "FROM migration_manifest WHERE source_path LIKE ?",
            (f"%{basename}",),
        ).fetchone()
    finally:
        conn.close()
    return (row[0], row[1]) if row else ("", 0)


def _await(predicate, *, timeout: float = 6.0):
    """Poll predicate() until truthy or timeout; return its last value."""
    deadline = time.monotonic() + timeout
    val = predicate()
    while not val and time.monotonic() < deadline:
        time.sleep(0.1)
        val = predicate()
    return val


def _run_variant(base_url: str, *, lock_btn: str, keep_should_survive: bool) -> None:
    tmpdir = tempfile.mkdtemp(prefix="qa_s67_")
    try:
        write_cluster(Path(tmpdir), (_KEEP, _DROP), exif_month=7)
        db_path = os.path.join(tmpdir, "s67_manifest.db")
        keep_copy = os.path.join(tmpdir, _KEEP)

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            run_scan(page, sources=[tmpdir], output_path=db_path, scan_timeout=120_000)
            rows = _by_name(_manifest(base_url, db_path))
            assert set(rows) == {_KEEP, _DROP}, f"expected the 2-file cluster, got {sorted(rows)}"
            gid = _first_group_id(base_url, db_path)

            # Stage DROP='delete' (so it appears in the execute tree) and LOCK KEEP.
            right_click_row(page, row_file_testid(gid, _DROP))
            click_context_item(page, CTX_SET_ACTION_DELETE)
            right_click_row(page, row_file_testid(gid, _KEEP))
            click_context_item(page, CTX_LOCK)
            locked = _await(
                lambda: _by_name(_manifest(base_url, db_path)).get(_KEEP, {}).get("is_locked") is True
            )
            assert locked, f"setup failed to lock {_KEEP}"

            # The whole point: prune pref = "always".
            set_prune_pref(base_url, "always")

            # Finalize-remove DROP via the execute dialog → group collapses to the
            # locked KEEP singleton → maybeOfferPrune("always") → lock gate.
            open_execute_dialog(page)
            right_click_row(page, execute_row_testid(gid, _DROP))
            click_context_item(page, CTX_SET_ACTION_REMOVE)
            page.get_by_test_id(EXECUTE_REMOVE_CONFIRM).wait_for(state="visible", timeout=10_000)
            page.get_by_test_id(EXECUTE_REMOVE_CONFIRM_YES).click()

            # D6 gate: the prune-context lock dialog MUST fire under "always".
            lock_dlg = page.get_by_test_id(LOCK_CONFIRM_DIALOG)
            lock_dlg.wait_for(state="visible", timeout=8_000)
            page.get_by_test_id(lock_btn).click()

            # The "always" path MUST NEVER show the prune dialog.
            prune_dlg = page.get_by_test_id(PRUNE_CONFIRM_DIALOG)
            try:
                prune_dlg.wait_for(state="visible", timeout=_PRUNE_ABSENCE_MS)
                raise AssertionError(
                    "SingletonPruneConfirmDialog surfaced under "
                    'ui.prune_singletons="always" — it must only fire on "ask".'
                )
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, AssertionError):
                    raise
                # PlaywrightTimeoutError → absent, as required.

            # Assert KEEP's fate via sqlite (the review API hides singletons).
            # State has settled: the lock gate was dismissed + the prune-absence
            # check already waited _PRUNE_ABSENCE_MS. Poll briefly for the async
            # resolve to land, then assert the definitive outcome.
            want = "" if keep_should_survive else "ignored"
            _await(lambda: _sqlite_row(db_path, _KEEP)[0] == want, timeout=4.0)
            outcome, is_locked = _sqlite_row(db_path, _KEEP)
            if keep_should_survive:
                assert outcome == "", f"Cancel must HOLD the locked singleton {_KEEP} (outcome=''), got {outcome!r}"
                assert is_locked == 1, f"{_KEEP} should still be locked after Cancel"
            else:
                assert outcome == "ignored", f"Unlock&Apply must PRUNE {_KEEP} (outcome='ignored'), got {outcome!r}"
            assert _sqlite_row(db_path, _DROP)[0] == "ignored", f"{_DROP} should be removed (outcome='ignored')"
            assert os.path.exists(keep_copy), f"{_KEEP} must NOT be deleted from disk (ignored, not deleted)"
            print(
                f"probe_status: s67 lock_btn={lock_btn} keep_survives={keep_should_survive} "
                f"keep_outcome={outcome!r} keep_on_disk={os.path.exists(keep_copy)}"
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run(*, base_url: str) -> None:
    # V1 — Cancel on the lock gate holds the locked singleton; nothing pruned.
    _run_variant(base_url, lock_btn=LOCK_CONFIRM_BTN_CANCEL, keep_should_survive=True)
    # V2 — Unlock & Apply prunes the (now-unlocked) locked singleton.
    _run_variant(base_url, lock_btn=LOCK_CONFIRM_BTN_UNLOCK_APPLY, keep_should_survive=False)
