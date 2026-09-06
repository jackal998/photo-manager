"""Shared pytest fixtures for the photo-manager test suite."""

from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    # Playwright types are only available when playwright is installed.
    # Never import at runtime here — the module must load cleanly in CI
    # where playwright is absent.
    from playwright.sync_api import Page


@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication for tests that need a Qt event loop."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _isolate_unc_resolution(monkeypatch):
    """Keep ``device_key``'s NAS-server grouping (#565) deterministic in tests.

    ``device_key`` memoises drive-letter→UNC-server in a module-level
    ``_unc_cache`` and, by default, resolves via the real
    ``WNetGetConnectionW``. On a dev machine where a test drive letter (e.g.
    ``J:``) is a live NAS mapping, the real call leaks the actual server
    (``\\\\LINXIAOYUN``) into the bucket key, and the memo persists across
    tests — so a later test that mocks ``is_remote_drive`` only for ``"J:"``
    sees a ``\\\\LINXIAOYUN`` key it doesn't recognise and the per-device
    worker count regresses 8→4. CI never hit this (no mapped drives there),
    so the suite passed in CI but failed on the dev machine in full-file runs.

    Force the default resolver to a no-op and clear the cache around every
    test, so grouping resolves to the bare drive letter — matching CI. Tests
    that exercise the resolution logic itself inject their own resolver via
    ``device_key(unc_resolver=...)`` and are unaffected by this patch.

    #622 Phase 2 moved ``device_key`` and its memo to
    ``infrastructure.device_key`` (``scanner.workers`` re-exports them). This
    fixture MUST patch the defining module: patching the re-exporting one
    would rebind a name ``device_key`` no longer reads, so the fixture would
    go quietly inert and the real ``WNetGetConnectionW`` would resolve a live
    ``J:`` back to ``\\\\LINXIAOYUN`` on the dev machine — the exact
    dev-passes/CI-differs asymmetry described above.
    """
    import infrastructure.device_key as _dk

    _dk._unc_cache.clear()
    monkeypatch.setattr(_dk, "_resolve_unc_via_win32", lambda letter: None)
    yield
    _dk._unc_cache.clear()


@pytest.fixture(autouse=True)
def _reset_source_mtime_ttl_cache():
    """Drop the module-global source-mtime TTL cache between tests (#622 Phase 2).

    ``infrastructure.image_service._mtime_cache`` memoises each path's mtime
    for 5 s of real monotonic time. Without this reset, one test's stat of a
    ``tmp_path`` file would still be cached when the next test writes a
    different file to the same reused path within 5 s — an order-dependent
    failure that only shows up in a full-suite run.
    """
    import infrastructure.image_service as _img

    _img._mtime_cache.clear()
    yield
    _img._mtime_cache.clear()


@pytest.fixture(autouse=True)
def _revive_wic_executor():
    """Undo the process-wide WIC-executor shutdown a lifespan test performs (#781).

    ``infrastructure.image_service`` owns a module-global STA thread pool
    (``_wic_executor``, image_service.py:139) that ``ImageService.
    _load_via_shell_thumbnail`` submits to. Every test that enters the FastAPI
    app's lifespan — ``with TestClient(app)`` in the ten ``tests/test_web_*``
    modules — runs the real ``_drain_wic_executor`` on exit (app/web/main.py:101),
    which calls ``ThreadPoolExecutor.shutdown``. The pool is per-process and
    never rebuilt, so any *later* test that reaches the Shell/WIC path gets
    ``RuntimeError: cannot schedule new futures after shutdown``. With the
    default collection order that victim is
    ``tests/test_image_service.py::TestBytesContract::
    test_placeholder_returned_when_load_fails``; which test loses depends purely
    on file order, which is why it reads as a flake.

    Same shape, same remedy as ``_isolate_unc_resolution`` above: reset the
    module global around each test. The replacement is built from the dead
    executor's own construction parameters, so it is production's configuration
    by definition — in particular ``thread_name_prefix='wic-sta'``, which
    ``_shell_thumbnail_sync``'s STA assertion (image_service.py:779) checks.

    This cannot mask a real failure: it runs only in teardown, only when the
    pool is already shut down, and it does not touch a live pool. A genuine
    "we shut the pool down and then used it" bug in production code would still
    fail inside the test that does it.
    """
    yield

    mod = sys.modules.get("infrastructure.image_service")
    if mod is None:  # module never imported by this test — nothing to revive
        return

    from concurrent.futures import ThreadPoolExecutor

    executor = mod._wic_executor
    if not isinstance(executor, ThreadPoolExecutor):
        # A test swapped in its own double; its own teardown restores the real
        # pool, and reviving someone else's stub would be wrong.
        return

    # Fail closed: if CPython ever renames these, revive silently becoming a
    # no-op would resurrect the #781 flake with no signal at all.
    missing = [
        attr
        for attr in ("_shutdown", "_max_workers", "_thread_name_prefix",
                     "_initializer", "_initargs")
        if not hasattr(executor, attr)
    ]
    if missing:
        raise RuntimeError(
            f"#781 fixture cannot inspect ThreadPoolExecutor internals {missing} "
            f"on {sys.version_info[:3]} — update tests/conftest.py::"
            "_revive_wic_executor instead of letting the order-dependent "
            "_wic_executor flake come back silently"
        )

    if not executor._shutdown:
        return

    mod._wic_executor = ThreadPoolExecutor(
        max_workers=executor._max_workers,
        thread_name_prefix=executor._thread_name_prefix,
        initializer=executor._initializer,
        initargs=executor._initargs,
    )


