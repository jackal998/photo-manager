"""Unified entry point: Qt desktop app (default) or pywebview web shell.

Launch modes
------------
- **Default** (``PHOTO_MANAGER_WEB`` unset / falsey): boots the existing
  PySide6 desktop app via ``main.main()`` — byte-for-byte the previous
  behaviour, so the classic Qt path is untouched and remains the default.
- **Web shell** (``PHOTO_MANAGER_WEB`` truthy): starts the FastAPI app
  under uvicorn on a loopback port in a daemon thread, then opens a native
  Edge WebView2 window (via pywebview) pointed at it. This is the desktop
  packaging of the localhost web app — the replacement shell that lets the
  Qt UI be removed in a later phase.

The Qt and web paths never both run in one process; the env var selects
exactly one. ``pywebview`` (and its Windows ``.NET``/pythonnet subtree) is
imported *lazily* inside the web path, so importing this module and the Qt
path carry no pywebview dependency.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Callable, Optional

# Loopback host + default port for the embedded web server. 127.0.0.1
# (never 0.0.0.0) keeps the API unreachable off-box — the app is a
# single-user desktop shell, not a network service.
_WEB_HOST = "127.0.0.1"
_DEFAULT_WEB_PORT = 8765

# Evergreen WebView2 Runtime client GUID (Microsoft-documented). A non-empty
# ``pv`` value under any of the EdgeUpdate\Clients keys means the runtime is
# installed. See the "Detect if a suitable WebView2 Runtime is already
# installed" section of the WebView2 distribution docs.
_WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def _env_flag_truthy(raw: Optional[str]) -> bool:
    """True unless *raw* is None or an explicit falsey string.

    Mirrors the app's other boolean env flags: mere presence opts in, but
    an explicit "0"/"false"/"no"/"off" (case-insensitive) turns it back off.
    """
    if raw is None:
        return False
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


def _web_enabled(env: Optional[dict] = None) -> bool:
    """True when ``PHOTO_MANAGER_WEB`` selects the web-shell path."""
    return _env_flag_truthy(
        (env if env is not None else os.environ).get("PHOTO_MANAGER_WEB")
    )


def _web_smoke_enabled(env: Optional[dict] = None) -> bool:
    """True when ``PHOTO_MANAGER_WEB_SMOKE`` selects the CI smoke path."""
    return _env_flag_truthy(
        (env if env is not None else os.environ).get("PHOTO_MANAGER_WEB_SMOKE")
    )


def _web_port(env: Optional[dict] = None) -> int:
    """Loopback port for the embedded server.

    ``PHOTO_MANAGER_WEB_PORT`` overrides the default; a non-integer value
    falls back to the default rather than crashing the launch.
    """
    raw = (env if env is not None else os.environ).get("PHOTO_MANAGER_WEB_PORT")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return _DEFAULT_WEB_PORT


def _query_webview2_version() -> Optional[str]:
    """Return the installed Evergreen WebView2 Runtime version, or None.

    Reads the Microsoft-documented EdgeUpdate ``pv`` value from the
    per-machine (HKLM, including the WOW6432Node 32-bit view) and per-user
    (HKCU) hives. Windows-only: returns None on other platforms, where the
    web shell uses a non-WebView2 backend so the check does not apply.
    """
    if sys.platform != "win32":
        return None
    import winreg  # Windows-only stdlib; imported lazily so non-win32 imports.

    clients = r"Microsoft\EdgeUpdate\Clients" + "\\" + _WEBVIEW2_CLIENT_GUID
    for hive, path in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node" + "\\" + clients),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE" + "\\" + clients),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE" + "\\" + clients),
    ):
        try:
            with winreg.OpenKey(hive, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return str(version)
    return None


def _ensure_webview2(
    version_query: Callable[[], Optional[str]] = _query_webview2_version,
) -> None:
    """Fail loudly if the WebView2 runtime is required but missing.

    On Windows the web shell renders through the Edge WebView2 runtime; when
    it is absent the pywebview window comes up blank with no obvious cause.
    We check up front and raise a clear, actionable error rather than leaving
    the user staring at a silent black window. No-op on non-Windows (a
    different backend is used there).
    """
    if sys.platform != "win32":
        return
    if version_query() is None:
        raise RuntimeError(
            "The Microsoft Edge WebView2 Runtime is required for the web "
            "shell but was not found. Install the Evergreen runtime from "
            "https://developer.microsoft.com/microsoft-edge/webview2/ and "
            "relaunch, or run the classic desktop app (leave "
            "PHOTO_MANAGER_WEB unset)."
        )


def _serve_uvicorn(port: int):
    """Start uvicorn for the web app in a daemon thread; return (server, thread).

    Uses ``uvicorn.Server`` (not ``uvicorn.run``) so the GUI event loop can
    own the main thread — pywebview's window loop must run on the main thread
    on Windows/macOS. A single worker is mandatory: ``app.web.main`` keeps a
    per-process ``asyncio.Lock`` for destructive ops that would not serialise
    across worker processes.
    """
    import threading

    import uvicorn

    config = uvicorn.Config(
        "app.web.main:create_app",
        factory=True,
        host=_WEB_HOST,
        port=port,
        reload=False,
        workers=1,
        log_level="info",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    return server, thread


def _wait_for_health(port: int, timeout: float = 30.0, interval: float = 0.25) -> bool:
    """Poll ``GET /api/health`` until it answers 200 or the timeout elapses."""
    import httpx

    url = f"http://{_WEB_HOST}:{port}/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(interval)
    return False


def run_web(port: Optional[int] = None) -> int:
    """Boot the embedded server + a native WebView2 window. Blocks until close."""
    import webview  # lazy: keeps pywebview/.NET off the import + Qt path.
    from loguru import logger

    from infrastructure.logging import init_logging

    init_logging()
    _ensure_webview2()

    resolved_port = port if port is not None else _web_port()
    server, thread = _serve_uvicorn(resolved_port)
    if not _wait_for_health(resolved_port):
        raise RuntimeError(
            f"Embedded web server did not become healthy on "
            f"{_WEB_HOST}:{resolved_port} within the startup timeout."
        )

    logger.info("Opening WebView2 window at http://{}:{}", _WEB_HOST, resolved_port)
    webview.create_window(
        "Photo Manager",
        f"http://{_WEB_HOST}:{resolved_port}",
        width=1280,
        height=800,
    )
    webview.start()

    # Window closed → ask uvicorn to shut down gracefully so app.web.main's
    # lifespan finally-block runs (scan-cancel + WIC executor drain) instead
    # of the daemon thread being torn down mid-flight at process exit.
    server.should_exit = True
    thread.join(timeout=5.0)
    return 0


def run_web_smoke(port: Optional[int] = None) -> int:
    """Boot the embedded server, wait for ``/api/health``, shut down. No window.

    Packaging smoke for the frozen bundle (release.yml): proves the FastAPI
    app, its bundled ``frontend/dist``, and uvicorn all survive freezing —
    without needing a display or the WebView2 runtime on the CI runner. The
    exit code is the verdict; the windowed exe has no visible stdout.
    """
    from loguru import logger

    from infrastructure.logging import init_logging

    init_logging()
    resolved_port = port if port is not None else _web_port()
    server, thread = _serve_uvicorn(resolved_port)
    # BOTH conditions: /api/health answering 200 is not enough on its own —
    # if OUR uvicorn failed to bind (port already taken by another instance),
    # the probe would hit the OTHER process's healthy endpoint and false-pass.
    # uvicorn sets Server.started only after ITS startup (bind included).
    healthy = _wait_for_health(resolved_port) and getattr(server, "started", False)
    server.should_exit = True
    thread.join(timeout=5.0)
    if not healthy:
        logger.error(
            "web smoke: server never became healthy on {}:{}",
            _WEB_HOST,
            resolved_port,
        )
        return 1
    logger.info("web smoke: healthy on {}:{} — OK", _WEB_HOST, resolved_port)
    return 0


def _surface_fatal(message: str) -> None:
    """Show a startup-fatal error where a windowed frozen exe can't print.

    The release bundle is built with ``console=False``, so a raised
    RuntimeError (missing WebView2, unhealthy embedded server, missing
    bundled frontend) would otherwise kill the process with no visible
    trace. Only fires for the frozen Windows exe; dev runs keep the
    ordinary traceback.
    """
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Photo Manager", 0x10)


def _run_qt() -> int:
    """Delegate to the classic PySide6 desktop entry point."""
    from main import main as qt_main

    return qt_main()


def main() -> int:
    """Dispatch to the web smoke, web shell, or Qt desktop app based on env."""
    if _web_smoke_enabled():
        return run_web_smoke()
    if _web_enabled():
        try:
            return run_web()
        except RuntimeError as exc:
            _surface_fatal(str(exc))
            raise
    return _run_qt()


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
