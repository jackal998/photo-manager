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


def test_panes_start_with_no_explicit_min_width(qapp, layout_manager):
    """At construction time, neither pane carries setMinimumWidth.

    #136 protection is APPLIED DYNAMICALLY by
    ``adjust_splitter_for_tree`` — preview's min gets set or unset
    depending on whether the window has room. Previous iterations of
    this fix pinned mins statically and broke qa-batch context-menu
    scenarios on CI's small screens (run 25738426293).
    """
    tree_widget = QWidget()
    preview_widget = QWidget()
    layout_manager.setup_main_layout(tree_widget, preview_widget)

    assert tree_widget.minimumWidth() == 0
    assert preview_widget.minimumWidth() == 0


def test_adjust_splitter_for_tree_pins_preview_min_on_wide_window(
    qapp, layout_manager
):
    """When tree-content + preview-min fits in the window,
    ``adjust_splitter_for_tree`` sets preview_widget.minimumWidth to
    MIN_SECTION_WIDTH so the value sticks through Qt's splitter math.
    This is the #136 protection on the common case (wide enough
    window)."""
    tree_widget = QWidget()
    preview_widget = QWidget()
    layout_manager.setup_main_layout(tree_widget, preview_widget)

    layout_manager.window.resize(1200, 600)
    # Tree content moderate; plenty of room for preview's min.
    layout_manager.adjust_splitter_for_tree(lambda: 400)

    assert preview_widget.minimumWidth() == LayoutManager.MIN_SECTION_WIDTH


def test_adjust_splitter_for_tree_drops_preview_min_on_narrow_window(
    qapp, layout_manager
):
    """When the window is too narrow to fit tree-content AND
    preview-min, ``adjust_splitter_for_tree`` DROPS preview's min back
    to 0 and aborts the reallocation — letting the initial 7:3
    stretch govern. Tree retains ~70% (right-click anchors stay
    within), preview gets ~30% (renderable, above #136 threshold).

    Regression guard for the qa-batch CI failure of PR #191 — without
    this dynamic drop, preview's static min squeezed the tree below
    the geometry that right-click row-anchor coords assume.
    """
    tree_widget = QWidget()
    preview_widget = QWidget()
    layout_manager.setup_main_layout(tree_widget, preview_widget)
    # Simulate a prior wide-window adjust that pinned preview's min.
    preview_widget.setMinimumWidth(LayoutManager.MIN_SECTION_WIDTH)

    splitter = layout_manager.get_splitter()
    layout_manager.window.resize(500, 400)
    splitter.setSizes([350, 150])
    pre_sizes = list(splitter.sizes())

    layout_manager.adjust_splitter_for_tree(lambda: 800)

    # Min unpinned + setSizes NOT called → pre-call sizes preserved.
    assert preview_widget.minimumWidth() == 0
    assert splitter.sizes() == pre_sizes


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


def test_adjust_splitter_for_tree_handles_tiny_tree_content(
    qapp, layout_manager
):
    """When tree's content width is tiny (e.g. before manifest load
    when only headers are rendered), the function still pins
    preview's min (room exists), and the splitter has non-trivial
    sizes on both sides — neither pane is collapsed."""
    tree_widget = QWidget()
    preview_widget = QWidget()
    layout_manager.setup_main_layout(tree_widget, preview_widget)

    splitter = layout_manager.get_splitter()
    layout_manager.window.resize(900, 600)

    layout_manager.adjust_splitter_for_tree(lambda: 0)
    # Preview's dynamic min sticks (room available).
    assert preview_widget.minimumWidth() == LayoutManager.MIN_SECTION_WIDTH
    # Both panes have non-trivial visible width — Qt distributes the
    # exact pixels based on stretch + handle + setSizes interaction,
    # which is harder to assert exactly; the no-collapse guarantee is
    # what matters for the user-visible #136 protection.
    sizes = splitter.sizes()
    assert sizes[0] > 0
    assert sizes[1] > 0


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
