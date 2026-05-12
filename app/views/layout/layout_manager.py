"""LayoutManager: Manages main window layout and splitter behavior."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


class LayoutManager:
    """Manages main window layout and splitter behavior.

    This class encapsulates all layout-related functionality including:
    - Main window layout creation
    - Splitter configuration and management
    - Dynamic splitter size adjustments
    - Window sizing and positioning
    """

    # Layout constants
    TREE_STRETCH_FACTOR = 7
    PREVIEW_STRETCH_FACTOR = 3
    MIN_SECTION_WIDTH = 200
    SPLITTER_MARGIN = 24
    WINDOW_SIZE_RATIO = 0.5

    def __init__(self, main_window: QMainWindow) -> None:
        """Initialize with main window reference.

        Args:
            main_window: The QMainWindow to manage layout for
        """
        self.window = main_window
        self.splitter: QSplitter | None = None

    def setup_main_layout(self, tree_widget: QWidget, preview_widget: QWidget) -> QWidget:
        """Create the main horizontal splitter layout.

        Args:
            tree_widget: Widget containing the tree view
            preview_widget: Widget containing the preview pane

        Returns:
            Central widget configured with the layout
        """
        # Create central widget
        central = QWidget(self.window)
        root = QHBoxLayout(central)

        # Create splitter. #136: at the Qt-enforced minimum window
        # width (~418 px) the preview pane was being squeezed to ~89 px
        # and nothing rendered. ``setChildrenCollapsible(False)``
        # prevents the user from dragging either pane to size 0. The
        # algorithmic squeeze that produced the 89-px state lives in
        # ``adjust_splitter_for_tree`` (it reallocates space to the
        # tree based on tree-content width on every manifest load);
        # the guard added there backs off when there's not enough room
        # to give preview MIN_SECTION_WIDTH AND tree its full content
        # width — leaving the 7:3 stretch in place on narrow windows.
        #
        # Why no per-widget setMinimumWidth: the previous iteration of
        # this fix pinned preview to MIN_SECTION_WIDTH via
        # ``setMinimumWidth``. On smaller windows, that forced the
        # splitter to keep preview at 200 px even when tree's natural
        # content needed more, leaving tree with as little as ~270 px.
        # The File Name column then sat geometrically outside the
        # tree pane and right-click row-anchors fell in the preview
        # — breaking qa-batch context-menu scenarios on CI
        # (s15/s19/s20/s21/s25/s30/s35). Algorithmic min in
        # ``adjust_splitter_for_tree`` accomplishes the same #136
        # protection without that side effect.
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(tree_widget)
        self.splitter.addWidget(preview_widget)
        self.splitter.setStretchFactor(0, self.TREE_STRETCH_FACTOR)
        self.splitter.setStretchFactor(1, self.PREVIEW_STRETCH_FACTOR)

        # Add splitter to root layout
        root.addWidget(self.splitter)

        return central

    def connect_splitter_signals(self, preview_refit_callback: Callable) -> None:
        """Connect splitter signals to callbacks.

        Args:
            preview_refit_callback: Callback to call when splitter moves
        """
        if self.splitter:
            try:
                self.splitter.splitterMoved.connect(lambda *_: preview_refit_callback())
            except Exception:
                pass

    def adjust_splitter_for_tree(self, tree_width_calculator: Callable[[], int]) -> None:
        """Adjust splitter sizes based on tree content width.

        Args:
            tree_width_calculator: Function that returns the required tree width
        """
        if not self.splitter:
            return

        try:
            tree_w = tree_width_calculator() + self.SPLITTER_MARGIN
            win_w = max(1, self.window.width())

            # #136 protection — applied dynamically because static
            # setMinimumWidth on either pane broke qa-batch right-click
            # scenarios on CI's smaller windows (PR #191 history). When
            # the window has room for tree-content + preview-min, we
            # PIN preview's min to ``MIN_SECTION_WIDTH`` so Qt enforces
            # it through subsequent splitter math. When room is tight,
            # we DROP preview's min back to 0 and abort the
            # reallocation; the splitter falls back to the initial 7:3
            # stretch so tree retains ~70% (right-click anchors stay
            # within it) and preview gets ~30% (renderable, well above
            # #136's 89-px bug threshold).
            preview_widget = self.splitter.widget(1)
            has_room = (
                tree_w + self.MIN_SECTION_WIDTH + self.SPLITTER_MARGIN
                <= win_w
            )
            if not has_room:
                if preview_widget is not None:
                    preview_widget.setMinimumWidth(0)
                return
            if preview_widget is not None:
                preview_widget.setMinimumWidth(self.MIN_SECTION_WIDTH)

            right_w = max(
                self.MIN_SECTION_WIDTH,
                win_w - tree_w - self.SPLITTER_MARGIN,
            )
            tree_w = max(
                self.MIN_SECTION_WIDTH,
                win_w - right_w - self.SPLITTER_MARGIN,
            )

            self.splitter.setSizes([tree_w, right_w])
        except Exception:
            pass

    def setup_initial_window_size(self) -> None:
        """Setup initial window size based on screen dimensions."""
        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                rect = screen.availableGeometry()
                width = int(rect.width() * self.WINDOW_SIZE_RATIO)
                height = int(rect.height() * self.WINDOW_SIZE_RATIO)
                self.window.resize(width, height)
        except Exception:
            pass

    def get_splitter(self) -> QSplitter | None:
        """Get the main splitter instance.

        Returns:
            QSplitter instance or None if not created
        """
        return self.splitter

    def create_tree_section(self) -> QWidget:
        """Create the tree section widget with layout.

        Returns:
            Widget configured for tree view
        """
        center_widget = QWidget()
        center = QVBoxLayout(center_widget)
        return center_widget, center

    def create_preview_section(self) -> QWidget:
        """Create the preview section widget with layout.

        Returns:
            Widget configured for preview pane
        """
        right_widget = QWidget()
        right = QVBoxLayout(right_widget)
        return right_widget, right
