"""Unit tests for FakeStelePromotionPort.

Proves: default COMMITTED outcome, configurable FAILED/INVALIDATED,
call recording, and artifact_hash integrity.
"""

import hashlib

from agentsync.kanon.models import PromotionState, SkillCandidate
from agentsync.kanon.stele_port import FakeStelePromotionPort


def _candidate(content: str = "some skill content", dir_name: str = "my-skill") -> SkillCandidate:
    return SkillCandidate(content=content, dir_name=dir_name)


# ---------------------------------------------------------------------------
# Default outcome
# ---------------------------------------------------------------------------

def test_default_outcome_is_committed():
    port = FakeStelePromotionPort()
    artifact = port.commit_artifact(_candidate())
    assert artifact.state is PromotionState.COMMITTED


def test_committed_artifact_fields_are_populated():
    port = FakeStelePromotionPort()
    candidate = _candidate("hello skill")
    artifact = port.commit_artifact(candidate)
    assert artifact.artifact_hash != ""
    assert artifact.record_id != ""
    assert artifact.run_id != ""


# ---------------------------------------------------------------------------
# Configurable outcomes
# ---------------------------------------------------------------------------

def test_failed_outcome():
    port = FakeStelePromotionPort(outcome=PromotionState.FAILED)
    artifact = port.commit_artifact(_candidate())
    assert artifact.state is PromotionState.FAILED


def test_invalidated_outcome():
    port = FakeStelePromotionPort(outcome=PromotionState.INVALIDATED)
    artifact = port.commit_artifact(_candidate())
    assert artifact.state is PromotionState.INVALIDATED


# ---------------------------------------------------------------------------
# Call recording
# ---------------------------------------------------------------------------

def test_calls_starts_empty():
    port = FakeStelePromotionPort()
    assert port.calls() == []
    assert port.call_count() == 0


def test_calls_records_each_invocation():
    port = FakeStelePromotionPort()
    c1 = _candidate("content one", "skill-one")
    c2 = _candidate("content two", "skill-two")
    port.commit_artifact(c1)
    port.commit_artifact(c2)
    assert port.call_count() == 2
    assert port.calls()[0].dir_name == "skill-one"
    assert port.calls()[1].dir_name == "skill-two"


def test_calls_returns_snapshot_not_live_list():
    """calls() must return a copy; modifying it must not affect the port state."""
    port = FakeStelePromotionPort()
    port.commit_artifact(_candidate())
    snapshot = port.calls()
    snapshot.clear()
    assert port.call_count() == 1


# ---------------------------------------------------------------------------
# artifact_hash integrity
# ---------------------------------------------------------------------------

def test_artifact_hash_is_sha256_of_content():
    port = FakeStelePromotionPort()
    content = "deterministic content for hash check"
    artifact = port.commit_artifact(_candidate(content))
    expected = hashlib.sha256(content.encode()).hexdigest()
    assert artifact.artifact_hash == expected


def test_different_content_gives_different_hash():
    port = FakeStelePromotionPort()
    a1 = port.commit_artifact(_candidate("alpha"))
    a2 = port.commit_artifact(_candidate("beta"))
    assert a1.artifact_hash != a2.artifact_hash


# ---------------------------------------------------------------------------
# record_id and run_id are unique across calls
# ---------------------------------------------------------------------------

def test_record_id_and_run_id_are_unique():
    port = FakeStelePromotionPort()
    a1 = port.commit_artifact(_candidate("x"))
    a2 = port.commit_artifact(_candidate("x"))
    assert a1.record_id != a2.record_id
    assert a1.run_id != a2.run_id
