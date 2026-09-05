"""Headless invariant probe — new Phase 2B modules must import no PySide6.

Extends the T6 probe pattern from test_ui_probes.py to cover the
Phase 2B headless modules:
  - core/app_service/review_view.py
  - core/app_service/review_service.py
  - core/app_service/fs_browse.py
  - app/web/routes/review.py
  - app/web/routes/settings.py
  - app/web/routes/fs.py

If any of these files imports PySide6, the web API process cannot start
without a display — this probe makes that regression a CI failure.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_PHASE2B_FILES = [
    REPO / "core" / "app_service" / "review_view.py",
    REPO / "core" / "app_service" / "review_service.py",
    REPO / "core" / "app_service" / "fs_browse.py",
    REPO / "app" / "web" / "routes" / "review.py",
    REPO / "app" / "web" / "routes" / "settings.py",
    REPO / "app" / "web" / "routes" / "fs.py",
    # Transitive deps of load_review: a Qt import sneaking into either of
    # these would break the web process (no display in the FastAPI worker).
    REPO / "app" / "viewmodels" / "main_vm.py",
    REPO / "core" / "services" / "sort_service.py",
]


def _check_file_qt_free(path: Path) -> list[str]:
    """Return a list of violation descriptions (empty = clean)."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "PySide6" in alias.name:
                    violations.append(
                        f"{path.name}:{node.lineno}: import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module and "PySide6" in node.module:
                names = ", ".join(a.name for a in node.names)
                violations.append(
                    f"{path.name}:{node.lineno}: from {node.module} import {names}"
                )

    return violations


def test_phase2b_modules_import_no_pyside6():
    """All Phase 2B headless modules must contain zero PySide6 imports."""
    all_violations: list[str] = []
    for path in _PHASE2B_FILES:
        assert path.exists(), f"Expected Phase 2B file not found: {path}"
        all_violations.extend(_check_file_qt_free(path))

    assert not all_violations, (
        "Phase 2B headless modules must not import PySide6.\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in all_violations)
    )


def test_decision_constant_sync():
    """VALID_DECISIONS must stay in sync with IGNORE_DECISION from core.constants.

    The review_service uses IGNORE_DECISION's value ('ignore') but defines its
    own VALID_DECISIONS frozenset; this test catches drift between the two.
    IGNORE_DECISION lives in the Qt-free core.constants so this probe survives
    the eventual app/views deletion.
    """
    from core.app_service.review_service import VALID_DECISIONS
    from core.constants import IGNORE_DECISION

    assert IGNORE_DECISION in VALID_DECISIONS, (
        f"IGNORE_DECISION ({IGNORE_DECISION!r}) must be in review_service.VALID_DECISIONS. "
        "Update VALID_DECISIONS to keep the two in sync."
    )
