"""Re-export of qa.scenarios._config for the web harness.

Web driver scripts that need per-scenario source-folder configuration
should import from here rather than directly from qa.scenarios._config.
This keeps the import path consistent within qa.web.* and makes it
possible to overlay or extend config without touching the Qt harness.
"""
from __future__ import annotations

# Re-export everything so callers can do:
#   from qa.web._config import build_settings, write_settings
from qa.scenarios._config import (  # noqa: F401
    PRUNE_PREF_OVERRIDES,
    SCENARIO_SOURCES,
    build_settings,
    write_settings,
)
