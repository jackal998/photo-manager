"""Run all qa.scenarios.sNN drivers sequentially in a single process.

For each scenario:
  1. configure qa/settings.json (writes scenario-specific source list)
  2. launch main.py as a subprocess
  3. poll until the main window is visible (max 8s; typically <2s)
  4. run the driver
  5. close the window via UIA
  6. wait for the subprocess to exit (or terminate if stuck)

Usage:  .venv/Scripts/python.exe -m qa.scenarios._batch [scenarios...]
        .venv/Scripts/python.exe -m qa.scenarios._batch s02_empty_folder s04_corrupted
"""
from __future__ import annotations

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
]


def _close_window() -> None:
    code = (
        "from pywinauto import Application;"
        "import sys;"
        "Application(backend='uia').connect(title_re=r'.*Photo Manager.*', timeout=3).top_window().close()"
    )
    subprocess.run([PY, "-c", code], cwd=REPO, capture_output=True, timeout=10)


_user32 = ctypes.windll.user32
_WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
)


def _wait_for_main_window(pid: int, timeout: float = 8.0) -> bool:
    """Poll until photo-manager's main window is visible for ``pid``.

    Replaces a fixed ``time.sleep`` after launching ``main.py``. The
    window typically appears in ~0.5–1.5 s on a real desktop and 2–4 s
    on hosted CI runners — fixed sleeps either over-wait or are too
    short under runner contention. Polling adapts to whichever side
    you're on and saves cumulative time across the batch (~2 s × 21
    scenarios ≈ 40 s on a green run).

    Uses ctypes ``EnumWindows`` rather than spawning pywinauto so the
    cost per check is microseconds, not subprocess-startup overhead.
    Returns ``True`` if the window appeared within ``timeout``,
    ``False`` if the timeout expired (caller logs a warning; the
    driver's own UIA ``connect`` will then surface a clearer error).
    """
    deadline = time.monotonic() + timeout
    found = [False]

    def cb(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        ppid = ctypes.c_ulong()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(ppid))
        if ppid.value != pid:
            return True
        title = ctypes.create_unicode_buffer(256)
        _user32.GetWindowTextW(hwnd, title, 256)
        if "Photo Manager" in title.value:
            found[0] = True
            return False
        return True

    while time.monotonic() < deadline:
        found[0] = False
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
        print(f"DRIVER TIMEOUT after 180s", flush=True)
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
        print(f"app did not exit cleanly, terminating", flush=True)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    return driver_rc, driver_err


def main() -> int:
    targets = sys.argv[1:] or ALL_SCENARIOS
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
