"""Scenario 60 — Execute Action filter by action type (#502).

Required source: qa/sandbox/_disposable/s60_source/ (regenerated each run by
the driver — 8 fresh JPEGs in two visual clusters of 4 each).

Drives the in-dialog type-filter combo end-to-end:
  regen disposable fixture (2 distinct visual seeds × 4 quality variants) →
  scan → close & load →
  mark Group A files (filename ^s60_groupA_) decision=delete via
    "Action → Set Action by Field…" regex →
  mark Group B files (filename ^s60_groupB_) decision=remove_from_list
    via the same standalone regex flow →
  open "Action → Execute Action…" →
  assert the new type-filter combo lists exactly 3 options →
  switch combo to "Delete only" → assert tree shows 4 visible file rows
    (group A only) →
  switch combo to "Remove from list only" → assert tree shows 4 visible
    rows (group B) AND the warning banner is visible with the
    hidden-destructive line (4 pending deletes are now hidden) →
  switch combo back to "Delete only" → click Execute → dismiss the
    complete-group confirm (group A is fully delete-decided in the
    visible scope) →
  verify (a) all 4 group A files no longer exist on disk,
         (b) all 4 group B files still exist on disk,
         (c) manifest rows for group B retain decision='remove_from_list'
             with executed=0.

⚠ HEADS-UP: every run sends 4 files to the operator's real Windows
recycle bin (group A). The fixture is regenerated next run, so the bin
grows by 4 each run until manually emptied. Same destructive-scenario
contract as s13 / s44.

Catches drift in:
  - The type-filter combo's existence and the three documented options
    ("All decisions", "Delete only", "Remove from list only").
  - The "visible = committed" contract: Execute on "Delete only" must
    leave the Remove-decision rows untouched (decision intact,
    executed=0).
  - The hidden-destructive banner line: filter ≠ Delete only AND
    pending delete decisions exist → banner visible.
  - The lock-confirm ordering fix from #502 risk #1 (covered at
    layer 1 in TestExecuteDialogTypeFilter::
    test_lock_confirm_does_not_fire_under_remove_only_filter — this
    scenario doesn't exercise locked rows but does prove the
    intersection plumbing under real Qt + UIA threading).
"""
from __future__ import annotations

import io
import sqlite3
import sys
import time
from pathlib import Path

import imagehash
import numpy as np
from PIL import Image

from qa.scenarios import _uia

REPO = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO / "qa" / "sandbox" / "_disposable" / "s60_source"
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

# Two visual seeds, each saved at 4 qualities → 8 files total / 2 groups.
QUALITIES = [95, 88, 80, 72]

# Same clustering pre-flight as s44 — reduces per-run flake probability
# of the scanner splitting a near-dup cluster into multiple groups.
_SCANNER_THRESHOLD = 10
_REGEN_MAX_ATTEMPTS = 5

# Filter combo objectName (set in execute_action_dialog.py:_build_ui).
# UIA exposes Qt objectName as auto_id, so we can find the combo
# locale-independently — the combo's visible text is localised but
# this identifier isn't.
FILTER_COMBO_AID = "executeDialogTypeFilterCombo"

# Combo item labels — English (the qa-batch CI environment).
FILTER_ALL = "All decisions"
FILTER_DELETE_ONLY = "Delete only"
FILTER_REMOVE_ONLY = "Remove from list only"


def _build_seed(rng: np.random.Generator) -> Image.Image:
    """Reuse s44's gradient-base recipe (per-seed offset + sinusoidal
    perturbation) so two distinct seeds produce two visually distinct
    images that each cluster INTERNALLY at all sampled qualities."""
    base_color = rng.integers(0, 256, size=(3,))
    fx = float(rng.uniform(0.5, 4.0))
    fy = float(rng.uniform(0.5, 4.0))
    h, w = 480, 640
    yy, xx = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    arr = np.zeros((h, w, 3), dtype=np.float32)
    for c in range(3):
        arr[..., c] = (
            base_color[c]
            + 60 * np.sin(2 * np.pi * fx * xx / w + c)
            + 60 * np.cos(2 * np.pi * fy * yy / h + c * 0.7)
        )
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _max_pairwise_phash(base: Image.Image, qualities: list[int]) -> int:
    saved: list[Image.Image] = []
    for q in qualities:
        buf = io.BytesIO()
        base.save(buf, "JPEG", quality=q)
        buf.seek(0)
        saved.append(Image.open(buf).copy())
    hashes = [imagehash.phash(im) for im in saved]
    return max(
        hashes[i] - hashes[j]
        for i in range(len(hashes))
        for j in range(i + 1, len(hashes))
    )


