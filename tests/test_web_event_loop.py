"""Event-loop discipline for the web routes (#790).

FastAPI runs ``async def`` handlers **on the single event-loop thread** and
auto-dispatches plain ``def`` handlers to its worker threadpool. So any
synchronous filesystem / SQLite call made directly in an ``async def``
handler body stalls *every* other in-flight request — including the live SSE
scan-progress stream — for its whole duration. On a NAS-backed library that
is a multi-second freeze from one ``stat()``.

Two complementary guards live here:

* :class:`TestNoBlockingCallsInAsyncHandlers` — a **static probe** (the
  AST-inspection form documented in ``docs/testing.md``) that walks every
  ``@router.*``-decorated ``async def`` in ``app/web/routes/`` and fails if
  its body calls a known-blocking primitive outside an executor. This is the
  invariant; it keeps covering routes added long after #790.
* :class:`TestHealthStaysResponsiveUnderBlockingRoute` — the **behavioural**
  test: a real ASGI round-trip proving ``GET /api/health`` is still served
  while a slow blocking route is in flight. The static probe alone cannot
  prove the threadpool dispatch actually happens.
"""

from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path

import httpx
import pytest

from app.web.main import create_app

_ROUTES_DIR = Path(__file__).resolve().parent.parent / "app" / "web" / "routes"

# Callee names that mean "this touches the disk or the DB, synchronously".
# Matched on the *called* name only (``x.stat()`` → ``stat``), so a bare
# reference handed to an executor (``run_in_executor(None, _load_settings)``)
# is correctly NOT a call and never matches.
_BLOCKING_CALLEES = frozenset(
    {
        # pathlib / os stat-family and directory walks
        "stat",
        "lstat",
        "exists",
        "is_file",
        "is_dir",
        "resolve",
        "iterdir",
        "glob",
        "rglob",
        "scandir",
        "listdir",
        # file read/write
        "open",
        "read_text",
        "read_bytes",
        "write_text",
        "write_bytes",
        "unlink",
        "mkdir",
        "replace",
        "fsync",
        # json.load (json.dumps/loads on an in-memory str is fine)
        "load",
        # sqlite3 / repository entry points
        "connect",
        "executemany",
        # project-level helpers that wrap the above
        "_load_settings",
        "save",
        "browse",
        "load_review",
        "set_decisions",
        "set_locks",
        "validate_under_roots",
    }
)


def _callee_name(node: ast.Call) -> str | None:
    """Return the bare name being called, or None for exotic callees."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_router_handler(node: ast.AsyncFunctionDef) -> bool:
    """True when *node* is decorated with ``@router.<method>(...)``."""
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "router"
        ):
            return True
    return False


def _executor_exempt_nodes(func_node: ast.AST) -> set[int]:
    """ids() of every node sitting inside a ``run_in_executor(...)`` argument.

    Work handed to the executor is off-loop by construction, so anything in
    those argument subtrees is exempt — including ``functools.partial(f, x)``
    wrappers and ``Path(p).is_file`` bound-method references.
    """
    exempt: set[int] = set()
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call) and _callee_name(node) == "run_in_executor":
            for arg in node.args[1:]:
                for inner in ast.walk(arg):
                    exempt.add(id(inner))
    return exempt


def _blocking_violations(path: Path) -> list[str]:
    """Return ``file:line handler → callee`` for each on-loop blocking call."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef) or not _is_router_handler(node):
            continue
        exempt = _executor_exempt_nodes(node)
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call) or id(inner) in exempt:
                continue
            name = _callee_name(inner)
            if name in _BLOCKING_CALLEES:
                violations.append(
                    f"{path.name}:{inner.lineno} {node.name}() calls {name}() on the event loop"
                )
    return violations


