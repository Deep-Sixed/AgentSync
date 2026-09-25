"""AgentSync MCP Service smoke tests — local, no ContextForge.

Starts one AgentSync MCP Service subprocess and drives it through a real
stdio MCP transport using ClientSession. Proves:

  1. Server starts and lists all expected tools.
  2. agentsync_evaluate_task      — Rule Server identifies skill gap
  3. agentsync_get_task_rules     — read-only rule inspection (no side effects)
  4. agentsync_find_matching_skill — Skill Server finds covered task
  5. Full loop through MCP:
       agentsync_evaluate_task
         ↓
       agentsync_enforce_task      — gap confirmed, token minted
         ↓
       agentsync_submit_candidate_skill  — obligation → SUBMITTED
         ↓
       agentsync_promote_candidate_skill — Kanon + Stele → REDEEMED
         ↓
       closure_unblocked=True

All mutable state (obligations.jsonl, stele.db, artifacts/) is written to
pytest tmp_path. The repo's storage/ directory is never modified.

Requires:  pip install 'agentsync[mcp,stele,dev]'
Run with:  python -m pytest tests/test_mcp_service_smoke.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ---------------------------------------------------------------------------
# Repo-relative paths (read-only in tests)
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]
RULES_PATH = REPO / "storage" / "rules" / "rules.yaml"
CAPS_PATH = REPO / "storage" / "rules" / "capabilities.yaml"
APPROVED_PATH = REPO / "storage" / "skills" / "approved"

# Canonical tool contract — one service, one registration.
EXPECTED_TOOLS = {
    # Rule
    "agentsync_evaluate_task",
    "agentsync_get_task_rules",
    # Skill
    "agentsync_list_skills",
    "agentsync_read_skill_file",
    "agentsync_activate_skill",
    "agentsync_find_matching_skill",
    # Enforcer
    "agentsync_enforce_task",
    "agentsync_submit_candidate_skill",
    "agentsync_get_obligation",
    # Kanon
    "agentsync_promote_candidate_skill",
}

# SKILL.md candidate — passes Kanon PROMOTE level.
# rules.yaml requires three evidence items for iam-provisioning-sailpoint:
#   dry_run_output, rollback_step, approval_reference
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
    """Env dict that points all mutable state at tmp_path.

    approved/ is copied into tmp_path because promotion installs into it.
    """
    approved = tmp_path / "approved"
    if not approved.exists():
        shutil.copytree(APPROVED_PATH, approved)
    return {
        **os.environ,
        "AGENTSYNC_RULES_PATH": str(RULES_PATH),
        "AGENTSYNC_CAPABILITIES_PATH": str(CAPS_PATH),
        "AGENTSYNC_PRE_OBLIGATIONS_PATH": str(tmp_path / "pre_obligations.jsonl"),
        "AGENTSYNC_APPROVED_ROOT": str(approved),
        "AGENTSYNC_OBLIGATIONS_PATH": str(tmp_path / "obligations.jsonl"),
        "AGENTSYNC_STELE_DB_PATH": str(tmp_path / "stele.db"),
        "AGENTSYNC_STELE_ARCHIVE_ROOT": str(tmp_path / "stele-archive"),
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
    """JSON-decode a tool result.

    FastMCP serializes list[BaseModel] as one TextContent per item.
    Collect all items into a list when multiple content blocks are returned.
    """
    assert result.content, "tool returned empty content"
    if len(result.content) == 1:
        return json.loads(result.content[0].text)
    return [json.loads(item.text) for item in result.content]


# ---------------------------------------------------------------------------
# Test 1: tool catalog
# ---------------------------------------------------------------------------

def test_mcp_tool_catalog(tmp_path: Path) -> None:
    """AgentSync MCP Service must advertise exactly the expected tool set."""

    async def _run():
        async with stdio_client(_server_params(tmp_path)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.list_tools()
                return {t.name for t in response.tools}

    names = anyio.run(_run)
    assert EXPECTED_TOOLS == names, (
        f"Tool catalog mismatch.\n"
        f"  missing from server: {EXPECTED_TOOLS - names}\n"
        f"  extra on server:     {names - EXPECTED_TOOLS}"
    )


# ---------------------------------------------------------------------------
# Test 2: agentsync_evaluate_task and agentsync_get_task_rules
# ---------------------------------------------------------------------------

def test_mcp_evaluate_task_and_get_task_rules(tmp_path: Path) -> None:
    """Both rule tools must identify the IAM skill gap correctly.

    agentsync_get_task_rules is read-only — calling it twice with the same
    input must produce identical output (no pre_obligation side effects).
    """
    task_payload = {
        "task": {
            "task_id": "MCP-RULE-1",
            "task_description": "Provision a brand new birthright entitlement set",
            "task_type": "iam-provisioning",
        }
    }

    async def _run():
        async with stdio_client(_server_params(tmp_path)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                eval_result = await session.call_tool(
                    "agentsync_evaluate_task", task_payload
                )
                evaluation = _parse(eval_result)

                # Call get_task_rules twice — must be idempotent
                rules_result_1 = await session.call_tool(
                    "agentsync_get_task_rules", task_payload
                )
                rules_result_2 = await session.call_tool(
                    "agentsync_get_task_rules", task_payload
                )
                rules_1 = _parse(rules_result_1)
                rules_2 = _parse(rules_result_2)

                return evaluation, rules_1, rules_2

    evaluation, rules_1, rules_2 = anyio.run(_run)

    # evaluate_task assertions
    assert evaluation["skill_expected"] is True
    assert evaluation["closure_blocked_if_missing_skill"] is True
    assert set(evaluation["required_evidence"]) == {
        "dry_run_output", "rollback_step", "approval_reference"
    }

    # get_task_rules assertions — same shape as evaluate_task
    assert rules_1["skill_expected"] is True
    assert set(rules_1["required_evidence"]) == {
        "dry_run_output", "rollback_step", "approval_reference"
    }

    # Idempotency: two calls produce identical required_evidence
    assert set(rules_1["required_evidence"]) == set(rules_2["required_evidence"])


# ---------------------------------------------------------------------------
# Test 3: agentsync_find_matching_skill
# ---------------------------------------------------------------------------

def test_mcp_find_matching_skill(tmp_path: Path) -> None:
    """Skill Server must find an existing approved skill by description."""

    async def _run():
        async with stdio_client(_server_params(tmp_path)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "agentsync_find_matching_skill",
                    {"query": {
                        "text": "run sailpoint-joiner-provisioning for this joiner"
                    }},
                )
                return _parse(result)

    lookup = anyio.run(_run)
    assert lookup["status"] == "found"
    assert lookup["canonical_id"] == "sailpoint-joiner-provisioning"


# ---------------------------------------------------------------------------
# Test 4: full loop through MCP
# ---------------------------------------------------------------------------

def test_mcp_full_loop(tmp_path: Path) -> None:
    """Full AgentSync pipeline exercised entirely through MCP tool calls.

    Step 1  agentsync_evaluate_task           → skill_expected=True
    Step 2  agentsync_enforce_task            → outcome=obligation_open, token minted
    Step 3  agentsync_get_obligation          → status=open
    Step 4  agentsync_submit_candidate_skill  → status=submitted
    Step 5  agentsync_promote_candidate_skill → closure_unblocked=True
    Step 6  agentsync_get_obligation          → status=redeemed, closure_blocked=False
    Step 7  the promoted skill is served, and re-enforcing the task → covered
    """
    task_description = (
        "Provision a brand new birthright entitlement set for a new employee identity"
    )

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
                            "task_description": task_description,
                            "task_type": "iam-provisioning",
                        }
                    },
                )
                evaluation = _parse(eval_result)
                assert evaluation["skill_expected"] is True, (
                    f"Rule Server did not identify skill gap: {evaluation}"
                )

                # ---- Step 2: enforce — confirm gap, mint token ------------------
                enforce_result = await session.call_tool(
                    "agentsync_enforce_task",
                    {
                        "evaluation": evaluation,
                        "task_description": task_description,
                    },
                )
                enforcement = _parse(enforce_result)
                assert enforcement["outcome"] == "obligation_open", (
                    f"Expected obligation_open, got {enforcement['outcome']!r}"
                )
                token = enforcement["skill_obligation_token"]
                assert token is not None

                # ---- Step 3: get_obligation → OPEN ------------------------------
                status_result = await session.call_tool(
                    "agentsync_get_obligation",
                    {"token": token},
                )
                obl = _parse(status_result)
                assert obl["status"] == "open"
                assert obl["closure_blocked"] is True
                run_id = obl["run_id"]

                # ---- Step 4: submit candidate -----------------------------------
                submit_result = await session.call_tool(
                    "agentsync_submit_candidate_skill",
                    {
                        "token": token,
                        "candidate_path": (
                            f"staging/MCP-LOOP-1/{SKILL_DIR_NAME}/SKILL.md"
                        ),
                    },
                )
                submitted_obl = _parse(submit_result)
                assert submitted_obl["status"] == "submitted"

                # ---- Step 5: promote through Kanon + Stele ----------------------
                promote_result = await session.call_tool(
                    "agentsync_promote_candidate_skill",
                    {
                        "token": token,
                        "skill_md_content": VALID_SKILL_MD,
                        "dir_name": SKILL_DIR_NAME,
                    },
                )
                promotion = _parse(promote_result)
                assert promotion["closure_unblocked"] is True, (
                    f"Promotion did not unblock closure.\n"
                    f"  state:  {promotion.get('state')}\n"
                    f"  errors: {promotion.get('validation_errors')}\n"
                    f"  note:   {promotion.get('note')}"
                )
                assert promotion["state"] == "committed"
                assert promotion["artifact"]["run_id"] == run_id, (
                    "Join-key broken over MCP: artifact.run_id != obligation.run_id"
                )

                # ---- Step 6: get_obligation → REDEEMED --------------------------
                final_result = await session.call_tool(
                    "agentsync_get_obligation",
                    {"token": token},
                )
                final_obl = _parse(final_result)
                assert final_obl["status"] == "redeemed"
                assert final_obl["closure_blocked"] is False
                assert (
                    final_obl["redeemed_artifact_hash"]
                    == promotion["artifact"]["artifact_hash"]
                )
                assert final_obl["redeemed_skill_id"] == SKILL_DIR_NAME

                # ---- Step 7: the loop closes ------------------------------------
                skills = _parse(await session.call_tool("agentsync_list_skills", {}))
                if isinstance(skills, dict):  # one skill -> one content block
                    skills = skills.get("result", [skills])
                assert SKILL_DIR_NAME in {s["canonical_id"] for s in skills}

                again = _parse(await session.call_tool(
                    "agentsync_enforce_task",
                    {"evaluation": evaluation, "task_description": task_description},
                ))
                assert again["outcome"] == "covered", again
                assert again["existing_skill_id"] == SKILL_DIR_NAME

                return promotion, obl["run_id"]

    promotion, run_id = anyio.run(_run)

    # SKILL.md must be written inside tmp_path artifacts — not in the repo
    skill_path = tmp_path / "artifacts" / run_id / "SKILL.md"
    assert skill_path.exists(), (
        f"SKILL.md not found at expected path: {skill_path}"
    )
    assert skill_path.read_text(encoding="utf-8") == VALID_SKILL_MD
    installed = tmp_path / "approved" / SKILL_DIR_NAME / "SKILL.md"
    assert installed.read_text(encoding="utf-8") == VALID_SKILL_MD
    assert not (APPROVED_PATH / SKILL_DIR_NAME).exists()
