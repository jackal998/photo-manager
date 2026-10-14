"""Tests for scripts/docs_reconcile.py (the /docs-reconcile helper).

Pure functions only: no git, no network. Each fixture is a small register or
log text written inline, so a case shows the input that decides its result.
"""
from __future__ import annotations

import pytest

import scripts.docs_reconcile as dr

REGISTER = """\
# Register

### R-LOCK-1 — A locked row is refused

- **Rule:** A write on a locked row answers 409.
- **Owning code:** `core/review.py::set_decisions`, `core/action.py::bulk_decide`, `core/review.py::set_decisions`
- **Pins:** `tests/test_review.py::TestGate::test_locked_409`, `qa/web/scenarios/s32_lock.py`, `tests/test_review.py::TestGate::test_locked_409`
- **Status:** pinned

### R-EXEC-1 — Execute scope

- **Rule:** Only the visible rows are committed.
- **Owning code:** `frontend/src/components/ExecuteDialog.tsx::visibleSelectedPaths`
- **Pins:** `frontend/src/components/ExecuteDialog.test.tsx::"keeps only visible rows"`
- **Status:** pinned

### R-DELETE-1 — Delete goes to the Recycle Bin

- **Rule:** Each file goes to the Recycle Bin by default.
- **Owning code:** `infrastructure/delete_service.py::delete_to_recycle`
- **Pins:** none
- **Status:** unpinned
"""

LOG = """\
# Docs reconcile log

## 2026-10-01 — baseline
- SHA: 1111111
- Reviewer: orchestrator

## 2026-10-10 — batch after #960
- SHA: 2222222aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
- Reviewer: orchestrator
"""


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("feat(web): hint when HEVC cannot decode natively (#969)", 969),
        ("docs(behaviour): add the rules register (#971)", 971),
        ("chore: no PR number here", None),
        ("fix: a number in the middle (#3) and then text", None),
    ],
)
def test_parse_pr_number_reads_only_a_trailing_squash_suffix(subject, expected):
    assert dr.parse_pr_number(subject) == expected


def test_last_logged_sha_is_the_sha_of_the_last_entry():
    assert dr.last_logged_sha(LOG) == "2222222aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def test_last_logged_sha_reads_a_crlf_log():
    assert dr.last_logged_sha(LOG.replace("\n", "\r\n")) == "2222222aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def test_last_logged_sha_is_none_for_a_log_without_entries():
    assert dr.last_logged_sha("# Docs reconcile log\n\nNo entries yet.\n") is None


def test_parse_rules_extracts_owning_and_pin_paths_and_skips_none_pins():
    rules = {rule.rule_id: rule for rule in dr.parse_rules(REGISTER)}
    # A path cited twice in one rule is listed once, in first-seen order.
    assert rules["R-LOCK-1"].owners == ("core/review.py", "core/action.py")
    assert rules["R-LOCK-1"].pins == (
        "tests/test_review.py",
        "qa/web/scenarios/s32_lock.py",
    )
    assert rules["R-EXEC-1"].pins == ("frontend/src/components/ExecuteDialog.test.tsx",)
    assert rules["R-DELETE-1"].pins == ()


def test_rules_at_risk_matches_only_owning_code_paths():
    rules = dr.parse_rules(REGISTER)
    assert dr.rules_at_risk(rules, {"core/review.py"}) == {"R-LOCK-1": ["core/review.py"]}
    assert dr.rules_at_risk(rules, {"infrastructure/delete_service.py"}) == {
        "R-DELETE-1": ["infrastructure/delete_service.py"]
    }
    # A pin file is not owning code: changing a test does not put the rule at risk as code.
    assert dr.rules_at_risk(rules, {"tests/test_review.py"}) == {}


def test_pins_at_risk_matches_test_vitest_and_scenario_pin_files():
    rules = dr.parse_rules(REGISTER)
    assert dr.pins_at_risk(rules, {"tests/test_review.py"}) == {
        "R-LOCK-1": ["tests/test_review.py"]
    }
    assert dr.pins_at_risk(rules, {"qa/web/scenarios/s32_lock.py"}) == {
        "R-LOCK-1": ["qa/web/scenarios/s32_lock.py"]
    }
    assert dr.pins_at_risk(rules, {"frontend/src/components/ExecuteDialog.test.tsx"}) == {
        "R-EXEC-1": ["frontend/src/components/ExecuteDialog.test.tsx"]
    }
    # Owning code is not a pin, and a rule pinned to `none` can never be at risk through its pins.
    assert dr.pins_at_risk(rules, {"core/review.py", "infrastructure/delete_service.py"}) == {}