class TestNoBlockingCallsInAsyncHandlers:
    """Static probe for issue #790's first acceptance criterion."""

    def test_probe_covers_the_route_modules_by_name(self):
        """Guard the guard: a probe that scans nothing must not report clean.

        Asserts the covered modules BY NAME rather than by count — a count
        keeps passing after a module is renamed or moved out from under the
        probe, which is exactly how a guard goes silent without failing.
        """
        found = {p.name for p in _ROUTES_DIR.glob("*.py")}
        expected = {
            "action.py",
            "execute.py",
            "fs.py",
            "health.py",
            "i18n.py",
            "image.py",
            "media.py",
            "review.py",
            "scan.py",
            "settings.py",
        }
        missing = expected - found
        assert not missing, (
            f"route modules the #790 probe should be scanning are gone: {sorted(missing)} "
            "— if a module was renamed, update this list so the probe keeps watching it"
        )

    def test_probe_catches_a_known_violation(self, tmp_path):
        """The probe must actually fire — a false-negative guard is silence.

        Feeds it the exact shape #790 was about (an ``async def`` handler
        stat-ing a path in its own body) and requires a hit.
        """
        tmp = tmp_path / "violating_route.py"
        tmp.write_text(
            "from pathlib import Path\n"
            "@router.post('/api/x')\n"
            "async def handler(body):\n"
            "    if not Path(body.p).is_file():\n"
            "        raise HTTPException(404)\n"
            "    return {}\n",
            encoding="utf-8",
        )
        hits = _blocking_violations(tmp)
        assert hits, "probe reported clean on a handler that stats on the loop"
        assert "is_file" in hits[0]

    def test_probe_passes_the_sanctioned_executor_form(self, tmp_path):
        """...and must NOT fire on the idiom the fix uses (false-positive half)."""
        tmp = tmp_path / "clean_route.py"
        tmp.write_text(
            "from pathlib import Path\n"
            "@router.post('/api/x')\n"
            "async def handler(body):\n"
            "    loop = asyncio.get_running_loop()\n"
            "    if not await loop.run_in_executor(None, Path(body.p).is_file):\n"
            "        raise HTTPException(404)\n"
            "    settings = await loop.run_in_executor(None, _load_settings)\n"
            "    return {}\n",
            encoding="utf-8",
        )
        hits = _blocking_violations(tmp)
        assert hits == [], f"probe rejected the sanctioned executor form: {hits}"

    @pytest.mark.parametrize(
        "module",
        sorted(p.name for p in _ROUTES_DIR.glob("*.py") if p.name != "__init__.py"),
    )
    def test_no_async_handler_blocks_the_loop(self, module: str):
        violations = _blocking_violations(_ROUTES_DIR / module)
        assert violations == [], (
            "async def route handlers must not do synchronous fs/sqlite work in "
            "their own body — make the handler a plain `def` (FastAPI dispatches "
            "it to the threadpool) or await it via run_in_executor. See #790.\n"
            + "\n".join(violations)
        )


class TestHealthStaysResponsiveUnderBlockingRoute:
    """Behavioural half of #790's third acceptance criterion.

    The block is gated on a ``threading.Event`` rather than a fixed sleep, so
    the healthy path costs milliseconds (the test releases the handler as soon
    as health has answered) while a regression costs the full
    :data:`MAX_BLOCK_S`. That asymmetry is what makes the assertion robust:
    ``HEALTH_BUDGET_S`` sits 5x below ``MAX_BLOCK_S``, so ordinary scheduling
    jitter in a loaded suite cannot reach the failure threshold — an earlier
    fixed-sleep version of this test flaked at ~1.1 s of contention delay in a
    full-suite run, which is why the separation is now this wide.
    """

    # How long the patched settings read blocks if nobody releases it. Only a
    # REGRESSION ever pays this; the passing path releases it immediately.
    MAX_BLOCK_S = 15.0
    # Health must answer well inside that. A threadpool-dispatched handler
    # leaves the loop free, so health lands in single-digit ms; the budget is
    # loose enough to absorb a heavily loaded CI runner.
    HEALTH_BUDGET_S = 3.0

    def test_health_is_served_while_settings_read_is_in_flight(self, monkeypatch):
        """GET /api/health must not queue behind a blocking GET /api/settings.

        Before #790's fix ``get_settings`` was ``async def`` and called
        ``_load_settings()`` (``Path.exists()`` + ``open()`` + ``json.load()``)
        directly on the event loop, so the whole server — every other request
        and the live SSE scan stream — froze for the read's duration. With the
        handler dispatched to FastAPI's threadpool, health is unaffected.

        ``threading.Event.wait`` models a blocking syscall the way a real SMB
        round-trip behaves: it releases the GIL and parks the calling thread.
        """
        import threading

        from infrastructure.settings import JsonSettings
        from app.web.routes import settings as settings_routes

        entered = threading.Event()
        release = threading.Event()

        def _blocking_load_settings():
            entered.set()
            release.wait(timeout=self.MAX_BLOCK_S)
            # A real (empty) JsonSettings so the handler's own logic runs
            # unmodified and still returns 200.
            return JsonSettings(Path(__file__).parent / "_no_such_settings_790.json")

        monkeypatch.setattr(settings_routes, "_load_settings", _blocking_load_settings)
        app = create_app()

        async def _scenario():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                # t0 is taken BEFORE the blocking request is scheduled: if the
                # loop gets stalled, every later timestamp is already downstream
                # of the stall, so the measurement cannot be fooled by being
                # taken after the block has ended.
                t0 = time.perf_counter()
                slow = asyncio.create_task(client.get("/api/settings"))
                await asyncio.sleep(0.05)  # let the handler be reached first
                health = await client.get("/api/health")
                health_done = time.perf_counter() - t0
                reached = entered.is_set()
                release.set()
                slow_resp = await slow
                return health, health_done, reached, slow_resp

        health, health_done, reached, slow_resp = asyncio.run(_scenario())

        # Setup validity: if the handler was never entered, the timing
        # assertion below would pass for the wrong reason.
        assert reached, (
            "the patched blocking settings read was never reached — this test "
            "would then prove nothing"
        )
        assert slow_resp.status_code == 200
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        assert health_done < self.HEALTH_BUDGET_S, (
            f"GET /api/health took {health_done:.3f}s while a blocking settings "
            f"read was in flight (it blocks up to {self.MAX_BLOCK_S}s) — the read "
            "is running on the event loop and stalling the whole server (#790)"
        )
