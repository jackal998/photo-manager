"""Scenario 61 — SingletonPruneConfirmDialog actioned-singleton flow (#484).

Required source: qa/sandbox/_disposable/s61_source/ — regenerated each run.
Two independent near-duplicate clusters of 2 files each (groups A and B).

⚠ HEADS-UP: this scenario does NOT delete any file from disk. "Remove from
List" sets ``user_decision='removed'`` in the manifest (a deferred, reversible
DB-only mutation) — no send2trash. The DESTRUCTIVE-COVERAGE GUARD still
applies because the fixture is regenerated and lives under the isolated
``qa/sandbox/_disposable/`` dir (asserted at startup); the scenario never
touches a real or shared path.

Why s61 exists
--------------
Layer 1 (``tests/test_file_operations.py::TestSingletonPruneOffer``) drives
``_maybe_offer_singleton_prune`` with a mocked ``SingletonPruneConfirmDialog
.ask`` — it pins the per-bucket prune dispatch but never builds the real
dialog. s61 pins the live three-layout / PruneVerdict wiring: that removing
rows so two groups collapse to singletons (one PLAIN — remaining item has no
decision; one ACTIONED — remaining item carries an un-executed
``user_decision='delete'``) surfaces the mixed-bucket dialog with its opt-in
checkbox, and that each verdict produces the right manifest outcome.

Three variants, each a fresh scan + setup + Remove-from-List that collapses
both groups to a plain + actioned singleton pair:

  * Variant A — click "Remove" WITHOUT checking the actioned-bucket box →
    ONLY the plain singleton is pruned (``removed``); the actioned singleton
    stays in the list with its ``delete`` decision intact.
  * Variant B — click "Remove" WITH the box checked → BOTH singletons pruned.
  * Variant C — click "Keep all" → NOTHING pruned; both singletons stay.

The prune dialog only fires when ``ui.prune_singletons == "ask"`` — s61's
configure step overrides the qa default of ``"never"`` (see the
``[QA:s60/s61]`` note in ``qa/scenarios/_config.py``).

Tree-content assertions use direct sqlite reads (s14/s32/s35 pattern), never
``read_result_rows``. Row picks use ``ctrl_click`` + ``right_click`` +
``select_popup_menu_path`` (s20 multi-remove precedent).
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
FIXTURE_DIR = REPO / "qa" / "sandbox" / "_disposable" / "s61_source"
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

QUALITIES = [95, 65]              # 2 files per cluster
_SCANNER_THRESHOLD = 10           # scanner/dedup.py default — see s13
_REGEN_MAX_ATTEMPTS = 5

PRUNE_TITLE = "Prune singleton groups?"
PRUNE_BTN_KEEP = "Keep all"
_PRUNE_BTN_REMOVE_PREFIX = "Remove "       # "Remove {count}" — variable N
_PRUNE_CHECKBOX_PREFIX = "Also remove "    # actioned-bucket opt-in checkbox


def _build_base(rng: np.random.Generator) -> Image.Image:
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


def _build_cluster() -> Image.Image:
    last_worst: int | None = None
    for _ in range(_REGEN_MAX_ATTEMPTS):
        candidate = _build_base(np.random.default_rng())
        worst = _max_pairwise_phash(candidate, QUALITIES)
        if worst <= _SCANNER_THRESHOLD:
            return candidate
        last_worst = worst
    raise RuntimeError(
        f"Could not generate a clustering near-duplicate base after "
        f"{_REGEN_MAX_ATTEMPTS} attempts (last worst pHash distance "
        f"{last_worst})."
    )


# Fixed basenames so the scenario can target specific rows by name.
# Cluster A (plain): A_keep stays undecided → plain singleton after A_drop
# is removed. Cluster B (actioned): B_keep is marked delete (un-executed) →
# actioned singleton after B_drop is removed.
A_KEEP = "s61_a_keep_q95.jpg"
A_DROP = "s61_a_drop_q65.jpg"
B_KEEP = "s61_b_keep_q95.jpg"
B_DROP = "s61_b_drop_q65.jpg"


def _regen_fixture() -> None:
    """Wipe FIXTURE_DIR and write 2 clusters × 2 JPEGs with fixed names."""
    if FIXTURE_DIR.exists():
        for f in FIXTURE_DIR.iterdir():
            if f.is_file():
                f.unlink()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    names_by_cluster = {
        "a": [A_KEEP, A_DROP],
        "b": [B_KEEP, B_DROP],
    }
    for ci, (cluster, names) in enumerate(names_by_cluster.items(), start=1):
        base = _build_cluster()
        for i, (q, name) in enumerate(zip(QUALITIES, names)):
            exif = base.getexif()
            exif[36867] = f"2024:0{ci}:01 1{i}:00:00"
            base.save(str(FIXTURE_DIR / name), "JPEG", quality=q,
                      exif=exif.tobytes())


def _read_decisions() -> dict[str, str]:
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, COALESCE(user_decision, '') "
            "FROM migration_manifest WHERE source_path LIKE ?",
            (f"%{FIXTURE_DIR.name}%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: d for p, d in rows}


def _scan_and_setup(win):
    """Fresh scan + mark B_KEEP delete. Returns the reconnected main window.

    Each variant runs this so the prune dialog sees a clean plain+actioned
    singleton pair. After a previous variant has mutated decisions, the
    re-scan rebuilds the manifest from disk (files are never deleted —
    only ``removed`` decisions were written, which a re-scan clears).
    """
    print("step: open_scan_dialog")
    pid = win.process_id()
    dlg, _ = _uia.open_scan_dialog(win)
    print("step: run_scan")
    # On variants B/C the previous variant left pending decisions in the
    # manifest, so clicking Start Scan surfaces the #142 "Discard pending
    # decisions?" prompt. Click Start, dismiss the prompt with Yes if it
    # appears (we WANT the re-scan to rebuild from disk — the files are all
    # still present; only DB decisions were mutated), then wait for "Done.".
    start_btn = dlg.child_window(title=_uia.SCAN_BTN_START, control_type="Button")
    log_edit = dlg.child_window(auto_id=_uia.SCAN_AID_LOG, control_type="Edit")
    _uia._focus(dlg)
    t0 = time.time()
    start_btn.invoke()
    try:
        prompt_hwnd = _uia.wait_for_dialog(pid, "Discard pending decisions?", timeout=3)
        prompt = _uia.connect_by_handle(prompt_hwnd)
        _uia._focus(prompt)
        time.sleep(0.2)
        prompt.child_window(title="Yes", control_type="Button").click_input()
        time.sleep(0.4)
        print("  rescan_discard_prompt=dismissed_yes")
    except TimeoutError:
        pass  # first variant — no pending decisions, no prompt
    _uia.wait_for_text_in(log_edit, ["Done.", "Error", "Failed"], timeout=30)
    print(f"  scan_elapsed_s={time.time() - t0:.2f}")
    print("step: close_dialog")
    _uia.close_and_load_manifest(dlg)
    _, win = _uia.connect_main()

    # Mark ONLY B_KEEP as delete (un-executed) so that after B_DROP is
    # removed, the surviving B_KEEP singleton is classified ACTIONED.
    print("step: mark_B_keep_delete")
    _uia.mark_all_via_regex_standalone(
        win, field="File Name", regex=r"s61_b_keep", action_label="delete"
    )
    _, win = _uia.connect_main()
    time.sleep(0.3)
    dec = _read_decisions()
    print(f"  decisions_after_setup={dec}")
    if dec.get(B_KEEP) != "delete":
        print(f"FAIL: setup did not set {B_KEEP} decision=delete (got {dec.get(B_KEEP)!r})")
        return None
    return win


def _remove_drop_rows_and_wait_prune(win):
    """Multi-select A_DROP + B_DROP, Remove from List, wait for the prune
    dialog. Returns the prune-dialog wrapper, or None if it never appeared.
    """
    pid = win.process_id()
    print(f"step: multiselect_drop_rows [{A_DROP}, {B_DROP}]")
    _uia.left_click_tree_row(win, A_DROP)
    _uia.ctrl_click_tree_row(win, B_DROP)
    _uia.right_click_tree_row(win, B_DROP)
    _uia.select_popup_menu_path(pid, ["Remove from List"])

    print("step: wait_for_prune_dialog")
    try:
        hwnd = _uia.wait_for_dialog(pid, PRUNE_TITLE, timeout=6)
    except TimeoutError:
        print(
            "FAIL: SingletonPruneConfirmDialog did not appear after Remove "
            "from List — either ui.prune_singletons is not 'ask' (configure "
            "override missing) or no singletons were produced"
        )
        return None
    dlg = _uia.connect_by_handle(hwnd)
    _uia._focus(dlg)
    time.sleep(0.3)
    return dlg


def _find_remove_button(dlg):
    for btn in dlg.descendants(control_type="Button"):
        try:
            if (btn.window_text() or "").strip().startswith(_PRUNE_BTN_REMOVE_PREFIX):
                return btn
        except Exception:
            continue
    return None


def _find_actioned_checkbox(dlg):
    for cb in dlg.descendants(control_type="CheckBox"):
        try:
            if (cb.window_text() or "").strip().startswith(_PRUNE_CHECKBOX_PREFIX):
                return cb
        except Exception:
            continue
    return None


def _run_variant(win, label: str, action: str) -> int:
    """action ∈ {"remove_plain_only", "remove_both", "keep_all"}."""
    print(f"\n=== variant {label}: {action} ===")
    win = _scan_and_setup(win)
    if win is None:
        return 1
    dlg = _remove_drop_rows_and_wait_prune(win)
    if dlg is None:
        return 1

    # The mixed-bucket dialog must expose the actioned-bucket opt-in checkbox
    # (only rendered when BOTH count_plain>0 and count_actioned>0).
    checkbox = _find_actioned_checkbox(dlg)
    remove_btn = _find_remove_button(dlg)
    print(
        f"  actioned_checkbox_present={checkbox is not None} "
        f"remove_btn_present={remove_btn is not None}"
    )
    if checkbox is None:
        print(
            "FAIL: actioned-bucket opt-in checkbox absent — the dialog should "
            "render it when both plain and actioned singletons exist "
            "(mixed-layout mode)"
        )
        # Still dismiss the dialog so the app isn't left modal.
        try:
            _uia.cancel_scan_dialog(dlg)
        except Exception:
            pass
        return 1

    pid = win.process_id()
    if action == "keep_all":
        print("  click: Keep all")
        keep_btn = _uia._find_dialog_button(dlg, PRUNE_BTN_KEEP)
        keep_btn.click_input()
    elif action == "remove_both":
        print("  check actioned box + click Remove")
        try:
            checkbox.click_input()
            time.sleep(0.2)
        except Exception:
            pass
        if remove_btn is None:
            print("FAIL: Remove button not found")
            return 1
        remove_btn.click_input()
    else:  # remove_plain_only
        print("  click Remove (box UNCHECKED)")
        if remove_btn is None:
            print("FAIL: Remove button not found")
            return 1
        remove_btn.click_input()
    time.sleep(1.0)

    # Verify outcome via direct sqlite read of the 'removed' decision.
    post = _read_decisions()
    print(f"  decisions_after_variant={post}")
    a_keep = post.get(A_KEEP, "")
    b_keep = post.get(B_KEEP, "")

    if action == "remove_plain_only":
        # Plain singleton (A_KEEP) pruned → 'removed'; actioned (B_KEEP)
        # stays with its 'delete' decision intact.
        if a_keep != "removed":
            print(f"FAIL[A]: plain singleton {A_KEEP} should be 'removed', got {a_keep!r}")
            return 1
        if b_keep != "delete":
            print(
                f"FAIL[A]: actioned singleton {B_KEEP} should KEEP its 'delete' "
                f"decision (box unchecked), got {b_keep!r}"
            )
            return 1
        print("  variant_A=PASS (plain removed, actioned intact)")
    elif action == "remove_both":
        if a_keep != "removed" or b_keep != "removed":
            print(
                f"FAIL[B]: both singletons should be 'removed' with the box "
                f"checked — A_KEEP={a_keep!r} B_KEEP={b_keep!r}"
            )
            return 1
        print("  variant_B=PASS (both removed)")
    else:  # keep_all
        if a_keep == "removed" or b_keep == "removed":
            print(
                f"FAIL[C]: Keep all should prune NOTHING — "
                f"A_KEEP={a_keep!r} B_KEEP={b_keep!r}"
            )
            return 1
        # B_KEEP should still carry its delete decision; A_KEEP still blank.
        if b_keep != "delete":
            print(f"FAIL[C]: actioned singleton lost its decision on Keep all (got {b_keep!r})")
            return 1
        print("  variant_C=PASS (nothing pruned)")

    return 0


def main() -> int:
    print("scenario: s61_actioned_singleton_prune")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # DESTRUCTIVE-COVERAGE GUARD — refuse to run if the fixture root is not
    # isolated under the disposable sandbox. (s61 only writes DB decisions,
    # never deletes files, but the guard is uniform across destructive QA.)
    print("step: assert_isolated_fixture_root")
    disposable_root = (REPO / "qa" / "sandbox" / "_disposable").resolve()
    if disposable_root not in FIXTURE_DIR.resolve().parents:
        print(
            f"FAIL: refusing to run — FIXTURE_DIR {FIXTURE_DIR} is not under "
            f"the isolated disposable sandbox {disposable_root}"
        )
        return 1
    print(f"  fixture_root_isolated=True ({FIXTURE_DIR})")

    print("step: regen_fixture")
    _regen_fixture()
    present = sorted(p.name for p in FIXTURE_DIR.glob("*.jpg"))
    print(f"  fixture_files={present}")

    # Variant A — Remove without the actioned box.
    rc = _run_variant(win, "A", "remove_plain_only")
    if rc != 0:
        return rc
    _, win = _uia.connect_main()

    # Variant B — Remove WITH the actioned box checked.
    rc = _run_variant(win, "B", "remove_both")
    if rc != 0:
        return rc
    _, win = _uia.connect_main()

    # Variant C — Keep all.
    rc = _run_variant(win, "C", "keep_all")
    if rc != 0:
        return rc

    print("scenario: s61_actioned_singleton_prune DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
