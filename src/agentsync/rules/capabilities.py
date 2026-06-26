"""Capability fallback resolver.

Consulted ONLY when the pattern layer returns no confident match. Maps a
capability hint (from task_type or command_family) to a capability_family,
then applies that family's defaults. No LLM, no network.
"""


from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .models import EnforcementLevel, MatchedRule


@dataclass
class CapabilityMap:
    hint_to_capability: Dict[str, str]
    capability_defaults: Dict[str, Dict[str, Any]]


def load_capabilities(cap_path: Path) -> CapabilityMap:
    """Load the capability map. Missing file -> empty maps (no crash)."""
    if not cap_path.exists():
        return CapabilityMap({}, {})
    data = yaml.safe_load(cap_path.read_text(encoding="utf-8")) or {}
    return CapabilityMap(
        hint_to_capability={k.lower(): v for k, v in (data.get("hint_to_capability") or {}).items()},
        capability_defaults=data.get("capability_defaults") or {},
    )


def resolve_capability(task, cap: CapabilityMap) -> Optional[MatchedRule]:
    """Map task hints -> capability_family -> defaults. None if no hint matches."""
    # Try the strong, explicit hints first: command_family, then task_type.
    family: Optional[str] = None
    for hint in (task.command_family, task.task_type):
        if hint and hint.lower() in cap.hint_to_capability:
            family = cap.hint_to_capability[hint.lower()]
            break

    if family is None:
        return None

    defaults = cap.capability_defaults.get(family, {})
    return MatchedRule(
        rule_id=f"capability:{family}",
        skill_expected=defaults.get("skill_family") is not None,
        skill_family=defaults.get("skill_family"),
        capability_family=family,
        required_evidence=list(defaults.get("required_evidence") or []),
        enforcement_level=EnforcementLevel(defaults.get("enforcement_level", "none")),
    )
