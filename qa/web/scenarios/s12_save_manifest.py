"""Web scenario s12 — Save Manifest Decisions, ported as the live-persistence
durability guarantee (#673 Save-Manifest sub-item).

Ported from qa/scenarios/s12_save_manifest.py (Qt UIA).

Qt intent
---------
In the Qt desktop app the manifest is held in memory (``_vm.groups``, tracked by
``_is_dirty``) and per-photo keep/delete decisions are LOST on close unless the
user explicitly clicks **File → Save Manifest Decisions…**, which writes
``user_decision`` for every row back to the ``migration_manifest`` table on disk
and shows a "Saved N decision(s)" status message. The user-facing INTENT is
durability: don't lose my review work when I close the app.

Web reframing (the parity decision — see scenario_map.yml notes)
----------------------------------------------------------------
The web app has NO explicit Save step and NO dirty state. Every decision is
persisted live and durably the moment it is made: ``PATCH /api/decision`` →
``review_service.set_decisions()`` → ``ManifestRepository.batch_update_decisions()``
opens a connection, ``executemany``, ``commit()``, ``close()`` — synchronously
(manifest_repository.py:462-471). ``GET /api/manifest`` is stateless and re-reads
the DB ("SQLite is the only source of truth", review_service.py:4), and
``POST /api/save`` is a pure WAL-checkpoint/copy utility whose own docstring
states "decisions are already persisted per-PATCH" (execute_service.py:504-546).

So Qt's Save-Manifest GUARANTEE (decisions survive the session) is met by
construction. This scenario verifies that guarantee — and verifies it MORE
strictly than the Qt original, which only checks the ``migration_manifest`` table
exists + row counts (no value round-trip) and WARNs (not FAILs) on the status
message. Here:

  * a decision's VALUE is asserted to round-trip (hard FAIL),
  * durability is proven across a FULL CLIENT RESET — the Zustand store is wiped
    by ``page.reload()``, the manifest is re-opened from disk, and the decision
    is still present (re-rendered in the UI **and** in ``GET /api/manifest``),
  * the decision is asserted COMMITTED ON DISK by opening the ``.db`` read-only
    (the Qt-parity ``SQLite format 3`` header + ``migration_manifest`` table
    checks, plus the specific row's ``user_decision``).

No explicit "Save Manifest" FE surface is added: a button that only WAL-checks
would be a semantically-hollow affordance contradicting live-persist (it would
teach a false "click Save or lose work" model — the opposite of the web's
guarantee). The genuine save-as / export-snapshot question (``POST /api/save``
``target_path`` as a UI flow) is unevidenced and tracked as a deferred
follow-up, not bolted on here.

Web slice
---------
  1. tmpdir copy of all 5 near-duplicate fixtures (1 group of 5); db under tmpdir.
     ``run_scan([tmpdir])`` → manifest loaded, 0 decisions.
  2. Stage 'Delete' on neardup_00_q95.jpg via the DecisionControl dropdown (the
     canonical reversible keep/delete affordance, PATCH-persisted). GET
     /api/manifest: 1 decision, and that row's ``user_decision == 'delete'``
     (VALUE round-trip — stricter than Qt s12).
  3. DURABILITY: ``page.reload()`` (wipes the Zustand store → empty state), then
     re-open the SAME manifest by path (``load_manifest``). The decision
     survives — proven three ways:
       * the re-rendered DecisionControl trigger shows 'Delete' (the client
         re-hydrated from the persisted DB, not from the wiped store),
       * ``GET /api/manifest`` still shows 1 decision with value 'delete',
       * the ``.db`` on disk has ``user_decision='delete'`` committed.
  4. finally: ``shutil.rmtree`` tmpdir.

Qt divergences
--------------
  - No File → Save menu click and no "Saved N decision(s)" status message — the
    web has no explicit Save step (durability is automatic), so those are not the
    mechanism of persistence and aren't reproduced. The durability GUARANTEE they
    exist to provide IS verified, stricter than Qt s12.
  - Decision read from ``GET /api/manifest`` JSON + a read-only sqlite3 disk read,
    vs Qt's sqlite read of a saved copy.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

from qa.web._pw import PWContext
from qa.web._invariants import (
    run_scan,
    set_row_decision,
    load_manifest,
    wait_for_manifest,
)
from qa.web.testid_constants import (
    MAIN_EMPTY_STATE,
    row_decision_testid,
    row_decision_option_testid,
)

_REPO = Path(__file__).resolve().parents[3]
_NEAR_DUPS_DIR = _REPO / "qa" / "sandbox" / "near-duplicates"

# The row the scenario stages a decision on (highest-quality of the group,
# matching the Qt s12 / s27 target). Lives in the near-duplicate fixture.
ROW_TARGET = "neardup_00_q95.jpg"

_ALL_BASENAMES = [
    "neardup_00_q95.jpg",
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
]


# ---------------------------------------------------------------------------
# HTTP / manifest helpers
# ---------------------------------------------------------------------------


def _get_manifest(base_url: str, db_path: str) -> dict:
    """Return the full manifest JSON via GET /api/manifest?path=<db_path>."""
    encoded = urllib.parse.quote(db_path, safe="")
    url = f"{base_url.rstrip('/')}/api/manifest?path={encoded}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 (loopback)
        return json.loads(resp.read())


def _items(manifest: dict) -> list[dict]:
    """Flatten all file items from all groups (group key is "items")."""
    items: list[dict] = []
    for group in manifest.get("groups", []):
        items.extend(group.get("items", []))
    return items


def _decision_count(manifest: dict) -> int:
    """Number of rows carrying a non-empty user_decision."""
    return sum(1 for it in _items(manifest) if (it.get("user_decision") or "") != "")


def _decision_for(manifest: dict, basename: str) -> str:
    """Return the user_decision of the item whose file_path basename == ``basename``."""
    for it in _items(manifest):
        if Path(it["file_path"]).name == basename:
            return it.get("user_decision") or ""
    raise AssertionError(
        f"basename {basename!r} not found in manifest; "
        f"present={[Path(i['file_path']).name for i in _items(manifest)]}"
    )


def _group_id_for(manifest: dict, basename: str) -> str:
    """Return str(group_number) of the group containing ``basename``."""
    for group in manifest.get("groups", []):
        for it in group.get("items", []):
            if Path(it["file_path"]).name == basename:
                return str(group["group_number"])
    raise AssertionError(
        f"basename {basename!r} not found in any group; "
        f"present={[Path(i['file_path']).name for i in _items(manifest)]}"
    )


def _disk_state(db_path: str, basename: str) -> tuple[bytes, bool, str]:
    """Read the manifest .db on disk (read-only) and return the durability facts.

    Returns ``(header16, migration_manifest_present, user_decision)``:

    * ``header16`` — the first 16 bytes (Qt s12 asserts the ``SQLite format 3``
      magic),
    * ``migration_manifest_present`` — whether the canonical decisions table
      exists (Qt s12 structural parity),
    * ``user_decision`` — the committed decision for the row whose ``source_path``
      basename matches ``basename`` (``""`` if the row/value is absent).

    Opens read-only (``mode=ro``) so the live server's WAL-mode DB is never
    locked or mutated; a read-only reader still sees the committed PATCH.
    """
    with open(db_path, "rb") as fh:
        header16 = fh.read(16)
    uri = f"{Path(db_path).as_uri()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        tables = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        decision = ""
        if "migration_manifest" in tables:
            for src, dec in con.execute(
                "SELECT source_path, user_decision FROM migration_manifest"
            ):
                if Path(src).name == basename:
                    decision = dec or ""
                    break
    finally:
        con.close()
    return header16, ("migration_manifest" in tables), decision


# ---------------------------------------------------------------------------
# Scenario entry point
# ---------------------------------------------------------------------------


def run(*, base_url: str) -> None:
    """A staged decision is durable: it survives reload + reopen and is on disk."""
    tmpdir = tempfile.mkdtemp(prefix="qa_s12_")
    db_path = os.path.join(tmpdir, "s12_manifest.db")
    try:
        for basename in _ALL_BASENAMES:
            shutil.copy(str(_NEAR_DUPS_DIR / basename), os.path.join(tmpdir, basename))

        with PWContext(base_url=base_url) as ctx:
            page = ctx.new_page()
            page.goto("/")

            # ── Step 1: scan → manifest loaded, no pending decisions ─────────
            run_scan(
                page,
                sources=[tmpdir],
                output_path=db_path,
                scan_timeout=120_000,
            )
            manifest = _get_manifest(base_url, db_path)
            assert manifest.get("total_files") == 5, (
                f"Expected 5 files after scan, got {manifest.get('total_files')}"
            )
            assert _decision_count(manifest) == 0, (
                "Freshly scanned manifest should carry no pending decisions; "
                f"got {_decision_count(manifest)}"
            )

            # ── Step 2: stage a decision via the DecisionControl dropdown ────
            gid = _group_id_for(manifest, ROW_TARGET)
            set_row_decision(page, row_decision_testid(gid, ROW_TARGET), "Delete")

            # The click stages the decision via an async PATCH; poll until it
            # lands server-side instead of racing it (#815 s12 CI flake).
            manifest = wait_for_manifest(
                base_url, db_path, lambda m: _decision_count(m) == 1
            )
            assert _decision_count(manifest) == 1, (
                "Expected exactly 1 pending decision after staging Delete; "
                f"got {_decision_count(manifest)}"
            )
            assert _decision_for(manifest, ROW_TARGET) == "delete", (
                "the staged decision VALUE must round-trip through "
                "PATCH /api/decision to GET /api/manifest (stricter than Qt s12, "
                "which never asserts the value); got "
                f"{_decision_for(manifest, ROW_TARGET)!r}"
            )

            # ── Step 3: DURABILITY across a full client reset ────────────────
            # Reload wipes the Zustand store (→ empty state). The decision can
            # only reappear if it was persisted to disk — this is the web
            # equivalent of Qt's "decisions survive the session" guarantee.
            page.reload()
            page.get_by_test_id(MAIN_EMPTY_STATE).wait_for(
                state="visible", timeout=10_000
            )

            # Re-open the SAME manifest from disk.
            load_manifest(page, db_path)
            reopened = _get_manifest(base_url, db_path)
            gid_after = _group_id_for(reopened, ROW_TARGET)

            # (a) the re-rendered control reflects the persisted decision — the
            # client re-hydrated from the DB, not from the wiped store. The
            # DecisionControl is a button row, so the active decision is the
            # button with aria-pressed="true" (every label renders as text, so an
            # inner-text check would false-pass).
            delete_btn = page.get_by_test_id(
                row_decision_option_testid(gid_after, ROW_TARGET, "delete")
            )
            delete_btn.wait_for(state="visible", timeout=10_000)
            delete_btn.scroll_into_view_if_needed(timeout=10_000)
            pressed = delete_btn.get_attribute("aria-pressed")
            assert pressed == "true", (
                "after reload + reopen the DecisionControl must re-render the "
                "persisted 'Delete' decision as the active button; delete button "
                f"aria-pressed was {pressed!r}"
            )

            # (b) the server's view of the DB still carries the decision value.
            assert _decision_count(reopened) == 1, (
                "the decision must survive a full page reload + manifest re-open "
                "(the web persists live; a reload wipes only client state); got "
                f"decision_count={_decision_count(reopened)}"
            )
            assert _decision_for(reopened, ROW_TARGET) == "delete", (
                "after reload + reopen the decision VALUE must still be 'delete'; "
                f"got {_decision_for(reopened, ROW_TARGET)!r}"
            )

            # (c) the decision is committed ON DISK (Qt s12 structural parity +
            # the value), proving durability is real — not a server-side cache.
            header16, table_present, disk_decision = _disk_state(db_path, ROW_TARGET)
            assert header16.startswith(b"SQLite format 3"), (
                f"manifest .db must be a valid SQLite file; header={header16!r}"
            )
            assert table_present, (
                "manifest .db must contain the migration_manifest table "
                "(Qt s12 structural parity)"
            )
            assert disk_decision == "delete", (
                "the decision must be committed to the .db on disk, not merely "
                f"held in a server cache; on-disk user_decision={disk_decision!r}"
            )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
