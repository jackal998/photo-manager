"""Lint that user-facing UI label constants in qa/scenarios still appear
somewhere in app/ source.

Why this test exists
--------------------
Drivers and invariants couple to the app's UI labels — button titles,
menu items, dialog titles. When app source renames a label without
updating the corresponding constant in qa/scenarios/_uia.py (or the
hardcoded string in qa/scenarios/_invariants.py), the qa-explore
batch breaks. Because the batch runs locally and only on demand,
that drift can sit silently for weeks before someone notices.

This test catches the rename at PR time so the qa batch stays
runnable. It runs in CI (layer 1) without needing the Windows + UIA
stack.

Limitations (be honest about what this does NOT catch)
------------------------------------------------------
- Hardcoded strings inside individual scenario files (status-bar
  regex, dialog body substrings). Those use regex semantics rather
  than exact-match and live in arbitrary positions; the existing
  test_status_messages.py covers the formatter side, which is the
  origin of those strings.
- Auto IDs (SCAN_AID_*) — those are computed from the QObject
  hierarchy at runtime. Renaming a class breaks the auto_id without
  any source-text drift visible to a static check.
- A constant could exist in app/ source but in an unrelated context
  (false negative on the "still wired up correctly" question). This
  test only verifies the string is present, not that it labels the
  right widget.

For comprehensive label-drift detection, run the full qa batch
(`python -m qa.scenarios._batch`). This test is the cheap, fast,
CI-runnable subset.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_DIR = REPO / "app"
UIA_FILE = REPO / "qa" / "scenarios" / "_uia.py"
INVARIANTS_FILE = REPO / "qa" / "scenarios" / "_invariants.py"


def _collect_app_text() -> str:
    """Concatenate every .py file under app/ for substring search."""
    parts: list[str] = []
    for p in APP_DIR.rglob("*.py"):
        try:
            parts.append(p.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
    return "\n".join(parts)


def _normalize_for_match(text: str) -> str:
    """Normalize Qt's mnemonic-escape ampersand: ``&&`` in source becomes a
    single ``&`` at runtime / in the UIA accessible name. So _uia.py stores
    e.g. ``"Close & Load"`` while source has ``setText("Close && Load")``.
    """
    return text.replace("&&", "&")


def _is_user_facing_constant(name: str) -> bool:
    """Return True iff ``name`` looks like a user-facing UI label constant.

    Filters out:
      - private / internal names (``_VK_CONTROL`` etc.)
      - regex patterns (``WINDOW_TITLE_RE`` and any ``*_RE``)
      - automation IDs computed from QObject hierarchy (``SCAN_AID_*``,
        ``*_AID_*``) — these are not visible source text
    """
    if name.startswith("_"):
        return False
    if name.endswith("_RE"):
        return False
    if "AID" in name:
        return False
    return True


def _module_assignments(file_path: Path):
    """Yield ``(target_name, value_node)`` for module-level ``NAME = ...`` and
    ``NAME: T = ...`` forms (handles both ``ast.Assign`` and ``ast.AnnAssign``).
    """
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name):
                yield tgt.id, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name):
                yield node.target.id, node.value


def _direct_string_constants(file_path: Path) -> list[tuple[str, str]]:
    """Return ``[(name, value)]`` for top-level ``NAME = "value"`` (string
    literal only) where ``name`` looks user-facing per
    ``_is_user_facing_constant``. Tuple/list values are NOT walked — _uia.py
    has tuples like ``DEFAULT_SHELL_CLASSES`` that legitimately contain
    non-source strings (Win32 class names, etc.).
    """
    out: list[tuple[str, str]] = []
    for name, value in _module_assignments(file_path):
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        if not _is_user_facing_constant(name):
            continue
        if not value.value:
            continue
        out.append((name, value.value))
    return out


def _strings_in_named_tuple(file_path: Path, target_name: str) -> list[tuple[str, str]]:
    """Return ``[(context, value)]`` for every string literal nested at any
    depth inside the module-level tuple/list assigned to ``target_name``.

    Used for _invariants.py's ``MANIFEST_GATED_MENU_ITEMS`` table — opt-in
    by exact name so we don't accidentally lint Win32 class lists or other
    internal tuples.
    """
    out: list[tuple[str, str]] = []
    for name, value in _module_assignments(file_path):
        if name != target_name:
            continue
        if not isinstance(value, (ast.Tuple, ast.List)):
            continue
        for inner in ast.walk(value):
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                if inner.value:
                    out.append((f"{target_name}[…]", inner.value))
    return out


def test_uia_constants_exist_in_app_source():
    """Every user-facing label constant in qa/scenarios/_uia.py must appear
    as a substring of some app/*.py file. If you rename a button or dialog
    title without updating the constant, the corresponding qa-explore
    driver will fail on the next batch run; this test catches that drift
    at PR time so the qa batch stays trustworthy.

    The test normalizes Qt's ``&&`` mnemonic escape in source — a button
    `setText("Close && Load")` displays (and exposes via UIA) as
    `Close & Load`, which is what the constant stores.
    """
    app_text = _normalize_for_match(_collect_app_text())
    missing = [
        f"{name} = {value!r}"
        for name, value in _direct_string_constants(UIA_FILE)
        if value not in app_text
    ]
    assert not missing, (
        "These _uia.py constants were not found in any app/*.py source — "
        "label drift?\n  " + "\n  ".join(missing)
    )


def test_invariants_hardcoded_labels_exist_in_app_source():
    """Same drift check, but for hardcoded string literals inside
    qa/scenarios/_invariants.py — entries in the manifest-gated menu-item
    table that were never hoisted to _uia.py constants. Without this
    check, ``("MENU_LIST", "Remove from List")`` could silently desync
    from the actual menu_controller wiring.
    """
    app_text = _normalize_for_match(_collect_app_text())
    missing = [
        f"{ctx}: {value!r}"
        for ctx, value in _strings_in_named_tuple(
            INVARIANTS_FILE, "MANIFEST_GATED_MENU_ITEMS"
        )
        if value not in app_text
    ]
    assert not missing, (
        "These hardcoded strings in _invariants.py were not found in any "
        "app/*.py source:\n  " + "\n  ".join(missing)
    )
