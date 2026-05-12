"""StatusReporter protocol implementation.

Lives in its own module so tests can import it without dragging in the
whole ``main_window`` assembly. ``main_window.py`` itself transitively
imports ``preview_pane``, ``layout_manager``, ``image_tasks`` and other
heavy GUI modules; loading any of them in a unit test process registers
them in coverage.py at near-zero coverage and tanks the global floor.
See ``docs/testing.md`` for the same trap that drove the
``ActionHandlersImpl`` extraction in #182.
"""
from __future__ import annotations

from PySide6.QtWidgets import QMainWindow


class StatusReporterImpl:
    """Routes ``StatusReporter`` protocol calls to a real QMainWindow.

    The window must expose ``statusBar()`` (PySide6 default) and
    ``set_status_baseline(text)`` (defined on ``MainWindow`` for the
    persistent baseline introduced for #138 / #140).
    """

    def __init__(self, main_window: QMainWindow):
        self.window = main_window

    def show_status(self, message: str, timeout: int = 3000) -> None:
        """Show a transient status message (auto-clears after timeout)."""
        self.window.statusBar().showMessage(message, timeout)

    def set_baseline(self, message: str) -> None:
        """Update the persistent baseline shown when no temp message is active."""
        self.window.set_status_baseline(message)
