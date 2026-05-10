"""Scenario 11 — Video + Live Photo (photo-manager#88 regression guard).

Required sources: qa/sandbox/videos, qa/sandbox/live-photo
Probes (asserted via SQLite — see "Verification path" below):
  * Walker emits a FileRecord for BOTH halves of the
    ``IMG_0001.HEIC`` + ``IMG_0001.MOV`` pair (pre-#88 the MOV was
    silently dropped before hashing — the manifest had IMG_0001.HEIC
    but no IMG_0001.MOV).
  * Both rows share the same ``group_id`` regardless of either side's
    SHA / pHash dedup status. Per photo-manager#88: pairing is coupled
    at matching/grouping but per-row at action / user_decision.

Verification path
-----------------
Reads the persisted manifest after the GUI scan completes. The earlier
incarnation of this scenario tried to walk the result tree via
``_uia.read_result_rows``, but on the windows-latest runner all tree
items render with screen ``top < 600``, falling below the helper's
``y_min`` filter — the helper returns 0 rows in CI even when the
manifest is correctly populated (verified across s01 / s09 / s10 in
the same run). SQLite verification gives the same end-state confidence
without depending on UIA's screen-coordinate filter.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"


def _read_pair_rows() -> dict[str, dict]:
    """Return ``{basename: row_dict}`` for the IMG_0001 Live Photo pair."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT source_path, action, group_id, source_hash, user_decision "
            "FROM migration_manifest WHERE source_path LIKE '%IMG_0001.%'"
        ).fetchall()
    finally:
        conn.close()
    return {Path(r["source_path"]).name: dict(r) for r in rows}


def main() -> int:
    print("scenario: s11_video_live")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, scan_hwnd = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=60)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)

    # ── Assertions (photo-manager#88) — manifest-level ─────────────────
    print("step: verify_pair_in_manifest")
    pair = _read_pair_rows()
    print(f"  pair_rows_found={sorted(pair)}")
    for name, row in sorted(pair.items()):
        gid_short = Path(row["group_id"]).name if row["group_id"] else None
        print(f"    {name}  action={row['action']!r}  group_id={gid_short!r}")

    # 1) Both halves present — walker no longer drops the MOV partner.
    if "IMG_0001.HEIC" not in pair:
        print("FAIL: IMG_0001.HEIC missing from manifest — walker bug?")
        return 1
    if "IMG_0001.MOV" not in pair:
        print(
            "FAIL: IMG_0001.MOV missing from manifest. Pre-#88 the walker "
            "added the MOV path to a 'paired' set when processing the HEIC, "
            "then skipped emitting a FileRecord for it on the next loop "
            "iteration. The video never reached hashing or the manifest."
        )
        return 1

    # 2) Both rows share a group_id (pair edges from
    #    scanner/dedup._collect_pair_edges union them via union-find,
    #    even when the HEIC is unique and has no SHA/pHash duplicate).
    heic_gid = pair["IMG_0001.HEIC"]["group_id"]
    mov_gid  = pair["IMG_0001.MOV"]["group_id"]
    if not heic_gid:
        print("FAIL: HEIC has no group_id — pair edge wasn't unioned")
        return 1
    if not mov_gid:
        print("FAIL: MOV has no group_id — pair edge wasn't unioned")
        return 1
    if heic_gid != mov_gid:
        print(
            f"FAIL: pair NOT grouped together. "
            f"heic.group_id={heic_gid!r} mov.group_id={mov_gid!r}. "
            f"#88 invariant: Live Photo HEIC+MOV always share a group_id."
        )
        return 1
    print(f"  pair_group_id={Path(heic_gid).name!r}  (#88 invariant satisfied)")

    # 3) Decoupling spec — actions are independent. The HEIC has its own
    #    action (typically MOVE since the qa fixture writes a
    #    DateTimeOriginal) and the MOV has its own. Pre-#88 propagation
    #    would have forced MOV's action to mirror the HEIC's; that
    #    behavior is removed.
    if pair["IMG_0001.MOV"]["user_decision"] != "":
        print(
            f"WARN: pre-scan user_decision on MOV is "
            f"{pair['IMG_0001.MOV']['user_decision']!r} — expected empty"
        )

    print("scenario: s11_video_live DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