def _try_build_clustering_seed() -> Image.Image:
    """Generate a seed whose all-quality re-saves cluster as one
    REVIEW_DUPLICATE group. Mirrors s44's retry contract."""
    last_worst: int | None = None
    for _ in range(_REGEN_MAX_ATTEMPTS):
        candidate = _build_seed(np.random.default_rng())
        worst = _max_pairwise_phash(candidate, QUALITIES)
        if worst <= _SCANNER_THRESHOLD:
            return candidate
        last_worst = worst
    raise RuntimeError(
        f"Could not generate near-duplicate seed that clusters "
        f"after {_REGEN_MAX_ATTEMPTS} attempts; last worst pairwise "
        f"pHash distance was {last_worst}."
    )


def _regen_fixture() -> tuple[list[Path], list[Path]]:
    """Wipe FIXTURE_DIR and write 8 near-duplicate JPEGs across two
    distinct visual seeds. Returns ``(group_a_paths, group_b_paths)``
    — 4 paths each, named ``s60_groupA_NN_qXX.jpg`` /
    ``s60_groupB_NN_qXX.jpg`` so the regex partition is unambiguous.
    """
    if FIXTURE_DIR.exists():
        for f in FIXTURE_DIR.iterdir():
            if f.is_file():
                f.unlink()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    seed_a = _try_build_clustering_seed()
    seed_b = _try_build_clustering_seed()

    group_a_paths: list[Path] = []
    group_b_paths: list[Path] = []
    for i, q in enumerate(QUALITIES):
        # Distinct EXIF dates per group so the scanner doesn't collapse
        # them by capture-date heuristic.
        exif_a = seed_a.getexif()
        exif_a[36867] = f"2024:05:01 1{i}:00:00"
        path_a = FIXTURE_DIR / f"s60_groupA_{i:02d}_q{q}.jpg"
        seed_a.save(str(path_a), "JPEG", quality=q, exif=exif_a.tobytes())
        group_a_paths.append(path_a)

        exif_b = seed_b.getexif()
        exif_b[36867] = f"2024:06:01 1{i}:00:00"
        path_b = FIXTURE_DIR / f"s60_groupB_{i:02d}_q{q}.jpg"
        seed_b.save(str(path_b), "JPEG", quality=q, exif=exif_b.tobytes())
        group_b_paths.append(path_b)

    return group_a_paths, group_b_paths


def _file_row_count_in_tree(tree) -> int:
    """Return the number of file (non-header) rows currently rendered
    in the given QTreeView (passed as the UIA wrapper).

    File rows have a valid parent index (sit under a group header);
    group header rows are at the root level. The Tree exposes both
    as TreeItem under UIA, so we discriminate by checking whether the
    leftmost cell text starts with "Group ".
    """
    count = 0
    seen_rows: set[int] = set()
    for it in tree.descendants(control_type="TreeItem"):
        try:
            r = it.rectangle()
        except Exception:
            continue
        if r.top in seen_rows:
            continue
        # Take the leftmost cell at this y-position to classify.
        text = (it.window_text() or "").strip()
        seen_rows.add(r.top)
        if text.lower().startswith("group "):
            continue
        count += 1
    return count


def _read_manifest_rows() -> list[tuple[str, str, int]]:
    """Return ``(source_path, user_decision, executed)`` for every
    fixture row currently in the manifest."""
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, COALESCE(user_decision, ''), executed "
            "FROM migration_manifest WHERE source_path LIKE ?",
            (f"%{FIXTURE_DIR.name}%",),
        ).fetchall()
    finally:
        conn.close()
    return [(p, d, e) for p, d, e in rows]


