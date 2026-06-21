"""CI-safe structural probes for the qa/web/ scaffold.

These tests run in the main CI job (no Playwright, no live server).
They verify the scaffold's correctness as pure Python: constant shapes,
YAML parity, and import isolation.

None of these tests import playwright at module level — they catch
real bugs (testid typos, YAML drift, broken imports) without needing
a browser.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Static testid registry — all non-parameterised testids that must be present
# in testid_constants.py.  The live Playwright probe (marked web_probe) uses
# this set to verify they also appear in the rendered DOM.  Here we run the
# cheaper static check: every name in this set must be a defined UPPERCASE
# constant in testid_constants.py with the expected string value.
#
# Add new names here when §5 or later phases introduce new static testids.
# Parameterised helpers (rowFileTestid etc.) are NOT listed here — those are
# covered by the TestTestidConstants unit tests above.
# ---------------------------------------------------------------------------

REQUIRED_TESTIDS: dict[str, str] = {
    # Main window
    "MAIN_RESULT_TREE": "main-result-tree",
    "MAIN_STATUS_BAR": "main-status-bar",
    "MAIN_SCAN_BUTTON": "main-scan-button",
    "MAIN_EXECUTE_BUTTON": "main-execute-button",
    "MAIN_LANG_TOGGLE": "main-lang-toggle",
    "MAIN_SETTINGS_BUTTON": "main-settings-button",
    "MAIN_MANIFEST_INPUT": "main-manifest-input",
    "MAIN_MANIFEST_OPEN": "main-manifest-open-button",
    # Scan dialog
    "SCAN_ADD_SOURCE": "scan-add-source-button",
    "SCAN_DIALOG": "scan-dialog",
    "SCAN_SOURCE_LIST": "scan-source-list",
    "SCAN_OUTPUT_PATH": "scan-output-path",
    "SCAN_START_BUTTON": "scan-start-button",
    "SCAN_CANCEL_BUTTON": "scan-cancel-button",
    "SCAN_PROGRESS_LOG": "scan-progress-log",
    "SCAN_PROGRESS_BAR": "scan-progress-bar",
    "SCAN_STATUS_TEXT": "scan-status-text",
    # Execute dialog (§5 canonical)
    "EXECUTE_DIALOG": "execute-dialog",
    "EXECUTE_ALL_DELETE_BANNER": "execute-all-delete-banner",
    "EXECUTE_TYPE_FILTER": "execute-type-filter",
    "EXECUTE_TREE": "execute-tree",
    "EXECUTE_BTN_EXECUTE": "execute-btn-execute",
    "EXECUTE_BTN_EXECUTE_SELECTED": "execute-btn-execute-selected",
    "EXECUTE_PREVIEW_PANE": "execute-preview-pane",
    "EXECUTE_PREVIEW_IMAGE": "execute-preview-image",
    "EXECUTE_ALL_DELETE_CONFIRM": "execute-all-delete-confirm",
    "EXECUTE_ALL_DELETE_CONFIRM_YES": "execute-all-delete-confirm-yes",
    "EXECUTE_ALL_DELETE_CONFIRM_NO": "execute-all-delete-confirm-no",
    # Lock-conflict dialog
    "LOCK_CONFIRM_DIALOG": "lock-confirm-dialog",
    "LOCK_CONFIRM_BTN_UNLOCK_APPLY": "lock-confirm-btn-unlock-apply",
    "LOCK_CONFIRM_BTN_UNLOCKED_ONLY": "lock-confirm-btn-unlocked-only",
    "LOCK_CONFIRM_BTN_CANCEL": "lock-confirm-btn-cancel",
    # Delete confirmation dialog
    "DELETE_CONFIRM_DIALOG": "delete-confirm-dialog",
    "DELETE_CONFIRM_BTN_CONFIRM": "delete-confirm-btn-confirm",
    "DELETE_CONFIRM_BTN_CANCEL": "delete-confirm-btn-cancel",
    # Prune confirmation dialog
    "PRUNE_CONFIRM_DIALOG": "prune-confirm-dialog",
    # Preview pane
    "PREVIEW_PANE": "preview-pane",
    "PREVIEW_SINGLE_IMAGE": "preview-single-image",
    "PREVIEW_INFO": "preview-info",
    # Full-resolution dialog
    "FULLRES_DIALOG": "fullres-dialog",
    "FULLRES_IMAGE": "fullres-image",
    # Context menu
    "CONTEXT_MENU": "context-menu",
    "CTX_SET_ACTION_KEEP": "ctx-set-action-keep",
    "CTX_SET_ACTION_DELETE": "ctx-set-action-delete",
    "CTX_SET_ACTION_REMOVE": "ctx-set-action-remove",
    "CTX_OPEN_FOLDER": "ctx-open-folder",
    "CTX_LOCK": "ctx-lock",
    "CTX_UNLOCK": "ctx-unlock",
    "CTX_APPLY_BEST_COPY": "ctx-apply-best-copy",
    # Settings dialog
    "DLGE_SETTINGS_DIALOG": "settings-dialog",
    "DLGE_SETTINGS_SAVE": "settings-save-button",
    "DLGE_SETTINGS_CANCEL": "settings-cancel-button",
    # Generic cancel (kept for settings-level confirmations)
    "DLGE_CONFIRM_CANCEL": "confirm-cancel-button",
}


# ---------------------------------------------------------------------------
# 1. testid_constants — shape and naming conventions
# ---------------------------------------------------------------------------

class TestTestidConstants:
    """Verify testid_constants exports are well-formed strings."""

    def test_row_file_testid_format(self) -> None:
        """row_file_testid(group_id, basename) must embed both values."""
        from qa.web.testid_constants import row_file_testid

        result = row_file_testid("abc123", "photo.jpg")
        assert result == "row-file-abc123-photo.jpg"

    def test_row_file_testid_distinct_groups(self) -> None:
        """The same basename in two groups must produce different testids.

        This is the whole reason group_id is included — Playwright strict
        mode raises if two elements share a testid.
        """
        from qa.web.testid_constants import row_file_testid

        t1 = row_file_testid("grp1", "shared.jpg")
        t2 = row_file_testid("grp2", "shared.jpg")
        assert t1 != t2

    def test_all_constants_are_kebab_strings(self) -> None:
        """Every public constant in testid_constants must be a non-empty string.

        Catches typos where a constant is accidentally set to None, an int,
        or a tuple.  Kebab-case (lowercase + hyphens) is the convention.
        """
        import qa.web.testid_constants as mod

        _kebab = re.compile(r"^[a-z][a-z0-9-]*$")
        bad: list[str] = []
        for name in dir(mod):
            # Skip private names, callables, and names that are not
            # ALL_UPPER module constants (e.g. skip 'annotations', which is
            # a _Feature object injected by 'from __future__ import annotations').
            if name.startswith("_") or not name.isupper():
                continue
            value = getattr(mod, name)
            if callable(value):
                continue
            if not isinstance(value, str):
                bad.append(f"{name}: expected str, got {type(value).__name__!r}")
            elif not _kebab.match(value):
                bad.append(
                    f"{name}={value!r}: not kebab-case "
                    "(must match ^[a-z][a-z0-9-]*$)"
                )
        assert not bad, "Bad testid constants:\n" + "\n".join(f"  {b}" for b in bad)

    def test_row_group_testid_format(self) -> None:
        """row_group_testid(group_id) must embed the group_id."""
        from qa.web.testid_constants import row_group_testid

        result = row_group_testid("xyz")
        assert result == "row-group-xyz"

    def test_row_decision_testid_format(self) -> None:
        """row_decision_testid(group_id, basename) must embed both values."""
        from qa.web.testid_constants import row_decision_testid

        result = row_decision_testid("42", "photo.jpg")
        assert result == "row-decision-42-photo.jpg"

    def test_row_decision_testid_distinct_groups(self) -> None:
        """The same basename in two groups must produce different decision testids.

        Playwright strict-mode raises when two elements share a testid, so
        group_id inclusion is required even for decision controls.
        """
        from qa.web.testid_constants import row_decision_testid

        t1 = row_decision_testid("grp1", "shared.jpg")
        t2 = row_decision_testid("grp2", "shared.jpg")
        assert t1 != t2

    def test_row_lock_testid_format(self) -> None:
        """row_lock_testid(group_id, basename) must embed both values."""
        from qa.web.testid_constants import row_lock_testid

        result = row_lock_testid("7", "archive.png")
        assert result == "row-lock-7-archive.png"

    def test_row_lock_testid_distinct_groups(self) -> None:
        """The same basename in two groups must produce different lock testids.

        Lock toggles must be locatable unambiguously across groups.
        """
        from qa.web.testid_constants import row_lock_testid

        t1 = row_lock_testid("grp1", "shared.jpg")
        t2 = row_lock_testid("grp2", "shared.jpg")
        assert t1 != t2

    def test_execute_all_delete_jump_testid_format(self) -> None:
        """execute_all_delete_jump_testid(group_id) must embed the group_id."""
        from qa.web.testid_constants import execute_all_delete_jump_testid

        result = execute_all_delete_jump_testid("42")
        assert result == "execute-all-delete-jump-42"

    def test_execute_all_delete_jump_testid_distinct(self) -> None:
        """Different group_ids must produce different jump testids."""
        from qa.web.testid_constants import execute_all_delete_jump_testid

        t1 = execute_all_delete_jump_testid("grp1")
        t2 = execute_all_delete_jump_testid("grp2")
        assert t1 != t2


# ---------------------------------------------------------------------------
# 1b. REQUIRED_TESTIDS — static registry parity with testid_constants
# ---------------------------------------------------------------------------


class TestRequiredTestidsParity:
    """Every entry in REQUIRED_TESTIDS must exist in testid_constants.py
    as an UPPERCASE constant with the exact expected string value.

    This is a pure-Python CI probe — no Playwright or live server needed.
    It catches the class of bug where REQUIRED_TESTIDS drifts from the
    canonical constants (e.g. after a rename or removal).
    """

    def test_all_required_testids_defined_in_constants(self) -> None:
        """Every REQUIRED_TESTIDS entry must be a defined constant with the right value."""
        import qa.web.testid_constants as mod

        mismatches: list[str] = []
        for const_name, expected_value in REQUIRED_TESTIDS.items():
            actual = getattr(mod, const_name, None)
            if actual is None:
                mismatches.append(
                    f"{const_name}: not defined in testid_constants.py"
                )
            elif actual != expected_value:
                mismatches.append(
                    f"{const_name}: expected {expected_value!r}, got {actual!r}"
                )

        assert not mismatches, (
            "REQUIRED_TESTIDS has entries that don't match testid_constants.py:\n"
            + "\n".join(f"  {m}" for m in mismatches)
        )

    def test_required_testids_values_are_kebab(self) -> None:
        """Every value in REQUIRED_TESTIDS must be a non-empty kebab-case string."""
        _kebab = re.compile(r"^[a-z][a-z0-9-]*$")
        bad: list[str] = []
        for const_name, value in REQUIRED_TESTIDS.items():
            if not isinstance(value, str) or not _kebab.match(value):
                bad.append(f"{const_name}={value!r}")
        assert not bad, "Non-kebab values in REQUIRED_TESTIDS:\n" + "\n".join(f"  {b}" for b in bad)


# ---------------------------------------------------------------------------
# 2. scenario_map.yml — count parity with ALL_SCENARIOS
# ---------------------------------------------------------------------------

class TestScenarioMapParity:
    """scenario_map.yml must have exactly one entry per ALL_SCENARIOS key."""

    def _load_all_scenarios(self) -> list[str]:
        sys.path.insert(0, str(_REPO))
        from qa.scenarios._batch import ALL_SCENARIOS  # type: ignore[import]
        return list(ALL_SCENARIOS)

    def _load_map_names(self) -> list[str]:
        map_path = _REPO / "qa" / "web" / "scenario_map.yml"
        try:
            import yaml  # type: ignore[import]
            with map_path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return [e["scenario"] for e in data.get("scenarios", [])]
        except ImportError:
            # Fallback line scanner (no PyYAML in CI).
            names: list[str] = []
            with map_path.open(encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("- scenario:"):
                        names.append(stripped.removeprefix("- scenario:").strip())
            return names

    def test_count_matches_all_scenarios(self) -> None:
        """scenario_map.yml row count == len(ALL_SCENARIOS)."""
        all_scenarios = self._load_all_scenarios()
        map_names = self._load_map_names()

        assert len(map_names) == len(all_scenarios), (
            f"scenario_map.yml has {len(map_names)} entries "
            f"but ALL_SCENARIOS has {len(all_scenarios)}.  "
            "Add or remove rows in qa/web/scenario_map.yml to match."
        )

    def test_no_extra_or_missing_keys(self) -> None:
        """scenario_map.yml must not have entries absent from ALL_SCENARIOS."""
        all_scenarios = self._load_all_scenarios()
        map_names = self._load_map_names()

        source_set = set(all_scenarios)
        map_set = set(map_names)

        missing = source_set - map_set
        extra = map_set - source_set

        assert not missing, f"Missing from scenario_map.yml: {sorted(missing)}"
        assert not extra, f"Extra in scenario_map.yml (not in ALL_SCENARIOS): {sorted(extra)}"

    def test_no_duplicate_entries(self) -> None:
        """No scenario key appears twice in scenario_map.yml."""
        map_names = self._load_map_names()
        seen: set[str] = set()
        dupes: list[str] = []
        for name in map_names:
            if name in seen:
                dupes.append(name)
            seen.add(name)
        assert not dupes, f"Duplicate entries in scenario_map.yml: {dupes}"


# ---------------------------------------------------------------------------
# 3. Import isolation — no playwright at module load time
# ---------------------------------------------------------------------------

class TestImportIsolation:
    """All qa.web.* modules must be importable without playwright installed.

    The approach: temporarily shadow playwright in sys.modules with None,
    which causes 'import playwright' to raise ImportError exactly as it
    would in a CI environment without playwright installed.
    """

    def _check_module_importable_without_playwright(self, module_name: str) -> None:
        """Assert that ``module_name`` can be imported with playwright absent."""
        # Stash and remove any already-imported playwright submodules.
        stashed = {k: v for k, v in sys.modules.items() if k == "playwright" or k.startswith("playwright.")}
        for k in stashed:
            sys.modules.pop(k, None)
        # Remove the target module from cache too (so it re-executes).
        sys.modules.pop(module_name, None)
        # Block playwright by inserting None sentinel.
        sys.modules["playwright"] = None  # type: ignore[assignment]
        try:
            import importlib
            importlib.import_module(module_name)
        except ImportError as exc:
            # Any ImportError that mentions playwright is a violation.
            if "playwright" in str(exc).lower():
                pytest.fail(
                    f"{module_name} imported playwright at module level: {exc}"
                )
            # Other ImportErrors (e.g. missing yaml) are fine — they are
            # env-specific, not playwright-specific.
        finally:
            # Restore: remove sentinel, restore stashed modules.
            sys.modules.pop("playwright", None)
            sys.modules.update(stashed)
            # Also remove the target module so it doesn't pollute other tests.
            sys.modules.pop(module_name, None)

    def test_pw_module_no_playwright_at_import(self) -> None:
        """qa.web._pw must not import playwright at module load."""
        self._check_module_importable_without_playwright("qa.web._pw")

    def test_batch_module_no_playwright_at_import(self) -> None:
        """qa.web._batch must not import playwright at module load."""
        self._check_module_importable_without_playwright("qa.web._batch")

    def test_invariants_module_no_playwright_at_import(self) -> None:
        """qa.web._invariants must not import playwright at module load."""
        self._check_module_importable_without_playwright("qa.web._invariants")

    def test_smoke_test_no_playwright_at_import(self) -> None:
        """qa.web.smoke_test must not HARD-fail when playwright is absent.

        smoke_test.py sets _PW_AVAILABLE = False when playwright is absent —
        the module must import without raising.
        """
        self._check_module_importable_without_playwright("qa.web.smoke_test")
