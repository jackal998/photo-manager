"""MenuController: Manages menu creation and action connections."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QMainWindow, QMenuBar, QMessageBox

from infrastructure.i18n import get_translator, t

# Actions that are only meaningful with a manifest loaded. Both the
# scan-then-load path (main_window) and the Open-Manifest path
# (file_operations) flip these together so users see the same enabled set
# regardless of how the manifest was acquired. ``execute_mode`` is the
# #165-prototype toggle — a destructive surface should stay greyed-out
# until there's something to act on.
MANIFEST_ACTIONS: tuple[str, ...] = (
    "save_manifest",
    "execute_action",
    "remove_from_list",
    "execute_mode",
)


class MenuController:
    """Manages main window menu creation and action connections."""

    def __init__(self, main_window: QMainWindow, settings: Any | None = None) -> None:
        self.window = main_window
        self.settings = settings
        self.actions: dict[str, QAction] = {}
        # Per-locale QActions inside the View → Language submenu.
        self._language_actions: dict[str, QAction] = {}

    def setup_menus(self) -> dict[str, QAction]:
        """Create all menus and return action references.

        #135 — top-level menu titles use Qt's ``&`` mnemonic prefix so
        Alt+letter opens each menu without a mouse. List and Log both
        start with L; List wins ``Alt+L`` (more frequently used —
        Remove from List), Log uses ``Alt+G`` (``Lo&g``).
        """
        menubar = QMenuBar(self.window)

        # File Menu — Alt+F
        file_menu = menubar.addMenu(t("menu.file.title"))
        self.actions["scan_sources"] = file_menu.addAction(t("menu.file.scan_sources"))
        file_menu.addSeparator()
        self.actions["open_manifest"] = file_menu.addAction(t("menu.file.open_manifest"))
        self.actions["save_manifest"] = file_menu.addAction(t("menu.file.save_manifest"))
        self.actions["save_manifest"].setEnabled(False)
        file_menu.addSeparator()
        self.actions["exit"] = file_menu.addAction(t("menu.file.exit"))

        # Action Menu — Alt+A
        action_menu = menubar.addMenu(t("menu.action.title"))
        self.actions["action_by_regex"] = action_menu.addAction(t("menu.action.by_regex"))
        action_menu.addSeparator()
        self.actions["execute_action"] = action_menu.addAction(t("menu.action.execute"))
        self.actions["execute_action"].setEnabled(False)

        # List Menu — Alt+L
        list_menu = menubar.addMenu(t("menu.list.title"))
        self.actions["remove_from_list"] = list_menu.addAction(t("menu.list.remove"))
        # Disabled until a manifest loads — gives the user a visible-but-greyed
        # entry instead of a menu that appears empty / no-op before any data is
        # present. Re-enabled in MainWindow._load_manifest_from_path.
        self.actions["remove_from_list"].setEnabled(False)

        # Log Menu — Alt+G ("Lo&g") — L is taken by List
        log_menu = menubar.addMenu(t("menu.log.title"))
        self.actions["open_latest_log"] = log_menu.addAction(t("menu.log.open_latest"))
        self.actions["open_latest_delete_log"] = log_menu.addAction(t("menu.log.open_latest_delete"))
        log_menu.addSeparator()
        self.actions["open_log_directory"] = log_menu.addAction(t("menu.log.open_directory"))
        self.actions["open_delete_log_directory"] = log_menu.addAction(t("menu.log.open_delete_directory"))

        # View Menu — Alt+V
        view_menu = menubar.addMenu(t("menu.view.title"))
        # #165 prototype — Execute Mode toggle. Checkable so the menu
        # entry itself acts as the active-mode indicator (no separate
        # status pill needed for the prototype). Ctrl+E is the canonical
        # shortcut from the issue's open-questions list. Disabled until
        # a manifest loads (gated by MANIFEST_ACTIONS).
        self.actions["execute_mode"] = view_menu.addAction(
            t("menu.view.execute_mode")
        )
        self.actions["execute_mode"].setCheckable(True)
        self.actions["execute_mode"].setShortcut(QKeySequence("Ctrl+E"))
        self.actions["execute_mode"].setEnabled(False)
        view_menu.addSeparator()
        language_menu = view_menu.addMenu(t("menu.view.language.title"))
        translator = get_translator()
        current = translator.locale
        # QActionGroup with exclusive=True gives the submenu radio-button
        # semantics: clicking one locale unchecks the previously-active
        # one automatically. Without the group, individually-checkable
        # QActions accumulate ticks across clicks (#multi-select bug).
        self._language_group = QActionGroup(self.window)
        self._language_group.setExclusive(True)
        for code, display in translator.available_locales():
            act = language_menu.addAction(display)
            act.setCheckable(True)
            act.setChecked(code == current)
            self._language_group.addAction(act)
            # Bind code via default-arg trick; otherwise the loop variable
            # closure captures only the last code from the iteration.
            act.triggered.connect(lambda _checked=False, c=code: self._on_language_chosen(c))
            self._language_actions[code] = act

        self.window.setMenuBar(menubar)

        # Store actions as window attributes for backward compatibility
        self.window.action_exit = self.actions["exit"]
        self.window.action_remove_from_list = self.actions["remove_from_list"]

        return self.actions

    def connect_actions(self, handlers: dict[str, Callable]) -> None:
        """Connect menu actions to their handler methods."""
        for name, action in self.actions.items():
            if name in handlers:
                action.triggered.connect(handlers[name])
            elif name == "exit":
                action.triggered.connect(self.window.close)

    def get_action(self, name: str) -> QAction | None:
        return self.actions.get(name)

    def enable_action(self, name: str, enabled: bool = True) -> None:
        action = self.actions.get(name)
        if action:
            action.setEnabled(enabled)

    def set_manifest_actions(self, enabled: bool) -> None:
        """Flip every manifest-gated menu action to *enabled* in one call."""
        for name in MANIFEST_ACTIONS:
            self.enable_action(name, enabled)

    def get_all_actions(self) -> dict[str, QAction]:
        return self.actions.copy()

    # ------------------------------------------------------------------ language

    def _on_language_chosen(self, code: str) -> None:
        """Confirm with the user, persist the locale, and trigger a live
        window rebuild via ``MainWindow.relocalize``.

        Always prompts before the rebuild — even though the loaded
        manifest and decisions survive (vm outlives the window),
        recreating the MainWindow closes any open dialog and resets
        scroll/preview state. The Yes/No prompt makes that disruption
        deliberate. On No, we revert the QActionGroup checked state
        so the submenu doesn't lie about the active locale.
        """
        current = get_translator().locale
        if code == current:
            return

        chosen_action = self._language_actions.get(code)
        display_name = chosen_action.text() if chosen_action is not None else code

        reply = QMessageBox.question(
            self.window,
            t("language.confirm_title"),
            t("language.confirm_body", language=display_name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            # User declined. Revert the checked state in the submenu —
            # the QActionGroup auto-flipped to the picked action when
            # they clicked, but they didn't actually want to switch.
            current_action = self._language_actions.get(current)
            if current_action is not None:
                current_action.setChecked(True)
            return

        if self.settings is not None:
            try:
                self.settings.set("ui.locale", code)
                self.settings.save()
            except OSError:
                pass  # Non-fatal — relocalize still picks up the new locale via in-memory settings.
        # Hand off to MainWindow. We use a duck-typed `relocalize`
        # call instead of importing MainWindow here so this module
        # stays free of a backward dep on a concrete view.
        relocalize = getattr(self.window, "relocalize", None)
        if callable(relocalize):
            relocalize()
