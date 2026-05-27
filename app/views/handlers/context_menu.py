"""ContextMenuHandler: Manages context menu creation and actions."""

from __future__ import annotations

from typing import Any, Protocol

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QMenu, QTreeView

from app.views.constants import settable_decisions
from app.views.handlers.file_opener import open_folder_containing
from infrastructure.i18n import t


class ActionHandlers(Protocol):
    """Protocol for action handler callbacks."""

    def remove_items_from_list(self, items: list[dict]) -> None:
        """Remove items from list."""
        ...

    def show_action_dialog(self, clicked_col: int | None = None) -> None:
        """Show set action by field/regex dialog."""
        ...

    def set_decision(self, items: list[dict], decision: str) -> None:
        """Set the user decision (delete/keep) for file items.

        Silent-applier — called by paths that have already resolved
        lock state (post-confirm). Most context-menu callers should
        use :meth:`set_decision_with_lock_check` so the
        LockedRowsConfirmDialog fires for locked rows (#182).
        """
        ...

    def set_decision_with_lock_check(
        self, items: list[dict], new_decision: str
    ) -> None:
        """Set decisions, surfacing the LockedRowsConfirmDialog
        when any target row is locked (#182)."""
        ...

    def set_locked_state(self, items: list[dict], locked: bool) -> None:
        """Lock or unlock file items (#164)."""
        ...

    def execute_action_selected_only(self, items: list[dict]) -> None:
        """Open the Execute Action dialog scoped by group membership
        (#429, #430). Inherits the menu-bar entry's semantic: any
        selected file row pulls its parent group whole."""
        ...


class TreeItemProvider(Protocol):
    """Protocol for tree item provider."""

    def get_selected_items(self) -> list[dict]:
        """Get currently selected items."""
        ...


class ContextMenuHandler:
    """Manages context menu creation and actions."""

    def __init__(
        self,
        tree_view: QTreeView,
        tree_item_provider: TreeItemProvider,
        action_handlers: ActionHandlers,
        parent_widget: Any,
    ) -> None:
        self.tree = tree_view
        self.item_provider = tree_item_provider
        self.handlers = action_handlers
        self.parent = parent_widget

    def setup_context_menu(self) -> None:
        """Setup context menu policy and connect signals."""
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, point: QPoint) -> None:
        index = self.tree.indexAt(point)
        if not index.isValid():
            return

        clicked_col: int | None = index.column()
        selected_items = self.item_provider.get_selected_items()
        if not selected_items:
            return

        menu = QMenu(self.parent)

        if len(selected_items) == 1:
            self._create_single_selection_menu(menu, selected_items[0], clicked_col)
        else:
            self._create_multi_selection_menu(menu, selected_items)

        menu.exec(self.tree.viewport().mapToGlobal(point))

    def _create_single_selection_menu(
        self, menu: QMenu, item: dict, clicked_col: int | None = None
    ) -> None:
        if item["type"] == "file":
            # Set Action submenu. Routes through set_decision_with_lock_check
            # so picking a destructive decision on a locked row surfaces
            # the unified confirm dialog (#182). Previously called
            # set_decision directly to silently bypass the lock.
            set_action_menu = menu.addMenu(t("context_menu.set_action"))
            for label, value in settable_decisions():
                a = set_action_menu.addAction(label)
                a.triggered.connect(
                    lambda checked=False, _v=value, _item=item:
                        self.handlers.set_decision_with_lock_check([_item], _v)
                )

            # Lock / Unlock — both shown unconditionally; the handler is
            # idempotent so re-locking a locked row is a no-op (and the
            # UX cost of showing only the contextually-relevant action
            # would mean threading lock state into the menu builder for
            # one label flip — see photo-manager#164).
            lock_action = menu.addAction(t("context_menu.lock"))
            lock_action.triggered.connect(
                lambda checked=False, _item=item:
                    self.handlers.set_locked_state([_item], True)
            )
            unlock_action = menu.addAction(t("context_menu.unlock"))
            unlock_action.triggered.connect(
                lambda checked=False, _item=item:
                    self.handlers.set_locked_state([_item], False)
            )

            # Open Folder action — delegates to shared OS-opener helper
            # (#143 factored out the inline cascade so right-click and the
            # new double-click dispatcher share one implementation).
            open_folder_action = menu.addAction(t("context_menu.open_folder"))
            open_folder_action.triggered.connect(
                lambda checked=False, _path=item.get("path", ""):
                    open_folder_containing(_path)
            )

        # Common actions for single selection
        menu.addSeparator()
        action_dialog_action = menu.addAction(t("context_menu.set_action_by_regex"))
        action_dialog_action.triggered.connect(
            lambda: self.handlers.show_action_dialog(clicked_col=clicked_col)
        )

        # #429: Execute Action (only selected) — only on file rows.
        # Group-row single-selection skips this entry (the group's
        # peer file rows aren't part of the selection set, which
        # would surprise the user who right-clicked only a group
        # header). The menu-bar entry remains the path for that case.
        if item["type"] == "file":
            execute_selected_action = menu.addAction(
                t("context_menu.execute_selected_only")
            )
            execute_selected_action.triggered.connect(
                lambda checked=False, _item=item:
                    self.handlers.execute_action_selected_only([_item])
            )

        remove_action = menu.addAction(t("context_menu.remove_from_list"))
        remove_action.triggered.connect(lambda: self.handlers.remove_items_from_list([item]))

    def _create_multi_selection_menu(self, menu: QMenu, selected_items: list[dict]) -> None:
        # Set Action submenu — only applies to file-type items. Routes
        # through set_decision_with_lock_check so any locked rows in
        # the multi-selection surface the unified confirm dialog (#182).
        set_action_menu = menu.addMenu(t("context_menu.set_action"))
        file_items = [it for it in selected_items if it.get("type") == "file"]
        for label, value in settable_decisions():
            a = set_action_menu.addAction(label)
            a.triggered.connect(
                lambda checked=False, _v=value, _items=file_items:
                    self.handlers.set_decision_with_lock_check(_items, _v)
            )

        # Bulk Lock / Unlock — see photo-manager#164.
        if file_items:
            lock_action = menu.addAction(t("context_menu.lock"))
            lock_action.triggered.connect(
                lambda checked=False, _items=file_items:
                    self.handlers.set_locked_state(_items, True)
            )
            unlock_action = menu.addAction(t("context_menu.unlock"))
            unlock_action.triggered.connect(
                lambda checked=False, _items=file_items:
                    self.handlers.set_locked_state(_items, False)
            )

        # Right-click parity with the single-selection branch — the regex
        # dialog is the bulk power tool, so it has to be reachable from
        # multi-select right-click too. clicked_col=None falls back to
        # the dialog's "File Name" default.
        menu.addSeparator()
        action_dialog_action = menu.addAction(t("context_menu.set_action_by_regex"))
        action_dialog_action.triggered.connect(
            lambda: self.handlers.show_action_dialog(clicked_col=None)
        )

        # #429: Execute Action (only selected) — gated on ≥1 file
        # row in the multi-selection. Group-only multi-selection
        # skips this entry, matching the single-selection branch.
        if file_items:
            execute_selected_action = menu.addAction(
                t("context_menu.execute_selected_only")
            )
            execute_selected_action.triggered.connect(
                lambda checked=False, _items=selected_items:
                    self.handlers.execute_action_selected_only(_items)
            )

        remove_action = menu.addAction(t("context_menu.remove_from_list"))
        remove_action.triggered.connect(
            lambda: self.handlers.remove_items_from_list(selected_items)
        )