def main() -> int:
    print("scenario: s60_execute_filter_by_action_type")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    print("step: regen_fixture")
    group_a_paths, group_b_paths = _regen_fixture()
    print(f"  fixture_dir={FIXTURE_DIR}")
    print(f"  group_a_count={len(group_a_paths)} group_b_count={len(group_b_paths)}")

    print("step: open_scan_dialog")
    dlg, _ = _uia.open_scan_dialog(win)

    print("step: run_scan")
    log, elapsed = _uia.run_scan_and_wait(dlg, timeout=30)
    print(f"  scan_elapsed_s={elapsed:.2f}")

    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    print("step: mark_group_a_as_delete")
    # Anchored regex matches the 4 group A files only.
    counter_a = _uia.mark_all_via_regex_standalone(
        win, field="File Name", regex="^s60_groupA_", action_label="delete",
    )
    print(f"  group_a_match_counter={counter_a!r}")
    _, win = _uia.connect_main()
    time.sleep(0.3)

    print("step: mark_group_b_as_remove_from_list")
    counter_b = _uia.mark_all_via_regex_standalone(
        win, field="File Name", regex="^s60_groupB_",
        action_label="remove from list",
    )
    print(f"  group_b_match_counter={counter_b!r}")
    _, win = _uia.connect_main()
    time.sleep(0.3)

    print("step: open_execute_action_dialog")
    exec_dlg, exec_hwnd = _uia.open_execute_action_dialog(win)
    _uia._focus(exec_dlg)
    time.sleep(0.3)

    print("step: locate_filter_combo")
    # Qt's UIA bridge exposes objectName as the trailing path segment of
    # the auto_id — full id reads like
    # "QApplication.ExecuteActionDialog.QWidget.executeDialogTypeFilterCombo".
    # Use suffix-match (same idiom as ActionDialog combos at
    # _uia.py:1796/1815), NOT child_window(auto_id=exact-match).
    combo = _uia._find_descendant_by_aid_suffix(
        exec_dlg, "ComboBox", "." + FILTER_COMBO_AID,
    )
    if combo is None:
        print(
            f"FAIL: type-filter ComboBox with objectName "
            f"{FILTER_COMBO_AID!r} not found via aid-suffix descendant "
            f"search — combo missing from _build_ui?"
        )
        return 1

    return _continue_main(
        pid, win, exec_dlg, combo, group_a_paths, group_b_paths,
    )


def _combo_current_text(combo) -> str:
    """Return the QComboBox's current selection text via the UIA
    ValuePattern.

    Qt's QComboBox does NOT surface its current text through the UIA
    Name property — ``combo.window_text()`` returns ``''`` for it
    (confirmed empirically on this widget; that wrong read was the
    original s60 CI failure). The current selection IS exposed through
    the ValuePattern's ``CurrentValue``, which mirrors Qt's
    ``currentText()``. Use this for any "did the combo change?" check.
    """
    try:
        return (combo.iface_value.CurrentValue or "").strip()
    except Exception:
        return ""


def _select_combo_robust(combo, label: str) -> bool:
    """Set ``combo`` to ``label`` and confirm via the ValuePattern.

    ``combo.select(text)`` drives Qt's QComboBox correctly —
    SelectionItemPattern.Select on the popup item fires Qt's
    ``currentIndexChanged``, which is what triggers
    ``_on_type_filter_changed`` and re-filters the tree. Verification
    reads ``iface_value.CurrentValue`` (Qt's ``currentText()``), NOT
    ``window_text()`` — the latter is empty for a QComboBox and was the
    root cause of the original s60 CI failure (3× "combo state stuck"
    while the select had in fact worked on the first attempt).

    Retried up to 3× for the hosted-CI combo flake documented at
    _uia.py:1820 (s29 burned twice on a non-default item with a single
    un-retried select). Returns ``True`` once the current value matches
    ``label``."""
    for _ in range(3):
        try:
            combo.set_focus()
        except Exception:
            pass
        time.sleep(0.1)
        try:
            combo.select(label)
        except Exception:
            pass
        time.sleep(0.4)
        if _combo_current_text(combo) == label:
            return True
    return False


