"""Scenario 65 — the #538 "passenger" forms end-to-end on real photos (#544).

Required sources: qa/sandbox/passenger-bridge-a (priority 0),
                  qa/sandbox/passenger-bridge-b (priority 1).

This is the live, real-data counterpart to the layer-1 passenger tests
(``tests/test_tree_model_builder.py::TestPassengerRelabel`` + the build_model
end-to-end). Those use synthetic ``HashResult``s; this proves that *scanning
three real burst frames* produces the passenger structure the relabel depends on.

The fixture is a genuine pHash+dHash bridge (see the fixture README):
``scene_bridge`` (source B, lower priority) is a near-dup of BOTH
``scene_left`` and ``scene_right`` (source A), while left ≁ right. After #538's
true transitive closure both source-A frames stay Ref-tier and one becomes the
"passenger" (a 2nd Ref-tier row in the group) — which renders a real similarity
(`N%` / `N*%`), never a bare "—", because it has a pHash.

Per CLAUDE.md, this driver asserts on the manifest (sqlite) rather than the live
tree, because ``read_result_rows`` filters rows below y=600 on CI's smaller
render — same pattern as s14 / s32 / s35 / s52.

PRE: PHOTO_MANAGER_HOME=qa QT_ACCESSIBILITY=1 .venv/Scripts/python.exe main.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
REF_TIER_ACTIONS = {"", "KEEP", "UNDATED"}


def _read_bridge_rows() -> list[dict]:
    """Return the three passenger-bridge fixture rows from the manifest."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, action, phash, group_id "
            "FROM migration_manifest WHERE source_path LIKE ?",
            ("%passenger-bridge%",),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"name": Path(p).name, "action": a or "", "phash": ph, "group_id": gid}
        for p, a, ph, gid in rows
    ]


def main() -> int:
    print("scenario: s65_passenger_bridge")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)
    sources = _uia.read_configured_sources(dlg)
    print(f"  configured_sources={sources!r}  (a first → priority 0)")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: read_bridge_rows")
    rows = _read_bridge_rows()
    for r in rows:
        print(f"  row: name={r['name']} action={r['action']!r} "
              f"phash={'yes' if r['phash'] else 'NO'} group={Path(r['group_id']).name if r['group_id'] else None}")

    if len(rows) != 3:
        print(f"FAIL: expected 3 passenger-bridge rows, got {len(rows)} — fixture changed?")
        return 1

    # ── Invariant 1: all three share one (non-null) group_id ─────────────
    print("step: assert_single_group")
    gids = {r["group_id"] for r in rows}
    if None in gids or len(gids) != 1:
        print(f"FAIL: the bridge must union all three into ONE group; got group_ids={gids}")
        return 1

    # ── Invariant 2: exactly two Ref-tier rows (the passenger) + one
    # REVIEW_DUPLICATE (the bridge). This is the #538-closure structure the
    # relabel renders. ───────────────────────────────────────────────────
    print("step: assert_passenger_structure")
    ref_tier = [r for r in rows if r["action"] in REF_TIER_ACTIONS]
    review = [r for r in rows if r["action"] == "REVIEW_DUPLICATE"]
    if len(ref_tier) != 2 or len(review) != 1:
        print(
            f"FAIL: expected 2 Ref-tier rows (a passenger) + 1 REVIEW_DUPLICATE; "
            f"got ref_tier={[r['name'] for r in ref_tier]} review={[r['name'] for r in review]}. "
            f"If #538's transitive closure regressed, the bridge orphans instead."
        )
        return 1
    if review[0]["name"] != "scene_bridge.jpg":
        print(f"FAIL: the lower-priority bridge should be the REVIEW_DUPLICATE; got {review[0]['name']!r}")
        return 1

    # ── Invariant 3: every grouped row has a pHash, so the passenger renders
    # a real similarity (N% / N*%), never a bare "—" (which is reserved for a
    # no-pHash passenger like a Live Photo MOV). ─────────────────────────
    print("step: assert_passenger_is_renderable")
    no_phash = [r["name"] for r in rows if not r["phash"]]
    if no_phash:
        print(f"FAIL: rows {no_phash} have no pHash — the passenger would render '—' not a %")
        return 1

    print("scenario: s65_passenger_bridge DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
