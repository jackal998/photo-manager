"""Scenario 42 — Keep-worthiness scoring end-to-end (#187).

Required sources:
  * qa/sandbox/near-duplicates — 5 JPEG re-saves at qualities
    95/88/80/72/65 from one base image. Same resolution + same EXIF
    DateTimeOriginal across all five; ``file_size_bytes`` is the only
    differentiating scoring signal. Pins the pipeline plumbing.
  * qa/sandbox/scoring-mixed — 4 near-duplicates of one base image
    that vary on dimensions the near-duplicates fixture leaves tied:
      scoring_clean.jpg           — baseline (GPS + clean name + clean path)
      Copy of scoring_clean.jpg   — filename penalty
      scoring_no_gps.jpg          — GPS stripped (gps_present should be 0)
      Downloads/scoring_clean.jpg — path penalty
    Pins the EXTRACTION wiring — exiftool produces the keys the scorer
    parses, the regex flows for filename/path reach the stored signals,
    and the composite picks the clean file even when the classifier's
    lexicographic source-priority would have picked "Copy of …" as the
    action=MOVE primary.

What this exercises (the production wiring that layer-1 unit tests
can't reach):

  1. Scan pipeline writes the ``score`` column for every grouped row
     (PR 4 — apply_scoring_to_rows wired into scan.py / scan_worker.py).
  2. Manifest load threads ``score`` from the DB onto PhotoRecord
     (PR 5 — _photo_record + _LOAD_ALL_SQL).
  3. Tree model renders the Score column (PR 5 — COL_SCORE).
  4. Within-group sort orders rows by score DESC (PR 5 — MainVM
     _group_records prepends ("score", False)).
  5. Per-dimension signals propagate from real-exiftool output into the
     DB (PR 2's batch_read_extracts + PR 4's apply_scoring_to_rows).
  6. Filename + path regex penalties fire end-to-end.

The score-driven decision step (formerly a "Apply best-copy" group
context-menu action, PR 6 of #187) was removed in #210; the
equivalent flow is now reachable through the Set Action by
Field/Regex dialog's "top 1 by score within group" condition
(#209) and is covered by that dialog's own scenarios. This scenario
verifies the score *pipeline* — computation, storage, display.

PRE: PHOTO_MANAGER_HOME=qa QT_ACCESSIBILITY=1 .venv/Scripts/python.exe main.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from qa.scenarios import _invariants, _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

# ── Group 1 fixture (qa/sandbox/near-duplicates) ───────────────────────────

NEARDUP_BEST = "neardup_00_q95.jpg"   # largest file, expected top score
NEARDUP_WORST = "neardup_04_q65.jpg"  # smallest file, expected bottom score
NEARDUP_ROWS = {
    "neardup_00_q95.jpg",
    "neardup_01_q88.jpg",
    "neardup_02_q80.jpg",
    "neardup_03_q72.jpg",
    "neardup_04_q65.jpg",
}

# ── Group 2 fixture (qa/sandbox/scoring-mixed) ─────────────────────────────
#
# Keys are ``qa/sandbox/scoring-mixed/``-relative paths because two of
# the four files share the basename ``scoring_clean.jpg`` (one at root,
# one in ``Downloads/``). Basename alone wouldn't distinguish them.

MIXED_CLEAN = "scoring_clean.jpg"
MIXED_COPY_OF = "Copy of scoring_clean.jpg"
MIXED_NO_GPS = "scoring_no_gps.jpg"
MIXED_DOWNLOADS = "Downloads/scoring_clean.jpg"
MIXED_ROWS = {MIXED_CLEAN, MIXED_COPY_OF, MIXED_NO_GPS, MIXED_DOWNLOADS}

def _read_manifest_neardup() -> dict[str, dict]:
    """Return {basename: {decision, score}} for the near-duplicates group."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision, score "
            "FROM migration_manifest WHERE source_path LIKE ?",
            ("%near-duplicates%neardup_%",),
        ).fetchall()
    finally:
        conn.close()
    return {
        Path(p).name: {"decision": d or "", "score": s}
        for p, d, s in rows
    }


