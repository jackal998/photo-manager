"""Daylight · light theme — centralised design tokens + QSS for the app.

Phase 1 of the Review-screen redesign (feat/review-ux-daylight). This is
the *foundation* layer: a single application-level stylesheet that replaces
the flat default Qt palette with the Daylight · light visual language
(warm paper background, hairline borders, rounded controls, a calm warm
accent). It is **purely visual** — no behaviour, no model, no delegate.

Why a token dict + a string template (not scattered ``setStyleSheet``):
the project's UX baseline audit flagged 15 ad-hoc inline ``setStyleSheet``
calls with hard-coded RGB and *no* shared palette. Centralising the
colours here makes them reviewable in one place and lets later phases
(delegates, density) read the same tokens instead of re-inventing hex
values. The single ``DAYLIGHT`` dict is the source of truth; a second
theme could be added later by supplying another dict, but we ship exactly
one now (no speculative abstraction).

Applied globally via :func:`apply_theme` on the ``QApplication`` so it
cascades to every widget *and* every child dialog. Called from
``main.main()`` for the real app and from ``scripts/render_ux_baseline.py``
for the before/after render harness — one code path, identical output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


# --- Design tokens (Daylight · light) ---------------------------------------
# Hex values lifted directly from the chosen Claude Design prototype
# ("Photo Dedup Review.dc.html", Daylight · light variant). Grouped by role
# so delegates/density phases can reference them by intent, not by colour.
DAYLIGHT: dict[str, str] = {
    # Surfaces
    "bg_app": "#f5efe6",       # window / app canvas (warm paper)
    "bg_panel": "#fffdf9",     # cards, tree, inputs (near-white)
    "bg_toolbar": "#faf6ee",   # toolbar / header strip / alt row
    "bg_titlebar": "#f3ede3",  # menu bar / status bar chrome
    "bg_subtle": "#f7ebda",    # hover / group tint
    # Borders (hairlines, warm gray)
    "border": "#e7ddcd",
    "border_soft": "#f0e8da",
    "border_input": "#ddd2bf",
    # Text
    "text": "#2c2823",         # primary
    "text_muted": "#867d70",   # secondary
    "text_faint": "#a89f8f",   # tertiary / placeholders
    # Brand / state accents
    "accent": "#bd6b39",       # warm terracotta — brand / primary action
    "accent_hover": "#cf7c43",
    "danger": "#c4503f",       # delete / destructive
    "positive": "#2f8a5a",     # keep
    # Selection (warm accent tint, not the harsh default blue)
    "select_bg": "#f1e3d0",
    "select_text": "#2c2823",
    # Scrollbar thumb
    "scroll_thumb": "#ddd2bf",
    "scroll_thumb_hover": "#cdbfa6",
}


_QSS_TEMPLATE = """
/* ===== Base ===== */
QWidget {
    background-color: @bg_app@;
    color: @text@;
    font-family: 'Segoe UI', system-ui, sans-serif;
}
QMainWindow, QDialog {
    background-color: @bg_app@;
}
QToolTip {
    background-color: @bg_panel@;
    color: @text@;
    border: 1px solid @border@;
    padding: 4px 8px;
}

/* ===== Results tree ===== */
QTreeView {
    background-color: @bg_panel@;
    alternate-background-color: @bg_toolbar@;
    color: @text@;
    border: 1px solid @border@;
    outline: 0;
    show-decoration-selected: 1;
    selection-background-color: @select_bg@;
    selection-color: @select_text@;
}
QTreeView::item {
    padding: 4px 6px;
    border: none;
}
QTreeView::item:hover {
    background-color: @bg_subtle@;
}
QTreeView::item:selected {
    background-color: @select_bg@;
    color: @select_text@;
}

/* ===== Column header ===== */
QHeaderView::section {
    background-color: @bg_toolbar@;
    color: @text_muted@;
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid @border@;
    border-right: 1px solid @border_soft@;
    font-weight: 600;
}
QHeaderView::section:hover {
    background-color: @bg_subtle@;
}

/* ===== Buttons ===== */
QPushButton {
    background-color: @bg_panel@;
    color: @text@;
    border: 1px solid @border_input@;
    border-radius: 8px;
    padding: 6px 14px;
}
QPushButton:hover {
    background-color: @bg_toolbar@;
}
QPushButton:pressed {
    background-color: @bg_subtle@;
}
QPushButton:default {
    border-color: @accent@;
}
QPushButton:disabled {
    color: @text_faint@;
    background-color: @bg_app@;
    border-color: @border_soft@;
}

/* ===== Inputs ===== */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
    background-color: @bg_panel@;
    color: @text@;
    border: 1px solid @border_input@;
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: @select_bg@;
    selection-color: @select_text@;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border-color: @accent@;
}

/* ===== Menu bar / menus ===== */
QMenuBar {
    background-color: @bg_titlebar@;
    color: @text@;
    border-bottom: 1px solid @border@;
}
QMenuBar::item {
    padding: 4px 10px;
    background: transparent;
    border-radius: 6px;
}
QMenuBar::item:selected {
    background-color: @bg_subtle@;
}
QMenu {
    background-color: @bg_panel@;
    color: @text@;
    border: 1px solid @border@;
    padding: 4px;
}
QMenu::item {
    padding: 5px 22px;
    border-radius: 5px;
}
QMenu::item:selected {
    background-color: @bg_subtle@;
}
QMenu::separator {
    height: 1px;
    background-color: @border_soft@;
    margin: 4px 8px;
}

/* ===== Status bar ===== */
QStatusBar {
    background-color: @bg_titlebar@;
    color: @text_muted@;
    border-top: 1px solid @border@;
}
QStatusBar::item {
    border: none;
}

/* ===== Splitter ===== */
QSplitter::handle {
    background-color: @border@;
}
QSplitter::handle:horizontal {
    width: 1px;
}
QSplitter::handle:vertical {
    height: 1px;
}

/* ===== Scrollbars ===== */
QScrollBar:vertical {
    background: transparent;
    width: 11px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: @scroll_thumb@;
    min-height: 32px;
    border-radius: 5px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background: @scroll_thumb_hover@;
}
QScrollBar:horizontal {
    background: transparent;
    height: 11px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: @scroll_thumb@;
    min-width: 32px;
    border-radius: 5px;
    margin: 2px;
}
QScrollBar::handle:horizontal:hover {
    background: @scroll_thumb_hover@;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}
"""


def build_qss(tokens: dict[str, str] | None = None) -> str:
    """Render the QSS string by substituting ``@token@`` placeholders.

    Uses ``@name@`` sentinels (not ``str.format``) so the QSS rule braces
    ``{ }`` don't need escaping. Unknown placeholders left in the template
    would simply survive as literal ``@name@`` text — caught immediately
    by a visibly-broken render, so no silent-failure risk.
    """
    qss = _QSS_TEMPLATE
    for key, value in (tokens or DAYLIGHT).items():
        qss = qss.replace(f"@{key}@", value)
    return qss


def apply_theme(app: QApplication, tokens: dict[str, str] | None = None) -> None:
    """Install the Daylight · light stylesheet on the ``QApplication``.

    Global (app-level) rather than per-widget so it cascades to every
    widget and every child dialog in one call. Idempotent — safe to call
    again after a live language switch rebuilds the main window (the new
    window inherits the already-installed app stylesheet, so re-applying
    is unnecessary but harmless).
    """
    app.setStyleSheet(build_qss(tokens))
