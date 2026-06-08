"""Deterministic repro for the "won't-close" bug — main python.exe stays
alive after the user closes the last window.

Launches a fresh photo-manager process, waits for its main window to
appear, sends WM_CLOSE to that window, then watches whether the
process actually exits within a generous budget. Reports the outcome
as a numeric exit code suitable for shell scripts:

  exit 0  — the process exited cleanly within budget (no bug, or fixed)
  exit 1  — the process is still alive after budget (BUG REPRODUCED)
  exit 2  — couldn't find the main window within the launch budget

Why this is the load-bearing repro: the bug we're chasing is
NOT exiftool / ProcessPool orphans (those clean up correctly per the
audit doc scanner-perf-per-device-process-pool-2026-06-08.md). The
bug is the PARENT python.exe staying idle-but-alive after every
visible window is hidden, because Qt's lastWindowClosed signal is
not firing → aboutToQuit is not firing → app.exec() never returns.

Usage:
    .venv/Scripts/python.exe scripts/probe_close_exit.py
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

# Win32 close-message constants
WM_CLOSE = 0x0010


def find_main_window(pid: int) -> int | None:
    """Return the HWND of the first visible top-level window owned by ``pid``,
    or None if none found."""
    EnumWindows = ctypes.windll.user32.EnumWindows
    GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId
    IsWindowVisible = ctypes.windll.user32.IsWindowVisible
    GetWindowTextW = ctypes.windll.user32.GetWindowTextW

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, ctypes.c_void_p)
    found = {"hwnd": None, "title": ""}

    def callback(hwnd, _lparam):
        owner_pid = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid and IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(256)
            GetWindowTextW(hwnd, title, 256)
            # Skip Qt's internal invisible helper windows. We want a
            # user-visible primary window.
            if title.value:
                found["hwnd"] = hwnd
                found["title"] = title.value
                return False  # stop enumeration
        return True

    EnumWindows(WNDENUMPROC(callback), 0)
    return found["hwnd"]


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    venv_py = repo_root / ".venv" / "Scripts" / "python.exe"
    main_py = repo_root / "main.py"
    if not venv_py.exists():
        print(f"FATAL: venv python not at {venv_py}", file=sys.stderr)
        return 3
    if not main_py.exists():
        print(f"FATAL: main.py not at {main_py}", file=sys.stderr)
        return 3

    # Launch a fresh photo-manager process.
    print(f"Launching {venv_py} {main_py}")
    # CREATE_NEW_PROCESS_GROUP so we can signal it cleanly later if needed.
    proc = subprocess.Popen(
        [str(venv_py), str(main_py)],
        cwd=str(repo_root),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    print(f"  PID = {proc.pid}")

    # Wait for the main window to become visible.
    launch_budget = 60.0
    deadline = time.monotonic() + launch_budget
    hwnd = None
    last_dump = 0.0
    while time.monotonic() < deadline:
        hwnd = find_main_window(proc.pid)
        if hwnd:
            break
        if proc.poll() is not None:
            print(f"FATAL: process exited during launch with code {proc.returncode}",
                  file=sys.stderr)
            return 3
        # Diagnostic: every 5s, dump ALL windows of this PID so we can see
        # what Qt has up.
        now = time.monotonic()
        if now - last_dump > 5:
            last_dump = now
            elapsed = launch_budget - (deadline - now)
            print(f"  [{elapsed:.0f}s] still waiting — dumping all windows of PID {proc.pid}:")
            EnumWindows = ctypes.windll.user32.EnumWindows
            GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId
            IsWindowVisible = ctypes.windll.user32.IsWindowVisible
            GetWindowTextW = ctypes.windll.user32.GetWindowTextW
            GetClassNameW = ctypes.windll.user32.GetClassNameW
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, ctypes.c_void_p)
            def cb(h, _l):
                opid = wintypes.DWORD()
                GetWindowThreadProcessId(h, ctypes.byref(opid))
                if opid.value == proc.pid:
                    title = ctypes.create_unicode_buffer(256)
                    cls = ctypes.create_unicode_buffer(256)
                    GetWindowTextW(h, title, 256)
                    GetClassNameW(cls, 256, 256)  # wrong sig but harmless
                    vis = IsWindowVisible(h)
                    GetClassNameW(h, cls, 256)
                    print(f"    HWND={hex(h)} visible={bool(vis)} title={title.value!r} class={cls.value!r}")
                return True
            EnumWindows(WNDENUMPROC(cb), 0)
        time.sleep(0.5)

    if not hwnd:
        print(f"BUG-SHIFT: never saw a visible window from PID {proc.pid} "
              f"within {launch_budget:.0f}s — can't test close-exit. Killing.",
              file=sys.stderr)
        proc.kill()
        return 2

    print(f"  found window HWND={hex(hwnd)} — sending WM_CLOSE")
    PostMessageW = ctypes.windll.user32.PostMessageW
    PostMessageW(hwnd, WM_CLOSE, 0, 0)
    t_close = time.monotonic()

    # Watch for exit. Be generous — Qt teardown + loguru.complete() can
    # take a few seconds, especially on Windows ProcessPool shutdown.
    exit_budget = 15.0
    while time.monotonic() - t_close < exit_budget:
        rc = proc.poll()
        if rc is not None:
            elapsed = time.monotonic() - t_close
            print(f"OK: process exited {elapsed:.2f}s after WM_CLOSE "
                  f"(exit code {rc})")
            return 0
        time.sleep(0.2)

    elapsed = time.monotonic() - t_close
    print(f"BUG REPRODUCED: process PID={proc.pid} still alive {elapsed:.1f}s "
          f"after WM_CLOSE on visible window. Killing for cleanup.",
          file=sys.stderr)
    proc.kill()
    proc.wait(timeout=5)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
