"""TeammateIdle hook: log when a teammate has been idle longer than expected.

A teammate going idle immediately after a task is normal — they've
done their work and are waiting for the next assignment. But a
teammate idle for more than :data:`IDLE_WARN_SECONDS` while the team
still has pending tasks is suspicious: the LEAD may have forgotten to
assign work, or the teammate hit a permission prompt that fell
through the cracks.

This hook NEVER blocks — it only writes a one-line log entry to
:data:`LOG_PATH`. Operators can grep the log to spot dropped tasks.

The hook is intentionally conservative on payload shape: the
Agent-Teams `TeammateIdle` event schema is not yet documented at the
time this hook ships, so the hook sniffs known keys and silently
no-ops on unknown shapes.

Hook protocol
-------------
* stdin  — JSON, expected shape (subject to confirmation):
  ``{"event": "TeammateIdle", "teammate": {"name": "...", "idleSeconds": N, ...}}``
* exit 0 — always (this hook never blocks).
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

IDLE_WARN_SECONDS = 180
LOG_PATH = Path(".claude") / "team_idle.log"


def _extract(payload: dict) -> tuple[str | None, float | None]:
    """Sniff (teammate_name, idle_seconds) from the payload.

    Returns (None, None) when the payload doesn't look like a
    TeammateIdle event we recognise — caller silently no-ops.
    """
    teammate = payload.get("teammate")
    if isinstance(teammate, dict):
        name = teammate.get("name") if isinstance(teammate.get("name"), str) else None
        seconds = teammate.get("idleSeconds")
        if isinstance(seconds, (int, float)):
            return name, float(seconds)
    # Fallback: some payloads might surface seconds at top level.
    seconds = payload.get("idleSeconds")
    name = payload.get("name") if isinstance(payload.get("name"), str) else None
    if isinstance(seconds, (int, float)):
        return name, float(seconds)
    return None, None


def _log_line(name: str | None, seconds: float) -> str:
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    who = name or "<unknown>"
    return f"{ts}  idle_warn  teammate={who}  idleSeconds={seconds:.0f}\n"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    name, seconds = _extract(payload)
    if seconds is None or seconds < IDLE_WARN_SECONDS:
        return 0
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(_log_line(name, seconds))
    except OSError:
        # Logging is best-effort; never block on filesystem hiccups.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
