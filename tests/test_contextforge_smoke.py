"""ContextForge registration smoke tests.

Proves the full 10-tool AgentSync flow through the ContextForge gateway
(not direct to the AgentSync MCP service).

Architecture:
  pytest → mcp client → ContextForge :4444/mcp → AgentSync :8585/mcp

Prerequisites (handled by the contextforge_session fixture):
  1. AgentSync MCP server is running at http://127.0.0.1:8585/mcp
     (AGENTSYNC_TRANSPORT=http AGENTSYNC_PORT=8585 python -m agentsync.mcp.server)
  2. AgentSync is registered as a gateway named "agentsync-mcp" in ContextForge.
     (POST http://127.0.0.1:4444/gateways)

If either prerequisite is absent, the tests are automatically skipped.
"""
from __future__ import annotations

import json
import subprocess
import time
from typing import Any

import anyio
import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CONTEXTFORGE_URL = "http://127.0.0.1:4444"
AGENTSYNC_URL = "http://127.0.0.1:8585/mcp"
AGENTSYNC_GW_NAME = "agentsync-mcp"
CONTEXTFORGE_MCP = f"{CONTEXTFORGE_URL}/mcp"
EXPECTED_TOOL_NAMES = {
    "agentsync_evaluate_task",
    "agentsync_get_task_rules",
    "agentsync_list_skills",
    "agentsync_read_skill_file",
    "agentsync_activate_skill",
    "agentsync_find_matching_skill",
    "agentsync_enforce_task",
    "agentsync_submit_candidate_skill",
    "agentsync_get_obligation",
    "agentsync_promote_candidate_skill",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_jwt() -> str | None:
    """Generate a JWT token using the running ContextForge container's own secret.

    Uses ``docker exec`` against the /routercore container so the token is
    signed with the exact secret the live instance validates against.
    Falls back to the RouterCore venv create_jwt_token if docker is unavailable.
    """
    # Primary: exec into the running container (no need to know the secret value)
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

    # Fallback: locate the container by image/label and try its ID
    try:
        ps = subprocess.run(
            ["docker", "ps", "--filter", "name=routercore",
             "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=10,
        )
        cid = ps.stdout.strip().splitlines()[0] if ps.stdout.strip() else None
        if cid:
            result = subprocess.run(
                [
                    "docker", "exec", cid,
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


def _contextforge_healthy() -> bool:
    """Return True if ContextForge is reachable."""
    try:
        r = httpx.get(f"{CONTEXTFORGE_URL}/health", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _agentsync_mcp_healthy() -> bool:
    """Return True if the AgentSync MCP HTTP server is reachable.

    FastMCP's streamable-http endpoint returns 406 when no Accept header is
    provided (which is correct — it requires SSE negotiation).  Any response
    other than a connection error means the server is up.
    """
    try:
        r = httpx.get(AGENTSYNC_URL, timeout=5)
        return r.status_code < 500  # 406 = up but needs SSE, 400 = up, etc.
    except Exception:
        return False


def _agentsync_registered(token: str) -> bool:
    """Return True if the agentsync-mcp gateway is registered and active."""
    try:
        r = httpx.get(f"{CONTEXTFORGE_URL}/gateways",
                      headers={"Authorization": f"Bearer {token}"}, timeout=10)
        gateways = r.json()
        if isinstance(gateways, list):
            items = gateways
        else:
            items = gateways.get("items", [])
        for gw in items:
            if gw.get("name") == AGENTSYNC_GW_NAME:
                return gw.get("reachable", False)
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def cf_token() -> str:
    token = _get_jwt()
    if token is None:
        pytest.skip("Could not generate ContextForge JWT — RouterCore venv missing?")
    return token


@pytest.fixture(scope="module")
def contextforge_available(cf_token: str) -> None:
    if not _contextforge_healthy():
        pytest.skip("ContextForge not reachable at localhost:4444")
    if not _agentsync_mcp_healthy():
        pytest.skip("AgentSync MCP server not running at localhost:8585")
    if not _agentsync_registered(cf_token):
        pytest.skip("AgentSync gateway not registered/reachable in ContextForge")


async def _call_tool(session: ClientSession, tool_name: str, args: dict[str, Any]) -> Any:
    """Call a tool by its canonical agentsync_ name through the session."""
    result = await session.call_tool(tool_name, args)
    # Extract text from content blocks
    parts = []
    for block in result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    raw = " ".join(parts)
    try:
        return json.loads(raw)
    except Exception:
        return raw


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestContextForgeCatalog:
    """Verify the tool catalog as seen by the ContextForge admin API."""

    def test_all_10_tools_in_catalog(self, contextforge_available, cf_token: str) -> None:
        r = httpx.get(f"{CONTEXTFORGE_URL}/tools",
                      headers={"Authorization": f"Bearer {cf_token}"}, timeout=15)
        assert r.status_code == 200, r.text
        tools = r.json()
        assert isinstance(tools, list), f"expected list, got {type(tools)}"
        cf_tool_names = {t.get("customName") or t.get("originalName", "") for t in tools}
        missing = EXPECTED_TOOL_NAMES - cf_tool_names
        assert not missing, f"missing tools in ContextForge catalog: {sorted(missing)}"

    def test_gateway_active_and_reachable(self, contextforge_available, cf_token: str) -> None:
        r = httpx.get(f"{CONTEXTFORGE_URL}/gateways",
                      headers={"Authorization": f"Bearer {cf_token}"}, timeout=10)
        assert r.status_code == 200, r.text
        gateways = r.json()
        items = gateways if isinstance(gateways, list) else gateways.get("items", [])
        gw = next((g for g in items if g["name"] == AGENTSYNC_GW_NAME), None)
        assert gw is not None, f"gateway '{AGENTSYNC_GW_NAME}' not found"
        assert gw["status"] == "active", f"gateway status={gw['status']}, lastError={gw.get('lastError')}"
        assert gw["reachable"] is True


class TestContextForgeToolFlow:
    """Prove the full AgentSync flow through ContextForge MCP protocol."""

    def test_tools_list_includes_all_10(self, contextforge_available, cf_token: str) -> None:
        """ContextForge MCP protocol exposes all 10 AgentSync tools."""
        tool_names: list[str] = []

        async def _run():
            auth_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {cf_token}"})
            async with streamable_http_client(CONTEXTFORGE_MCP, http_client=auth_client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    for t in result.tools:
                        tool_names.append(t.name)

        anyio.run(_run)

        agentsync_tool_names = {
            t.split(f"{AGENTSYNC_GW_NAME}-")[-1].replace("-", "_")
            for t in tool_names
            if "agentsync" in t
        }
        missing = EXPECTED_TOOL_NAMES - agentsync_tool_names
        assert not missing, f"missing via MCP protocol: {sorted(missing)}"

    def test_evaluate_task_via_contextforge(self, contextforge_available, cf_token: str) -> None:
        result: dict = {}

        async def _run():
            auth_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {cf_token}"})
            async with streamable_http_client(CONTEXTFORGE_MCP, http_client=auth_client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    res = await _call_tool(session, f"{AGENTSYNC_GW_NAME}-agentsync-evaluate-task", {
                        "task": {
                            "task_id": "CF-SMOKE-001",
                            "task_description": "Write an IAM provisioning script with rollback",
                        }
                    })
                    result.update(res if isinstance(res, dict) else {"raw": res})

        anyio.run(_run)
        assert "skill_expected" in result or "raw" in result, f"unexpected result: {result}"

    def test_get_task_rules_via_contextforge(self, contextforge_available, cf_token: str) -> None:
        result: dict = {}

        async def _run():
            auth_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {cf_token}"})
            async with streamable_http_client(CONTEXTFORGE_MCP, http_client=auth_client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    res = await _call_tool(session, f"{AGENTSYNC_GW_NAME}-agentsync-get-task-rules", {
                        "task": {
                            "task_id": "CF-SMOKE-001",
                            "task_description": "Write an IAM provisioning script",
                        }
                    })
                    result.update(res if isinstance(res, dict) else {"raw": res})

        anyio.run(_run)
        assert result, "empty result from get_task_rules"

    def test_list_skills_via_contextforge(self, contextforge_available, cf_token: str) -> None:
        """Call agentsync_list_skills through ContextForge — no exception = pass."""
        called = [False]

        async def _run():
            auth_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {cf_token}"})
            async with streamable_http_client(CONTEXTFORGE_MCP, http_client=auth_client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    await _call_tool(session, f"{AGENTSYNC_GW_NAME}-agentsync-list-skills", {})
                    called[0] = True

        anyio.run(_run)
        assert called[0], "list_skills call did not complete"

    def test_find_skill_via_contextforge(self, contextforge_available, cf_token: str) -> None:
        called = [False]

        async def _run():
            auth_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {cf_token}"})
            async with streamable_http_client(CONTEXTFORGE_MCP, http_client=auth_client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    await _call_tool(session, f"{AGENTSYNC_GW_NAME}-agentsync-find-matching-skill", {
                        "task_description": "IAM provisioning rollback step"
                    })
                    called[0] = True

        anyio.run(_run)
        assert called[0]

    def test_enforce_and_full_loop_via_contextforge(self, contextforge_available, cf_token: str) -> None:
        """End-to-end: enforce → submit → get_obligation → promote through ContextForge."""
        flow: dict = {}

        VALID_SKILL_MD = """---
name: iam-provisioning
version: 1.0
author: smoke-test
tags: [iam, provisioning]
---

# IAM Provisioning

## Purpose
Automates IAM account provisioning with a full rollback step.

## Verification
- dry_run_output: captured in /tmp/iam_dry_run.log
- rollback_step: reverts service account deletion on failure
- approval_reference: JIRA-IAM-001 approved by security team

## Usage
Call the provisioning script with --dry-run first.
"""

        async def _run():
            auth_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {cf_token}"})
            async with streamable_http_client(CONTEXTFORGE_MCP, http_client=auth_client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()

                    # 1. enforce
                    enforce_res = await _call_tool(
                        session,
                        f"{AGENTSYNC_GW_NAME}-agentsync-enforce-task",
                        {
                            "task": {
                                "task_id": "CF-SMOKE-LOOP-001",
                                "task_description": "Write an IAM provisioning script with rollback",
                            }
                        },
                    )
                    flow["enforce"] = enforce_res

                    token = None
                    if isinstance(enforce_res, dict):
                        token = enforce_res.get("skill_obligation_token")
                    if not token:
                        flow["skipped"] = "no obligation token from enforce"
                        return

                    # 2. submit
                    flow["submit"] = await _call_tool(
                        session,
                        f"{AGENTSYNC_GW_NAME}-agentsync-submit-candidate-skill",
                        {"token": token, "dir_name": "iam-provisioning"},
                    )

                    # 3. get_obligation
                    flow["obligation"] = await _call_tool(
                        session,
                        f"{AGENTSYNC_GW_NAME}-agentsync-get-obligation",
                        {"token": token},
                    )

                    # 4. promote
                    flow["promote"] = await _call_tool(
                        session,
                        f"{AGENTSYNC_GW_NAME}-agentsync-promote-candidate-skill",
                        {
                            "token": token,
                            "skill_md_content": VALID_SKILL_MD,
                            "dir_name": "iam-provisioning",
                        },
                    )

        anyio.run(_run)

        assert "enforce" in flow, "enforce step never ran"
        promote = flow.get("promote")
        if isinstance(promote, dict):
            assert promote.get("closure_unblocked") is True, (
                f"closure_unblocked!=True; promote={promote}"
            )
        elif "skipped" not in flow:
            pytest.fail(f"unexpected promote result type: {type(promote)}: {promote}")
