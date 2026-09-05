"""Unit tests for the dual-mode entry point (launcher.py).

These cover the real decision + contract logic without opening a window:
- env → mode dispatch (Qt default vs web shell),
- the WebView2-runtime loud-fail (a blank-window failure mode),
- the single-worker / daemon-thread / factory uvicorn contract,
- health-gated startup and the correct loopback window URL.

The actual native window is validated by a manual Windows smoke, not here.
"""

from __future__ import annotations

import sys

import httpx
import pytest

import launcher


# --- env parsing -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, False),
        ("", False),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("off", False),
        ("1", True),
        ("true", True),
        ("yes", True),
        ("  1  ", True),
    ],
)
def test_env_flag_truthy(raw, expected):
    assert launcher._env_flag_truthy(raw) is expected


def test_web_enabled_reads_env():
    assert launcher._web_enabled({"PHOTO_MANAGER_WEB": "1"}) is True
    assert launcher._web_enabled({"PHOTO_MANAGER_WEB": "0"}) is False
    assert launcher._web_enabled({}) is False


def test_web_port_default_and_override():
    assert launcher._web_port({}) == launcher._DEFAULT_WEB_PORT
    assert launcher._web_port({"PHOTO_MANAGER_WEB_PORT": "9123"}) == 9123
    # A non-integer override must fall back to the default, not crash launch.
    assert launcher._web_port({"PHOTO_MANAGER_WEB_PORT": "nope"}) == launcher._DEFAULT_WEB_PORT


# --- WebView2 runtime gate -------------------------------------------------


def test_ensure_webview2_ok_when_present(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    launcher._ensure_webview2(version_query=lambda: "120.0.2210.61")  # no raise


def test_ensure_webview2_raises_when_missing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="WebView2 Runtime is required"):
        launcher._ensure_webview2(version_query=lambda: None)


def test_ensure_webview2_noop_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def _boom():
        raise AssertionError("version_query must not run on non-Windows")

    launcher._ensure_webview2(version_query=_boom)  # no raise, query untouched


def test_query_webview2_version_none_off_windows(monkeypatch):
    # Contract: the WebView2 registry probe does not apply off Windows.
    monkeypatch.setattr(sys, "platform", "linux")
    assert launcher._query_webview2_version() is None


@pytest.mark.skipif(sys.platform != "win32", reason="winreg is Windows-only")
def test_query_webview2_version_reads_pv(monkeypatch):
    import winreg

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _Key())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *a: ("120.0.2210.61", winreg.REG_SZ))
    assert launcher._query_webview2_version() == "120.0.2210.61"


@pytest.mark.skipif(sys.platform != "win32", reason="winreg is Windows-only")
def test_query_webview2_version_none_when_absent(monkeypatch):
    import winreg

    def _missing(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(winreg, "OpenKey", _missing)
    assert launcher._query_webview2_version() is None


@pytest.mark.skipif(sys.platform != "win32", reason="winreg is Windows-only")
def test_query_webview2_version_rejects_placeholder(monkeypatch):
    import winreg

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    # EdgeUpdate leaves a "0.0.0.0" pv behind when the runtime is not really
    # installed — that must read as absent, not present.
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _Key())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *a: ("0.0.0.0", winreg.REG_SZ))
    assert launcher._query_webview2_version() is None


# --- uvicorn boot contract -------------------------------------------------


def test_serve_uvicorn_is_single_worker_daemon_factory(monkeypatch):
    import threading

    import uvicorn

    captured = {}

    def _fake_config(app, **kwargs):
        captured["app"] = app
        captured.update(kwargs)
        return "CONFIG"

    class _FakeServer:
        def __init__(self, config):
            captured["server_config"] = config

        def run(self):  # target of the (faked) thread
            pass

    class _FakeThread:
        def __init__(self, target, name=None, daemon=None):
            captured["thread"] = {"target": target, "name": name, "daemon": daemon}
            self.started = False

        def start(self):
            self.started = True

    monkeypatch.setattr(uvicorn, "Config", _fake_config)
    monkeypatch.setattr(uvicorn, "Server", _FakeServer)
    monkeypatch.setattr(threading, "Thread", _FakeThread)

    server, thread = launcher._serve_uvicorn(8765)

    assert captured["app"] == "app.web.main:create_app"
    assert captured["factory"] is True
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8765
    assert captured["workers"] == 1  # single-worker: execute-lock is per-process
    assert captured["reload"] is False
    assert captured["thread"]["daemon"] is True
    assert thread.started is True


# --- health gate -----------------------------------------------------------


def test_wait_for_health_returns_true_on_200(monkeypatch):
    class _Resp:
        status_code = 200

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    assert launcher._wait_for_health(8765, timeout=1.0) is True


def test_wait_for_health_times_out(monkeypatch):
    def _refused(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "get", _refused)
    assert launcher._wait_for_health(8765, timeout=0.05, interval=0.01) is False


# --- run_web orchestration -------------------------------------------------


class _RecordingServer:
    def __init__(self):
        self.should_exit = False


class _RecordingThread:
    def __init__(self):
        self.joined = False

    def join(self, timeout=None):
        self.joined = True


def test_run_web_opens_window_at_loopback_and_shuts_down(monkeypatch):
    import webview

    server, thread = _RecordingServer(), _RecordingThread()
    windows = []

    monkeypatch.setattr(launcher, "_ensure_webview2", lambda: None)
    monkeypatch.setattr(launcher, "_serve_uvicorn", lambda port: (server, thread))
    monkeypatch.setattr(launcher, "_wait_for_health", lambda port: True)
    monkeypatch.setattr(webview, "create_window", lambda *a, **k: windows.append((a, k)))
    monkeypatch.setattr(webview, "start", lambda *a, **k: None)

    assert launcher.run_web(port=8765) == 0
    (title, url), kwargs = windows[0]
    assert title == "Photo Manager"
    assert url == "http://127.0.0.1:8765"  # loopback only, never 0.0.0.0
    assert server.should_exit is True  # graceful uvicorn shutdown on window close
    assert thread.joined is True


