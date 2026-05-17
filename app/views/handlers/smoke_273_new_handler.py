"""SMOKE TEST SENTINEL (#273 pr-gates verification) — REVERT BEFORE MERGE.

This file exists solely to add a NEW file under app/views/handlers/
so qa_scenario_guard fires in CI (user-facing change without a
matching qa/scenarios/sNN_*.py driver). The PR carrying this commit
must NOT merge.
"""


def smoke_handler_stub() -> None:
    """No-op smoke-test stub."""
    return None
