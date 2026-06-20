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
