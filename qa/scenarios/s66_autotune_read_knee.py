"""Scenario 66 — read-knee autotune opt-in, end-to-end (#551 Phase 3).

Required source: ``qa/sandbox/near-duplicates`` (5 JPEGs
``neardup_00_q95.jpg`` … ``neardup_04_q65.jpg`` — the same image at
varying JPEG quality, which the classifier groups into ONE near-dup
group of five).

What this exercises (the production wiring layer-1 unit tests can't reach):

  1. The "Auto-tune reader concurrency" checkbox in ScanDialog's Advanced
     Settings is wired to ScanWorker.autotune_read_knee (the #551 Phase-2
     in-pipeline read-knee ramp).
  2. Turning it ON and running a real scan **does not change the grouping** —
     the five near-duplicates still collapse into exactly one non-null
     ``group_id``, identical to the autotune-OFF result every other
     scan scenario (s01, s42, s49, …) produces.

Why a single run (not an OFF-vs-ON two-run): the read-knee determinism
property — reader concurrency never affects ``group_id`` — is *structural*
(idx is threaded through ``read_for_record`` → ``compute_from_bytes`` →
``hash_results[idx]`` → ``classify``; #526/#538 lex-min) and is pinned at
layer 1 by ``tests/test_scan_worker.py`` (idx-order under gating) and
``tests/test_autotune.py`` (completion-order invariance). On a qa-sized
fixture (5 files ≪ the 256-file ramp gate) the ramp also *falls open to
static*, so an OFF-vs-ON pair would compare two identical static paths.
This scenario's job is the integration the units can't reach: the UI toggle
→ setting → worker → real scan → manifest path, asserting autotune-ON still
yields the known-correct grouping. The actual ramp (NAS knee≈2 / HDD knee=1)
is verified on real hardware by the #551 dev-rig manual checkpoint.

PRE: PHOTO_MANAGER_HOME=qa QT_ACCESSIBILITY=1 .venv/Scripts/python.exe main.py
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

# UIA accessible names — must match translations/en.yml.
AUTOTUNE_CHECKBOX_TITLE = "Auto-tune reader concurrency (experimental)"
ADVANCED_GROUP_TITLE = "Advanced settings"


def _read_group_ids() -> dict[str, object]:
    """Return ``{basename: group_id}`` for the near-duplicates rows."""
    if not MANIFEST_PATH.exists():
        raise RuntimeError(f"manifest not found at {MANIFEST_PATH}")
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, group_id FROM migration_manifest "
            "WHERE source_path LIKE ?",
            ("%near-duplicates%neardup_%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: gid for p, gid in rows}


def _click_advanced_group(dlg) -> None:
    """Expand the Advanced Settings collapsible groupbox (mirror s49)."""
    grp = dlg.child_window(title=ADVANCED_GROUP_TITLE, control_type="CheckBox")
    grp.toggle()
    time.sleep(0.2)


def _toggle_autotune_checkbox(dlg) -> None:
    cb = dlg.child_window(title=AUTOTUNE_CHECKBOX_TITLE, control_type="CheckBox")
    cb.toggle()
    time.sleep(0.2)


def _is_autotune_checked(dlg) -> bool:
    cb = dlg.child_window(title=AUTOTUNE_CHECKBOX_TITLE, control_type="CheckBox")
    # get_toggle_state: 0 off / 1 on / 2 indeterminate.
    return cb.get_toggle_state() == 1


def main() -> int:
    print("scenario: s66_autotune_read_knee")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    failures: list[str] = []

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: expand_advanced_settings")
    _click_advanced_group(dlg)

    print("step: assert_checkbox_default_off")
    if _is_autotune_checked(dlg):
        failures.append("autotune checkbox starts ON; default must be OFF")
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("step: toggle_autotune_on")
    _toggle_autotune_checkbox(dlg)
    if not _is_autotune_checked(dlg):
        failures.append("toggling did not turn the autotune checkbox on")
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=60)
    print(f"  scan_elapsed_s={elapsed:.2f}")
    if "Done." not in log:
        failures.append("scan did not reach 'Done.' with autotune ON")

    # finished signal (renames the button) is queued on the main thread —
    # tiny pause so close_and_load's button lookup doesn't race it (s49 note).
    time.sleep(1.0)

    print("step: close_and_load")
    _uia.close_and_load_manifest(dlg)

    print("step: verify_grouping")
    group_ids = _read_group_ids()
    print(f"  group_ids={dict(sorted(group_ids.items()))}")

    if len(group_ids) != 5:
        failures.append(
            f"expected 5 near-duplicate rows in the manifest; got "
            f"{len(group_ids)} ({sorted(group_ids)})"
        )
    distinct = {gid for gid in group_ids.values()}
    grouped_correctly = (
        len(group_ids) == 5
        and len(distinct) == 1
        and None not in distinct
    )
    # Soft-probe for the LLM agent: the load-bearing determinism signal.
    print(f"probe_status: autotune_determinism grouped_correctly={grouped_correctly}")
    if not grouped_correctly:
        failures.append(
            f"autotune-ON broke grouping: the 5 near-duplicates must share "
            f"exactly one non-null group_id (the autotune-OFF result); got "
            f"distinct group_ids={distinct!r}. Reader concurrency must never "
            f"change group_id (#551 Q5 / #526/#538 idx-passthrough)."
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s66_autotune_read_knee DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
