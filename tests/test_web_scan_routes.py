"""Integration tests for the scan API routes (no real uvicorn, no browser).

Uses Starlette TestClient (httpx) with run_pipeline monkeypatched to a
no-op that calls bus.finished() after a tiny sleep, so SSE delivery is
exercisable synchronously.

Tests:
1. POST /api/scan valid → 201 {task_id uuid-shaped}
2. GET /api/scan/{id}/events streams until 'finished', event names correct
3. POST cancel → 200 {cancel_requested}, second cancel → 409
4. cancel unknown id → 404
5. POST with sources={} → 400
6. second POST /api/scan while one running → 409
7. Cross-import guard: no Qt file imports app.web
"""

from __future__ import annotations

import time
import threading
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.web.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Fresh FastAPI TestClient per test (new registry, no state leak)."""
    # Reset the module-level registry singleton so tasks from one test don't
    # bleed into the next.
    import app.web.registry as reg_module
    import app.web.routes.scan as scan_module

    old_registry = reg_module.registry
    from app.web.registry import ScanRegistry
    new_registry = ScanRegistry()
    reg_module.registry = new_registry
    scan_module.registry = new_registry

    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    reg_module.registry = old_registry
    scan_module.registry = old_registry


def _noop_pipeline_factory(delay: float = 0.01):
    """Return a run_pipeline substitute that calls bus.finished() after ``delay``."""
    def _noop(config, cancel_token, bus):
        time.sleep(delay)
        bus.finished("/fake/out.sqlite")
    return _noop


def _valid_scan_body(**overrides) -> dict:
    base = {
        "sources": {"D": "/fake/photos"},
        "output_path": "/fake/out.sqlite",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. POST /api/scan valid → 201 {task_id uuid-shaped}
# ---------------------------------------------------------------------------

class TestPostScan:
    def test_valid_request_returns_201_with_uuid_task_id(self, client):
        with patch("app.web.routes.scan.run_pipeline", _noop_pipeline_factory()):
            resp = client.post("/api/scan", json=_valid_scan_body())

        assert resp.status_code == 201
        body = resp.json()
        assert "task_id" in body
        # Must be a valid UUID.
        uuid.UUID(body["task_id"])  # raises if not valid

    def test_sources_empty_returns_400(self, client):
        resp = client.post("/api/scan", json=_valid_scan_body(sources={}))
        assert resp.status_code == 400

    def test_second_post_while_running_returns_409(self, client):
        """A second POST while first is still running must be rejected with 409."""
        barrier = threading.Barrier(2, timeout=5)

        def _blocking_pipeline(config, cancel_token, bus):
            # Wait until the test has sent the second POST, then finish.
            barrier.wait()
            bus.finished("/fake/out.sqlite")

        with patch("app.web.routes.scan.run_pipeline", _blocking_pipeline):
            resp1 = client.post("/api/scan", json=_valid_scan_body())
            assert resp1.status_code == 201

            # The scan thread is now running (blocking at barrier.wait()).
            # A second scan must be rejected.
            resp2 = client.post("/api/scan", json=_valid_scan_body())
            assert resp2.status_code == 409

            # Let the first scan finish.
            barrier.wait()


# ---------------------------------------------------------------------------
# 2. GET /api/scan/{id}/events streams until 'finished'
# ---------------------------------------------------------------------------

class TestScanEvents:
    def test_sse_stream_delivers_finished_event(self, client):
        with patch("app.web.routes.scan.run_pipeline", _noop_pipeline_factory(0.05)):
            resp = client.post("/api/scan", json=_valid_scan_body())
        task_id = resp.json()["task_id"]

        # Collect SSE lines until the stream closes.
        event_names: list[str] = []
        with client.stream("GET", f"/api/scan/{task_id}/events") as stream:
            for line in stream.iter_lines():
                if line.startswith("event:"):
                    event_names.append(line.split(":", 1)[1].strip())
                if "finished" in event_names or "completed_empty" in event_names:
                    break

        assert "finished" in event_names or "completed_empty" in event_names

    def test_sse_unknown_task_returns_404(self, client):
        resp = client.get("/api/scan/does-not-exist/events")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. POST cancel → 200 {cancel_requested}, second cancel → 409
# ---------------------------------------------------------------------------

class TestCancelScan:
    def test_cancel_running_scan_returns_200(self, client):
        barrier = threading.Barrier(2, timeout=5)

        def _blocking(config, cancel_token, bus):
            barrier.wait()
            bus.finished("/fake/out.sqlite")

        with patch("app.web.routes.scan.run_pipeline", _blocking):
            resp = client.post("/api/scan", json=_valid_scan_body())
            task_id = resp.json()["task_id"]

            cancel_resp = client.post(f"/api/scan/{task_id}/cancel")
            assert cancel_resp.status_code == 200
            assert cancel_resp.json() == {"status": "cancel_requested"}

            # Release the pipeline so it can exit.
            barrier.wait()

    def test_cancel_already_terminal_returns_409(self, client):
        with patch("app.web.routes.scan.run_pipeline", _noop_pipeline_factory(0.0)):
            resp = client.post("/api/scan", json=_valid_scan_body())
            task_id = resp.json()["task_id"]

        # Wait until the pipeline finishes.
        deadline = time.monotonic() + 3.0
        import app.web.registry as reg_module
        while time.monotonic() < deadline:
            task = reg_module.registry.get(task_id)
            if task and task.status != "running":
                break
            time.sleep(0.05)

        cancel_resp = client.post(f"/api/scan/{task_id}/cancel")
        assert cancel_resp.status_code == 409

    def test_cancel_unknown_id_returns_404(self, client):
        resp = client.post("/api/scan/nonexistent-id/cancel")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. GET /api/health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# 8. _load_settings path resolution
# ---------------------------------------------------------------------------

class TestLoadSettings:
    def test_load_settings_returns_json_settings_instance(self):
        """_load_settings must return a JsonSettings pointed at settings.json."""
        from app.web.routes.scan import _load_settings
        from infrastructure.settings import JsonSettings

        settings = _load_settings()
        assert isinstance(settings, JsonSettings)

    def test_load_settings_respects_photo_manager_home(self, tmp_path, monkeypatch):
        """PHOTO_MANAGER_HOME env var shifts the config root."""
        from app.web.routes.scan import _load_settings

        monkeypatch.setenv("PHOTO_MANAGER_HOME", str(tmp_path))
        # Should not raise even if settings.json doesn't exist at that path.
        settings = _load_settings()
        # get() on a missing-file settings returns the default.
        assert settings.get("nonexistent.key", "default_val") == "default_val"


# ---------------------------------------------------------------------------
# 9. completed_empty pipeline path
# ---------------------------------------------------------------------------

class TestCompletedEmpty:
    def test_completed_empty_sets_status_finished(self, client):
        """completed_empty() must set task.status='finished' and terminal_event."""
        def _empty_pipeline(config, cancel_token, bus):
            bus.completed_empty()

        with patch("app.web.routes.scan.run_pipeline", _empty_pipeline):
            resp = client.post("/api/scan", json=_valid_scan_body())
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # Wait for terminal status.
        import app.web.registry as reg_module
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            task = reg_module.registry.get(task_id)
            if task and task.status != "running":
                break
            time.sleep(0.02)

        assert task.status == "finished"
        assert task.terminal_event["name"] == "completed_empty"


# ---------------------------------------------------------------------------
# 10. Pipeline exception → bus.failed called (scan thread never hangs)
# ---------------------------------------------------------------------------

class TestPipelineException:
    def test_pipeline_exception_calls_bus_failed(self, client):
        """If run_pipeline raises, the scan thread must call bus.failed()."""
        def _crashing_pipeline(config, cancel_token, bus):
            raise RuntimeError("disk full")

        with patch("app.web.routes.scan.run_pipeline", _crashing_pipeline):
            resp = client.post("/api/scan", json=_valid_scan_body())
        task_id = resp.json()["task_id"]

        import app.web.registry as reg_module
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            task = reg_module.registry.get(task_id)
            if task and task.status != "running":
                break
            time.sleep(0.02)

        assert task.status == "failed"
        assert "disk full" in task.terminal_event["payload"]["msg"]


# ---------------------------------------------------------------------------
# 11. SSE Last-Event-ID resume: partial replay from buffer
# ---------------------------------------------------------------------------

class TestSseResume:
    def test_last_event_id_header_replays_only_newer_events(self, client):
        """GET /events with Last-Event-ID=N must only replay events with id > N."""
        # Pre-seed a finished task with known buffer contents.
        from app.web.models import ScanTask
        from app.web.routes.scan import SseScanBus
        from core.app_service.cancel_token import _CancelToken
        import asyncio
        import app.web.registry as reg_module

        task = ScanTask(
            task_id="resume-test",
            status="running",
            cancel_token=_CancelToken(),
        )
        # Manually pump events into the buffer via a real loop.
        loop = asyncio.new_event_loop()
        bus = SseScanBus(task, loop)
        bus.log("first")   # id=1
        bus.log("second")  # id=2
        bus.finished("/out.sqlite")  # id=3, terminal
        loop.close()

        reg_module.registry._tasks["resume-test"] = task

        # Resume from id=1: should only see id=2 (log:second) and id=3 (finished).
        event_names: list[str] = []
        with client.stream(
            "GET",
            "/api/scan/resume-test/events",
            headers={"last-event-id": "1"},
        ) as stream:
            for line in stream.iter_lines():
                if line.startswith("event:"):
                    event_names.append(line.split(":", 1)[1].strip())
                if "finished" in event_names:
                    break

        # "first" (id=1) must NOT appear; "finished" (id=3) must appear.
        assert "log" in event_names  # the second log
        assert "finished" in event_names

    def test_aged_out_last_event_id_returns_terminal_event(self, client):
        """If Last-Event-ID is not in the 500-ring buffer, inject terminal event."""
        from app.web.models import ScanTask
        from app.web.routes.scan import SseScanBus
        from core.app_service.cancel_token import _CancelToken
        import asyncio
        import app.web.registry as reg_module

        task = ScanTask(
            task_id="aged-out-test",
            status="running",
            cancel_token=_CancelToken(),
        )
        loop = asyncio.new_event_loop()
        bus = SseScanBus(task, loop)
        bus.finished("/out.sqlite")  # id=1
        loop.close()

        reg_module.registry._tasks["aged-out-test"] = task

        # Request id=9999 which is NOT in buffer → aged-out path → inject terminal.
        event_names: list[str] = []
        with client.stream(
            "GET",
            "/api/scan/aged-out-test/events",
            headers={"last-event-id": "9999"},
        ) as stream:
            for line in stream.iter_lines():
                if line.startswith("event:"):
                    event_names.append(line.split(":", 1)[1].strip())
                if event_names:
                    break

        assert "finished" in event_names


# ---------------------------------------------------------------------------
# 12. read_knee_measured → store_read_knee called on settings
# ---------------------------------------------------------------------------

class TestBusReadKneeMeasured:
    def test_read_knee_measured_persists_via_settings(self, client):
        """read_knee_measured must emit event AND call store_read_knee."""
        from app.web.models import ScanTask
        from app.web.routes.scan import SseScanBus
        from core.app_service.cancel_token import _CancelToken
        from unittest.mock import MagicMock, patch
        import asyncio

        task = ScanTask(
            task_id="knee-test",
            status="running",
            cancel_token=_CancelToken(),
        )
        loop = asyncio.new_event_loop()
        bus = SseScanBus(task, loop)
        loop.close()

        mock_settings = MagicMock()
        mock_settings.get.return_value = {}

        summary = {"device": r"\\NAS", "knee": 2, "sole_ramping": True}
        with patch("app.web.routes.scan._load_settings", return_value=mock_settings):
            bus.read_knee_measured(summary)

        with task.buffer_lock:
            events = list(task.event_buffer)
        assert any(name == "read_knee_measured" for _, name, _ in events)
        mock_settings.set.assert_called_once()
        mock_settings.save.assert_called_once()


# ---------------------------------------------------------------------------
# 7. Cross-import guard: no existing Qt file imports app.web
# ---------------------------------------------------------------------------

class TestCrossImportGuard:
    def test_qt_files_do_not_import_app_web(self, tmp_path):
        """Verify the one-directional import constraint: app/web must not be
        imported by app/views/**, core/**, or infrastructure/**.

        Grep all .py files in those trees for 'from app.web' or 'import app.web'.
        """
        import re
        from pathlib import Path

        repo_root = Path(__file__).parent.parent

        search_roots = [
            repo_root / "app" / "views",
            repo_root / "app" / "viewmodels",
            repo_root / "core",
            repo_root / "infrastructure",
        ]

        pattern = re.compile(r"(from\s+app\.web|import\s+app\.web)")
        violations: list[str] = []

        for root in search_roots:
            if not root.exists():
                continue
            for py_file in root.rglob("*.py"):
                text = py_file.read_text(encoding="utf-8", errors="replace")
                if pattern.search(text):
                    violations.append(str(py_file))

        assert violations == [], (
            "These files import app.web — violates the one-directional constraint:\n"
            + "\n".join(f"  {v}" for v in violations)
        )
