"""Kanon → Stele seam.

StelePromotionPort is the interface Kanon calls to commit a validated
candidate. It is an explicit seam: the real EVECOR services/stele
wiring is a later phase. In tests FakeStelePromotionPort is used.

Contract (for implementors):
  - commit_artifact() receives run_id from the obligation — this is the
    Stele ledger join key that was minted by the Enforcer when the obligation
    was created. Implementations MUST use it as SandboxResult.run_id so the
    ArtifactRecord joins back to the obligation correctly.
  - commit_artifact() must NOT raise on FAILED or INVALIDATED outcomes.
    Encode those in PromotionArtifact.state.
  - commit_artifact() MAY raise on genuine infrastructure errors
    (network, serialization). PromotionAdapter does not catch those;
    they bubble up as unexpected failures.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Protocol

from .models import PromotionArtifact, PromotionState, SkillCandidate


@dataclass
class FakeCommitCall:
    """Record of a single FakeStelePromotionPort.commit_artifact() invocation."""
    candidate: SkillCandidate
    run_id: str


class StelePromotionPort(Protocol):
    """Interface between Kanon and the Stele substrate."""

    def commit_artifact(
        self,
        candidate: SkillCandidate,
        run_id: str,
    ) -> PromotionArtifact:
        """Commit a validated candidate to Stele.

        run_id is the Stele join key from the obligation (obl.run_id). It MUST
        be threaded into the Stele SandboxResult so the resulting ArtifactRecord
        joins back to the obligation.

        Returns a PromotionArtifact. Never raises on FAILED/INVALIDATED —
        those are encoded in artifact.state.
        """
        ...


class FakeStelePromotionPort:
    """In-memory Stele port for integration tests.

    Configured with a fixed outcome at construction time. Records every
    commit_artifact() call as a FakeCommitCall so tests can assert on both
    the submitted candidate and the run_id join key.

    Usage::

        port = FakeStelePromotionPort()                            # COMMITTED
        port = FakeStelePromotionPort(outcome=PromotionState.FAILED)

        artifact = port.commit_artifact(candidate, run_id="some-uuid")
        assert port.calls()[0].candidate.dir_name == "my-skill"
        assert port.calls()[0].run_id == "some-uuid"
        assert artifact.run_id == "some-uuid"   # echoed back for join-key assertion
    """

    def __init__(self, *, outcome: PromotionState = PromotionState.COMMITTED) -> None:
        self._outcome = outcome
        self._calls: list[FakeCommitCall] = []

    def commit_artifact(
        self,
        candidate: SkillCandidate,
        run_id: str,
    ) -> PromotionArtifact:
        self._calls.append(FakeCommitCall(candidate=candidate, run_id=run_id))
        artifact_hash = hashlib.sha256(candidate.content.encode()).hexdigest()
        return PromotionArtifact(
            artifact_hash=artifact_hash,
            record_id=str(uuid.uuid4()),
            run_id=run_id,          # echoed from obligation — proves the join key
            state=self._outcome,
        )

    def calls(self) -> list[FakeCommitCall]:
        """Snapshot of all commit_artifact() invocations (chronological)."""
        return list(self._calls)

    def call_count(self) -> int:
        return len(self._calls)
