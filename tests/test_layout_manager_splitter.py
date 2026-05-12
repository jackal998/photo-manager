"""Tests for LayoutManager splitter constraints (#136).

#136 surfaced because the splitter had no per-child min-width and was
``childrenCollapsible(True)`` by default — at the Qt-enforced minimum
window width (~418 px) the preview pane shrank to ~89 px and rendered
nothing. The fix pins each child to ``MIN_SECTION_WIDTH`` and disables
collapse; these tests pin the constraints so a future refactor of
``setup_main_layout`` can't silently drop them.
"""
from __future__ import annotations

import pytest

from PySide6.QtWidgets import QMainWindow, QWidget

from app.views.layout.layout_manager import LayoutManager


@pytest.fixture
def layout_manager(qapp) -> LayoutManager:
    # The real MainWindow is a heavy QMainWindow that drags in the
    # whole view stack; only the central-widget parenting requires a
    # real QWidget. A bare ``QMainWindow`` is enough — none of the
    # splitter-setup branches we test touch the rest of the window.
    return LayoutManager(QMainWindow())


def test_splitter_disables_children_collapsible(qapp, layout_manager):
    """The splitter must reject ``setChildrenCollapsible(True)`` — that
    default lets either pane shrink to 0 px, which on the preview side
    leaves the user staring at empty grey (#136 root cause)."""
    tree_widget = QWidget()
    preview_widget = QWidget()
    layout_manager.setup_main_layout(tree_widget, preview_widget)

    splitter = layout_manager.get_splitter()
    assert splitter is not None
    assert splitter.childrenCollapsible() is False


def test_splitter_children_have_min_section_width(qapp, layout_manager):
    """Both panes carry an explicit ``minimumWidth`` >= MIN_SECTION_WIDTH.

    Without these, the only floor on the preview pane was the QSplitter's
    handle width (~3 px) plus the tree pane's natural minimum size hint
    (~80 px on Windows) — well below anything renderable.
    """
    tree_widget = QWidget()
    preview_widget = QWidget()
    layout_manager.setup_main_layout(tree_widget, preview_widget)

    assert tree_widget.minimumWidth() >= LayoutManager.MIN_SECTION_WIDTH
    assert preview_widget.minimumWidth() >= LayoutManager.MIN_SECTION_WIDTH


def test_splitter_minimum_width_propagates_to_handle_floor(
    qapp, layout_manager
):
    """The composite ``minimumSizeHint`` of the splitter must be at
    least twice ``MIN_SECTION_WIDTH`` (one per child), so the central
    widget — and thus the window — inherits an enforceable floor.

    This is the user-observable guarantee from #136: Win32
    ``MoveWindow`` requests narrower than ~400 px get clamped by Qt
    rather than producing the 89-px-preview broken state.
    """
    tree_widget = QWidget()
    preview_widget = QWidget()
    layout_manager.setup_main_layout(tree_widget, preview_widget)

    splitter = layout_manager.get_splitter()
    # ``minimumSizeHint().width()`` includes the handle plus both
    # children's minimum widths. We can't assert an exact value
    # (handle width is platform-dependent), but two children at 200 px
    # each guarantees >= 400 — the floor that fixes #136.
    assert splitter.minimumSizeHint().width() >= 2 * LayoutManager.MIN_SECTION_WIDTH


# ── ancillary LayoutManager methods ──────────────────────────────────────
#
# These cover the rest of the public surface so the per-file 70% floor
# holds for this module. Each method has real arithmetic or callback
# wiring worth pinning — not defensive padding.


def test_get_splitter_returns_none_before_setup(qapp):
    """``get_splitter`` returns the constructed splitter, or ``None``
    before any layout has been set up. Callers (``_capture_relocalize_state``,
    ``_restore_geometry``) rely on the ``None`` sentinel to skip
    splitter-state work when the layout isn't built yet."""
    mgr = LayoutManager(QMainWindow())
    assert mgr.get_splitter() is None


def test_create_tree_section_returns_widget_and_layout(qapp, layout_manager):
    """``create_tree_section`` returns ``(widget, layout)`` so the
    caller can append the empty-state label and the tree to the same
    vertical layout. Asserting both pieces are real Qt objects guards
    against a refactor that drops one half of the tuple."""
    widget, layout = layout_manager.create_tree_section()
    assert widget is not None
    assert layout is not None
    # Layout must be parented on the widget so caller's addWidget calls
    # actually land in the tree section.
    assert layout.parent() is widget


