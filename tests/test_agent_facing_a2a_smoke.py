"""Agent-facing A2A smoke — ContextForge MCP only.

Proves a real agent client can complete the full AgentSync promotion loop
using only ContextForge's MCP surface. No admin REST, no direct AgentSync
HTTP, no architecture changes.

Architecture (agent view):
  Agent MCP client
    ↓  Authorization: Bearer $ROUTERCORE_MCP_BEARER_TOKEN
  ContextForge  http://127.0.0.1:4444/mcp
    ↓  gateway federation (agentsync-mcp)
  AgentSync MCP service  (backend — not contacted by this test)

Required flow (single session, ordered):
  1. agentsync_get_task_rules
  2. agentsync_evaluate_task
  3. agentsync_find_matching_skill
  4. agentsync_enforce_task
  5. agentsync_submit_candidate_skill
  6. agentsync_get_obligation
  7. agentsync_promote_candidate_skill
  → closure_unblocked=True

Auth (agent pattern):
  Prefer ROUTERCORE_MCP_BEARER_TOKEN from the environment (mint via
  eval "$(/mnt/jarvis-data/projects/EVECOR/bin/evecor-routercore-mcp-bearer)").
  Fallback: docker exec routercore create_jwt_token for local dev.

Skips gracefully when ContextForge is unreachable or AgentSync tools are
not visible via MCP list_tools.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import anyio
import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

CONTEXTFORGE_URL = "http://127.0.0.1:4444"
CONTEXTFORGE_MCP = f"{CONTEXTFORGE_URL}/mcp"
AGENTSYNC_GW_NAME = "agentsync-mcp"

TASK_ID = "A2A-SMOKE-001"
TASK_DESCRIPTION = (
    "Provision a brand new birthright entitlement set for a new employee identity"
)
SKILL_DIR_NAME = "new-birthright-provisioning-skill"
CANDIDATE_PATH = f"staging/{TASK_ID}/{SKILL_DIR_NAME}/SKILL.md"

VALID_SKILL_MD = """\
---
name: new-birthright-provisioning-skill
description: Provisions birthright access for a new employee via SailPoint. Use when an IAM provisioning task requires a new birthright entitlement workflow not covered by existing skills.
---

# Overview
Provisions the full birthright entitlement set for a new employee identity
via SailPoint IdentityIQ, covering joiner events where no existing skill applies.

# When to Use
Use when an IAM provisioning task requires authoring a new birthright entitlement
workflow that is confirmed missing from the approved skill registry.

# Process
1. Resolve the identity from the HR event feed and confirm no existing skill applies.
2. Select the appropriate birthright entitlement set for the identity role.
3. Execute the provisioning workflow in dry-run mode and capture dry_run_output.
4. Review dry_run_output for unexpected or over-provisioned grants.
5. Obtain approval_reference from the access request system before applying.
6. Apply the entitlement set, recording the rollback_step for every granted item.

