"""Skill obligation models.

The Enforcer mints the AUTHORITATIVE skill_obligation_token — the thing the Rule
Server deliberately never mints. The token carries a `run_id` (UUID) so the
later Kanon->Stele promotion can join obligation <-> Stele ArtifactRecord
(Stele keys on run_id/record_id). Designing it in now means the Stele adapter
slots in next without a token-format change.

Lifecycle:
    OPEN       — gap confirmed, authoring obligation in force, closure blocked
    SUBMITTED  — agent submitted a candidate SKILL.md (awaiting Kanon)
    REDEEMED   — Kanon validated + promoted the candidate; obligation satisfied
    CANCELLED  — obligation withdrawn (e.g. a covering skill appeared, or task dropped)
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ObligationStatus(str, Enum):
    OPEN = "open"
    SUBMITTED = "submitted"
    REDEEMED = "redeemed"
    CANCELLED = "cancelled"


class SkillObligation(BaseModel):
    """An authoritative authoring obligation. One per confirmed-missing gap."""
    model_config = ConfigDict(extra="forbid")

    # Identity
    skill_obligation_token: str          # UUID — the authoritative token
    run_id: str                          # UUID — Stele join key for the future promotion
    task_id: str

    # Provenance back to the Rule Server decision
    pre_obligation_id: str | None = None
    skill_family: str | None = None
    capability_family: str | None = None
    required_evidence: list[str] = Field(default_factory=list)

    # Lifecycle
    status: ObligationStatus = ObligationStatus.OPEN
    closure_blocked: bool = True

    # Audit
    created_at: str
    updated_at: str
    candidate_path: str | None = None   # set on SUBMITTED
    redeemed_artifact_hash: str | None = None  # set on REDEEMED (from Stele)
    note: str | None = None


class EnforcementResult(BaseModel):
    """What the Enforcer returns when asked to enforce an obligation for a task."""
    model_config = ConfigDict(extra="forbid")

    task_id: str
    outcome: str                          # "covered" | "obligation_open" | "no_obligation"
    skill_obligation_token: str | None = None
    run_id: str | None = None
    closure_blocked: bool = False
    existing_skill_id: str | None = None     # set when covered
    best_near_match: str | None = None        # surfaced from the lookup, never coverage
    required_evidence: list[str] = Field(default_factory=list)
    note: str | None = None
