"""Thin FastMCP transport wrapper for the AgentSync Skill Server.

Transport shim only. All logic lives in skill_server.py and is testable
without MCP. Exposes the four-tool contract so the server can register behind
ContextForge. Requires the optional `mcp` extra.

Run locally (stdio):   python -m agentsync.skills.mcp_server
"""

import os
from pathlib import Path
from typing import List, Optional

from .skill_server import (
    ActivatedSkill,
    SkillFile,
    SkillLookupResult,
    SkillQuery,
    SkillServer,
    SkillSummary,
)

_SERVER = SkillServer(
    approved_root=Path(os.environ.get("AGENTSYNC_APPROVED_ROOT", "storage/skills/approved"))
)


def _build_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("agentsync_skills_mcp")

    @mcp.tool(
        name="agentsync_list_skills",
        annotations={"title": "List approved skills", "readOnlyHint": True,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    def agentsync_list_skills() -> List[SkillSummary]:
        """List all approved skills with canonical_id, name, description, and families."""
        return _SERVER.list_skills()

    @mcp.tool(
        name="agentsync_read_skill_file",
        annotations={"title": "Read a skill's SKILL.md", "readOnlyHint": True,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    def agentsync_read_skill_file(canonical_id: str) -> Optional[SkillFile]:
        """Return the full SKILL.md content for a canonical_id, or null if unknown."""
        return _SERVER.read_skill_file(canonical_id)

    @mcp.tool(
        name="agentsync_activate_skill",
        annotations={"title": "Activate a skill (load instructions)", "readOnlyHint": True,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    def agentsync_activate_skill(canonical_id: str) -> Optional[ActivatedSkill]:
        """Load a skill's full instructions for injection into agent context."""
        return _SERVER.activate_skill(canonical_id)

    @mcp.tool(
        name="agentsync_find_matching_skill",
        annotations={"title": "Find a skill matching a task", "readOnlyHint": True,
                     "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    def agentsync_find_matching_skill(query: SkillQuery) -> SkillLookupResult:
        """Deterministically look up whether an approved skill covers a task.

        Conservative about 'found': weak description overlap returns 'missing'
        with the near-miss surfaced, so the Enforcer's gap verdict is trustworthy.
        """
        return _SERVER.find_matching_skill(query)

    return mcp


def main() -> None:
    server = _build_server()
    transport = os.environ.get("AGENTSYNC_TRANSPORT", "stdio")
    if transport == "http":
        server.run(transport="streamable_http", port=int(os.environ.get("AGENTSYNC_SKILLS_PORT", "8082")))
    else:
        server.run()


if __name__ == "__main__":
    main()
