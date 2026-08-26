"""Builds the main-window toolbar that surfaces the core review verbs.

The UX baseline audit's gap #5: every primary verb (Scan, Open, Execute,
set-decision) lived only in menus — no toolbar. This surfaces them.

Reuses the existing menu ``QAction`` objects where one already exists
(Scan / Open / Execute), so their enabled-state gating (Execute is
disabled until a manifest loads, via MANIFEST_ACTIONS) carries over for
free — the same QAction appears in both the menu and the toolbar. The
bulk Keep / Delete / Remove buttons are new actions wired to the
selection-decision handlers (the toolbar equivalent of the d / k keys).

Styling is by objectName (``primaryAction`` / ``dangerAction``) so the
theme QSS can render Scan as the warm primary and Execute as the red
destructive button; everything else uses the default toolbutton style.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QSizePolicy, QToolBar, QWidget

from app.views.constants import IGNORE_DECISION
from infrastructure.i18n import t


def build_main_toolbar(
    window: Any,
    actions: dict[str, QAction],
    *,
    on_bulk_keep: Callable[[], None],
    on_bulk_delete: Callable[[], None],
    on_bulk_remove: Callable[[], None],
) -> QToolBar:
    """Create, populate, and attach the main toolbar; return it.

    ``actions`` is MenuController's action dict (already created + wired).
    The three ``on_bulk_*`` callbacks apply a decision to the current tree
    selection.
    """
    toolbar = QToolBar(t("toolbar.title"), window)
    toolbar.setMovable(False)
    toolbar.setFloatable(False)

    scan_action = actions.get("scan_sources")
    if scan_action is not None:
        toolbar.addAction(scan_action)
    open_action = actions.get("open_manifest")
    if open_action is not None:
        toolbar.addAction(open_action)

    toolbar.addSeparator()

    label = QLabel(t("toolbar.selected_label"))
    label.setContentsMargins(4, 0, 4, 0)
    toolbar.addWidget(label)

    keep_action = QAction(t("decision.keep_short"), window)
    keep_action.triggered.connect(lambda: on_bulk_keep())
    toolbar.addAction(keep_action)

    delete_action = QAction(t("decision.delete_short"), window)
    delete_action.triggered.connect(lambda: on_bulk_delete())
    toolbar.addAction(delete_action)

    remove_action = QAction(t("decision.remove_short"), window)
    remove_action.triggered.connect(lambda: on_bulk_remove())
    toolbar.addAction(remove_action)

    toolbar.addSeparator()

    # Push the destructive Execute button to the right edge.
    spacer = QWidget()
    spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    toolbar.addWidget(spacer)

    execute_action = actions.get("execute_action")
    if execute_action is not None:
        toolbar.addAction(execute_action)

    # objectNames drive the themed primary / danger button styling.
    if scan_action is not None:
        scan_btn = toolbar.widgetForAction(scan_action)
        if scan_btn is not None:
            scan_btn.setObjectName("primaryAction")
    if execute_action is not None:
        exec_btn = toolbar.widgetForAction(execute_action)
        if exec_btn is not None:
            exec_btn.setObjectName("dangerAction")

    window.addToolBar(toolbar)
    return toolbar
