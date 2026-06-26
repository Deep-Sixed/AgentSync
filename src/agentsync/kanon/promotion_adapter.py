"""Kanon PromotionAdapter — the promotion pipeline.

Seam contract (explicit, do not cross):
  - PromotionAdapter never imports EVECOR services/stele directly.
  - It calls the StelePromotionPort interface only.
  - The Enforcer's redeem() is the ONLY path to closure_unblocked=True.
  - SKILL.md validation (validate_skill_md) runs BEFORE Stele is called.
    Stele is never called on an invalid candidate.

Pipeline:
    Enforcer SUBMITTED obligation
      ↓
    Kanon validates candidate SKILL.md        (validate_skill_md PROMOTE)
      ↓
    StelePromotionPort.commit_artifact()
      ↓ state=COMMITTED
    Enforcer.redeem(token, artifact_hash)
      ↓
    PromotionResult(closure_unblocked=True)
"""
from __future__ import annotations

from agentsync.enforcer.enforcer import SkillBuilderEnforcer
from agentsync.enforcer.models import ObligationStatus, SkillObligation
from agentsync.skills.skill_schema import ValidationLevel, validate_skill_md

from .models import PromotionResult, PromotionState, SkillCandidate
from .stele_port import StelePromotionPort


class PromotionAdapter:
    """Wires together Enforcer → SKILL.md validation → Stele → token redemption."""

    def __init__(
        self,
        enforcer: SkillBuilderEnforcer,
        stele_port: StelePromotionPort,
        *,
        validation_level: ValidationLevel = ValidationLevel.PROMOTE,
    ) -> None:
        self._enforcer = enforcer
        self._stele_port = stele_port
        self._level = validation_level

    def promote(self, token: str, candidate: SkillCandidate) -> PromotionResult:
        """Run the full promotion pipeline.

        Steps:
          1. Resolve obligation — must exist and be SUBMITTED.
          2. Validate candidate SKILL.md at the configured level.
          3. Call StelePromotionPort.commit_artifact().
          4. COMMITTED  → enforcer.redeem() → closure_unblocked=True.
          5. FAILED     → PromotionResult(closure_unblocked=False).
          6. INVALIDATED→ PromotionResult(closure_unblocked=False).

        Raises:
            KeyError:   token is unknown to the Enforcer.
            ValueError: obligation is not in SUBMITTED state.
        """
        # Step 1: gate on token state via the Enforcer's public surface
        obl: SkillObligation | None = self._enforcer.get_obligation(token)
        if obl is None:
            raise KeyError(f"unknown skill_obligation_token: {token}")
        if obl.status is not ObligationStatus.SUBMITTED:
            raise ValueError(
                f"obligation {token} is {obl.status.value}; expected submitted"
            )

        # Step 2: validate SKILL.md — never call Stele on an invalid candidate
        validation = validate_skill_md(
            candidate.content,
            level=self._level,
            required_evidence=obl.required_evidence,
            dir_name=candidate.dir_name,
            file_name=candidate.file_name,
        )
        if not validation.valid:
            return PromotionResult(
                closure_unblocked=False,
                state=None,
                validation_errors=validation.errors,
                note="SKILL.md validation failed; Stele not called",
            )

        # Step 3: commit through the port — thread obl.run_id as the ledger join key
        artifact = self._stele_port.commit_artifact(candidate, obl.run_id)

        # Step 4: COMMITTED — redeem and unblock closure
        if artifact.state is PromotionState.COMMITTED:
            self._enforcer.redeem(token, artifact_hash=artifact.artifact_hash)
            return PromotionResult(
                closure_unblocked=True,
                state=PromotionState.COMMITTED,
                artifact=artifact,
                note="promoted and redeemed",
            )

        # Step 5: FAILED — obligation stays SUBMITTED, closure stays blocked
        if artifact.state is PromotionState.FAILED:
            return PromotionResult(
                closure_unblocked=False,
                state=PromotionState.FAILED,
                artifact=artifact,
                note="Stele commit FAILED; obligation remains SUBMITTED",
            )

        # Step 6: INVALIDATED — obligation stays SUBMITTED, closure stays blocked
        return PromotionResult(
            closure_unblocked=False,
            state=PromotionState.INVALIDATED,
            artifact=artifact,
            note="Stele INVALIDATED the artifact; obligation remains SUBMITTED",
        )
