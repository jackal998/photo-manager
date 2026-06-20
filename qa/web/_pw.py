"""Playwright context wrapper for the photo-manager web QA harness.

Playwright is an OPTIONAL dependency in this project — it is installed
locally and in the web-qa CI job, but NOT in the main unit-test CI job.
All module-level code in this file must import cleanly without playwright.

Pattern used to satisfy that constraint
----------------------------------------
- Type annotations only used under ``TYPE_CHECKING`` (never at runtime).
- The actual ``from playwright.sync_api import sync_playwright`` call is
  deferred to inside methods (``_get_playwright()`` helper below).
- Any file that imports from this module must therefore also be
  importable without playwright installed.

Usage (runtime, playwright present)
------------------------------------
::

    from qa.web._pw import PWContext

    with PWContext(base_url="http://127.0.0.1:8765") as ctx:
        page = ctx.new_page()
        page.goto("/")
        ...
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # These imports are only evaluated by type-checkers (mypy, pyright) —
    # never at runtime.  Playwright may not be installed in the environment
    # that runs unit tests.
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright


def _get_playwright() -> "Playwright":
    """Lazy import so CI unit-test runs don't fail on missing playwright."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "playwright is not installed.  "
            "Run: pip install playwright && playwright install chromium"
        ) from exc
    # sync_playwright() returns a context manager; __enter__ returns the
    # Playwright object.  Callers that want to manage the lifecycle themselves
    # should use sync_playwright() directly.
    raise RuntimeError(
        "_get_playwright() returns a context manager, not a Playwright instance. "
        "Use PWContext as a context manager instead."
    )


@dataclass
class PWContext:
    """Thin wrapper around a Playwright browser + context + page lifecycle.

    Designed to be used as a context manager::

        with PWContext(base_url="http://127.0.0.1:8765") as ctx:
            page = ctx.new_page()
            page.goto("/")

    Parameters
    ----------
    base_url:
        The root URL of the running FastAPI server.
    headless:
        Run Chromium headless (default: True).  Set to False for local
        debugging to watch the browser.
    slow_mo:
        Slow down Playwright operations by N milliseconds (useful for
        debugging flaky assertions).
    """

    base_url: str = "http://127.0.0.1:8765"
    headless: bool = True
    slow_mo: float = 0.0

    # Private — populated in __enter__, cleaned up in __exit__.
    _playwright: "Playwright | None" = field(default=None, init=False, repr=False)
    _browser: "Browser | None" = field(default=None, init=False, repr=False)
    _context: "BrowserContext | None" = field(default=None, init=False, repr=False)

    def __enter__(self) -> "PWContext":
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "playwright is not installed.  "
                "See qa/web/INSTALL.md for setup instructions."
            ) from exc

        self._playwright = sync_playwright().__enter__()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
        )
        self._context = self._browser.new_context(base_url=self.base_url)
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            # sync_playwright().__exit__() needs a real exc tuple; safe to
            # call with Nones when we handle cleanup ourselves.
            try:
                self._playwright.stop()
            except Exception:  # pragma: no cover — cleanup path
                pass
            self._playwright = None

    def new_page(self) -> "Page":
        """Open a new page inside the browser context."""
        if self._context is None:
            raise RuntimeError("PWContext must be used as a context manager")
        return self._context.new_page()

    # ------------------------------------------------------------------
    # Higher-level convenience stubs (Phase 2+ will flesh these out)
    # ------------------------------------------------------------------

    def goto(self, path: str) -> "Page":
        """Open a new page and navigate to ``base_url + path``.

        Returns the Page so the caller can chain assertions.
        """
        page = self.new_page()
        page.goto(path)
        return page

    def wait_for_status_bar(self, page: "Page", pattern: str, timeout: float = 10_000) -> str:
        """Wait until the main-window status bar matches ``pattern`` (regex).

        Returns the matched text.  Delegates to ``_invariants.wait_status``.
        """
        from qa.web._invariants import wait_status  # local import avoids circular
        return wait_status(page, pattern, timeout=timeout)

    def get_by_testid(self, page: "Page", testid: str) -> "object":
        """Shorthand for ``page.get_by_test_id(testid)``."""
        return page.get_by_test_id(testid)
