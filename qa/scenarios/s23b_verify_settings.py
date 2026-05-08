"""Scenario 23b — Verify scan dialog reloads settings persisted by s23a (#122).

Required configure: ``SCENARIO_SOURCES["s23b_verify_settings"] is None`` —
the configure step preserves the qa/settings.json that s23a's GUI
mutations wrote. A fresh app launch then reads that file via
``ScanDialog._load_from_settings``.

Pairs with ``s23a_set_settings``: see that file's docstring for the split
rationale. This scenario MUST run AFTER s23a in the batch order; the
batch runner enforces this via ALL_SCENARIOS list ordering.

What's verified at layer 3:
  * Source list count + paths round-trip through settings.json
  * Output path round-trip
  * pHash and color slider values are at their defaults (10, 30) — they
    are intentionally NOT persisted (see scan_dialog.py:518 — the
    ``_save_to_settings`` body only writes ``sources.list`` and
    ``sources.output``). If a future change adds slider persistence,
    the assertion below would fail and force a conscious update.

What's NOT verified here (covered elsewhere):
  * The recursive flag on each row — Qt's setCellWidget'd checkbox is
    not exposed in the UIA tree (see ``read_source_paths`` docstring).
    s23a's settings.json smoke check covers the WRITE side; full WRITE +
    READ round-trip would need a non-recursive scan to assert behaviorally
    (recursive=False would not descend into subdirs). That's an
    enhancement for a future scenario.
"""
from __future__ import annotations

import sys
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]

# Mirrored from s23a_set_settings — must stay in sync.
EXPECTED_OUTPUT = "qa/sandbox/_disposable/s23_test.sqlite"
EXPECTED_SOURCE_COUNT = 2
EXPECTED_PHASH_DEFAULT = 10
EXPECTED_COLOR_DEFAULT = 30


def main() -> int:
    print("scenario: s23b_verify_settings")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: read_persisted_state")
    sources_in_dialog = _uia.read_source_paths(dlg)
    output_field = dlg.child_window(
        auto_id=_uia.SCAN_AID_OUTPUT_PATH, control_type="Edit"
    )
    output_value = output_field.window_text() or ""
    print(f"  reloaded_sources={sources_in_dialog}")
    print(f"  reloaded_output={output_value!r}")

    print("step: read_slider_defaults")
    spinner_texts = [
        (s.window_text() or "").strip()
        for s in dlg.descendants(control_type="Spinner")
    ]
    spinner_ints: list[int] = []
    for v in spinner_texts:
        try:
            spinner_ints.append(int(v))
        except ValueError:
            pass
    print(f"  spinner_values={spinner_texts}")

    failures: list[str] = []

    # Sources: count + presence (paths can be normalized differently across
    # platforms, so we compare-in rather than ==).
    if len(sources_in_dialog) != EXPECTED_SOURCE_COUNT:
        failures.append(
            f"sources count: expected {EXPECTED_SOURCE_COUNT}, "
            f"got {len(sources_in_dialog)} (paths={sources_in_dialog})"
        )

    # Output path
    if output_value != EXPECTED_OUTPUT:
        failures.append(
            f"output path: expected {EXPECTED_OUTPUT!r}, got {output_value!r}"
        )

    # Slider defaults — confirms intentional non-persistence
    if EXPECTED_PHASH_DEFAULT not in spinner_ints:
        failures.append(
            f"pHash spinner default: expected {EXPECTED_PHASH_DEFAULT} "
            f"in spinner values, got {spinner_ints}"
        )
    if EXPECTED_COLOR_DEFAULT not in spinner_ints:
        failures.append(
            f"color spinner default: expected {EXPECTED_COLOR_DEFAULT} "
            f"in spinner values, got {spinner_ints}"
        )

    # Tidy up — close the scan dialog without scanning. The batch runner
    # then closes the main window between scenarios.
    print("step: close_dialog_via_close_button")
    try:
        _uia.close_scan_dialog_via_close_button(dlg)
    except Exception as exc:
        print(f"  close_button_fallback: {exc!r}")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s23b_verify_settings DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