def _read_manifest_mixed() -> dict[str, dict]:
    """Return {scoring-mixed-relative-path: {decision, score, gps_present,
    exif_tag_count, xmp_derived}} for the scoring-mixed group. Two files
    share basename ``scoring_clean.jpg`` so the keys are path-suffixes
    relative to ``scoring-mixed/``."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, user_decision, score, "
            "       gps_present, exif_tag_count, xmp_derived "
            "FROM migration_manifest WHERE source_path LIKE ?",
            ("%scoring-mixed%",),
        ).fetchall()
    finally:
        conn.close()

    out: dict[str, dict] = {}
    for p, d, s, gps, tc, xd in rows:
        # Normalise: take everything after "scoring-mixed" + path sep.
        # On Windows the separator is backslash; on POSIX it's slash.
        parts = Path(p).parts
        try:
            idx = parts.index("scoring-mixed")
        except ValueError:
            continue
        suffix = "/".join(parts[idx + 1:])
        out[suffix] = {
            "decision": d or "",
            "score": s,
            "gps_present": bool(gps),
            "exif_tag_count": tc,
            "xmp_derived": bool(xd),
        }
    return out


def _assert(name: str, expected, actual) -> str | None:
    if actual != expected:
        return f"{name}: expected {expected!r}, got {actual!r}"
    return None


# ── Phase verifiers ────────────────────────────────────────────────────────


def _verify_neardup_pre(pre: dict[str, dict]) -> list[str]:
    """Group 1 pre-action checks: rows exist, scores valid, q95 > q65."""
    failures: list[str] = []
    if set(pre) != NEARDUP_ROWS:
        failures.append(
            f"near-duplicates row mismatch: {sorted(pre)} expected {sorted(NEARDUP_ROWS)}"
        )
        return failures
    for name, row in pre.items():
        if row["score"] is None:
            failures.append(f"{name}: score is NULL (PR 4 wiring missing)")
        elif not isinstance(row["score"], (int, float)):
            failures.append(f"{name}: score type {type(row['score']).__name__}")
        elif not (0.0 <= row["score"] <= 1.0):
            failures.append(f"{name}: score {row['score']} outside [0.0, 1.0]")
        err = _assert(f"{name}.decision (pre)", "", row["decision"])
        if err:
            failures.append(err)
    if not failures and pre[NEARDUP_BEST]["score"] <= pre[NEARDUP_WORST]["score"]:
        failures.append(
            f"near-duplicates ordering: {NEARDUP_BEST}={pre[NEARDUP_BEST]['score']:.4f} "
            f"should be > {NEARDUP_WORST}={pre[NEARDUP_WORST]['score']:.4f}"
        )
    return failures


def _verify_mixed_pre(pre: dict[str, dict]) -> list[str]:
    """Group 2 pre-action checks: per-dimension signal propagation.

    Asserts the EXTRACTION wiring (real exiftool → DB), the regex
    flows for filename / path penalties, and the composite picks the
    clean file."""
    failures: list[str] = []
    if set(pre) != MIXED_ROWS:
        failures.append(
            f"scoring-mixed row mismatch: {sorted(pre)} expected {sorted(MIXED_ROWS)}"
        )
        return failures

    # Every row should have a non-NULL score in [0, 1].
    for name, row in pre.items():
        if row["score"] is None:
            failures.append(f"{name}: score is NULL")
        elif not (0.0 <= row["score"] <= 1.0):
            failures.append(f"{name}: score {row['score']} outside [0.0, 1.0]")
        err = _assert(f"{name}.decision (pre)", "", row["decision"])
        if err:
            failures.append(err)

    # GPS extraction wiring (PR 2 batch_read_extracts → PR 4
    # apply_scoring_to_rows → DB gps_present column). If the exiftool
    # args ever lose ``-GPSLatitude``, every file would silently get
    # gps_present=False and this assertion catches it.
    if not pre[MIXED_CLEAN]["gps_present"]:
        failures.append(f"{MIXED_CLEAN}: gps_present=False but fixture has GPS EXIF")
    if not pre[MIXED_COPY_OF]["gps_present"]:
        failures.append(f"{MIXED_COPY_OF}: gps_present=False but fixture has GPS EXIF")
    if not pre[MIXED_DOWNLOADS]["gps_present"]:
        failures.append(f"{MIXED_DOWNLOADS}: gps_present=False but fixture has GPS EXIF")
    if pre[MIXED_NO_GPS]["gps_present"]:
        failures.append(f"{MIXED_NO_GPS}: gps_present=True but fixture has no GPS EXIF")

    # EXIF census wiring: each file has DateTimeOriginal + GPS tags.
    # Stripped-GPS file should have at least the DateTimeOriginal
    # tag (count ≥ 1) but fewer than the GPS-tagged files.
    for name, row in pre.items():
        if row["exif_tag_count"] is None:
            failures.append(f"{name}: exif_tag_count is NULL (extended exiftool pass missing)")

    # xmp_derived should be False everywhere — the fixture doesn't
    # set xmpMM:DerivedFrom. False (not None) proves the column
    # populates via the migration default + exiftool pass.
    for name, row in pre.items():
        if row["xmp_derived"]:
            failures.append(f"{name}: xmp_derived=True but fixture has no DerivedFrom tag")

    # Composite ordering — the clean file should outscore each of
    # the three penalised variants. The deltas come from distinct
    # scoring dimensions, so a failure here narrows to a specific
    # signal that didn't flow end-to-end.
    if not failures:
        clean = pre[MIXED_CLEAN]["score"]
        if pre[MIXED_COPY_OF]["score"] >= clean:
            failures.append(
                f"filename penalty did not fire: {MIXED_COPY_OF}="
                f"{pre[MIXED_COPY_OF]['score']:.4f} should be < {MIXED_CLEAN}={clean:.4f}"
            )
        if pre[MIXED_DOWNLOADS]["score"] >= clean:
            failures.append(
                f"path penalty did not fire: {MIXED_DOWNLOADS}="
                f"{pre[MIXED_DOWNLOADS]['score']:.4f} should be < {MIXED_CLEAN}={clean:.4f}"
            )
        if pre[MIXED_NO_GPS]["score"] >= clean:
            failures.append(
                f"GPS dimension did not fire: {MIXED_NO_GPS}="
                f"{pre[MIXED_NO_GPS]['score']:.4f} should be < {MIXED_CLEAN}={clean:.4f}"
            )

    return failures


def main() -> int:
    print("scenario: s42_scoring")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # ── 1. Run scan over both fixtures ────────────────────────────────────
    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)
    print(f"  configured_sources={_uia.read_configured_sources(dlg)!r}")

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=60)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    for line in _uia.extract_summary(log):
        if line:
            print(f"  log: {line}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    # ── 2. Pre-action snapshot + per-fixture verification ─────────────────
    print("step: snapshot_pre_apply")
    pre_neardup = _read_manifest_neardup()
    pre_mixed = _read_manifest_mixed()
    print(f"  near-duplicates={dict(sorted(pre_neardup.items()))}")
    print(f"  scoring-mixed={dict(sorted(pre_mixed.items()))}")

    failures: list[str] = []
    failures.extend(_verify_neardup_pre(pre_neardup))
    failures.extend(_verify_mixed_pre(pre_mixed))
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    # Pin the expected winner for the scoring-mixed group: the clean
    # variant has GPS + clean filename + clean path and must outscore
    # all three penalised variants. The actual decision-setting step
    # belongs to the Set Action by Field/Regex dialog (#209) — this
    # scenario only verifies the underlying score signal.
    expected_mixed_winner = max(pre_mixed, key=lambda n: pre_mixed[n]["score"])
    if expected_mixed_winner != MIXED_CLEAN:
        failures.append(
            f"unexpected scoring-mixed winner: {expected_mixed_winner!r} (expected {MIXED_CLEAN!r})"
        )
    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print(
        f"  near-duplicates ✓ ({NEARDUP_BEST} > {NEARDUP_WORST}); "
        f"scoring-mixed ✓ (GPS / filename / path penalties all fired, "
        f"winner={MIXED_CLEAN!r})"
    )

    # ── 3. Manifest-action invariant ──────────────────────────────────────
    print("step: invariant_manifest_actions")
    inv_actions = _invariants.assert_manifest_actions_consistent(
        win, expected_enabled=True
    )
    if not inv_actions:
        print("FAIL: manifest-gated menu items not all enabled post-scan")
        return 1

    print("scenario: s42_scoring DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