def _continue_main(
    pid, win, exec_dlg, combo, group_a_paths, group_b_paths,
) -> int:
    """Continuation point after the combo is located. Kept separate so
    the lookup-failure return path above stays a flat early-exit
    instead of an over-indented try/else."""

    print("step: verify_combo_has_three_options")
    items = _uia.read_combobox_items(combo)
    print(f"  combo_items={items!r}")
    expected = {FILTER_ALL, FILTER_DELETE_ONLY, FILTER_REMOVE_ONLY}
    if set(items) != expected:
        print(
            f"FAIL: combo items {set(items)!r} did not match expected "
            f"{expected!r} — translation key drift or extra/missing options"
        )
        return 1

    print("step: switch_to_delete_only")
    if not _select_combo_robust(combo, FILTER_DELETE_ONLY):
        print(
            f"FAIL: could not switch combo to {FILTER_DELETE_ONLY!r} after "
            f"3 retries + iface_value fallback; combo state stuck"
        )
        return 1
    time.sleep(0.4)
    tree = exec_dlg.descendants(control_type="Tree")[0]
    delete_visible = _file_row_count_in_tree(tree)
    print(f"  delete_only_visible_rows={delete_visible}")
    if delete_visible != len(group_a_paths):
        print(
            f"FAIL: expected {len(group_a_paths)} visible delete rows "
            f"under Delete-only filter, got {delete_visible}"
        )
        return 1

    print("step: switch_to_remove_only")
    if not _select_combo_robust(combo, FILTER_REMOVE_ONLY):
        print(
            f"FAIL: could not switch combo to {FILTER_REMOVE_ONLY!r} after "
            f"3 retries + iface_value fallback; combo state stuck"
        )
        return 1
    time.sleep(0.4)
    remove_visible = _file_row_count_in_tree(tree)
    print(f"  remove_only_visible_rows={remove_visible}")
    if remove_visible != len(group_b_paths):
        print(
            f"FAIL: expected {len(group_b_paths)} visible remove rows "
            f"under Remove-only filter, got {remove_visible}"
        )
        return 1

    print("step: verify_hidden_destructive_banner_visible")
    # Under Remove-only the 4 group A delete decisions are hidden,
    # which must surface as the hidden-destructive line in the banner.
    # We can't read the banner text reliably across locales without an
    # auto_id on the label, but the count appears in the text — assert
    # via the QFrame's visibility through UIA.
    # The banner is the QFrame whose first child is the warning_label;
    # we walk descendants looking for a static text that contains both
    # "4" and either "hidden" (en) or "隱藏" (zh_TW).
    banner_hit = False
    for w in exec_dlg.descendants():
        try:
            text = (w.window_text() or "").strip()
        except Exception:
            continue
        if "4" in text and (
            "hidden" in text.lower() or "隱藏" in text
        ):
            banner_hit = True
            break
    if not banner_hit:
        print(
            "FAIL: hidden-destructive banner line not detected under "
            "Remove-only filter — expected mention of 4 hidden pending "
            "delete row(s); banner missing the new #502 second line"
        )
        return 1
    print("  hidden_destructive_banner_visible=True")

    print("step: switch_back_to_delete_only_for_execute")
    if not _select_combo_robust(combo, FILTER_DELETE_ONLY):
        print(
            f"FAIL: could not switch combo back to {FILTER_DELETE_ONLY!r} "
            f"before Execute; combo state stuck"
        )
        return 1
    time.sleep(0.4)

    print("step: snapshot_pre_disk_state")
    pre_a = {p: p.exists() for p in group_a_paths}
    pre_b = {p: p.exists() for p in group_b_paths}
    print(f"  pre_present_group_a={sum(pre_a.values())}")
    print(f"  pre_present_group_b={sum(pre_b.values())}")

    print("step: click_execute_under_delete_filter")
    # Group A is fully delete-decided within the visible-after-filter
    # scope, so the complete-group confirm WILL fire. Drive it with Yes
    # via the standard helper.
    _uia.execute_and_confirm(exec_dlg, dialog_timeout=8)

    print("step: verify_disk_state")
    removed_a = [p for p in group_a_paths if not p.exists()]
    remaining_a = [p for p in group_a_paths if p.exists()]
    print(f"  group_a_removed={len(removed_a)} group_a_remaining={len(remaining_a)}")
    if len(removed_a) != len(group_a_paths):
        print(
            f"FAIL: expected all {len(group_a_paths)} group A files removed "
            f"(decision=delete, filter=Delete only), got "
            f"{len(removed_a)} removed / {len(remaining_a)} remaining: "
            f"remaining={[p.name for p in remaining_a]}"
        )
        return 1

    surviving_b = [p for p in group_b_paths if p.exists()]
    missing_b = [p for p in group_b_paths if not p.exists()]
    print(f"  group_b_surviving={len(surviving_b)} group_b_missing={len(missing_b)}")
    if len(surviving_b) != len(group_b_paths):
        print(
            f"FAIL: expected all {len(group_b_paths)} group B files to "
            f"survive (decision=remove_from_list NOT in Delete-only "
            f"filter scope), got {len(missing_b)} unexpectedly removed: "
            f"removed={[p.name for p in missing_b]} — the filter "
            f"intersection failed and Execute committed too much"
        )
        return 1

    print("step: verify_manifest_state")
    rows = _read_manifest_rows()
    by_name = {Path(src).name: (decision, executed) for src, decision, executed in rows}
    print(f"  manifest_row_count={len(rows)}")

    failures: list[str] = []
    for p in group_a_paths:
        decision, executed = by_name.get(p.name, ("?", -1))
        # The delete-then-mark_executed flow leaves user_decision==''
        # (the executor clears it after a successful op so the row no
        # longer carries a pending state). executed==1 is the load-bearing
        # signal.
        if executed != 1:
            failures.append(
                f"group A file {p.name} should have executed=1 after "
                f"delete commit, got decision={decision!r} executed={executed}"
            )

    for p in group_b_paths:
        decision, executed = by_name.get(p.name, ("?", -1))
        if decision != "remove_from_list":
            failures.append(
                f"group B file {p.name} should retain "
                f"decision='remove_from_list' (filter excluded it from "
                f"execute scope), got decision={decision!r}"
            )
        if executed != 0:
            failures.append(
                f"group B file {p.name} should have executed=0 (not "
                f"committed under Delete-only filter), got executed={executed}"
            )

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1

    print("scenario: s60_execute_filter_by_action_type DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
