"""Unit tests for the Daylight · light theme module.

These guard against a real failure mode: the QSS template references
colour tokens by ``@name@`` sentinel, and the ``DAYLIGHT`` dict supplies
them. If a future edit adds a ``@new_token@`` to the template but forgets
the dict entry (or renames a key on one side only), the substitution
leaves a literal ``@new_token@`` in the stylesheet — Qt silently ignores
the broken rule and the affected widgets render unthemed. The
"no unresolved placeholders" test catches exactly that drift before it
ships.
"""

from __future__ import annotations

import re

from app.views.theme import DAYLIGHT, apply_theme, build_qss


def test_build_qss_substitutes_known_tokens() -> None:
    qss = build_qss()
    # Sentinel for a token that definitely exists must be gone, and its
    # resolved hex value must be present.
    assert "@bg_app@" not in qss
    assert DAYLIGHT["bg_app"] in qss
    assert DAYLIGHT["accent"] in qss


def test_build_qss_leaves_no_unresolved_placeholders() -> None:
    """Every ``@token@`` in the template must resolve from DAYLIGHT.

    A leftover ``@name@`` means the template names a token the dict
    doesn't define — a broken QSS rule. This is the drift-catcher.
    """
    qss = build_qss()
    leftovers = re.findall(r"@[a-z_]+@", qss)
    assert leftovers == [], f"unresolved theme placeholders: {leftovers}"


def test_build_qss_contains_core_selectors() -> None:
    qss = build_qss()
    for selector in ("QTreeView", "QHeaderView::section", "QPushButton", "QScrollBar"):
        assert selector in qss


def test_build_qss_custom_tokens_override() -> None:
    custom = dict(DAYLIGHT)
    custom["bg_app"] = "#123456"
    qss = build_qss(custom)
    assert "#123456" in qss
    assert "@bg_app@" not in qss


def test_apply_theme_installs_stylesheet(qapp) -> None:
    prior = qapp.styleSheet()
    try:
        apply_theme(qapp)
        installed = qapp.styleSheet()
        assert installed
        assert DAYLIGHT["bg_app"] in installed
    finally:
        # Don't leak the theme onto other tests sharing the session app.
        qapp.setStyleSheet(prior)
