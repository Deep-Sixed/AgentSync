"""Skill-lookup seam.

The Enforcer must confirm a skill gap before minting an obligation. It does that
through this thin interface, NOT by importing SkillServer directly. Today the
only implementation calls the in-process SkillServer; later a remote
implementation can call the Skill Server as an MCP tool through ContextForge.
Swapping implementations is a one-class change that never touches obligation
logic or its tests.
"""

from pathlib import Path
from typing import Optional, Protocol

from agentsync.skills.skill_server import SkillLookupResult, SkillQuery, SkillServer


class SkillLookup(Protocol):
    """Confirm whether an approved skill covers a task."""

    def find_matching_skill(self, query: SkillQuery) -> SkillLookupResult:
        ...


class InProcessSkillLookup:
    """In-process lookup backed by a local SkillServer. Single-box deployment."""

    def __init__(self, approved_root: Optional[Path] = None) -> None:
        self._server = SkillServer(approved_root=approved_root)

    def find_matching_skill(self, query: SkillQuery) -> SkillLookupResult:
        return self._server.find_matching_skill(query)

    def reload(self) -> None:
        self._server.reload()
