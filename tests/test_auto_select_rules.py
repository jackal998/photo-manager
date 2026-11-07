"""Tests for the auto-select rule toggles (PR-4a, core/services/auto_select_rules.py)."""

from __future__ import annotations

import pytest

from core.services.auto_select_rules import (
    DEFAULT_RULES,
    LEGACY_RULES,
    RULE_IDS,
    RULES_VERSION,
    RuleToggles,
    resolve_rule_toggles,
    validate_rule_toggles,
)


def test_default_rules_match_prd():
    assert DEFAULT_RULES.to_dict() == {"B3": True, "B4": True, "B5": True, "B7": True, "B8": True}


def test_legacy_rules_match_master():
    # Master behaviour: only B8 differs from the defaults.
    assert LEGACY_RULES.to_dict() == {"B3": True, "B4": True, "B5": True, "B7": True, "B8": False}


def test_rules_version_and_ids_are_pinned():
    assert RULES_VERSION == "v1"
    assert RULE_IDS == ("B3", "B4", "B5", "B7", "B8")


def test_resolve_merges_partial_over_defaults():
    resolved = resolve_rule_toggles({"B3": False})
    assert resolved.to_dict() == {**DEFAULT_RULES.to_dict(), "B3": False}


def test_resolve_ignores_unknown_and_non_bool():
    assert resolve_rule_toggles({"B9": True, "B4": "no"}) == DEFAULT_RULES


@pytest.mark.parametrize("value", [None, "x", []])
def test_resolve_non_mapping_gives_defaults(value):
    assert resolve_rule_toggles(value) == DEFAULT_RULES


def test_validate_rejects_unknown_id():
    with pytest.raises(ValueError, match="unknown"):
        validate_rule_toggles({"B9": True})


@pytest.mark.parametrize("flag", [1, "true"])
def test_validate_rejects_non_bool(flag):
    with pytest.raises(ValueError, match="true or false"):
        validate_rule_toggles({"B3": flag})


def test_validate_rejects_non_mapping():
    with pytest.raises(ValueError, match="object"):
        validate_rule_toggles(["B3"])


def test_validate_accepts_partial():
    assert validate_rule_toggles({"B8": False}) is None


def test_from_mapping_is_frozen():
    toggles = RuleToggles.from_mapping({}, DEFAULT_RULES)
    with pytest.raises(AttributeError):
        toggles.b3 = False  # type: ignore[misc]