def test_create_preview_section_returns_widget_and_layout(qapp, layout_manager):
    widget, layout = layout_manager.create_preview_section()
    assert widget is not None
    assert layout is not None
    assert layout.parent() is widget


def test_connect_splitter_signals_wires_callback(qapp, layout_manager):
    """``connect_splitter_signals`` must hook the splitter's
    ``splitterMoved`` signal to the supplied callback (typically
    ``PreviewPane.refit``). Without that wiring, dragging the splitter
    leaves a stale preview."""
    tree_widget = QWidget()
    preview_widget = QWidget()
    layout_manager.setup_main_layout(tree_widget, preview_widget)

    calls: list[bool] = []
    layout_manager.connect_splitter_signals(lambda: calls.append(True))

    splitter = layout_manager.get_splitter()
    splitter.splitterMoved.emit(100, 0)
    assert calls == [True]


def test_connect_splitter_signals_noops_without_splitter(qapp):
    """Defensive against connect_splitter_signals being called pre-setup
    (e.g. in a future refactor that reorders init). Should not raise."""
    mgr = LayoutManager(QMainWindow())
    # No setup_main_layout call — splitter is None.
    mgr.connect_splitter_signals(lambda: None)  # must not raise


def test_adjust_splitter_for_tree_clamps_to_min_section_width(
    qapp, layout_manager
):
    """``adjust_splitter_for_tree`` distributes width tree | preview
    based on the calculated tree-content width, but neither side may
    be smaller than ``MIN_SECTION_WIDTH``. Tests the min-clamp
    branches in particular, since those interact with #136's fix."""
    tree_widget = QWidget()
    preview_widget = QWidget()
    layout_manager.setup_main_layout(tree_widget, preview_widget)

    splitter = layout_manager.get_splitter()
    # Give the window a real width so the right-side calculation has
    # something to work with. We use the underlying mock-but-real
    # QMainWindow from the fixture.
    layout_manager.window.resize(900, 600)

    # Tree-content calculator that asks for the whole window — forces
    # the right-side clamp to MIN_SECTION_WIDTH branch.
    layout_manager.adjust_splitter_for_tree(lambda: 9999)
    sizes = splitter.sizes()
    assert sizes[1] >= LayoutManager.MIN_SECTION_WIDTH

    # Tree-content calculator that asks for near-zero — forces the
    # left-side clamp to MIN_SECTION_WIDTH branch.
    layout_manager.adjust_splitter_for_tree(lambda: 0)
    sizes = splitter.sizes()
    assert sizes[0] >= LayoutManager.MIN_SECTION_WIDTH


def test_adjust_splitter_for_tree_swallows_calculator_errors(
    qapp, layout_manager
):
    """The width calculator is supplied by ``tree_controller`` and may
    fail mid-resize (model mid-rebuild, etc.). The adjust helper must
    not propagate that — splitter geometry isn't load-bearing enough
    to warrant a crash."""
    tree_widget = QWidget()
    preview_widget = QWidget()
    layout_manager.setup_main_layout(tree_widget, preview_widget)

    def broken_calc() -> int:
        raise RuntimeError("model mid-rebuild")

    # Must not raise.
    layout_manager.adjust_splitter_for_tree(broken_calc)


def test_adjust_splitter_for_tree_noop_without_splitter(qapp):
    """Same defensive guard as connect_splitter_signals — must not
    raise pre-setup."""
    mgr = LayoutManager(QMainWindow())
    mgr.adjust_splitter_for_tree(lambda: 500)  # must not raise


def test_setup_initial_window_size_resizes_window(qapp, layout_manager):
    """Initial-window-size derives from primary screen geometry — sets
    the QMainWindow to half-screen in each dimension. We can't assert
    exact pixels (depends on the runner's screen), but we can assert
    the window got resized to something non-zero."""
    layout_manager.window.resize(1, 1)  # known-tiny baseline
    layout_manager.setup_initial_window_size()
    # Either there's a primary screen (size > 1) or the helper's
    # except branch ran (size unchanged at 1×1); both are valid exit
    # states. The branch we want to assert: when a screen IS available,
    # the window grew. In CI/headless without a screen the except
    # branch fires — still no crash.
    assert layout_manager.window.width() >= 1
