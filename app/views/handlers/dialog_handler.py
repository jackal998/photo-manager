"""DialogHandler: Coordinates dialog operations and user interactions.

The load-bearing decision logic — initial-field lookup, canonical
field list, per-row values dict assembly, safe records-provider
invocation — lives in :mod:`app.views.handlers.dialog_handler_helpers`
so it is unit-testable without cascade-importing the Qt dialog
stack. This file is the thin Qt-binding layer over those helpers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox

from app.views.handlers.dialog_handler_helpers import (
    CHILD_ROW_FIELDS,
    GROUP_ROW_FIELDS,
    TOP_ROW_FIELDS,
    default_action_dialog_fields,
    dict_from_pairs,
    resolve_initial_field,
    safe_call_records_provider,
)
from infrastructure.i18n import t


class TreeDataProvider(Protocol):
    """Protocol for tree data provider."""

    def get_selection_model(self):
        """Get tree selection model."""
        ...

    def get_view_model(self):
        """Get tree view model."""
        ...

    def get_source_model(self):
        """Get source model."""
        ...

    def get_proxy_model(self):
        """Get proxy model."""
        ...


class DialogHandler:
    """Coordinates dialog operations and user interactions."""

    def __init__(
        self,
        parent_widget: QObject,
        tree_data_provider: TreeDataProvider,
        action_handler: Callable[[str, str, str], None] | None = None,
        records_provider: Callable[[], list] | None = None,
        settings: object | None = None,
    ) -> None:
        self.parent = parent_widget
        self.tree_provider = tree_data_provider
        self.action_handler = action_handler
        # Optional callable returning the loaded PhotoGroups so the
        # ActionDialog can build its live preview. We resolve it lazily
        # at dialog-open time so the latest manifest state is reflected
        # without re-wiring on every load.
        self.records_provider = records_provider
        # Optional JsonSettings handle for persistence — the regex
        # dialog uses it for the Simple/Regex mode preference and the
        # recent-patterns history. Optional so test callers can pass None.
        self.settings = settings

    def show_action_dialog(self, clicked_col: int | None = None) -> None:
        """Show the Set Action by Field dialog."""
        try:
            from app.views.dialogs.select_dialog import ActionDialog
        except Exception:
            QMessageBox.critical(
                self.parent,
                t("file_op.set_action_internal_error_title"),
                t("file_op.set_action_internal_error_body"),
            )
            return

        fields = list(default_action_dialog_fields())
        initial_field = resolve_initial_field(clicked_col)
        row_values = self._get_highlighted_row_values()

        groups = safe_call_records_provider(self.records_provider)
        match_fn = None
        if groups:
            from app.views.handlers.file_operations import build_match_fn

            match_fn = build_match_fn(groups)
        # #237 — pass groups through so the numeric-condition panel is
        # reachable when the user picks Size / Score / Group Count /
        # Similarity / Creation Date / Shot Date from the dropdown.
        # Without this, ActionDialog._groups stays empty and the numeric
        # panel's visibility gate (`_field_panel_is_numeric`) silently
        # keeps the regex panel shown instead.
        dlg = ActionDialog(
            fields=fields, parent=self.parent,
            row_values=row_values, initial_field=initial_field,
            match_fn=match_fn, settings=self.settings,
            groups=groups,
        )

        if self.action_handler is not None:
            dlg.setActionRequested.connect(self.action_handler)

        dlg.exec()

    # Backward-compatibility alias
    def show_select_dialog(self, clicked_col: int | None = None) -> None:
        self.show_action_dialog(clicked_col=clicked_col)

    def _get_highlighted_row_values(self) -> dict[str, str]:
        """Get values from the currently highlighted row for dialog pre-population."""
        values: dict[str, str] = {}
        try:
            sel = self.tree_provider.get_selection_model()
            if not sel:
                return values

            rows = sel.selectedRows()
            if not rows:
                return values

            idx = rows[0]
            view_model = self.tree_provider.get_view_model()
            src_model = self.tree_provider.get_source_model()
            proxy = self.tree_provider.get_proxy_model()

            if proxy is not None and hasattr(proxy, "mapToSource"):
                idx = proxy.mapToSource(idx)
                model = src_model
            else:
                model = view_model

            if idx.parent().isValid():
                parent_idx = idx.parent()

                def _child(col: int) -> str:
                    return model.data(model.index(idx.row(), col, parent_idx)) or ""

                def _group(col: int) -> str:
                    return model.data(model.index(parent_idx.row(), col, parent_idx.parent())) or ""

                values.update(dict_from_pairs(GROUP_ROW_FIELDS, _group))
                values.update(dict_from_pairs(CHILD_ROW_FIELDS, _child))
            else:
                def _top(col: int) -> str:
                    return model.data(model.index(idx.row(), col)) or ""

                values.update(dict_from_pairs(TOP_ROW_FIELDS, _top))
        except Exception:
            pass

        return values
