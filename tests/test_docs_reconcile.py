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


def test_unpinned_rules_are_listed_for_unconditional_review():
    # R-DELETE-1 is the only unpinned rule; it has no test net, so it is always re-verified.
    assert [rule.rule_id for rule in dr.unpinned_rules(dr.parse_rules(REGISTER))] == ["R-DELETE-1"]


FEATURES_BASE = """\
## Index

### Alpha

Shows [x](../core/x.py) once.

### Beta

Plain text.
"""

FEATURES_HEAD = FEATURES_BASE.replace("once.", "twice.")

FEATURES_DIFF = """\
diff --git a/docs/features.md b/docs/features.md
--- a/docs/features.md
+++ b/docs/features.md
@@ -5 +5 @@
-Shows [x](../core/x.py) once.
+Shows [x](../core/x.py) twice.
"""


def test_touched_entries_maps_the_docs_impact_diff_and_reads_surface_paths():
    # The entries live in docs/features/scan.md (#958). The line-5 hunk lies inside
    # Alpha, so only Alpha is touched; its key carries the file it sits in.
    area = "docs/features/scan.md"
    diff = FEATURES_DIFF.replace("docs/features.md", area)
    touched, labels, entries = dr.touched_entries(
        {area: FEATURES_BASE}, {area: FEATURES_HEAD}, diff
    )
    assert touched == {"alpha"}
    assert labels == {"alpha": "Alpha", "beta": "Beta"}
    assert entries == [
        ("Alpha", "alpha", {"core/x.py"}),
        ("Beta", "beta", set()),
    ]


def test_surface_paths_keeps_source_links_and_drops_tests_scenarios_and_docs():
    lines = [
        "See [a](../core/a.py#L3) and `core/b/c.ts`.",
        "Layer-1: [t](../tests/test_x.py) and [v](../frontend/src/X.test.tsx) and `docs/y.md`.",
    ]
    assert dr.surface_paths(lines) == {"core/a.py", "core/b/c.ts"}


def test_stale_candidates_flags_untouched_entries_whose_cited_source_changed():
    entries = [
        ("Alpha", "alpha", {"core/x.py"}),
        ("Beta", "beta", {"core/y.py"}),
        ("Gamma", "gamma", {"core/z.py"}),
    ]
    stale = dr.stale_candidates(entries, touched={"gamma"}, changed={"core/x.py", "core/z.py"})
    assert stale == [("Alpha", ["core/x.py"])]


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["app/web/routes/x.py"], True),
        (["frontend/src/a.ts"], True),
        (["docs/x.md", "scripts/y.py"], False),
        (["frontend/test.md"], False),
    ],
)
def test_is_behaviour_bearing_follows_the_behaviour_source_roots(files, expected):
    assert dr.is_behaviour_bearing(files) is expected
