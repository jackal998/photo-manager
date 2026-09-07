"""Domain constants shared across the web, service and scanner layers.

Pure wire values with no client dependency, so the web API,
``core.app_service`` services and the headless invariant tests can all
import them without reaching into a UI package.
"""
from __future__ import annotations

# Stored user_decision value for the deferred "remove from list" (ignore)
# flow. Displayed in the Action column via the localised
# "remove from list" label, and applied at execute time
# (outcome='ignored', drop from vm). The wire value is internal-only; the
# user-facing label is ``decision.remove_from_list``.
#
# ``core.app_service.review_service.VALID_DECISIONS`` must stay in sync with
# this value — the drift-check tests in tests/test_review_service.py and
# tests/test_web_qt_free.py pin that contract.
IGNORE_DECISION: str = "ignore"