# ---------------------------------------------------------------------------
# Playwright live-server fixture — web_probe tests only
# ---------------------------------------------------------------------------
# This fixture is intentionally placed here (top-level conftest.py) so that
# tests/test_web_dom_probes.py can discover it without pytest needing a
# conftest in the tests/ sub-directory that imports playwright at module
# level.  All playwright imports are deferred to fixture body so the main
# CI run (no playwright) never sees an ImportError.

_REPO = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    """Return a free TCP port on 127.0.0.1 by binding to :0 and releasing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, timeout: float = 8.0) -> bool:
    """Poll GET /api/health until it returns 200 or *timeout* expires."""
    import urllib.request
    import urllib.error

    url = f"{base_url}/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


@pytest.fixture(scope="session")
def pw_empty_state_page():  # type: ignore[return]  # -> Page | None via skip
    """Session-scoped Playwright page fixture for web_probe tests.

    Preconditions (skip cleanly when absent):
    - ``playwright`` Python package is importable.
    - Chromium is installed (``playwright install chromium``).
    - ``frontend/dist/index.html`` exists (frontend has been built).

    The fixture:
    1. Picks a free TCP port.
    2. Starts a uvicorn subprocess serving ``app.web.main:create_app``
       (factory mode) on 127.0.0.1:<port>.
    3. Polls ``GET /api/health`` for up to 8 s; skips with a clear message
       if the server never comes up.
    4. Launches Chromium headless via sync_playwright; navigates to ``/``.
    5. Waits for ``[data-testid='main-status-bar']`` (the always-present
       footer element) to confirm the React shell has hydrated.
    6. Yields the ``Page`` for probe tests to inspect.

    Teardown (always, no zombies):
    - Closes the page, browser context, browser, and stops playwright.
    - Terminates the uvicorn subprocess; sends SIGKILL on timeout.

    The fixture is ``scope="session"`` because only one probe uses it now —
    a single server launch covers all ``web_probe`` tests in this session.
    """
    # --- precondition: playwright installed ---
    pw_mod = pytest.importorskip(
        "playwright",
        reason="playwright not installed — skipping web_probe (pip install playwright && playwright install chromium)",
    )
    _ = pw_mod  # importorskip returns the module; we just needed the skip gate.

    # --- precondition: frontend/dist built ---
    index_html = _REPO / "frontend" / "dist" / "index.html"
    if not index_html.exists():
        pytest.skip(
            f"frontend/dist/index.html not found at {index_html} — "
            "build the frontend first (npm run build inside frontend/) "
            "to run web_probe tests"
        )

    # All remaining work is inside a single try/finally so every resource
    # is cleaned up regardless of how we exit (skip, exception, or normal).
    # Variables are pre-declared so the finally block never hits NameError.
    proc = None
    stderr_file = None
    pw = None
    browser = None
    context = None
    page = None

    try:
        # --- start uvicorn subprocess ---
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        uvicorn_cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.web.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ]
        # Capture stderr to a temp file (not a PIPE — avoids a buffer-fill
        # deadlock on a session-lived server) so a startup crash can be
        # surfaced as a FAIL instead of a silent skip.
        stderr_file = tempfile.TemporaryFile()
        proc = subprocess.Popen(
            uvicorn_cmd,
            cwd=str(_REPO),
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
        )

        # --- wait for server readiness ---
        if not _wait_for_health(base_url, timeout=20.0):
            # Distinguish a CRASHED server (a real regression — e.g. app.web.main
            # failed to import) from a merely-slow one. A crash must FAIL loudly
            # so the advisory CI job still shows red; a slow start may SKIP.
            if proc.poll() is not None:
                stderr_file.seek(0)
                err = stderr_file.read().decode("utf-8", "replace")
                pytest.fail(
                    f"uvicorn exited (code {proc.returncode}) before /api/health "
                    f"came up — app.web.main likely failed to import:\n{err[-2000:]}"
                )
            pytest.skip(
                f"uvicorn server at {base_url} did not respond to /api/health "
                "within 20 s — check server logs for startup errors"
            )

        # --- launch Playwright chromium ---
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import]
        except ImportError:
            pytest.skip("playwright sync_api not importable — chromium may not be installed")

        pw = sync_playwright().__enter__()
        try:
            browser = pw.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(
                f"Chromium failed to launch (not installed?): {exc}\n"
                "Run: python -m playwright install chromium"
            )

        context = browser.new_context(base_url=base_url)
        page = context.new_page()

        # Navigate and wait for the app shell to hydrate.
        page.goto("/")
        try:
            page.wait_for_selector("[data-testid='main-status-bar']", timeout=15_000)
            # Also wait for main-empty-state, which renders in the same
            # synchronous pass today (App.tsx renders it when noManifest).
            # Waiting explicitly makes the probe resilient if a future refactor
            # moves this element behind an async API call: instead of a false
            # "testid missing" failure we get a clean timeout → assertion error.
            page.wait_for_selector("[data-testid='main-empty-state']", timeout=5_000)
        except Exception:
            # Shell did not hydrate in time — still yield; the probe will fail
            # with a meaningful assertion error rather than a conftest crash.
            pass

        yield page

    finally:
        # Teardown — always runs, even on skip / exception.
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
        # Terminate uvicorn.
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        # Close the captured-stderr temp file (after the subprocess is gone).
        if stderr_file is not None:
            try:
                stderr_file.close()
            except Exception:
                pass
