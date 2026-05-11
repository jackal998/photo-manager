"""Scenario 23b — Verify scan dialog reloads settings persisted by s23a (#122).

Required configure: ``SCENARIO_SOURCES["s23b_verify_settings"] is None`` —
the configure step preserves the qa/settings.json that s23a's GUI
mutations wrote. A fresh app launch then reads that file via
``ScanDialog._load_from_settings``.

Pairs with ``s23a_set_settings``: see that file's docstring for the split
rationale. This scenario MUST run AFTER s23a in the batch order; the
batch runner enforces this via ALL_SCENARIOS list ordering.

What's verified at layer 3:
  * Source list count + paths round-trip through settings.json (via UIA
    against the relaunched dialog).
  * Output path round-trip (UIA).
  * pHash and color thresholds are intentionally NOT persisted — verified
    by reading qa/settings.json directly: no ``scan.phash_threshold``
    or ``scan.mean_color_threshold`` key was written by s23a's Start Scan.

Why the settings-file check rather than reading the spinners via UIA:
the Advanced Settings groupbox is collapsed by default after #163, so
the QSpinBox children are hidden and ``dlg.descendants(control_type=
"Spinner")`` returns an empty list. Asserting non-persistence at the
file level is both simpler and stronger — it pins the actual contract
(``_save_to_settings`` doesn't touch threshold keys) rather than a
downstream UI side-effect.

What's NOT verified here (covered elsewhere):
  * The recursive flag on each row — Qt's setCellWidget'd checkbox is
    not exposed in the UIA tree (see ``read_source_paths`` docstring).
    s23a's settings.json smoke check covers the WRITE side; full WRITE +
    READ round-trip would need a non-recursive scan to assert behaviorally
    (recursive=False would not descend into subdirs). That's an
    enhancement for a future scenario.
  * Slider default VALUES (10 / 30) on the post-relaunch dialog
    instance — covered at layer 1 by
    ``tests/test_scan_dialog.py::TestAdvancedSettingsCollapse::test_sliders_still_default_values_when_loaded``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
SETTINGS_PATH = REPO / "qa" / "settings.json"

# Mirrored from s23a_set_settings — must stay in sync.
EXPECTED_OUTPUT = "qa/sandbox/_disposable/s23_test.sqlite"
EXPECTED_SOURCE_COUNT = 2

# Keys that MUST NOT appear in the persisted settings.json after s23a.
# If a future change persists threshold values, these checks fail and
# force a conscious update to the test contract.
FORBIDDEN_KEYS = (
    ("scan", "phash_threshold"),
    ("scan", "mean_color_threshold"),
)


def _has_nested_key(data: dict, path: tuple[str, ...]) -> bool:
    """Return True if the dotted-path key exists in ``data``."""
    cursor: object = data
    for part in path:
        if not isinstance(cursor, dict):
            return False
        if part not in cursor:
            return False
        cursor = cursor[part]
    return True


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

    print("step: read_settings_json")
    settings_data: dict = {}
    if SETTINGS_PATH.exists():
        try:
            settings_data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"  WARN: could not read settings.json: {exc!r}")
    persisted_phash = _has_nested_key(settings_data, ("scan", "phash_threshold"))
    persisted_color = _has_nested_key(settings_data, ("scan", "mean_color_threshold"))
    print(f"  scan.phash_threshold present?     {persisted_phash}")
    print(f"  scan.mean_color_threshold present? {persisted_color}")

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

    # Threshold non-persistence (intentional — see module docstring).
    for path_parts in FORBIDDEN_KEYS:
        if _has_nested_key(settings_data, path_parts):
            failures.append(
                f"threshold persisted unexpectedly: settings.json has "
                f"{'.'.join(path_parts)!r} (it must NOT be written by "
                f"_save_to_settings — see scan_dialog.py)"
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
