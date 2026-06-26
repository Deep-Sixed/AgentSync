"""Kanon → Stele seam.

StelePromotionPort is the interface Kanon calls to commit a validated
candidate. It is an explicit seam: the real EVECOR services/stele
wiring is a later phase. In tests FakeStelePromotionPort is used.

Contract (for implementors):
  - commit_artifact() must NOT raise on FAILED or INVALIDATED outcomes.
    Encode those in PromotionArtifact.state.
  - commit_artifact() MAY raise on genuine infrastructure errors
    (network, serialization). PromotionAdapter does not catch those;
    they bubble up as unexpected failures.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Protocol

from .models import PromotionArtifact, PromotionState, SkillCandidate


class StelePromotionPort(Protocol):
    """Interface between Kanon and the Stele substrate."""

    def commit_artifact(self, candidate: SkillCandidate) -> PromotionArtifact:
        """Commit a validated candidate to Stele.

        Returns a PromotionArtifact. Never raises on FAILED/INVALIDATED —
        those are encoded in artifact.state.
        """
        ...


class FakeStelePromotionPort:
    """In-memory Stele port for integration tests.

    Configured with a fixed outcome at construction time. Records every
    commit_artifact() call so tests can assert on what was submitted.

    Usage::

        port = FakeStelePromotionPort()                            # COMMITTED
        port = FakeStelePromotionPort(outcome=PromotionState.FAILED)
        port = FakeStelePromotionPort(outcome=PromotionState.INVALIDATED)

        artifact = port.commit_artifact(candidate)
        assert port.calls()[0].dir_name == "my-skill"
    """

    def __init__(self, *, outcome: PromotionState = PromotionState.COMMITTED) -> None:
        self._outcome = outcome
        self._calls: list[SkillCandidate] = []

    def commit_artifact(self, candidate: SkillCandidate) -> PromotionArtifact:
        self._calls.append(candidate)
        artifact_hash = hashlib.sha256(candidate.content.encode()).hexdigest()
        return PromotionArtifact(
            artifact_hash=artifact_hash,
            record_id=str(uuid.uuid4()),
            run_id=str(uuid.uuid4()),
            state=self._outcome,
        )

    def calls(self) -> list[SkillCandidate]:
        """Snapshot of all commit_artifact() invocations (chronological)."""
        return list(self._calls)

    def call_count(self) -> int:
        return len(self._calls)
