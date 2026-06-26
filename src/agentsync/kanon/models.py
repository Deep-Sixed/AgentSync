"""Kanon domain models — skill promotion pipeline.

These are the types that cross the Kanon seam:

  SkillCandidate    — what an agent submits for promotion
  PromotionState    — the outcome Stele reports for a commit
  PromotionArtifact — the Stele commit receipt (join key back to ledger)
  PromotionResult   — what PromotionAdapter returns to its caller
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PromotionState(str, Enum):
    """Possible outcomes returned by the Stele substrate for a commit attempt."""
    COMMITTED = "committed"
    FAILED = "failed"
    INVALIDATED = "invalidated"


@dataclass
class SkillCandidate:
    """A validated SKILL.md candidate submitted for Stele promotion.

    dir_name and file_name are passed to the promote-level SKILL.md validator
    (kebab-case dir + exact 'SKILL.md' filename rules).
    required_evidence is forwarded from the obligation so the validator can
    confirm each evidence key is represented in the Verification section.
    """
    content: str
    dir_name: str
    file_name: str = "SKILL.md"
    required_evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PromotionArtifact:
    """The Stele commit receipt.

    artifact_hash is the Stele ArtifactRecord.artifact_hash — passed to
    enforcer.redeem() as proof the promotion traversed the substrate.
    record_id and run_id are the Stele ledger join keys.
    """
    artifact_hash: str
    record_id: str
    run_id: str
    state: PromotionState


@dataclass
class PromotionResult:
    """What PromotionAdapter returns to its caller.

    closure_unblocked=True only when the obligation token was REDEEMED
    after a COMMITTED Stele artifact — that is the closure gate.
    """
    closure_unblocked: bool
    state: PromotionState | None = None
    artifact: PromotionArtifact | None = None
    validation_errors: list[str] = field(default_factory=list)
    note: str = ""
