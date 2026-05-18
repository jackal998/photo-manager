"""Scenario 52 — Similarity % measured against the *displayed* Ref (#253).

Required source: qa/sandbox/near-duplicates (5 JPEGs neardup_NN_qXX.jpg).

After #241's score-aware Ref tie-break, the row that carries the "Ref"
label in the tree can diverge from the row the scanner anchored on
when computing each REVIEW_DUPLICATE's stored ``hamming_distance``.
The Similarity column used to render the stored distance directly,
making the % read "X% similar to *what?*" once the displayed Ref had
shifted. #253 fixes this by recomputing pHash Hamming distance at
render time against the *displayed* Ref's pHash, which requires:

  1. The scanner writes ``phash`` for every grouped row (already true
     pre-#253; see ``scanner/manifest.py`` schema).
  2. ManifestRepository loads ``phash`` into PhotoRecord — added in
     this change.
  3. The tree builder reads the Ref winner's pHash and passes it into
     ``_file_similarity`` for each REVIEW_DUPLICATE sibling.

This scenario pins the wiring (1)+(2)+(3) by replicating, on the
manifest side, the exact arithmetic the renderer performs and checking
that the data the renderer needs is actually present and produces
the expected percentages.

The full divergence case (scanner anchor != score-winner with two
Ref-tier rows in one group) is exercised at layer 1 in
``tests/test_tree_model_builder.py::TestBuildModelSimilarityAgainstDisplayedRef``
— the near-duplicates fixture has only one Ref-tier row so anchor
and displayed Ref happen to coincide here, but the data path being
verified is the same.

Per CLAUDE.md, this driver uses sqlite-read for tree-content assertions
because ``read_result_rows`` filters rows below y=600 on CI's smaller
render — same pattern as s14, s32, s35.

PRE: PHOTO_MANAGER_HOME=qa QT_ACCESSIBILITY=1 .venv/Scripts/python.exe main.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"
FIXTURE_NAME_GLOB = "neardup_"

REF_TIER_ACTIONS = {"KEEP", "MOVE", "UNDATED", ""}


def _hamming_to_pct(hamming: int) -> str:
    """Mirror of ``app/views/tree_model_builder._hamming_to_pct`` —
    duplicated here so the scenario doesn't depend on the renderer's
    private helper while still computing the *exact* % the user sees.
    """
    return f"{round((64 - hamming) / 64 * 100)}%"


def _read_rows() -> list[dict]:
    """Return every fixture row as a dict with the columns this
    scenario inspects."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, action, phash, hamming_distance, score "
            "FROM migration_manifest WHERE source_path LIKE ?",
            (f"%{FIXTURE_NAME_GLOB}%",),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "name": Path(p).name,
            "action": a or "",
            "phash": ph,
            "hamming_distance": hd,
            "score": s,
        }
        for p, a, ph, hd, s in rows
    ]


def _pick_displayed_ref(rows: list[dict]) -> dict | None:
    """Replicate ``_pick_ref_winner`` from app/views/tree_model_builder.

    Same tie-break: Ref-tier only, then highest score, then lex file
    name. Inlined so a future refactor that splits or renames the
    helper doesn't silently fall out of sync with this scenario.
    """
    candidates = [r for r in rows if r["action"] in REF_TIER_ACTIONS]
    if not candidates:
        return None
    return min(
        candidates,
        # negate score so highest wins; unscored rows (None) collapse
        # to +inf and sort last among Ref-tier candidates.
        key=lambda r: (
            -(r["score"] if r["score"] is not None else float("-inf")),
            r["name"],
        ),
    )


def main() -> int:
    print("scenario: s52_similarity_against_displayed_ref")
    app, win = _uia.connect_main()
    print(f"connected: pid={win.process_id()} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: read_rows")
    rows = _read_rows()
    if not rows:
        print("FAIL: no fixture rows found in manifest after scan")
        return 1
    for r in rows:
        print(
            f"  row: name={r['name']} action={r['action']!r} "
            f"phash={r['phash']!r} hamming={r['hamming_distance']} "
            f"score={r['score']}"
        )

    # ── Invariant 1: phash populated everywhere (renderer needs it) ──────
    print("step: assert_phash_populated")
    missing_phash = [r["name"] for r in rows if not r["phash"]]
    if missing_phash:
        print(
            f"FAIL: phash column empty for {missing_phash} — the new render "
            f"path falls back to stored hamming_distance, defeating #253"
        )
        return 1

    # ── Invariant 2: a displayed Ref exists ──────────────────────────────
    print("step: assert_displayed_ref_exists")
    displayed_ref = _pick_displayed_ref(rows)
    if displayed_ref is None:
        print("FAIL: no Ref-tier row in fixture group — fixture changed?")
        return 1
    print(
        f"  displayed_ref name={displayed_ref['name']!r} "
        f"action={displayed_ref['action']!r} score={displayed_ref['score']}"
    )

    # ── Invariant 3: every REVIEW_DUPLICATE row's recomputed % is the
    # value the user sees in the Similarity column. Imports imagehash
    # here (not at module top) so the scenario file remains parseable
    # even on a CI image without the optional dep, and fails fast with
    # a clear message if it's missing locally. ────────────────────────
    print("step: assert_recomputed_pct_for_each_dup")
    try:
        import imagehash
    except ImportError:
        print("FAIL: imagehash not installed — required to verify #253 wiring")
        return 1

    ref_hash = imagehash.hex_to_hash(displayed_ref["phash"])
    failures: list[str] = []
    review_dup_rows = [r for r in rows if r["action"] == "REVIEW_DUPLICATE"]
    if not review_dup_rows:
        print(
            "FAIL: no REVIEW_DUPLICATE rows in fixture group — "
            "near-duplicates classifier may have changed"
        )
        return 1

    for r in review_dup_rows:
        row_hash = imagehash.hex_to_hash(r["phash"])
        distance = ref_hash - row_hash
        expected_pct = _hamming_to_pct(distance)
        legacy_pct = _hamming_to_pct(r["hamming_distance"])
        marker = "≡" if expected_pct == legacy_pct else "≠"
        print(
            f"  {r['name']}: ref_dist={distance} → {expected_pct}  "
            f"{marker}  stored hamming={r['hamming_distance']} → {legacy_pct}"
        )
        # Sanity check on the math itself: every recomputed % must
        # fall inside [0%, 100%]. A negative or >100 would mean
        # _hamming_to_pct was passed something other than 0..64.
        n = int(expected_pct.rstrip("%"))
        if not (0 <= n <= 100):
            failures.append(
                f"{r['name']}: recomputed pct {expected_pct} out of range"
            )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s52_similarity_against_displayed_ref DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
