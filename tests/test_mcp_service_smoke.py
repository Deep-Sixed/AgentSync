"""MCP service smoke tests — single AgentSync server, no ContextForge.

Tests the unified agentsync.mcp.server over a real stdio MCP transport
using a subprocess + ClientSession. No mocks. No monkeypatching.

Each test starts a fresh server subprocess configured to write into tmp_path
(obligations.jsonl, stele.db, artifacts/) so tests are isolated and leave
no state in the repo's storage/.

Requires:  pip install 'agentsync[mcp,stele,dev]'
Run with:  python -m pytest tests/test_mcp_service_smoke.py -v
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ---------------------------------------------------------------------------
# Repo-relative paths (real rules / skills — read-only in tests)
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]
RULES_PATH = REPO / "storage" / "rules" / "rules.yaml"
CAPS_PATH = REPO / "storage" / "rules" / "capabilities.yaml"
APPROVED_PATH = REPO / "storage" / "skills" / "approved"

EXPECTED_TOOLS = {
    "agentsync_evaluate_task",
    "agentsync_list_skills",
    "agentsync_read_skill_file",
    "agentsync_activate_skill",
    "agentsync_find_matching_skill",
    "agentsync_enforcer_enforce",
    "agentsync_enforcer_submit_candidate",
    "agentsync_enforcer_get_status",
    "agentsync_kanon_promote",
}

# SKILL.md candidate — passes Kanon PROMOTE level with all three required evidence items
# (dry_run_output, rollback_step, approval_reference from iam-provisioning-sailpoint rule)
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

SKILL_DIR_NAME = "new-birthright-provisioning-skill"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _server_env(tmp_path: Path) -> dict[str, str]:
    """Build the env dict that points all mutable state at tmp_path."""
    return {
        **os.environ,
        "AGENTSYNC_RULES_PATH": str(RULES_PATH),
        "AGENTSYNC_CAPABILITIES_PATH": str(CAPS_PATH),
        "AGENTSYNC_PRE_OBLIGATIONS_PATH": str(tmp_path / "pre_obligations.jsonl"),
        "AGENTSYNC_APPROVED_ROOT": str(APPROVED_PATH),
        "AGENTSYNC_OBLIGATIONS_PATH": str(tmp_path / "obligations.jsonl"),
        "AGENTSYNC_STELE_DB_PATH": str(tmp_path / "stele.db"),
        "AGENTSYNC_ARTIFACTS_BASE": str(tmp_path / "artifacts"),
        "AGENTSYNC_TRANSPORT": "stdio",
    }


def _server_params(tmp_path: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentsync.mcp.server"],
        env=_server_env(tmp_path),
    )


def _parse(result) -> Any:
    """Extract and JSON-decode a tool result.

    FastMCP serializes list[BaseModel] as one TextContent per item.
    Collect all items and return a list when multiple content blocks appear.
    """
    assert result.content, "tool returned empty content"
    if len(result.content) == 1:
        return json.loads(result.content[0].text)
    return [json.loads(item.text) for item in result.content]


# ---------------------------------------------------------------------------
# Test 1: tool catalog
# ---------------------------------------------------------------------------

def test_mcp_tool_catalog(tmp_path: Path) -> None:
    """Server must advertise exactly the expected set of AgentSync tools."""

    async def _run():
        async with stdio_client(_server_params(tmp_path)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.list_tools()
                names = {t.name for t in response.tools}
                return names

    names = anyio.run(_run)
    assert EXPECTED_TOOLS == names, (
        f"Tool catalog mismatch.\n"
        f"  missing: {EXPECTED_TOOLS - names}\n"
        f"  extra:   {names - EXPECTED_TOOLS}"
    )


# ---------------------------------------------------------------------------
# Test 2: rule server — evaluate_task over MCP
# ---------------------------------------------------------------------------

def test_mcp_evaluate_task(tmp_path: Path) -> None:
    """agentsync_evaluate_task must identify the IAM skill gap via MCP transport."""

    async def _run():
        async with stdio_client(_server_params(tmp_path)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "agentsync_evaluate_task",
                    {
                        "task": {
                            "task_id": "MCP-EVAL-1",
                            "task_description": "Provision a brand new birthright entitlement set",
                            "task_type": "iam-provisioning",
                        }
                    },
                )
                return _parse(result)

    data = anyio.run(_run)
    assert data["skill_expected"] is True
    assert data["closure_blocked_if_missing_skill"] is True
    assert set(data["required_evidence"]) == {
        "dry_run_output", "rollback_step", "approval_reference"
    }


# ---------------------------------------------------------------------------
# Test 3: skill server — list and find over MCP
# ---------------------------------------------------------------------------

def test_mcp_list_and_find_skills(tmp_path: Path) -> None:
    """Skill Server tools must return real approved-skill data over MCP transport."""

    async def _run():
        async with stdio_client(_server_params(tmp_path)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                list_result = await session.call_tool("agentsync_list_skills", {})
                skills = _parse(list_result)

                find_result = await session.call_tool(
                    "agentsync_find_matching_skill",
                    {"query": {"text": "run sailpoint-joiner-provisioning for this joiner"}},
                )
                lookup = _parse(find_result)

                return skills, lookup

    skills, lookup = anyio.run(_run)

    assert isinstance(skills, list)
    assert len(skills) >= 1, "approved/ tree must have at least one skill"
    ids = {s["canonical_id"] for s in skills}
    assert "sailpoint-joiner-provisioning" in ids

    assert lookup["status"] == "found"
    assert lookup["canonical_id"] == "sailpoint-joiner-provisioning"


# ---------------------------------------------------------------------------
# Test 4: full loop through MCP — evaluate → enforce → submit → promote
# ---------------------------------------------------------------------------

def test_mcp_full_loop(tmp_path: Path) -> None:
    """Full AgentSync pipeline exercised entirely through MCP tool calls.

    Step 1  agentsync_evaluate_task       → skill_expected=True
    Step 2  agentsync_enforcer_enforce    → outcome=obligation_open, token minted
    Step 3  agentsync_enforcer_get_status → OPEN
    Step 4  agentsync_enforcer_submit_candidate → SUBMITTED
    Step 5  agentsync_kanon_promote       → closure_unblocked=True
    Step 6  agentsync_enforcer_get_status → REDEEMED, closure_blocked=False
    """

    async def _run():
        async with stdio_client(_server_params(tmp_path)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # ---- Step 1: evaluate -------------------------------------------
                eval_result = await session.call_tool(
                    "agentsync_evaluate_task",
                    {
                        "task": {
                            "task_id": "MCP-LOOP-1",
                            "task_description": (
                                "Provision a brand new birthright entitlement set "
                                "for a new employee identity"
                            ),
                            "task_type": "iam-provisioning",
                        }
                    },
                )
                evaluation = _parse(eval_result)
                assert evaluation["skill_expected"] is True, (
                    f"Rule Server did not identify skill gap: {evaluation}"
                )

                # ---- Step 2: enforce --------------------------------------------
                enforce_result = await session.call_tool(
                    "agentsync_enforcer_enforce",
                    {
                        "evaluation": evaluation,
                        "task_description": (
                            "Provision a brand new birthright entitlement set "
                            "for a new employee identity"
                        ),
                    },
                )
                enforcement = _parse(enforce_result)
                assert enforcement["outcome"] == "obligation_open", (
                    f"Enforcer outcome was {enforcement['outcome']!r}; expected obligation_open"
                )
                token = enforcement["skill_obligation_token"]
                assert token is not None

                # ---- Step 3: get_status → OPEN ----------------------------------
                status_result = await session.call_tool(
                    "agentsync_enforcer_get_status",
                    {"token": token},
                )
                obl = _parse(status_result)
                assert obl["status"] == "open"
                assert obl["closure_blocked"] is True
                run_id = obl["run_id"]

                # ---- Step 4: submit candidate -----------------------------------
                submit_result = await session.call_tool(
                    "agentsync_enforcer_submit_candidate",
                    {
                        "token": token,
                        "candidate_path": f"staging/MCP-LOOP-1/{SKILL_DIR_NAME}/SKILL.md",
                    },
                )
                submitted_obl = _parse(submit_result)
                assert submitted_obl["status"] == "submitted"

                # ---- Step 5: promote through Kanon + Stele ----------------------
                promote_result = await session.call_tool(
                    "agentsync_kanon_promote",
                    {
                        "token": token,
                        "skill_md_content": VALID_SKILL_MD,
                        "dir_name": SKILL_DIR_NAME,
                    },
                )
                promotion = _parse(promote_result)
                assert promotion["closure_unblocked"] is True, (
                    f"Promotion did not unblock closure.\n"
                    f"  state: {promotion.get('state')}\n"
                    f"  errors: {promotion.get('validation_errors')}\n"
                    f"  note: {promotion.get('note')}"
                )
                assert promotion["state"] == "committed"
                assert promotion["artifact"]["run_id"] == run_id, (
                    "Join-key broken over MCP: artifact.run_id != obligation.run_id"
                )

                # ---- Step 6: get_status → REDEEMED ------------------------------
                final_result = await session.call_tool(
                    "agentsync_enforcer_get_status",
                    {"token": token},
                )
                final_obl = _parse(final_result)
                assert final_obl["status"] == "redeemed"
                assert final_obl["closure_blocked"] is False
                assert final_obl["redeemed_artifact_hash"] == promotion["artifact"]["artifact_hash"]

                return promotion

    promotion = anyio.run(_run)
    assert promotion["closure_unblocked"] is True

    # SKILL.md written to tmp_path artifacts dir
    obl_run_id = promotion["artifact"]["run_id"]
    skill_path = tmp_path / "artifacts" / obl_run_id / "SKILL.md"
    assert skill_path.exists(), (
        f"SKILL.md not written at expected path: {skill_path}"
    )
