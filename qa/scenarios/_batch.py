"""Run all qa.scenarios.sNN drivers sequentially in a single process.

For each scenario:
  1. configure qa/settings.json (writes scenario-specific source list)
  2. launch main.py as a subprocess
  3. wait 3s for the window
  4. run the driver
  5. close the window via UIA
  6. wait for the subprocess to exit (or terminate if stuck)

Usage:  .venv/Scripts/python.exe -m qa.scenarios._batch [scenarios...]
        .venv/Scripts/python.exe -m qa.scenarios._batch s02_empty_folder s04_corrupted
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = str(REPO / ".venv" / "Scripts" / "python.exe")

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
    "s21_list_menu_remove",
]


def _close_window() -> None:
    code = (
        "from pywinauto import Application;"
        "import sys;"
        "Application(backend='uia').connect(title_re=r'.*Photo Manager.*', timeout=3).top_window().close()"
    )
    subprocess.run([PY, "-c", code], cwd=REPO, capture_output=True, timeout=10)


def run_one(name: str) -> tuple[int, str]:
    print(f"\n===== {name} =====", flush=True)
    # 1. Configure
    r = subprocess.run(
        [PY, "-m", "qa.scenarios.configure", name],
        cwd=REPO, capture_output=True, text=True, timeout=15,
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
    time.sleep(3.5)

    # 3. Drive
    driver_rc = -1
    driver_err = ""
    try:
        r = subprocess.run(
            [PY, "-m", f"qa.scenarios.{name}"],
            cwd=REPO, capture_output=True, text=True, timeout=180,
        )
        print(r.stdout, end="", flush=True)
        if r.stderr.strip():
            print(f"DRIVER_STDERR: {r.stderr.strip()[:1000]}", flush=True)
        driver_rc = r.returncode
        if driver_rc != 0:
            driver_err = "non-zero exit"
    except subprocess.TimeoutExpired:
        driver_err = "driver timeout"
        print(f"DRIVER TIMEOUT after 180s", flush=True)
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
