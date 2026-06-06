"""Scenario 67 — D6 regression guard: locked singleton under prune="always" (#589).

Required source: qa/sandbox/_disposable/s67_source/ — regenerated each run.
One near-duplicate cluster of 2 files (s67_keep_q95.jpg, s67_drop_q65.jpg).

⚠ HEADS-UP: this scenario does NOT delete any file from disk. "Remove from
List" writes ``outcome='ignored'`` in the manifest (a deferred, reversible
DB-only mutation) — no send2trash. The DESTRUCTIVE-COVERAGE GUARD still
applies because the fixture is regenerated and lives under the isolated
``qa/sandbox/_disposable/`` dir (asserted at startup).

Why s67 exists — the D6 hole the unit tests don't cover
------------------------------------------------------
Layer 1 (``tests/test_file_operations.py::TestSingletonPruneOffer``) pins
the D6 lock gate with a MOCKED ``LockedRowsConfirmDialog.ask``. s67 pins
the LIVE wiring — specifically the regression guard that was the whole
point of D6: under ``ui.prune_singletons="always"`` the lock dialog
STILL fires before any locked singleton is finalised to ``'ignored'``.
Pre-D6 (before PR #588) the "always" path swept locked singletons
silently; the unit tests verify the gate but only the live driver
proves the modal actually surfaces under a real Qt event loop with the
"always" pref active.

Sister of s61 (the "ask"-path coverage, including its Variant D for the
lock gate under "ask"); s67 owns the "always"-path slot.

Two variants, each a fresh scan + lock + Remove-from-List that collapses
the cluster to a single locked singleton:

  * Variant V1 — click "Cancel" on the lock dialog → the locked singleton
    is NOT pruned (``outcome`` stays ``''``); on the "always" path this
    means no prune at all because the locked subset is the ONLY content.
  * Variant V2 — click "Unlock & Apply to All" → the locked singleton is
    finalised to ``outcome='ignored'``.

In BOTH variants the SingletonPruneConfirmDialog must NEVER appear —
the "always" pref skips it by design. The driver asserts the absence
with a short-timeout ``wait_for_dialog`` after the lock dialog is
dismissed (a non-appearance is the regression guard).

The button label discipline matters here (carried over from PR #588's
qa fix): in the prune context the LockedRowsConfirmDialog uses the
generic ``LOCK_CONFIRM_BTN_UNLOCK_APPLY`` ("Unlock & Apply to All"),
NOT the Execute-time alias ``LOCK_CONFIRM_APPLY_ALL_UNLOCKED`` (which
resolves to ``"Unlock & Delete All"``). We pass the literal constant
directly.
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
FIXTURE_DIR = REPO / "qa" / "sandbox" / "_disposable" / "s67_source"
MANIFEST_PATH = REPO / "qa" / "run-manifest.sqlite"

QUALITIES = [95, 65]              # 2 files in one cluster
_SCANNER_THRESHOLD = 10           # scanner/dedup.py default — see s13
_REGEN_MAX_ATTEMPTS = 5

KEEP_NAME = "s67_keep_q95.jpg"
DROP_NAME = "s67_drop_q65.jpg"

# Per-context button label — see module docstring.
LOCK_CONFIRM_TITLE = _uia.LOCK_CONFIRM_TITLE
LOCK_BTN_UNLOCK_APPLY = _uia.LOCK_CONFIRM_BTN_UNLOCK_APPLY
LOCK_BTN_CANCEL = _uia.LOCK_CONFIRM_BTN_CANCEL

# The prune dialog title — short timeout used to ASSERT non-appearance
# on the "always" path. If it appears, the D6 regression-guard fails.
PRUNE_TITLE = "Prune singleton groups?"
_ABSENCE_TIMEOUT_S = 1.5


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


def _regen_fixture() -> None:
    """Wipe FIXTURE_DIR and write 1 cluster × 2 JPEGs with fixed names."""
    if FIXTURE_DIR.exists():
        for f in FIXTURE_DIR.iterdir():
            if f.is_file():
                f.unlink()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    base = _build_cluster()
    names = [KEEP_NAME, DROP_NAME]
    for i, (q, name) in enumerate(zip(QUALITIES, names)):
        exif = base.getexif()
        exif[36867] = f"2024:07:01 1{i}:00:00"
        base.save(str(FIXTURE_DIR / name), "JPEG", quality=q,
                  exif=exif.tobytes())


def _read_outcome(basename: str) -> str:
    """Return the ``outcome`` column for one fixture row."""
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT COALESCE(outcome, '') FROM migration_manifest "
            "WHERE source_path LIKE ?",
            (f"%{basename}",),
        ).fetchall()
    finally:
        conn.close()
    return rows[0][0] if rows else ""


def _read_is_locked(basename: str) -> int:
    """Return the ``is_locked`` flag for one fixture row (0 or 1)."""
    conn = sqlite3.connect(str(MANIFEST_PATH))
    try:
        rows = conn.execute(
            "SELECT COALESCE(is_locked, 0) FROM migration_manifest "
            "WHERE source_path LIKE ?",
            (f"%{basename}",),
        ).fetchall()
    finally:
        conn.close()
    return rows[0][0] if rows else 0


def _scan_and_lock_keep(win):
    """Fresh scan + lock the KEEP row. Returns the reconnected main window."""
    print("step: open_scan_dialog")
    pid = win.process_id()
    dlg, _ = _uia.open_scan_dialog(win)
    print("step: run_scan")
    # On variant 2 the previous variant left pending state in the manifest,
    # so clicking Start Scan surfaces the #142 "Discard pending decisions?"
    # prompt. Dismiss with Yes — we want the re-scan to rebuild from disk.
    # Files are never deleted by s67; only outcome / is_locked were mutated,
    # and write_manifest os.replace resets both.
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

    # Lock the KEEP row via the regex dialog — same mechanism as s32/s34/s36.
    # Regex anchors on the unique "keep_q95" suffix so only KEEP_NAME matches
    # (DROP is "drop_q65"). The action drops the LockedRowsConfirmDialog path
    # because Lock/Unlock are always-allowed (idempotent).
    print("step: lock_keep_row_via_regex")
    _uia.mark_all_via_regex_standalone(
        win, field="File Name", regex=r"s67_keep", action_label="lock"
    )
    _, win = _uia.connect_main()
    time.sleep(0.3)
    if _read_is_locked(KEEP_NAME) != 1:
        print(f"FAIL: setup did not lock {KEEP_NAME} (is_locked={_read_is_locked(KEEP_NAME)})")
        return None
    print(f"  is_locked[{KEEP_NAME}]=1 (confirmed)")
    return win


def _remove_drop_and_expect_lock_dialog(win):
    """Right-click → Remove from List on DROP. Returns True if the
    LockedRowsConfirmDialog appeared within the timeout, else False
    (which is itself a FAIL — the lock dialog must fire under "always")."""
    pid = win.process_id()
    print(f"step: remove_drop_row [{DROP_NAME}]")
    _uia.left_click_tree_row(win, DROP_NAME)
    _uia.right_click_tree_row(win, DROP_NAME)
    _uia.select_popup_menu_path(pid, ["Remove from List"])

    print("step: wait_for_lock_dialog")
    try:
        _uia.wait_for_dialog(pid, LOCK_CONFIRM_TITLE, timeout=6)
        return True
    except TimeoutError:
        print(
            "FAIL: LockedRowsConfirmDialog did not appear under "
            f"ui.prune_singletons=\"always\" — this is the D6 regression "
            f"this scenario exists to guard against (pre-D6 the \"always\" "
            f"path silently swept locked singletons to outcome='ignored')."
        )
        return False


def _assert_no_prune_dialog(pid: int) -> bool:
    """Short-timeout absence check for SingletonPruneConfirmDialog. The
    "always" path must skip it entirely (the prune dialog is the "ask"
    affordance). Returns True if absent (good), False if present (FAIL).
    """
    try:
        _uia.wait_for_dialog(pid, PRUNE_TITLE, timeout=_ABSENCE_TIMEOUT_S)
    except TimeoutError:
        return True
    print(
        "FAIL: SingletonPruneConfirmDialog surfaced under "
        "ui.prune_singletons=\"always\" — the dialog must only fire on "
        "the \"ask\" path."
    )
    return False


def _run_variant(win, label: str, lock_verdict: str, expected_outcome: str) -> int:
    """label: 'V1' or 'V2'. lock_verdict: button label on the lock dialog
    (one of LOCK_BTN_CANCEL / LOCK_BTN_UNLOCK_APPLY). expected_outcome:
    the assertion against KEEP's post-variant outcome ('' or 'ignored').
    """
    print(f"\n=== variant {label}: lock_verdict={lock_verdict!r} ===")
    win = _scan_and_lock_keep(win)
    if win is None:
        return 1
    pid = win.process_id()

    if not _remove_drop_and_expect_lock_dialog(win):
        return 1

    print(f"step: lock_confirm_click [{lock_verdict}]")
    if not _uia.drive_lock_confirm(pid, lock_verdict, timeout=3):
        print(f"FAIL: drive_lock_confirm could not click {lock_verdict!r}")
        return 1
    time.sleep(0.4)

    # Critical "always"-path assertion: prune dialog MUST NOT appear.
    if not _assert_no_prune_dialog(pid):
        return 1
    print("  prune_dialog_absent=True (correct for \"always\" path)")

    # Verify final outcome via direct sqlite read.
    time.sleep(0.4)
    actual = _read_outcome(KEEP_NAME)
    print(f"  outcome[{KEEP_NAME}]={actual!r} (expected {expected_outcome!r})")
    if actual != expected_outcome:
        print(
            f"FAIL[{label}]: outcome mismatch for locked singleton {KEEP_NAME} — "
            f"verdict={lock_verdict!r} expected {expected_outcome!r}, got {actual!r}"
        )
        return 1
    print(f"  variant_{label}=PASS")
    return 0


def main() -> int:
    print("scenario: s67_locked_singleton_prune_always")
    app, win = _uia.connect_main()
    pid = win.process_id()
    print(f"connected: pid={pid} title={win.window_text()!r}")

    # DESTRUCTIVE-COVERAGE GUARD — match s61's pattern. No file deletes
    # in s67, but the guard is uniform across the disposable family so a
    # mis-configured FIXTURE_DIR can never overlap a real source path.
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

    # V1 — CANCEL on the lock dialog. The specific D6 regression guard:
    # pre-fix this scenario would have silently swept KEEP to 'ignored'
    # under "always"; post-fix the lock dialog gates it and CANCEL holds.
    rc = _run_variant(win, "V1", LOCK_BTN_CANCEL, expected_outcome="")
    if rc != 0:
        return rc
    _, win = _uia.connect_main()

    # V2 — Unlock & Apply to All on the lock dialog. Confirms the verdict
    # round-trips into _apply_singleton_prune(prunable_locked) under
    # "always", writing outcome='ignored' to the locked singleton.
    rc = _run_variant(win, "V2", LOCK_BTN_UNLOCK_APPLY, expected_outcome="ignored")
    if rc != 0:
        return rc

    print("scenario: s67_locked_singleton_prune_always DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
