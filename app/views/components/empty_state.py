"""Builder for the first-run empty-state widget (#137).

Surfaces two primary-action buttons next to the hint label so the
user has a clickable entry point — not just an instruction to use the
File menu. Extracted out of ``MainWindow._setup_ui`` so the wiring
can be unit-tested without cascading the full QMainWindow view stack
through pytest's coverage measurement.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def build_empty_state_widget(
    label_text: str,
    scan_button_text: str,
    scan_handler: Callable[[], None],
    open_button_text: str,
    open_handler: Callable[[], None],
) -> tuple[QWidget, QLabel, QPushButton, QPushButton]:
    """Build the first-run empty-state container.

    Returns the wrapper widget plus references to the hint label and
    both buttons so the caller (MainWindow) can keep them on
    ``self._empty_state_*`` for downstream introspection — and so
    layer-1 tests can verify the wiring without constructing a real
    MainWindow.

    The wrapper is the single visibility-toggle target. Hiding it
    transitively hides the label and both buttons in one call, which
    preserves the #42 contract that the empty state disappears as an
    atomic unit once a manifest is loaded.

    Button labels match the corresponding File-menu QAction labels
    exactly (``Scan Sources…`` / ``Open Manifest…``) so the user sees
    the same affordance text in both places.

    Args:
        label_text: localised hint copy (currently
            ``main_window.empty_state``).
        scan_button_text: localised scan-button label
            (``main_window.empty_state_scan_button``).
        scan_handler: callable invoked when the scan button is
            clicked; production wires this to
            ``MainWindow.on_scan_sources``.
        open_button_text: localised open-manifest button label
            (``main_window.empty_state_open_button``).
        open_handler: callable invoked when the open button is
            clicked; production wires this to
            ``MainWindow.on_open_manifest``.

    Returns:
        (wrapper, label, scan_button, open_button) — the wrapper
        owns the layout containing the rest; the inner references
        are returned so the caller can introspect (e.g. retranslate
        on locale switch) without traversing the widget tree.
    """
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)

    label = QLabel(label_text)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    # Soft-grey hint text — matches the existing pre-#137 styling so
    # the buttons are the louder element, the hint is the supporting
    # context.
    label.setStyleSheet("color: #888; font-size: 14px; padding: 40px;")
    layout.addWidget(label)

    button_row = QHBoxLayout()
    button_row.addStretch()
    scan_button = QPushButton(scan_button_text)
    scan_button.clicked.connect(scan_handler)
    button_row.addWidget(scan_button)
    open_button = QPushButton(open_button_text)
    open_button.clicked.connect(open_handler)
    button_row.addWidget(open_button)
    button_row.addStretch()
    layout.addLayout(button_row)
    # Bottom stretch keeps the label + buttons grouped near the top
    # of the central area rather than centered vertically in the
    # full pane — matches the pre-#137 look where the hint label
    # sat where the tree's first row would otherwise appear.
    layout.addStretch()

    return wrapper, label, scan_button, open_button