def test_run_web_raises_when_server_never_healthy(monkeypatch):
    import webview

    opened = []
    monkeypatch.setattr(launcher, "_ensure_webview2", lambda: None)
    monkeypatch.setattr(
        launcher, "_serve_uvicorn", lambda port: (_RecordingServer(), _RecordingThread())
    )
    monkeypatch.setattr(launcher, "_wait_for_health", lambda port: False)
    monkeypatch.setattr(webview, "create_window", lambda *a, **k: opened.append(a))

    with pytest.raises(RuntimeError, match="did not become healthy"):
        launcher.run_web(port=8765)
    assert opened == []  # never open a window over a dead server


# --- top-level dispatch ----------------------------------------------------


def test_main_dispatches_to_qt_by_default(monkeypatch):
    monkeypatch.delenv("PHOTO_MANAGER_WEB", raising=False)
    monkeypatch.setattr(launcher, "_run_qt", lambda: 42)
    monkeypatch.setattr(
        launcher, "run_web", lambda *a, **k: pytest.fail("web path taken by default")
    )
    assert launcher.main() == 42


def test_main_dispatches_to_web_when_enabled(monkeypatch):
    monkeypatch.setenv("PHOTO_MANAGER_WEB", "1")
    monkeypatch.setattr(launcher, "run_web", lambda: 7)
    monkeypatch.setattr(
        launcher, "_run_qt", lambda: pytest.fail("qt path taken when web enabled")
    )
    assert launcher.main() == 7


# --- web smoke (packaging CI, #772) ----------------------------------------


def test_run_web_smoke_healthy_returns_zero_and_shuts_down(monkeypatch):
    server, thread = _RecordingServer(), _RecordingThread()
    server.started = True  # uvicorn sets this after a successful bind+startup
    monkeypatch.setattr(launcher, "_serve_uvicorn", lambda port: (server, thread))
    monkeypatch.setattr(launcher, "_wait_for_health", lambda port: True)

    assert launcher.run_web_smoke(port=8765) == 0
    assert server.should_exit is True  # graceful shutdown, not process teardown
    assert thread.joined is True


def test_run_web_smoke_rejects_foreign_server_on_port(monkeypatch):
    # Regression (found live): our uvicorn failed to bind because ANOTHER
    # photo-manager instance owned the port, its /api/health answered 200,
    # and the smoke false-passed. Health 200 alone is not proof that OUR
    # server started — Server.started must also be true.
    server, thread = _RecordingServer(), _RecordingThread()
    server.started = False  # bind failed; uvicorn never started
    monkeypatch.setattr(launcher, "_serve_uvicorn", lambda port: (server, thread))
    monkeypatch.setattr(launcher, "_wait_for_health", lambda port: True)

    assert launcher.run_web_smoke(port=8765) == 1


def test_run_web_smoke_unhealthy_returns_one_but_still_shuts_down(monkeypatch):
    server, thread = _RecordingServer(), _RecordingThread()
    monkeypatch.setattr(launcher, "_serve_uvicorn", lambda port: (server, thread))
    monkeypatch.setattr(launcher, "_wait_for_health", lambda port: False)

    assert launcher.run_web_smoke(port=8765) == 1
    assert server.should_exit is True
    assert thread.joined is True


def test_main_dispatches_to_smoke_before_web_or_qt(monkeypatch):
    # Smoke wins even when PHOTO_MANAGER_WEB is also set: CI sets exactly
    # one knob and must never open a window or the Qt app.
    monkeypatch.setenv("PHOTO_MANAGER_WEB_SMOKE", "1")
    monkeypatch.setenv("PHOTO_MANAGER_WEB", "1")
    monkeypatch.setattr(launcher, "run_web_smoke", lambda: 0)
    monkeypatch.setattr(
        launcher, "run_web", lambda *a, **k: pytest.fail("window path taken in smoke")
    )
    monkeypatch.setattr(
        launcher, "_run_qt", lambda: pytest.fail("qt path taken in smoke")
    )
    assert launcher.main() == 0


def test_main_surfaces_fatal_and_reraises_on_web_runtime_error(monkeypatch):
    # console=False in the frozen exe means a raised RuntimeError is
    # invisible — main() must route it through _surface_fatal, then
    # still exit non-zero via the re-raise.
    monkeypatch.delenv("PHOTO_MANAGER_WEB_SMOKE", raising=False)
    monkeypatch.setenv("PHOTO_MANAGER_WEB", "1")
    surfaced = []

    def _boom():
        raise RuntimeError("WebView2 runtime missing")

    monkeypatch.setattr(launcher, "run_web", _boom)
    monkeypatch.setattr(launcher, "_surface_fatal", surfaced.append)

    with pytest.raises(RuntimeError, match="WebView2 runtime missing"):
        launcher.main()
    assert surfaced == ["WebView2 runtime missing"]


def test_surface_fatal_is_noop_when_not_frozen(monkeypatch):
    # Dev runs keep the plain traceback; the MessageBox fires only for the
    # frozen Windows exe (sys.frozen is absent in a normal interpreter).
    monkeypatch.setattr(sys, "platform", "win32")
    assert not getattr(sys, "frozen", False)
    launcher._surface_fatal("boom")  # must not raise, must not block