# Verification
- dry_run_output reviewed and attached to the access request ticket
- approval_reference captured from the provisioning system before any live apply
- rollback_step documented for every granted birthright entitlement
"""

REQUIRED_LOGICAL_TOOLS = (
    "agentsync_get_task_rules",
    "agentsync_evaluate_task",
    "agentsync_find_matching_skill",
    "agentsync_enforce_task",
    "agentsync_submit_candidate_skill",
    "agentsync_get_obligation",
    "agentsync_promote_candidate_skill",
)


def _tool(gateway_name: str, logical: str) -> str:
    """Map canonical tool name to ContextForge federated MCP name."""
    slug = logical.removeprefix("agentsync_").replace("_", "-")
    return f"{gateway_name}-agentsync-{slug}"


def _agent_bearer_token() -> str | None:
    """Return agent bearer token — env first, then docker mint fallback."""
    token = os.environ.get("ROUTERCORE_MCP_BEARER_TOKEN", "").strip()
    if token:
        return token

    try:
        result = subprocess.run(
            [
                "docker", "exec", "routercore",
                "python", "-m", "mcpgateway.utils.create_jwt_token",
                "--username", "admin@nexus.local", "--admin",
            ],
            capture_output=True, text=True, timeout=20,
        )
        for line in result.stdout.splitlines():
            if line.startswith("ey"):
                return line.strip()
    except Exception:
        pass

    return None


def _contextforge_reachable() -> bool:
    try:
        return httpx.get(f"{CONTEXTFORGE_URL}/health", timeout=5).status_code == 200
    except Exception:
        return False


async def _call_tool(session: ClientSession, tool_name: str, args: dict[str, Any]) -> Any:
    result = await session.call_tool(tool_name, args)
    parts = [block.text for block in result.content if hasattr(block, "text")]
    raw = " ".join(parts)
    try:
        return json.loads(raw)
    except Exception:
        return raw


async def _agent_session(token: str):
    """Async context: MCP session as an agent client through ContextForge only."""
    auth_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    transport = streamable_http_client(CONTEXTFORGE_MCP, http_client=auth_client)
    return auth_client, transport


@pytest.fixture(scope="module")
def agent_token() -> str:
    token = _agent_bearer_token()
    if not token:
        pytest.skip(
            "No ROUTERCORE_MCP_BEARER_TOKEN and JWT mint failed — "
            "run: eval \"$(/mnt/jarvis-data/projects/EVECOR/bin/evecor-routercore-mcp-bearer)\""
        )
    return token


@pytest.fixture(scope="module")
def agent_contextforge_ready(agent_token: str) -> None:
    if not _contextforge_reachable():
        pytest.skip("ContextForge not reachable at localhost:4444")


class TestAgentFacingContextForgeMcp:
    """Agent client sees AgentSync tools and completes promotion through CF only."""

    def test_agent_lists_agentsync_tools(self, agent_contextforge_ready, agent_token: str) -> None:
        """Agent discovers all required tools via MCP tools/list (no admin API)."""
        tool_names: list[str] = []

        async def _run():
            auth_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {agent_token}"})
            async with streamable_http_client(CONTEXTFORGE_MCP, http_client=auth_client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    tool_names.extend(t.name for t in listed.tools)

        anyio.run(_run)

        agentsync_names = {
            n.split(f"{AGENTSYNC_GW_NAME}-")[-1].replace("-", "_")
            for n in tool_names
            if AGENTSYNC_GW_NAME in n
        }
        if not agentsync_names:
            pytest.skip(
                "No agentsync-mcp tools visible via ContextForge MCP — "
                "gateway may be down or unregistered"
            )
        missing = set(REQUIRED_LOGICAL_TOOLS) - agentsync_names
        assert not missing, f"agent missing tools via MCP list: {sorted(missing)}"

    def test_agent_full_promotion_loop_through_contextforge(
        self, agent_contextforge_ready, agent_token: str
    ) -> None:
        """One agent session: rules → evaluate → find → enforce → submit → promote."""
        flow: dict[str, Any] = {}

        async def _run():
            auth_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {agent_token}"})
            async with streamable_http_client(CONTEXTFORGE_MCP, http_client=auth_client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()

                    task_ctx = {"task_id": TASK_ID, "task_description": TASK_DESCRIPTION}

                    # 1. get_task_rules
                    flow["rules"] = await _call_tool(
                        session, _tool(AGENTSYNC_GW_NAME, "agentsync_get_task_rules"),
                        {"task": task_ctx},
                    )

                    # 2. evaluate_task
                    flow["evaluate"] = await _call_tool(
                        session, _tool(AGENTSYNC_GW_NAME, "agentsync_evaluate_task"),
                        {
                            "task": {
                                "task_id": TASK_ID,
                                "task_description": TASK_DESCRIPTION,
                                "task_type": "iam-provisioning",
                            }
                        },
                    )

                    evaluation = flow["evaluate"]
                    assert isinstance(evaluation, dict), f"evaluate failed: {evaluation}"
                    assert evaluation.get("skill_expected") is True, (
                        f"Rule Server did not identify skill gap: {evaluation}"
                    )

                    # 3. find_matching_skill
                    flow["find"] = await _call_tool(
                        session, _tool(AGENTSYNC_GW_NAME, "agentsync_find_matching_skill"),
                        {"task_description": TASK_DESCRIPTION},
                    )

                    # 4. enforce_task — requires prior evaluation result
                    flow["enforce"] = await _call_tool(
                        session, _tool(AGENTSYNC_GW_NAME, "agentsync_enforce_task"),
                        {
                            "evaluation": evaluation,
                            "task_description": TASK_DESCRIPTION,
                        },
                    )

                    token = None
                    if isinstance(flow["enforce"], dict):
                        token = flow["enforce"].get("skill_obligation_token")
                        assert flow["enforce"].get("outcome") == "obligation_open", (
                            f"expected obligation_open: {flow['enforce']}"
                        )
                    assert token, f"enforce did not mint obligation token: {flow['enforce']}"

                    # 5. submit_candidate_skill
                    flow["submit"] = await _call_tool(
                        session, _tool(AGENTSYNC_GW_NAME, "agentsync_submit_candidate_skill"),
                        {"token": token, "candidate_path": CANDIDATE_PATH},
                    )

                    # 6. get_obligation
                    flow["obligation"] = await _call_tool(
                        session, _tool(AGENTSYNC_GW_NAME, "agentsync_get_obligation"),
                        {"token": token},
                    )

                    # 7. promote_candidate_skill
                    flow["promote"] = await _call_tool(
                        session, _tool(AGENTSYNC_GW_NAME, "agentsync_promote_candidate_skill"),
                        {
                            "token": token,
                            "skill_md_content": VALID_SKILL_MD,
                            "dir_name": SKILL_DIR_NAME,
                        },
                    )

        anyio.run(_run)

        assert flow.get("rules"), "get_task_rules returned empty"
        assert isinstance(flow.get("evaluate"), dict), "evaluate_task did not return dict"
        assert flow["evaluate"].get("skill_expected") is not None or "closure_blocked" in flow["evaluate"]

        promote = flow.get("promote")
        assert isinstance(promote, dict), f"promote returned unexpected type: {promote}"
        assert promote.get("closure_unblocked") is True, (
            f"closure_unblocked!=True; promote={promote}"
        )
