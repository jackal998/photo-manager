"""Scenario 61 — SingletonPruneConfirmDialog actioned-singleton flow (#484)
plus D6 locked-singleton gate on the "ask" path (#589, follow-up to #588).

Required source: qa/sandbox/_disposable/s61_source/ — regenerated each run.
Two independent near-duplicate clusters of 2 files each (groups A and B).

⚠ HEADS-UP: this scenario does NOT delete any file from disk. "Remove from
List" writes ``outcome='ignored'`` in the manifest (a deferred, reversible
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

Five variants, each a fresh scan + setup + Remove-from-List that collapses
both groups to singletons (per-variant bucket layout below):

  * Variant A — actioned-bucket box UNCHECKED on Remove → ONLY the plain
    singleton is pruned (``outcome='ignored'``); the actioned singleton
    stays in the list with its ``delete`` decision intact.
  * Variant B — actioned box CHECKED on Remove → BOTH singletons pruned.
  * Variant C — Keep all → NOTHING pruned; both singletons stay.
  * Variant D-cancel (#589) — A_KEEP is LOCKED before the remove. The
    LockedRowsConfirmDialog fires FIRST (D6 gate); CANCEL holds the
    locked singleton (outcome=''). The SingletonPruneConfirmDialog fires
    next for the actioned bucket — dismissed with Keep all so the
    assertion isolates the lock-gate's effect.
  * Variant D-apply (#589) — same setup; click "Unlock & Apply to All"
    on the lock dialog → A_KEEP becomes pruned (outcome='ignored'); the
    prune dialog is dismissed with Keep all so the actioned bucket
    decision stays intact, and the locked-bucket prune happens via
    ``_apply_singleton_prune(prunable_locked)`` at the tail of the "ask"
    branch.

The prune dialog only fires when ``ui.prune_singletons == "ask"`` — s61's
configure step overrides the qa default of ``"never"`` (see
``PRUNE_PREF_OVERRIDES`` in ``qa/scenarios/_config.py``). The sibling
scenario s67 covers the D6 gate on the ``"always"`` path.

Tree-content assertions use direct sqlite reads (s14/s32/s35 pattern), never
``read_result_rows``. Row picks use ``ctrl_click`` + ``right_click`` +
``select_popup_menu_path`` (s20 multi-remove precedent).

Button label discipline for Variant D (carried from #588's qa fix): in the
prune context the LockedRowsConfirmDialog uses ``LOCK_CONFIRM_BTN_UNLOCK_APPLY``
("Unlock & Apply to All"), NOT the Execute-time alias
``LOCK_CONFIRM_APPLY_ALL_UNLOCKED`` (which resolves to "Unlock & Delete All").
The driver passes the literal constant directly to ``drive_lock_confirm``.
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
    """Return {basename: outcome} for fixture rows.

    Reads the outcome column (#584) rather than user_decision — the
    visibility predicate is now WHERE outcome='' and the prune path writes
    outcome='ignored' (not user_decision='removed').  user_decision is still
    read separately where needed (e.g. B_KEEP's 'delete' decision at setup).
    """
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT source_path, COALESCE(outcome, '') "
            "FROM migration_manifest WHERE source_path LIKE ?",
            (f"%{FIXTURE_DIR.name}%",),
        ).fetchall()
    finally:
        conn.close()
    return {Path(p).name: d for p, d in rows}


def _read_user_decisions() -> dict[str, str]:
    """Return {basename: user_decision} for fixture rows.

    Used only in _scan_and_setup to verify the staged intent ('delete')
    on B_KEEP — user_decision holds the pending action before execute,
    while outcome holds the final post-execute state.
    """
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
    only ``outcome='ignored'`` was written, which a re-scan clears).
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
    # Read user_decision (staged intent) — not outcome (post-execute) — to
    # verify the mark_all_via_regex_standalone step wrote 'delete'.
    dec = _read_user_decisions()
    print(f"  user_decisions_after_setup={dec}")
    if dec.get(B_KEEP) != "delete":
        print(f"FAIL: setup did not set {B_KEEP} user_decision=delete (got {dec.get(B_KEEP)!r})")
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

    # Verify outcome via direct sqlite read. (#584: outcome column replaces
    # user_decision='removed'; B_KEEP's pending 'delete' user_decision is
    # read separately since it is NOT a finalised outcome.)
    post_outcome = _read_decisions()   # {basename: outcome}
    print(f"  outcomes_after_variant={post_outcome}")

    # Read B_KEEP's user_decision separately (its pending intent, not outcome).
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        b_decision_rows = conn.execute(
            "SELECT user_decision FROM migration_manifest WHERE source_path LIKE ?",
            (f"%{B_KEEP}%",),
        ).fetchall()
    finally:
        conn.close()
    b_user_decision = b_decision_rows[0][0] if b_decision_rows else ""

    a_keep_outcome = post_outcome.get(A_KEEP, "")
    b_keep_outcome = post_outcome.get(B_KEEP, "")

    if action == "remove_plain_only":
        # Plain singleton (A_KEEP) pruned → outcome='ignored'; actioned
        # (B_KEEP) stays with its 'delete' user_decision intact (outcome='').
        if a_keep_outcome != "ignored":
            print(f"FAIL[A]: plain singleton {A_KEEP} should have outcome='ignored', got {a_keep_outcome!r}")
            return 1
        if b_user_decision != "delete":
            print(
                f"FAIL[A]: actioned singleton {B_KEEP} should KEEP its 'delete' "
                f"user_decision (box unchecked), got {b_user_decision!r}"
            )
            return 1
        print("  variant_A=PASS (plain ignored, actioned intact)")
    elif action == "remove_both":
        if a_keep_outcome != "ignored" or b_keep_outcome != "ignored":
            print(
                f"FAIL[B]: both singletons should have outcome='ignored' with the box "
                f"checked — A_KEEP={a_keep_outcome!r} B_KEEP={b_keep_outcome!r}"
            )
            return 1
        print("  variant_B=PASS (both ignored)")
    else:  # keep_all
        if a_keep_outcome == "ignored" or b_keep_outcome == "ignored":
            print(
                f"FAIL[C]: Keep all should prune NOTHING — "
                f"A_KEEP outcome={a_keep_outcome!r} B_KEEP outcome={b_keep_outcome!r}"
            )
            return 1
        # B_KEEP should still carry its delete user_decision; A_KEEP still blank.
        if b_user_decision != "delete":
            print(f"FAIL[C]: actioned singleton lost its decision on Keep all (got {b_user_decision!r})")
            return 1
        print("  variant_C=PASS (nothing pruned)")

    return 0


# ─────────────────────────────────────────────────────────────────────
# Variant D (#589) — D6 locked-singleton gate on the "ask" path.
# Setup is identical to A/B/C (B_KEEP=delete) PLUS A_KEEP is locked
# before the multi-remove, so the post-remove bucket layout is:
#   locked_paths   = [A_KEEP]      ← drives LockedRowsConfirmDialog
#   actioned_paths = [B_KEEP]      ← drives SingletonPruneConfirmDialog
#   plain_paths    = []
# The prune dialog is dismissed with "Keep all" so the assertion
# isolates the LOCK gate's effect (whether A_KEEP is pruned depends
# entirely on the lock-dialog verdict; the actioned bucket stays
# intact either way).
# ─────────────────────────────────────────────────────────────────────


def _scan_setup_lock_a_keep(win):
    """Fresh scan + mark B_KEEP delete + lock A_KEEP. Returns the
    reconnected main window, or None on a setup failure."""
    win = _scan_and_setup(win)
    if win is None:
        return None
    print("step: lock_A_keep_via_regex")
    _uia.mark_all_via_regex_standalone(
        win, field="File Name", regex=r"s61_a_keep", action_label="lock"
    )
    _, win = _uia.connect_main()
    time.sleep(0.3)
    # Verify the lock landed.
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT COALESCE(is_locked, 0) FROM migration_manifest "
            "WHERE source_path LIKE ?",
            (f"%{A_KEEP}",),
        ).fetchall()
    finally:
        conn.close()
    is_locked = rows[0][0] if rows else 0
    if is_locked != 1:
        print(f"FAIL: setup did not lock {A_KEEP} (is_locked={is_locked})")
        return None
    print(f"  is_locked[{A_KEEP}]=1 (confirmed)")
    return win


def _multi_remove_drops_no_wait(win) -> None:
    """Multi-select A_DROP+B_DROP, Remove from List. Does NOT wait for
    the prune dialog — the caller drives the lock dialog first (D6 fires
    before SingletonPruneConfirmDialog on the "ask" path)."""
    pid = win.process_id()
    print(f"step: multiselect_drop_rows [{A_DROP}, {B_DROP}]")
    _uia.left_click_tree_row(win, A_DROP)
    _uia.ctrl_click_tree_row(win, B_DROP)
    _uia.right_click_tree_row(win, B_DROP)
    _uia.select_popup_menu_path(pid, ["Remove from List"])


def _dismiss_prune_dialog_keep_all(pid: int) -> bool:
    """Wait for SingletonPruneConfirmDialog and click Keep all. The "ask"
    path fires the prune dialog AFTER the lock dialog is dismissed (the
    actioned bucket has B_KEEP). Returns True on success, False if the
    dialog never appeared (which is itself a FAIL in this flow)."""
    try:
        hwnd = _uia.wait_for_dialog(pid, PRUNE_TITLE, timeout=6)
    except TimeoutError:
        print(
            "FAIL: SingletonPruneConfirmDialog did not appear after the "
            "lock dialog was dismissed — the \"ask\" path should fire it "
            "for the unlocked actioned bucket."
        )
        return False
    dlg = _uia.connect_by_handle(hwnd)
    _uia._focus(dlg)
    time.sleep(0.3)
    keep_btn = _uia._find_dialog_button(dlg, PRUNE_BTN_KEEP)
    keep_btn.click_input()
    time.sleep(0.6)
    return True


def _run_locked_variant(win, label: str, lock_verdict: str, expected_a_outcome: str) -> int:
    """label: 'D-cancel' or 'D-apply'. lock_verdict: one of
    _uia.LOCK_CONFIRM_BTN_CANCEL or _uia.LOCK_CONFIRM_BTN_UNLOCK_APPLY.
    expected_a_outcome: '' (CANCEL) or 'ignored' (Unlock & Apply).
    """
    print(f"\n=== variant {label}: lock_verdict={lock_verdict!r} ===")
    win = _scan_setup_lock_a_keep(win)
    if win is None:
        return 1
    pid = win.process_id()

    _multi_remove_drops_no_wait(win)

    # D6 gate: LockedRowsConfirmDialog must fire BEFORE the prune dialog
    # on the "ask" path when locked_paths is non-empty.
    print(f"step: lock_confirm_click [{lock_verdict}]")
    if not _uia.drive_lock_confirm(pid, lock_verdict, timeout=6):
        print(
            f"FAIL[{label}]: LockedRowsConfirmDialog did not appear (or "
            f"button {lock_verdict!r} not found). D6 lock gate regressed?"
        )
        return 1
    time.sleep(0.4)

    # Then the SingletonPruneConfirmDialog fires for the actioned bucket
    # (B_KEEP). Dismiss with Keep all so the actioned bucket stays intact
    # and the assertion isolates the lock-gate's effect on A_KEEP.
    if not _dismiss_prune_dialog_keep_all(pid):
        return 1

    # Assert the locked-singleton outcome matches the verdict, AND that
    # the actioned bucket (B_KEEP) stayed intact under Keep all.
    post_outcome = _read_decisions()  # {basename: outcome}
    print(f"  outcomes_after_variant={post_outcome}")
    a_outcome = post_outcome.get(A_KEEP, "")
    b_outcome = post_outcome.get(B_KEEP, "")

    if a_outcome != expected_a_outcome:
        print(
            f"FAIL[{label}]: locked singleton {A_KEEP} outcome — verdict "
            f"{lock_verdict!r} expected {expected_a_outcome!r}, got {a_outcome!r}"
        )
        return 1
    if b_outcome == "ignored":
        print(
            f"FAIL[{label}]: actioned singleton {B_KEEP} was pruned despite "
            f"the prune dialog being dismissed with Keep all (outcome={b_outcome!r})"
        )
        return 1
    # B_KEEP's pending 'delete' user_decision should also be intact.
    b_user_decision = _read_user_decisions().get(B_KEEP, "")
    if b_user_decision != "delete":
        print(
            f"FAIL[{label}]: actioned singleton {B_KEEP} lost its 'delete' "
            f"user_decision (got {b_user_decision!r})"
        )
        return 1
    print(f"  variant_{label}=PASS")
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
    _, win = _uia.connect_main()

    # Variant D-cancel (#589) — A_KEEP locked; lock dialog CANCEL → A_KEEP
    # outcome stays '' (lock holds). Actioned bucket dismissed with Keep all.
    rc = _run_locked_variant(
        win, "D-cancel", _uia.LOCK_CONFIRM_BTN_CANCEL, expected_a_outcome=""
    )
    if rc != 0:
        return rc
    _, win = _uia.connect_main()

    # Variant D-apply (#589) — A_KEEP locked; lock dialog "Unlock & Apply
    # to All" → A_KEEP pruned to outcome='ignored' via the locked-bucket
    # tail in _maybe_offer_singleton_prune.
    rc = _run_locked_variant(
        win, "D-apply", _uia.LOCK_CONFIRM_BTN_UNLOCK_APPLY, expected_a_outcome="ignored"
    )
    if rc != 0:
        return rc

    print("scenario: s61_actioned_singleton_prune DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
