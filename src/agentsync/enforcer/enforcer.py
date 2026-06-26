"""Skill Builder Enforcer — the ratchet.

Consumes a Rule Server decision, confirms the gap against the Skill Server
through the SkillLookup seam, and on confirmed-missing mints the AUTHORITATIVE
skill_obligation_token. Owns the obligation lifecycle.

Boundary recap (do not cross):
  Rule Server   -> decides expectation, emits pre_obligation (non-authoritative).
  Enforcer      -> confirms gap, mints the authoritative token (THIS module).
  Kanon         -> validates candidate + promotes through Stele, then redeems.

The Enforcer never validates SKILL.md content and never promotes. It only
decides whether an authoring obligation exists and tracks its lifecycle.
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agentsync.rules.models import RuleEvaluation
from agentsync.skills.skill_server import SkillQuery

from .lookup import InProcessSkillLookup, SkillLookup
from .models import EnforcementResult, ObligationStatus, SkillObligation
from .store import append_obligation, find_open_for_task, get_obligation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SkillBuilderEnforcer:
    """Confirms gaps and mints/owns authoritative obligations."""

    def __init__(
        self,
        lookup: Optional[SkillLookup] = None,
        obligations_path: Optional[Path] = None,
        approved_root: Optional[Path] = None,
    ) -> None:
        # Default to in-process lookup; inject a remote one later without
        # touching anything below.
        self._lookup: SkillLookup = lookup or InProcessSkillLookup(approved_root=approved_root)
        self._path = obligations_path

    # --- the ratchet ---------------------------------------------------------

    def enforce(self, evaluation: RuleEvaluation, task_description: str = "") -> EnforcementResult:
        """Given a Rule Server decision, confirm coverage and mint if missing.

        task_description is the original task text; it is what the Skill Server
        matcher needs to find coverage. Without it the lookup is starved and
        would false-'missing' on tasks that actually name an existing skill.

        - skill not expected            -> no_obligation
        - expected, lookup found        -> covered (no token)
        - expected, lookup missing      -> obligation_open (mint authoritative token)
        - task already has live token   -> return that token (idempotent)
        """
        task_id = evaluation.task_id

        if not evaluation.skill_expected:
            return EnforcementResult(
                task_id=task_id, outcome="no_obligation", closure_blocked=False,
                note="rule did not expect a skill",
            )

        # Idempotency: never mint a second token for a task that already has one.
        existing = find_open_for_task(task_id, self._path)
        if existing is not None:
            return EnforcementResult(
                task_id=task_id, outcome="obligation_open",
                skill_obligation_token=existing.skill_obligation_token,
                run_id=existing.run_id,
                closure_blocked=existing.closure_blocked,
                required_evidence=existing.required_evidence,
                note="existing live obligation returned (idempotent)",
            )

        # Confirm the gap. The Skill Server's matcher is conservative, so a
        # 'missing' here is trustworthy. Feed it the real task text — without
        # the description the matcher can't find coverage.
        query_text = (task_description or "").strip() or task_id
        result = self._lookup.find_matching_skill(SkillQuery(
            text=query_text,
            skill_family=evaluation.skill_family,
            capability_family=evaluation.capability_family,
        ))

        if result.status == "found":
            return EnforcementResult(
                task_id=task_id, outcome="covered", closure_blocked=False,
                existing_skill_id=result.canonical_id,
                note="existing approved skill covers this task",
            )

        # Confirmed missing -> mint the authoritative token.
        now = _now()
        obl = SkillObligation(
            skill_obligation_token=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),               # Stele join key for promotion
            task_id=task_id,
            pre_obligation_id=evaluation.pre_obligation_id,
            skill_family=evaluation.skill_family,
            capability_family=evaluation.capability_family,
            required_evidence=evaluation.required_evidence,
            status=ObligationStatus.OPEN,
            closure_blocked=evaluation.closure_blocked_if_missing_skill,
            created_at=now,
            updated_at=now,
            note=f"minted on confirmed-missing (near_match={result.best_near_match})",
        )
        append_obligation(obl, self._path)
        return EnforcementResult(
            task_id=task_id, outcome="obligation_open",
            skill_obligation_token=obl.skill_obligation_token,
            run_id=obl.run_id,
            closure_blocked=obl.closure_blocked,
            best_near_match=result.best_near_match,
            required_evidence=obl.required_evidence,
            note="authoritative obligation minted",
        )

    # --- lifecycle transitions ----------------------------------------------

    def submit_candidate(self, token: str, candidate_path: str) -> SkillObligation:
        """Agent submitted a candidate SKILL.md. OPEN -> SUBMITTED."""
        obl = self._require_status(token, {ObligationStatus.OPEN})
        updated = obl.model_copy(update={
            "status": ObligationStatus.SUBMITTED,
            "candidate_path": candidate_path,
            "updated_at": _now(),
            "note": "candidate submitted; awaiting Kanon",
        })
        return append_obligation(updated, self._path)

    def redeem(self, token: str, artifact_hash: str) -> SkillObligation:
        """Kanon promoted the candidate through Stele. SUBMITTED -> REDEEMED.

        artifact_hash is Stele's committed ArtifactRecord.artifact_hash — the
        proof that promotion actually happened through the substrate. Closure
        is unblocked here.
        """
        obl = self._require_status(token, {ObligationStatus.SUBMITTED})
        updated = obl.model_copy(update={
            "status": ObligationStatus.REDEEMED,
            "closure_blocked": False,
            "redeemed_artifact_hash": artifact_hash,
            "updated_at": _now(),
            "note": "redeemed after Stele commit",
        })
        return append_obligation(updated, self._path)

    def cancel(self, token: str, reason: str) -> SkillObligation:
        """Withdraw an obligation (covering skill appeared, task dropped, etc.)."""
        obl = self._require_status(token, {ObligationStatus.OPEN, ObligationStatus.SUBMITTED})
        updated = obl.model_copy(update={
            "status": ObligationStatus.CANCELLED,
            "closure_blocked": False,
            "updated_at": _now(),
            "note": f"cancelled: {reason}",
        })
        return append_obligation(updated, self._path)

    # --- public read ---------------------------------------------------------

    def get_obligation(self, token: str) -> Optional[SkillObligation]:
        """Return the current state of one token, or None if unknown."""
        return get_obligation(token, self._path)

    # --- helpers -------------------------------------------------------------

    def _require_status(self, token: str, allowed: set) -> SkillObligation:
        obl = get_obligation(token, self._path)
        if obl is None:
            raise KeyError(f"unknown skill_obligation_token: {token}")
        if obl.status not in allowed:
            raise ValueError(
                f"obligation {token} is {obl.status.value}; "
                f"expected one of {sorted(s.value for s in allowed)}"
            )
        return obl
