"""Tests for the empty-state primary-action builder (#137).

Issue #137: the first-run empty state showed only a grey hint label —
a user with no manifest had to discover the File menu to make
anything happen. Fix: surface two QPushButton primary actions
("Scan Sources…" and "Open Manifest…") next to the hint, wired to the
caller's handler callbacks, and toggle their visibility via a wrapper
widget so the existing #42 hide-on-first-load semantics still apply
atomically to the whole group.

These tests pin the builder's contract:

  - Returns a wrapper widget plus references to the inner label,
    scan button, and open button (so MainWindow can introspect
    them without traversing the widget tree).
  - Each button's ``clicked`` signal fires the callback passed in,
    so MainWindow's File-menu and empty-state routes converge on
    the same handler method.
  - The wrapper widget — not just the label — is the single
    visibility-toggle target.

What we deliberately do NOT test:

  - That ``QPushButton.clicked.emit()`` reaches the connected slot.
    That's testing Qt, not our logic. (We DO assert that the
    callback fires on emit, because the wiring direction is our
    contract, not Qt's.)
  - The visual styling. Standard Qt buttons are intentional;
    pinning pixel placement would just churn on Qt-version
    updates.
  - That MainWindow uses the builder correctly. Layer-3
    ``qa/scenarios/s41_empty_state_action_buttons.py`` drives the
    real File-menu vs. button-click parity end-to-end through the
    UIA tree.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.views.components.empty_state import build_empty_state_widget


@pytest.fixture
def builder_inputs():
    """Default callbacks + labels for the builder under test."""
    return {
        "label_text": "No manifest loaded.\n\nFile → Scan Sources… to begin.",
        "scan_button_text": "Scan Sources…",
        "scan_handler": MagicMock(),
        "open_button_text": "Open Manifest…",
        "open_handler": MagicMock(),
    }


class TestBuilderReturnsExpectedShape:
    """The builder must return ``(wrapper, label, scan_button, open_button)``
    so MainWindow can stash the references on ``self`` for downstream
    introspection (visibility toggle, re-translate on locale switch)."""

    def test_returns_four_values(self, qapp, builder_inputs):
        result = build_empty_state_widget(**builder_inputs)
        assert len(result) == 4

    def test_wrapper_is_qwidget(self, qapp, builder_inputs):
        from PySide6.QtWidgets import QWidget
        wrapper, _, _, _ = build_empty_state_widget(**builder_inputs)
        assert isinstance(wrapper, QWidget)

    def test_label_is_qlabel_with_passed_text(self, qapp, builder_inputs):
        from PySide6.QtWidgets import QLabel
        wrapper, label, _, _ = build_empty_state_widget(**builder_inputs)
        assert isinstance(label, QLabel)
        assert label.text() == builder_inputs["label_text"]
        # Reference wrapper to keep the Qt children alive until the
        # assertion runs — without it Python may collect the wrapper
        # (whose parent is None) and shiboken tears down its children
        # before we read .text().
        assert wrapper is not None

    def test_scan_button_is_qpushbutton_with_passed_text(
        self, qapp, builder_inputs
    ):
        from PySide6.QtWidgets import QPushButton
        wrapper, _, scan_btn, _ = build_empty_state_widget(**builder_inputs)
        assert isinstance(scan_btn, QPushButton)
        assert scan_btn.text() == builder_inputs["scan_button_text"]
        assert wrapper is not None

    def test_open_button_is_qpushbutton_with_passed_text(
        self, qapp, builder_inputs
    ):
        from PySide6.QtWidgets import QPushButton
        wrapper, _, _, open_btn = build_empty_state_widget(**builder_inputs)
        assert isinstance(open_btn, QPushButton)
        assert open_btn.text() == builder_inputs["open_button_text"]
        assert wrapper is not None


class TestWrapperHoldsLabelAndButtons:
    """All three child widgets must live under the wrapper so a single
    ``setVisible(False)`` call on the wrapper hides everything atomically.
    If a regression accidentally adds a button as a sibling of the
    wrapper (e.g. directly to MainWindow's center_layout), this fails.
    """

    def test_wrapper_contains_label(self, qapp, builder_inputs):
        from PySide6.QtWidgets import QLabel
        wrapper, label, _, _ = build_empty_state_widget(**builder_inputs)
        assert label in wrapper.findChildren(QLabel)

    def test_wrapper_contains_scan_button(self, qapp, builder_inputs):
        from PySide6.QtWidgets import QPushButton
        wrapper, _, scan_btn, _ = build_empty_state_widget(**builder_inputs)
        assert scan_btn in wrapper.findChildren(QPushButton)

    def test_wrapper_contains_open_button(self, qapp, builder_inputs):
        from PySide6.QtWidgets import QPushButton
        wrapper, _, _, open_btn = build_empty_state_widget(**builder_inputs)
        assert open_btn in wrapper.findChildren(QPushButton)


class TestButtonWiring:
    """Each button must invoke the handler callback the caller passed
    in. Layer-1 contract: the builder doesn't route through some
    intermediate slot of its own — when scan_button.clicked fires,
    scan_handler runs, full stop. The same is true for the open
    button. This protects MainWindow's wiring from a refactor that
    accidentally swaps the two callbacks.
    """

    def test_scan_button_click_invokes_scan_handler(
        self, qapp, builder_inputs
    ):
        wrapper, _, scan_btn, _ = build_empty_state_widget(**builder_inputs)
        scan_btn.clicked.emit()
        builder_inputs["scan_handler"].assert_called_once()
        builder_inputs["open_handler"].assert_not_called()
        assert wrapper is not None  # keep wrapper alive past the emit

    def test_open_button_click_invokes_open_handler(
        self, qapp, builder_inputs
    ):
        wrapper, _, _, open_btn = build_empty_state_widget(**builder_inputs)
        open_btn.clicked.emit()
        builder_inputs["open_handler"].assert_called_once()
        builder_inputs["scan_handler"].assert_not_called()
        assert wrapper is not None


class TestWrapperVisibilityTogglesAllChildren:
    """Hiding the wrapper transitively hides the label and both
    buttons in one call — the atomic-toggle contract for #42's
    first-load disappear semantics. If anyone wires the visibility
    toggle to ``label.setVisible(False)`` instead of the wrapper,
    the buttons would stay visible above the tree view; the test
    below catches that regression.
    """

    def test_hiding_wrapper_hides_label_and_both_buttons(
        self, qapp, builder_inputs
    ):
        wrapper, label, scan_btn, open_btn = build_empty_state_widget(
            **builder_inputs
        )
        wrapper.show()
        assert wrapper.isVisible()
        assert label.isVisible()
        assert scan_btn.isVisible()
        assert open_btn.isVisible()

        wrapper.setVisible(False)

        assert not wrapper.isVisible()
        assert not label.isVisible()
        assert not scan_btn.isVisible()
        assert not open_btn.isVisible()


class TestMainWindowUsesBuilder:
    """Belt-and-braces source check: MainWindow must call
    ``build_empty_state_widget`` rather than re-inlining its own
    label-plus-buttons construction. If a future refactor splits the
    setup into two diverging code paths, every other test here passes
    against the builder while MainWindow silently drifts — this grep
    is the only thing that catches that.
    """

    def test_main_window_imports_and_calls_builder(self):
        from pathlib import Path
        source = Path("app/views/main_window.py").read_text(encoding="utf-8")
        assert "from app.views.components.empty_state import build_empty_state_widget" in source
        assert "build_empty_state_widget(" in source
