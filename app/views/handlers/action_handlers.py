"""ActionHandlersImpl — bridge between the context menu and the
domain handlers (FileOperationsHandler + DialogHandler).

Why this is its own module
--------------------------
The context menu (``ContextMenuHandler``) calls into this bridge for
every decision / lock / remove / regex-open invocation. Static type
hints alone won't catch a forgotten proxy method: Python's ``Protocol``
classes are advisory, so a missing method survives until the menu item
fires at runtime and the ``AttributeError`` gets swallowed by Qt's
signal dispatch.

Keeping the bridge in its own small module — separate from
``main_window.py``, which is a 400+ line ``QMainWindow`` assembly — means
the bridge stays unit-testable in isolation. ``tests/test_context_menu.py
::TestActionHandlersImplBridge`` asserts every required method exists
and delegates correctly. Importing this module from tests doesn't
cascade into the GUI-only widgets (preview pane, video player, etc.)
that ``main_window.py`` pulls in.

Background: see ``feedback_action_handlers_bridge.md`` in memory for
the #175/#182 history.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.views.handlers.dialog_handler import DialogHandler
    from app.views.handlers.file_operations import FileOperationsHandler


class ActionHandlersImpl:
    """Implementation of the ``ActionHandlers`` protocol declared in
    :mod:`app.views.handlers.context_menu`. Every method here is a
    proxy that forwards to the underlying file-ops / dialog handler —
    no business logic lives in this layer.
    """

    def __init__(
        self,
        file_operations: "FileOperationsHandler",
        dialog_handler: "DialogHandler",
    ) -> None:
        self.file_ops = file_operations
        self.dialog = dialog_handler

    def remove_items_from_list(self, items: list[dict]) -> None:
        """Remove items from the review list."""
        self.file_ops.remove_items_from_list(items)

    def show_action_dialog(self, clicked_col: int | None = None) -> None:
        """Show the Set Action by Field dialog."""
        self.dialog.show_action_dialog(clicked_col=clicked_col)

    def set_decision(self, items: list[dict], decision: str) -> None:
        """Set ``user_decision`` for file items (silent applier).

        Used by paths that have already validated lock state (post-
        confirm). Context-menu callers should use
        :meth:`set_decision_with_lock_check` so the
        ``LockedRowsConfirmDialog`` fires for locked rows (#182).
        """
        self.file_ops.set_decision(items, decision)

    def set_decision_with_lock_check(
        self, items: list[dict], new_decision: str
    ) -> None:
        """Set decisions, routing through ``LockedRowsConfirmDialog``
        when any target row is locked (#182)."""
        self.file_ops.set_decision_with_lock_check(items, new_decision)

    def set_locked_state(self, items: list[dict], locked: bool) -> None:
        """Toggle ``is_locked`` for file items (#164).

        Called from the main-window context menu's Lock / Unlock
        items. Without this proxy the menu items silently no-op
        because the call goes through ``ContextMenuHandler.handlers``
        (this class), not directly to ``FileOperationsHandler`` —
        the bug that escaped #175's coverage.
        """
        self.file_ops.set_locked_state(items, locked)

    def execute_action_selected_only(self, items: list[dict]) -> None:
        """Open the Execute Action dialog scoped by group membership
        (#429, #430).

        Inherits the menu-bar entry's semantic — calls into
        ``FileOperationsHandler.execute_action(selected_only=True)``
        which re-reads the current selection via the tree controller
        and pulls the parent group of each selected file row whole.
        The ``items`` argument is accepted for protocol uniformity
        with the other handler methods; selection is sourced from
        the tree controller at execute time so a stale ``items`` list
        cannot put the dialog out of sync with the visible selection.
        """
        del items  # selection re-read inside execute_action
        self.file_ops.execute_action(selected_only=True)
