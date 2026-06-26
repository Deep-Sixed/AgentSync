"""AgentSync unified MCP server.

Single FastMCP process — all AgentSync tools in one server, one ContextForge
registration. Subsystem logic lives in the respective engine modules; this file
is a transport shim only.

Tool groups
-----------
Rule Server      agentsync_evaluate_task
Skill Server     agentsync_list_skills
                 agentsync_read_skill_file
                 agentsync_activate_skill
                 agentsync_find_matching_skill
Enforcer         agentsync_enforcer_enforce
                 agentsync_enforcer_submit_candidate
                 agentsync_enforcer_get_status
Kanon            agentsync_kanon_promote

Run locally (stdio):   python -m agentsync.mcp.server
Run as HTTP service:   AGENTSYNC_TRANSPORT=http python -m agentsync.mcp.server

Requires the optional `mcp` extra:  pip install 'agentsync[mcp]'

Environment variables (all optional — defaults target the repo's storage/ tree)
--------------------------------------------------------------------------------
AGENTSYNC_RULES_PATH            path to rules.yaml
AGENTSYNC_CAPABILITIES_PATH     path to capabilities.yaml
AGENTSYNC_PRE_OBLIGATIONS_PATH  path to pre_obligations.jsonl
AGENTSYNC_APPROVED_ROOT         path to approved/ skills directory
AGENTSYNC_OBLIGATIONS_PATH      path to obligations.jsonl
AGENTSYNC_STELE_DB_PATH         path to Stele SQLite ledger DB
AGENTSYNC_ARTIFACTS_BASE        base directory for Stele artifact writes
AGENTSYNC_TRANSPORT             stdio (default) | http
AGENTSYNC_PORT                  HTTP port when transport=http (default 8080)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Service singletons — initialized once at import time from env vars.
# All paths are configurable so tests can redirect to tmp_path without
# touching the repo's storage/.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]  # …/AgentSync/

_RULES_PATH = Path(os.environ.get(
    "AGENTSYNC_RULES_PATH",
    str(_REPO_ROOT / "storage" / "rules" / "rules.yaml"),
))
_CAPS_PATH = Path(os.environ.get(
    "AGENTSYNC_CAPABILITIES_PATH",
    str(_REPO_ROOT / "storage" / "rules" / "capabilities.yaml"),
))
_PRE_OBLIGATIONS_PATH = Path(os.environ.get(
    "AGENTSYNC_PRE_OBLIGATIONS_PATH",
    str(_REPO_ROOT / "storage" / "obligations" / "pre_obligations.jsonl"),
))
_APPROVED_ROOT = Path(os.environ.get(
    "AGENTSYNC_APPROVED_ROOT",
    str(_REPO_ROOT / "storage" / "skills" / "approved"),
))
_OBLIGATIONS_PATH = Path(os.environ.get(
    "AGENTSYNC_OBLIGATIONS_PATH",
    str(_REPO_ROOT / "storage" / "obligations" / "obligations.jsonl"),
))
_STELE_DB_PATH = Path(os.environ.get(
    "AGENTSYNC_STELE_DB_PATH",
    str(_REPO_ROOT / "storage" / "stele" / "ledger.db"),
))
_ARTIFACTS_BASE = Path(os.environ.get(
    "AGENTSYNC_ARTIFACTS_BASE",
    str(_REPO_ROOT / "storage" / "stele" / "artifacts"),
))

# Rule Server
from agentsync.rules.rule_resolver import RuleResolver
from agentsync.rules.models import RuleEvaluation, TaskContext

_RESOLVER = RuleResolver(
    rules_path=_RULES_PATH,
    capabilities_path=_CAPS_PATH,
    pre_obligations_path=_PRE_OBLIGATIONS_PATH,
)

# Skill Server
from agentsync.skills.skill_server import (
    ActivatedSkill,
    SkillFile,
    SkillLookupResult,
    SkillQuery,
    SkillServer,
    SkillSummary,
)

_SKILL_SERVER = SkillServer(approved_root=_APPROVED_ROOT)

# Enforcer
from agentsync.enforcer.enforcer import SkillBuilderEnforcer
from agentsync.enforcer.models import EnforcementResult, SkillObligation

_ENFORCER = SkillBuilderEnforcer(
    approved_root=_APPROVED_ROOT,
    obligations_path=_OBLIGATIONS_PATH,
)

# Kanon + Stele port (stele is an optional dep; fail loud at startup if missing)
from stele.ledger.store import LedgerStore
from agentsync.kanon.evecor_stele_port import EvecorStelePort
from agentsync.kanon.models import SkillCandidate
from agentsync.kanon.promotion_adapter import PromotionAdapter

_STORE = LedgerStore(_STELE_DB_PATH)
_PORT = EvecorStelePort(_STORE, _ARTIFACTS_BASE)
_ADAPTER = PromotionAdapter(_ENFORCER, _PORT)


# ---------------------------------------------------------------------------
# Server construction (lazy so the engine stays importable without `mcp`)
# ---------------------------------------------------------------------------

def _build_server():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("agentsync")

    # ---- Rule Server --------------------------------------------------------

    @mcp.tool(
        name="agentsync_evaluate_task",
        annotations={
            "title": "Evaluate task against AgentSync rules",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    def agentsync_evaluate_task(task: TaskContext) -> RuleEvaluation:
        """Decide whether a task warrants a reusable skill.

        Resolver order: pattern match → capability fallback → none.
        Returns skill_expected, required_evidence, and closure_blocked_if_missing_skill.
        Never mints the authoritative skill_obligation_token — that is the Enforcer's job.
        """
        return _RESOLVER.evaluate_task(task)

    # ---- Skill Server -------------------------------------------------------

    @mcp.tool(
        name="agentsync_list_skills",
        annotations={
            "title": "List approved skills",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def agentsync_list_skills() -> list[SkillSummary]:
        """List all approved skills with canonical_id, name, description, and families."""
        return _SKILL_SERVER.list_skills()

    @mcp.tool(
        name="agentsync_read_skill_file",
        annotations={
            "title": "Read a skill's SKILL.md",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def agentsync_read_skill_file(canonical_id: str) -> Optional[SkillFile]:
        """Return the full SKILL.md content for a canonical_id, or null if unknown."""
        return _SKILL_SERVER.read_skill_file(canonical_id)

    @mcp.tool(
        name="agentsync_activate_skill",
        annotations={
            "title": "Activate a skill (load instructions into agent context)",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def agentsync_activate_skill(canonical_id: str) -> Optional[ActivatedSkill]:
        """Load a skill's full instructions for injection into agent context."""
        return _SKILL_SERVER.activate_skill(canonical_id)

    @mcp.tool(
        name="agentsync_find_matching_skill",
        annotations={
            "title": "Find a skill matching a task description",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def agentsync_find_matching_skill(query: SkillQuery) -> SkillLookupResult:
        """Deterministically look up whether an approved skill covers a task.

        Conservative: weak description overlap returns 'missing' with the
        near-miss surfaced, so the Enforcer's gap verdict is trustworthy.
        """
        return _SKILL_SERVER.find_matching_skill(query)

    # ---- Enforcer -----------------------------------------------------------

    @mcp.tool(
        name="agentsync_enforcer_enforce",
        annotations={
            "title": "Confirm skill gap and mint obligation token",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def agentsync_enforcer_enforce(
        evaluation: RuleEvaluation,
        task_description: str,
    ) -> EnforcementResult:
        """Confirm skill coverage and mint an obligation token if missing.

        Returns outcome: 'covered' | 'obligation_open' | 'no_obligation'.
        Idempotent: re-enforcing the same task returns the existing live token.
        Call agentsync_evaluate_task first; pass its result here as `evaluation`.
        """
        return _ENFORCER.enforce(evaluation, task_description=task_description)

    @mcp.tool(
        name="agentsync_enforcer_submit_candidate",
        annotations={
            "title": "Submit a SKILL.md candidate for Kanon promotion",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    def agentsync_enforcer_submit_candidate(
        token: str,
        candidate_path: str,
    ) -> SkillObligation:
        """Transition the obligation from OPEN → SUBMITTED.

        candidate_path is the location of the SKILL.md the agent authored.
        After this call, use agentsync_kanon_promote to validate and commit it.
        """
        return _ENFORCER.submit_candidate(token, candidate_path)

    @mcp.tool(
        name="agentsync_enforcer_get_status",
        annotations={
            "title": "Get current obligation status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def agentsync_enforcer_get_status(token: str) -> Optional[SkillObligation]:
        """Return the current state of an obligation token, or null if unknown."""
        return _ENFORCER.get_obligation(token)

    # ---- Kanon --------------------------------------------------------------

    @mcp.tool(
        name="agentsync_kanon_promote",
        annotations={
            "title": "Validate and promote a candidate SKILL.md through Stele",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    def agentsync_kanon_promote(
        token: str,
        skill_md_content: str,
        dir_name: str,
    ) -> dict:
        """Validate a candidate SKILL.md and promote it through the Stele substrate.

        Fetches required_evidence from the obligation automatically — the caller
        only needs the obligation token, the SKILL.md content, and the kebab-case
        directory name for the skill.

        Returns a JSON object with:
          closure_unblocked  bool   True only when Stele committed and token redeemed
          state              str    'committed' | 'failed' | 'invalidated' | null
          artifact           dict   Stele commit receipt (artifact_hash, record_id, run_id)
          validation_errors  list   non-empty when Kanon rejected the candidate
          note               str    human-readable outcome summary

        Prerequisite: the obligation must be in SUBMITTED state
        (call agentsync_enforcer_submit_candidate first).
        """
        obl = _ENFORCER.get_obligation(token)
        if obl is None:
            raise KeyError(f"unknown obligation token: {token}")

        candidate = SkillCandidate(
            content=skill_md_content,
            dir_name=dir_name,
            file_name="SKILL.md",
            required_evidence=obl.required_evidence,
        )
        result = _ADAPTER.promote(token, candidate)

        return {
            "closure_unblocked": result.closure_unblocked,
            "state": result.state.value if result.state else None,
            "artifact": {
                "artifact_hash": result.artifact.artifact_hash,
                "record_id": result.artifact.record_id,
                "run_id": result.artifact.run_id,
                "state": result.artifact.state.value,
            } if result.artifact else None,
            "validation_errors": result.validation_errors,
            "note": result.note,
        }

    return mcp


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    server = _build_server()
    transport = os.environ.get("AGENTSYNC_TRANSPORT", "stdio")
    if transport == "http":
        port = int(os.environ.get("AGENTSYNC_PORT", "8080"))
        server.run(transport="streamable_http", port=port)
    else:
        server.run()


if __name__ == "__main__":
    main()
