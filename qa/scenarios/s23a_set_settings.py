"""Scenario 23a — Set scan dialog settings via GUI, persist to qa/settings.json (#122).

Required source: qa/sandbox/unique (initial 1-source list — driver will mutate
to 2 sources via the dialog).

Pairs with ``s23b_verify_settings``: s23a opens the scan dialog, mutates the
source list / output path / row recursive flag, triggers a scan to fire
``ScanDialog._save_to_settings``, then exits. s23b launches a fresh app
(``SCENARIO_SOURCES[s23b] is None`` so configure preserves what s23a wrote)
and reads back the dialog state to verify the round-trip.

Why split: ``ScanDialog._save_to_settings`` is only called from
``_start_scan`` (verified at app/views/dialogs/scan_dialog.py:581). Closing
the dialog without scanning does NOT persist. We trigger a scan as the
canonical save path; the scan's side effect (a manifest at
qa/sandbox/_disposable/s23_test.sqlite) is acceptable noise.

Why two scenarios in the batch instead of one driver with internal restart:
the batch runner already restarts the app between scenarios. Splitting
into s23a/s23b makes the cross-launch boundary an explicit batch step,
which is also what would happen if a developer ran each driver standalone
in sequence.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]

# Mutations applied by this scenario. s23b reads these back from the
# fresh-launch dialog and asserts they round-tripped through
# qa/settings.json correctly.
SECOND_SOURCE_REL = "qa/sandbox/near-duplicates"
TARGET_OUTPUT = "qa/sandbox/_disposable/s23_test.sqlite"


def main() -> int:
    print("scenario: s23a_set_settings")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)
    initial = _uia.read_source_paths(dlg)
    print(f"  initial_sources={initial}")
    if len(initial) != 1:
        print(f"FAIL: expected 1 initial source from configure, got {len(initial)}")
        return 1

    # ── Mutation 1: add a second source via path-field ──────────────────
    print("step: add_second_source")
    second_abs = str((REPO / SECOND_SOURCE_REL).resolve())
    print(f"  adding={second_abs!r}")
    _uia.add_source_via_path_field(dlg, second_abs)
    after_add = _uia.read_source_paths(dlg)
    print(f"  sources_after_add={after_add}")
    if len(after_add) != 2:
        print(f"FAIL: expected 2 sources after add, got {len(after_add)}")
        return 1

    # ── Mutation 2: toggle Recursive on display row 0 (default True → False) ─
    # Since #213 the source list displays sorted by path, so display row 0
    # is the alphabetically-first basename. Here the dialog holds two
    # entries (`unique` pre-seeded + `near-duplicates` added) — display
    # row 0 = `near-duplicates`. The settings.list assertion below looks
    # up by basename rather than index so it stays correct regardless of
    # how the alphabetic sort interacts with insertion order.
    print("step: toggle_recursive_row_0")
    _uia.toggle_source_row_recursive(dlg, 0)

    # ── Mutation 3: change output path ──────────────────────────────────
    print("step: set_output_path")
    output_field = dlg.child_window(
        auto_id=_uia.SCAN_AID_OUTPUT_PATH, control_type="Edit"
    )
    output_field.iface_value.SetValue(TARGET_OUTPUT)
    rendered = output_field.window_text()
    print(f"  output_field_now={rendered!r}")
    if rendered != TARGET_OUTPUT:
        print(f"FAIL: expected output={TARGET_OUTPUT!r}, got {rendered!r}")
        return 1

    # ── Persist via Start Scan (the only path that calls _save_to_settings) ─
    print("step: trigger_save_via_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    # ── Close & Load to leave the app in a tidy state ───────────────────
    print("step: close_and_load")
    _uia.close_and_load_manifest(dlg)

    # ── Smoke-check: settings.json on disk reflects the mutations ───────
    # This is the layer-3 confirmation that _save_to_settings did its job
    # before the scan worker started. s23b's value-add is verifying the
    # LOAD path — that the next launch reads these values back correctly.
    print("step: verify_settings_json_on_disk")
    settings_path = REPO / "qa" / "settings.json"
    saved = json.loads(settings_path.read_text(encoding="utf-8"))
    saved_sources = saved.get("sources", {})
    saved_list = saved_sources.get("list", [])
    saved_output = saved_sources.get("output")
    print(f"  on_disk_sources={saved_list}")
    print(f"  on_disk_output={saved_output!r}")

    failures: list[str] = []
    if len(saved_list) != 2:
        failures.append(
            f"settings.json has {len(saved_list)} sources, expected 2"
        )
    if saved_output != TARGET_OUTPUT:
        failures.append(
            f"settings.json output={saved_output!r}, expected {TARGET_OUTPUT!r}"
        )
    # Recursive-flag round-trip: assert by path, not by settings.list
    # index. Since #213 the dialog's display is sorted by path
    # (case-insensitive), so display row 0 = alphabetically-first source
    # — here that's `near-duplicates`, not the pre-seeded `unique`. The
    # underlying settings.list stays insertion-ordered, so an index-based
    # assertion would flip when display sort and insertion order
    # disagree. We care about the *operation* (toggle worked, default
    # held), not which slot the entry occupies.
    by_basename = {Path(item["path"]).name: item for item in saved_list}
    toggled = by_basename.get("near-duplicates")  # display row 0 after sort
    untoggled = by_basename.get("unique")          # not touched in this run
    if not toggled:
        failures.append(
            "near-duplicates entry missing from settings.json"
        )
    elif toggled.get("recursive") is not False:
        failures.append(
            f"near-duplicates recursive={toggled.get('recursive')!r}, "
            f"expected False (we toggled display row 0)"
        )
    if not untoggled:
        failures.append(
            "unique entry missing from settings.json"
        )
    elif untoggled.get("recursive") is not True:
        failures.append(
            f"unique recursive={untoggled.get('recursive')!r}, "
            f"expected True (default for new sources, we did not toggle it)"
        )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s23a_set_settings DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
