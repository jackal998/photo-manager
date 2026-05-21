"""Run all qa.scenarios.sNN drivers sequentially in a single process.

For each scenario:
  1. configure qa/settings.json (writes scenario-specific source list)
  2. launch main.py as a subprocess
  3. poll until the main window is visible (max 8s; typically <2s)
  4. run the driver
  5. close the window via UIA
  6. wait for the subprocess to exit (or terminate if stuck)

Usage:
  .venv/Scripts/python.exe -m qa.scenarios._batch [scenarios...]
  .venv/Scripts/python.exe -m qa.scenarios._batch s02_empty_folder s04_corrupted
  .venv/Scripts/python.exe -m qa.scenarios._batch --shard 1 --total-shards 5
  .venv/Scripts/python.exe -m qa.scenarios._batch --shard 1 --total-shards 5 --dry-run
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Inherit the Python that invoked us — works under .venv (the local-dev
# convention), under a CI runner where actions/setup-python puts python on
# PATH directly, and under any other venv layout (conda, pyenv-win, etc).
# Previously hardcoded as REPO/.venv/Scripts/python.exe, which broke CI.
PY = sys.executable

ALL_SCENARIOS = [
    "s01_happy_path",
    "s02_empty_folder",
    "s03_cancel_scan",
    "s04_corrupted",
    "s05_huge_preview",
    "s06_formats",
    "s07_format_dup",
    "s08_exif_edge",
    "s09_walker_exclusions",
    "s10_multi_source",
    "s11_video_live",
    "s12_save_manifest",
    "s13_execute_action",
    "s14_action_by_regex",
    "s15_context_menu",
    "s16_open_manifest",
    "s17_scan_dialog_widgets",
    "s18_log_menu",
    "s19_context_menu_open_folder",
    "s20_multi_remove_from_list",
    "s21_list_menu_remove",
    "s22_language_switch",
    # s23 is split A/B so the cross-launch boundary is an explicit batch step.
    # Order matters: s23b reads what s23a's GUI mutations persisted to disk.
    "s23a_set_settings",
    "s23b_verify_settings",
    "s24_stale_manifest_paths",
    "s25_empty_area_context_menu",
    "s26_keyboard_navigation",
    "s27_rescan_confirm",
    # s28 — dirty-flag exit prompt. Run AFTER s27 so any test order
    # change still puts s28 next to its closest neighbour (manifest
    # state-mutation scenarios). Self-cleans by exiting the app with
    # "Leave"; the next scenario relaunches.
    "s28_exit_dirty_prompt",
    # s29 — bulk regex remove-from-list as a deferred decision. Sister
    # to s14 (bulk regex delete) but with the deferred-remove action.
    "s29_remove_from_list_by_regex",
    # s30 — Phase A regex-dialog UX upgrade: right-click parity in
    # Execute Action dialog opens the same enhanced ActionDialog.
    # Sister to s14 (menu route) and s13 (toolbar-button route).
    "s30_execute_dialog_regex_right_click",
    # s31 — Phase B Simple mode (renamed from "Beginner" in Phase C)
    # plus the Phase C regex-sync invariants. Verifies Simple is the
    # default, drives the Simple widgets, then round-trips through
    # Regex mode to confirm the regex line edit holds the synthesised
    # pattern and reverse-parsing back populates Simple cleanly.
    "s31_simple_mode_regex",
    # s32 (#182) — Bulk regex on locked rows now surfaces the unified
    # LockedRowsConfirmDialog. Scenario drives "Apply to Unlocked Only"
    # end-to-end (today's old silent-skip behavior made explicit); the
    # other two verdicts (Unlock & Apply All, Cancel) are unit-tested.
    "s32_lock_confirm_bulk_regex",
    # s33 (#166) — Execute Action dialog's all-delete banner renders
    # the flagged group number as a clickable anchor (the click → jump
    # itself is covered by unit tests since QLabel HTML anchors aren't
    # first-class UIA elements).
    "s33_execute_dialog_jump_to_all_delete",
    # s34 (#182) — Execute-time lock confirm drives the
    # LockedRowsConfirmDialog when locked rows have decision='delete'
    # at the moment the user clicks Execute. Sister to s32 (bulk regex
    # trigger); same fixture as s14.
    "s34_lock_confirm_at_execute",
    # s35 (#182 follow-up, closes the gap that hid #175's missing
    # ActionHandlersImpl.set_locked_state proxy) — main-window
    # right-click Lock / Unlock for single + multi-select.
    "s35_lock_via_context_menu",
    # s36 (#182) — DESTRUCTIVE Execute through the lock-confirm
    # dialog. Sister to s13 (destructive happy path) and s34 (lock-
    # confirm Cancel, non-destructive). Proves the full chain when
    # the user picks Unlock & Apply All at Execute time: locked row
    # unlocks, send2trash fires for every row, manifest writes
    # executed=1. Disposable fixture; sends 5 files to recycle bin.
    "s36_lock_confirm_destructive_execute",
    # s37 (#138, #140) — persistent status-bar baseline. Probes that the
    # startup "Ready" message survives past the original 3s timeout and
    # that a post-load summary survives opening + dismissing the File
    # menu (the QAction-hover path that previously wiped temp messages).
    "s37_status_bar_baseline",
    # s38 (#144) — scan dialog inline error when "+ Add" is clicked with
    # a typed path that doesn't exist. Sister to s17 (in-dialog widget
    # ops); only s38 exercises the failure path.
    "s38_scan_dialog_invalid_path",
    # s39 (#136 + #141) — window geometry + splitter state persist
    # across launches, AND the splitter min-width constraints lift
    # the window's own minimum width above the #136 broken threshold.
    # Owns its own re-launch mid-scenario (the geometry round-trip
    # is what's under test); writes ``qa/window_state.ini`` and
    # cleans it up at startup.
    "s39_window_geometry_persist",
    # s40 (#143) — double-click dispatcher in TreeController. Verifies
    # group-header rows toggle expand/collapse on double-click (file-row
    # branch → OS viewer is unit-tested at layer 1; not driven here
    # because an OS-spawned image viewer has no deterministic
    # observable / cleanup path).
    "s40_results_tree_double_click",
    # s41 (#137) — empty-state primary-action buttons. Drives the
    # pre-manifest state: clicks Scan Sources… (asserts the scan
    # dialog opens), then clicks Open Manifest… (asserts the native
    # file picker opens, then cancels via Esc). Verifies the buttons
    # converge on the same handlers as the File-menu route.
    "s41_empty_state_action_buttons",
    # s42 (#187) — end-to-end keep-worthiness scoring: scan populates
    # the score column, within-group sort orders by score-DESC, and
    # the new "Apply best-copy decisions to this group" right-click
    # action picks the top scorer for KEEP + marks the rest DELETE.
    # Reuses near-duplicates fixture (5 q-quality variants); file-size
    # is the only signal that differs across the 5 files, so q95 wins.
    "s42_scoring",
    # s43 (#209) — Set Action dialog's new numeric-condition panel.
    # Opens Execute Action → Set Action by Field/Regex → switches the
    # field combo to Size (Bytes) → verifies the numeric panel
    # surfaces → sets a threshold > (q72's size) → verifies the 3
    # larger files are marked delete and the 2 smaller ones stay
    # unchanged. Non-destructive: cancels Execute before deletion.
    "s43_numeric_condition",
    # s44 — selection-scoped Execute (#211). Highlights 2 of 5
    # delete-decision rows in the Execute dialog tree, clicks Execute,
    # asserts only the highlighted files leave disk and the rest keep
    # their decisions intact (executed=0). Destructive like s13 —
    # 2 files per run go to the recycle bin.
    "s44_execute_highlighted_rows",
    # s45 (#121) — column-header sort flow + in-memory sort
    # preservation across manifest reload. Clicks File Name + Size
    # (Bytes) column headers, asserts the displayed row order toggles
    # ASC ↔ DESC via a new y-filter-free read helper (avoids the
    # read_result_rows y_min=600 trap on the smaller windows-latest
    # render), then triggers File → Open Manifest on the same path
    # and asserts the sort survives. Non-destructive.
    "s45_sort_persistence",
    # s47 (#214) — column layout (visual order + widths) persists
    # across launches. Owns its own re-launch mid-scenario (mirrors
    # s39's lifecycle for window geometry, which has the same
    # save-on-close / restore-on-next-launch property). The drag-to-
    # reorder path is layer-1 — synthetic SendInput is reliable for a
    # resize (drag the right-edge handle) but flaky for a move (Qt's
    # section-drag threshold is sensitive to event pacing on busy CI).
    "s47_column_layout_persist",
    # s48 (#215) — geometry persists across close-and-reopen WITHIN
    # one app session for ScanDialog / ExecuteActionDialog /
    # ActionDialog. Companion to s39 which covers the main-window
    # round-trip across an app restart. Non-destructive: scans
    # near-duplicates to load a manifest, then resizes / closes /
    # reopens each dialog and asserts the size came back through.
    "s48_dialog_geometry_persist",
    # s49 (#212) — "Auto select after scan" checkbox end-to-end.
    # Two phases inside one app session against the near-duplicates
    # fixture: phase 1 toggles the new Advanced-Settings checkbox ON
    # via UIA and asserts the top-scored row carries action="KEEP" in
    # the manifest; phase 2 toggles it OFF and asserts zero KEEP rows.
    "s49_scan_auto_select",
    # s50 (#237) — Select dialog's numeric-condition panel must surface
    # when the dialog is opened from the main-window menu route.
    # Sister to s43 which covers the same numeric panel reached via the
    # Execute Action dialog's "Select by Field/Regex…" button. Non-
    # destructive — just probes that the widgets surface after picking
    # a numeric field, then closes the dialog without applying.
    "s50_select_numeric_panel_from_main_window",
    # s51 (#165) — Execute Action dialog now embeds a PreviewPane via a
    # horizontal splitter. Non-destructive: opens the dialog with one
    # row marked 'delete', clicks the row, asserts that the dialog
    # contains both a tree and a preview pane visible to UIA, then
    # cancels without executing.
    "s51_execute_dialog_preview",
    # s52 (#253) — REVIEW_DUPLICATE rows' Similarity % is recomputed at
    # render time against the *displayed* Ref winner (which can diverge
    # from the scanner's anchor after #241's score-aware tie-break).
    # Scans the near-duplicates fixture, reads back the manifest, and
    # verifies that for every REVIEW_DUPLICATE row the (Ref-winner pHash,
    # row pHash) Hamming distance reaches the renderer — phash column
    # has to be wired through PhotoRecord for the new render path to
    # work. Read-only on the manifest.
    "s52_similarity_against_displayed_ref",
    # s53 (#324) — Execute Action dialog right-click → Lock / Unlock /
    # Set Action → delete. Layer-3 anchor for the non-regex decision
    # paths #322 plumbed status_reporter through under [qa-not-needed].
    # L1 tests (TestExecuteDialogStatusEmission) pin the methods when
    # called directly; this driver pins the right-click → context menu
    # → method chain.
    "s53_execute_dialog_lock_decision",
    # s54 (#324) — Execute Action dialog right-click → Set Action →
    # Remove from List → Yes-confirm. Companion to s53 covering the
    # fourth non-regex decision path, which adds a QMessageBox confirm
    # the L1 unit tests can't drive.
    "s54_execute_dialog_remove_from_list",
]


def select_shard(
    scenarios: list[str], shard: int, total_shards: int
) -> list[str]:
    """Return the subset of ``scenarios`` belonging to ``shard`` of ``total_shards``.

    Sorted-stride selection over *units*: scenarios are sorted alphabetically,
    grouped into units, then units at positions (shard-1, shard-1+N, ...) are
    picked. Most units are singletons; ``s23a_set_settings`` and
    ``s23b_verify_settings`` form a single two-element unit so they always run
    in the same shard (s23b reads what s23a wrote — splitting them would break
    the scenario).

    Shards are pairwise disjoint and their union equals ``set(scenarios)``.
    Within a shard, original sorted order is preserved.

    ``shard`` is 1-indexed (matches CI matrix conventions).
    """
    if total_shards < 1:
        raise ValueError(f"total_shards must be >= 1, got {total_shards}")
    if not 1 <= shard <= total_shards:
        raise ValueError(
            f"shard must be in 1..{total_shards}, got {shard}"
        )
    sorted_scenarios = sorted(scenarios)
    units: list[tuple[str, ...]] = []
    i = 0
    while i < len(sorted_scenarios):
        name = sorted_scenarios[i]
        nxt = sorted_scenarios[i + 1] if i + 1 < len(sorted_scenarios) else None
        if name == "s23a_set_settings" and nxt == "s23b_verify_settings":
            units.append((name, nxt))
            i += 2
        else:
            units.append((name,))
            i += 1
    selected_units = units[shard - 1 :: total_shards]
    return [name for unit in selected_units for name in unit]


def _resolve_exit_button_labels() -> tuple[str, str]:
    """Resolve the localised ``("Leave" button, "Unsaved Changes" title)``
    pair for the locale currently persisted in ``qa/settings.json``.

    Reading the locale at parent-process time (here) keeps the
    subprocess helper free of YAML/Qt imports at startup, and means
    locale switches by previous scenarios (e.g. s22_language_switch
    runs but doesn't fully restore) don't make the close-window dance
    look for "Leave" when the running app actually shows "離開". On any
    failure we fall back to the English strings — that matches the
    pre-#325 behaviour exactly, so the worst case is "no worse than
    before" rather than "broken differently".
    """
    try:
        from infrastructure.i18n import init_translator
        from infrastructure.settings import JsonSettings

        home_env = os.environ.get("PHOTO_MANAGER_HOME") or ""
        config_home = (REPO / home_env).resolve() if home_env else REPO
        settings = JsonSettings(config_home / "settings.json")
        locale = settings.get("ui.locale", "en") or "en"
        translator = init_translator(locale, REPO / "translations")
        return translator.t("exit.button_leave"), translator.t("exit.confirm_title")
    except Exception:
        return "Leave", "Unsaved Changes"


def _close_window() -> None:
    """Close the Photo Manager top window; dismiss the dirty prompt if it fires.

    Many scenarios end with the manifest in a dirty state (any decision
    set during the run flips the dirty flag — see s28_exit_dirty_prompt).
    On close, the app fires the "Unsaved Changes" QMessageBox
    (`exit.confirm_title` / `exit.button_leave` in translations/en.yml).
    Before this dismissal logic, that prompt blocked the close and the
    batch runner force-terminated the process — 14/52 scenarios printed
    ``app did not exit cleanly, terminating`` on a green run, which is
    cosmetic noise that obscures real launch / shutdown bugs.

    Picking "Leave" is non-destructive because decisions auto-persist
    to the manifest as soon as they're set (see s12_save_manifest and
    the comments in MainWindow.set_decision). The prompt's real purpose
    is "save first if you want a SEPARATE manifest file" — irrelevant
    in batch mode.

    Implementation moved to :mod:`qa.scenarios._close_window_helper`
    (#325). The helper uses pywinauto's UIA backend with
    ``connect(handle=...)`` to look up the Leave button by its display
    text rather than by Tab-traversal position, which was load-bearing
    on ``MainWindow.closeEvent`` button order — a future reorder used
    to silently route Enter to the wrong button. ``EnumWindows`` +
    ``WM_CLOSE`` are kept (in the helper) because pywinauto's
    ``top_window().close()`` is synchronous and blocks while the modal
    runs, leaving no chance to click Leave.
    """
    leave_label, dialog_title = _resolve_exit_button_labels()
    subprocess.run(
        [
            PY,
            "-m",
            "qa.scenarios._close_window_helper",
            "--leave-label",
            leave_label,
            "--dialog-title",
            dialog_title,
        ],
        cwd=REPO,
        capture_output=True,
        timeout=15,
    )


_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
)


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.wintypes.DWORD),
        ("cntUsage", ctypes.wintypes.DWORD),
        ("th32ProcessID", ctypes.wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", ctypes.wintypes.DWORD),
        ("cntThreads", ctypes.wintypes.DWORD),
        ("th32ParentProcessID", ctypes.wintypes.DWORD),
        ("pcPriClassBase", ctypes.wintypes.LONG),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("szExeFile", ctypes.wintypes.WCHAR * 260),
    ]


def _find_descendants(parent_pid: int) -> set[int]:
    """Return the transitive descendant pids of ``parent_pid``.

    The venv's ``python.exe`` (Windows Python launcher behaviour) can
    spawn the actual interpreter as a child process — empirically
    confirmed: ``Popen([sys.executable, 'main.py']).pid`` differs from
    the pid that ends up owning the QMainWindow. Without descendant
    awareness, ``_wait_for_main_window`` polled for the launcher pid
    forever and timed out on every scenario (the WARN noise that
    obscured real launch failures).
    """
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = -1
    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return set()
    try:
        children_by_parent: dict[int, list[int]] = {}
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        if not _kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return set()
        while True:
            children_by_parent.setdefault(
                entry.th32ParentProcessID, []
            ).append(entry.th32ProcessID)
            if not _kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
        descendants: set[int] = set()
        stack = [parent_pid]
        while stack:
            pid = stack.pop()
            for child in children_by_parent.get(pid, ()):
                if child not in descendants:
                    descendants.add(child)
                    stack.append(child)
        return descendants
    finally:
        _kernel32.CloseHandle(snap)


def _wait_for_main_window(launcher_pid: int, timeout: float = 8.0) -> bool:
    """Poll until photo-manager's main window is visible under ``launcher_pid``
    or any of its descendants.

    Replaces a fixed ``time.sleep`` after launching ``main.py``. The
    window typically appears in ~0.5–1.5 s on a real desktop and 2–4 s
    on hosted CI runners — fixed sleeps either over-wait or are too
    short under runner contention. Polling adapts to whichever side
    you're on and saves cumulative time across the batch on green runs.

    Descendant-pid awareness fixes the pid race that fired
    ``WARN: main window did not appear within 8s`` on every scenario
    (see ``_find_descendants`` docstring). The Toolhelp32 snapshot is
    refreshed every poll iteration so a slow fork doesn't make us miss
    the child window.

    Uses ctypes ``EnumWindows`` rather than spawning pywinauto so the
    cost per check is microseconds, not subprocess-startup overhead.
    Returns ``True`` if the window appeared within ``timeout``,
    ``False`` if the timeout expired (caller logs a warning; the
    driver's own UIA ``connect`` will then surface a clearer error).
    """
    deadline = time.monotonic() + timeout
    found = [False]

    while time.monotonic() < deadline:
        target_pids = _find_descendants(launcher_pid) | {launcher_pid}
        found[0] = False

        def cb(hwnd, _):
            if not _user32.IsWindowVisible(hwnd):
                return True
            ppid = ctypes.c_ulong()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(ppid))
            if ppid.value not in target_pids:
                return True
            title = ctypes.create_unicode_buffer(256)
            _user32.GetWindowTextW(hwnd, title, 256)
            if "Photo Manager" in title.value:
                found[0] = True
                return False
            return True

        _user32.EnumWindows(_WNDENUMPROC(cb), 0)
        if found[0]:
            # Small grace for the QApplication event loop to finish
            # constructing widgets — without it, an immediate UIA
            # connect from the driver can race against widget setup.
            time.sleep(0.3)
            return True
        time.sleep(0.1)
    return False


def run_one(name: str) -> tuple[int, str]:
    print(f"\n===== {name} =====", flush=True)
    # 1. Configure
    #
    # Decode child stdout/stderr as UTF-8 (matches PYTHONIOENCODING=utf-8
    # the qa-batch workflow sets). subprocess.run(text=True) without an
    # explicit encoding falls back to locale.getpreferredencoding, which
    # is CP1252 on en-US Windows runners — that turns the scanner's
    # box-drawing chars (─ U+2500) into mojibake (`â”€`) before they
    # reach our own stdout.
    r = subprocess.run(
        [PY, "-m", "qa.scenarios.configure", name],
        cwd=REPO, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=15,
    )
    print(r.stdout, end="", flush=True)
    if r.returncode != 0:
        print(f"configure FAILED: {r.stderr}", flush=True)
        return r.returncode, "configure failed"

    # 2. Launch app
    env = os.environ.copy()
    env["PHOTO_MANAGER_HOME"] = "qa"
    env["QT_ACCESSIBILITY"] = "1"
    proc = subprocess.Popen(
        [PY, "main.py"], cwd=REPO, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print(f"launched main.py pid={proc.pid}", flush=True)
    if not _wait_for_main_window(proc.pid, timeout=8.0):
        print(
            f"WARN: main window did not appear within 8s for pid={proc.pid}; "
            f"continuing anyway — the driver's UIA connect will surface a "
            f"clearer error if the app really failed to launch.",
            flush=True,
        )

    # 3. Drive
    driver_rc = -1
    driver_err = ""
    try:
        r = subprocess.run(
            [PY, "-m", f"qa.scenarios.{name}"],
            cwd=REPO, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180,
        )
        print(r.stdout, end="", flush=True)
        if r.stderr.strip():
            print(f"DRIVER_STDERR: {r.stderr.strip()[:1000]}", flush=True)
        driver_rc = r.returncode
        if driver_rc != 0:
            driver_err = "non-zero exit"
    except subprocess.TimeoutExpired as exc:
        driver_err = "driver timeout"
        print("DRIVER TIMEOUT after 180s", flush=True)
        # Surface whatever the driver printed before hanging — by default
        # TimeoutExpired drops it on the floor, which makes hangs
        # essentially undebuggable from CI logs.
        if exc.stdout:
            partial = exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode("utf-8", "replace")
            print(f"DRIVER PARTIAL STDOUT:\n{partial}", flush=True)
        if exc.stderr:
            partial_err = exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode("utf-8", "replace")
            print(f"DRIVER PARTIAL STDERR:\n{partial_err.strip()[:2000]}", flush=True)
    except Exception as e:
        driver_err = repr(e)
        print(f"DRIVER EXC: {e!r}", flush=True)

    # 4. Close window
    try:
        _close_window()
    except Exception:
        pass

    # 5. Wait for subprocess
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        print("app did not exit cleanly, terminating", flush=True)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    return driver_rc, driver_err


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m qa.scenarios._batch",
        description=(
            "Run qa.scenarios drivers sequentially. With no args, runs every "
            "scenario in ALL_SCENARIOS. An explicit positional list always "
            "wins over --shard / --total-shards."
        ),
    )
    parser.add_argument(
        "scenarios",
        nargs="*",
        help=(
            "Explicit scenarios to run (e.g. s02_empty_folder s04_corrupted). "
            "When supplied, --shard / --total-shards are ignored."
        ),
    )
    parser.add_argument(
        "--shard",
        type=int,
        default=None,
        metavar="N",
        help="1-indexed shard number to run (use with --total-shards).",
    )
    parser.add_argument(
        "--total-shards",
        type=int,
        default=None,
        metavar="M",
        help=(
            "Total number of shards. Selection is sorted-stride; the "
            "s23a/s23b pair is kept on the same shard."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the selected scenarios and exit without launching any.",
    )
    args = parser.parse_args(argv)
    if (args.shard is None) != (args.total_shards is None):
        parser.error("--shard and --total-shards must be used together")
    return args


def main() -> int:
    args = _parse_args(sys.argv[1:])
    if args.scenarios:
        targets = args.scenarios
    elif args.shard is not None:
        targets = select_shard(ALL_SCENARIOS, args.shard, args.total_shards)
    else:
        targets = list(ALL_SCENARIOS)

    if args.dry_run:
        label = (
            f"shard {args.shard}/{args.total_shards}"
            if args.shard is not None and not args.scenarios
            else "explicit"
            if args.scenarios
            else "all"
        )
        print(
            f"dry-run ({label}): {len(targets)} scenario(s)", flush=True
        )
        for name in targets:
            print(f"  {name}", flush=True)
        return 0

    print(f"batch: running {len(targets)} scenarios: {targets}", flush=True)
    results: list[tuple[str, int, str]] = []
    for name in targets:
        rc, err = run_one(name)
        results.append((name, rc, err))

    print("\n===== BATCH SUMMARY =====", flush=True)
    ok = sum(1 for _, rc, _ in results if rc == 0)
    print(f"total: {len(results)}  ok: {ok}  failed: {len(results) - ok}")
    for name, rc, err in results:
        flag = "OK" if rc == 0 else "FAIL"
        print(f"  [{flag}] {name}  rc={rc}  err={err!r}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
