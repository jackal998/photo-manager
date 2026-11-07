"""Named, switchable auto-select rules (PR-4a, PRD v2 §6 / §11 item 4).

Each rule id names one decision the engine makes about a duplicate row.
The owner can turn a rule off; the default set is the PRD's answer for
the owner's library. This module is pure data: it imports nothing from
``scanner`` so the scan, the route layer and the dialog share one shape.

Scope of the toggles (see ``docs/audits/auto-select-prd-2026-11/pr4-design.md``):

* B5 (burst) and B7 (screenshot) act at classify time in every mode.
* B3, B4 and B8 only narrow the aggressive delete set. No toggle adds a delete.

``RULES_VERSION`` is written to the scan log so a later reader can tell
which rule set produced a scan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

RULES_VERSION = "v1"
RULE_IDS: tuple[str, ...] = ("B3", "B4", "B5", "B7", "B8")


@dataclass(frozen=True)
class RuleToggles:
    """One on/off flag per rule id."""

    b3: bool
    b4: bool
    b5: bool
    b7: bool
    b8: bool

    def to_dict(self) -> dict[str, bool]:
        """Return the uppercase-id mapping the API and settings file use."""
        return {
            "B3": self.b3,
            "B4": self.b4,
            "B5": self.b5,
            "B7": self.b7,
            "B8": self.b8,
        }

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object], base: "RuleToggles") -> "RuleToggles":
        """Merge a partial mapping over ``base``.

        Only strict booleans for known ids are honoured; anything else
        keeps the base value. Use this on validated input.
        """
        values = base.to_dict()
        for rule_id in RULE_IDS:
            candidate = mapping.get(rule_id)
            if isinstance(candidate, bool):
                values[rule_id] = candidate
        return cls(**{key.lower(): value for key, value in values.items()})


# PRD defaults for the owner's library. B7 stays ON here: today's behaviour.
DEFAULT_RULES = RuleToggles(b3=True, b4=True, b5=True, b7=True, b8=True)

# Master behaviour before PR-4a. ``apply_best_copy`` passes this explicitly.
LEGACY_RULES = RuleToggles(b3=True, b4=True, b5=True, b7=True, b8=False)


def resolve_rule_toggles(value: object) -> RuleToggles:
    """Resolve a stored value to toggles, reading it defensively.

    A non-mapping gives ``DEFAULT_RULES``. Per known id, only a strict
    bool wins; anything else takes the default. Unknown ids are ignored,
    so a bad settings file never breaks a scan.
    """
    if not isinstance(value, Mapping):
        return DEFAULT_RULES
    return RuleToggles.from_mapping(value, DEFAULT_RULES)


def describe_rules(rules: RuleToggles) -> str:
    """One scan-log line naming the rule version and each rule's state."""
    states = ", ".join(
        f"{rule_id} {'on' if flag else 'off'}" for rule_id, flag in rules.to_dict().items()
    )
    return f"Rules {RULES_VERSION}: {states}."


def validate_rule_toggles(value: object) -> None:
    """Raise ``ValueError`` unless ``value`` is a valid partial rule object.

    Used on write (PATCH and POST). A partial object is accepted.
    ``isinstance(v, bool)`` is strict, so ``1`` and ``"true"`` are refused.
    """
    if not isinstance(value, Mapping):
        raise ValueError("auto-select rules must be an object")
    for rule_id, flag in value.items():
        if rule_id not in RULE_IDS:
            raise ValueError(f"unknown auto-select rule id: {rule_id!r}")
        if not isinstance(flag, bool):
            raise ValueError(f"auto-select rule {rule_id} must be true or false")
