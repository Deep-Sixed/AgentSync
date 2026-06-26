"""Thin FastMCP transport wrapper for the AgentSync Rule Server.

This is a TRANSPORT shim only. All decision logic lives in rule_resolver.py
and is testable without MCP, ContextForge, A2A, or any gateway. This file
exists solely to expose evaluate_task over MCP so the server can later be
registered behind ContextForge (the IBM gateway). ContextForge is the network
door; AgentSync is the system.

Run locally (stdio):      python -m agentsync.rules.mcp_server
Run as HTTP service:      AGENTSYNC_TRANSPORT=http python -m agentsync.rules.mcp_server

Requires the optional `mcp` extra:  pip install 'agentsync[mcp]'
"""

import os
from pathlib import Path

from .models import RuleEvaluation, TaskContext
from .rule_resolver import RuleResolver

# Resolver is process-global; rules load once at startup.
_RESOLVER = RuleResolver(
    rules_path=Path(os.environ.get("AGENTSYNC_RULES_PATH", "storage/rules/rules.yaml")),
    capabilities_path=Path(os.environ.get("AGENTSYNC_CAPABILITIES_PATH", "storage/rules/capabilities.yaml")),
    pre_obligations_path=Path(os.environ.get("AGENTSYNC_PRE_OBLIGATIONS_PATH", "storage/obligations/pre_obligations.jsonl")),
)


def _build_server():
    """Construct the FastMCP server. Imported lazily so the engine stays
    importable/testable even when `mcp` isn't installed."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("agentsync_rules_mcp")

    @mcp.tool(
        name="agentsync_evaluate_task",
        annotations={
            "title": "Evaluate task against AgentSync rules",
            "readOnlyHint": False,      # may append a pre_obligation record on confirmed-missing
            "destructiveHint": False,
            "idempotentHint": False,    # MISSING status appends a record each call
            "openWorldHint": False,
        },
    )
    def agentsync_evaluate_task(task: TaskContext) -> RuleEvaluation:
        """Decide whether a task warrants a reusable skill.

        Resolver order: pattern match -> capability fallback -> none.
        Emits a non-authoritative pre_obligation record only when a skill is
        expected AND skill_match=missing. NEVER mints the authoritative
        skill_obligation_token (that is the Skill Builder Enforcer's job).

        Returns a RuleEvaluation whose authoritative_skill_obligation_token is
        ALWAYS null.
        """
        return _RESOLVER.evaluate_task(task)

    return mcp


def main() -> None:
    server = _build_server()
    transport = os.environ.get("AGENTSYNC_TRANSPORT", "stdio")
    if transport == "http":
        server.run(transport="streamable_http", port=int(os.environ.get("AGENTSYNC_PORT", "8081")))
    else:
        server.run()


if __name__ == "__main__":
    main()
