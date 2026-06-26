"""Integration tests for the Kanon → Stele promotion adapter.

Proves the full seam:

    Enforcer SUBMITTED obligation
      ↓
    Kanon validates candidate SKILL.md
      ↓
    Fake Stele commits artifact
      ↓
    Kanon redeems token
      ↓
    closure_unblocked=True

Coverage:
  - token missing            → KeyError
  - token not SUBMITTED      → ValueError
  - candidate missing/empty  → validation_errors, closure_unblocked=False
  - invalid SKILL.md         → validation_errors, closure_unblocked=False
  - Stele FAILED             → closure_unblocked=False, state=FAILED
  - Stele INVALIDATED        → closure_unblocked=False, state=INVALIDATED
  - happy path               → closure_unblocked=True, obligation REDEEMED
"""

import uuid
from pathlib import Path

import pytest

from agentsync.enforcer.enforcer import SkillBuilderEnforcer
from agentsync.enforcer.models import ObligationStatus
from agentsync.enforcer.store import get_obligation
from agentsync.rules.models import (
    EnforcementLevel,
    MatchedRule,
    ResolverPath,
    RuleEvaluation,
)
from agentsync.skills.skill_server import SkillLookupResult, SkillQuery

from agentsync.kanon.models import PromotionState, SkillCandidate
from agentsync.kanon.promotion_adapter import PromotionAdapter
from agentsync.kanon.stele_port import FakeStelePromotionPort


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class MissingLookup:
    """Stub — always reports a confirmed gap."""
    def find_matching_skill(self, query: SkillQuery) -> SkillLookupResult:
        return SkillLookupResult(
            status="missing",
            canonical_id=None,
            best_near_match="sailpoint-joiner-provisioning",
            score=0.35,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REQUIRED_EVIDENCE = ["dry_run_output", "rollback_step"]

VALID_SKILL_MD = """\
---
name: test-kanon-skill
description: Validates the Kanon promotion pipeline. Use when testing the promotion adapter end-to-end.
---

# Overview
Test skill for the Kanon-Stele promotion adapter integration lab.

# When to Use
Use when testing the Kanon-Stele promotion pipeline in the sandbox.

# Process
1. Submit the candidate SKILL.md to the PromotionAdapter.
2. Adapter validates content at PROMOTE level.
3. Adapter calls StelePromotionPort.commit_artifact().
4. On COMMITTED the Enforcer token is redeemed.

# Verification
- dry_run_output confirmed in the verification log
- rollback_step documented for every promoted action
"""


def _make_eval(task_id: str = "T-KANON") -> RuleEvaluation:
    return RuleEvaluation(
        task_id=task_id,
        matched_rules=[MatchedRule(
            rule_id="iam-kanon-test",
            skill_expected=True,
            skill_family="iam-lifecycle",
            capability_family="identity-governance",
            required_evidence=REQUIRED_EVIDENCE,
            enforcement_level=EnforcementLevel.BLOCK,
        )],
        resolver_path=ResolverPath.PATTERN,
        skill_expected=True,
        skill_family="iam-lifecycle",
        capability_family="identity-governance",
        required_evidence=REQUIRED_EVIDENCE,
        enforcement_level=EnforcementLevel.BLOCK,
        closure_blocked_if_missing_skill=True,
    )


def _submitted_token(
    enforcer: SkillBuilderEnforcer,
    task_id: str = "T-KANON",
    candidate_path: str = "staging/reviews/T-KANON/SKILL.md",
) -> str:
    """Mint an obligation and advance it to SUBMITTED; return the token."""
    r = enforcer.enforce(_make_eval(task_id), task_description="provision new birthright set")
    token = r.skill_obligation_token
    enforcer.submit_candidate(token, candidate_path)
    return token


@pytest.fixture
def oblig_path(tmp_path: Path) -> Path:
    return tmp_path / "skill_obligations.jsonl"


@pytest.fixture
def enforcer(oblig_path: Path) -> SkillBuilderEnforcer:
    return SkillBuilderEnforcer(lookup=MissingLookup(), obligations_path=oblig_path)


@pytest.fixture
def fake_stele() -> FakeStelePromotionPort:
    return FakeStelePromotionPort()


@pytest.fixture
def adapter(enforcer: SkillBuilderEnforcer, fake_stele: FakeStelePromotionPort) -> PromotionAdapter:
    return PromotionAdapter(enforcer, fake_stele)


@pytest.fixture
def valid_candidate() -> SkillCandidate:
    return SkillCandidate(
        content=VALID_SKILL_MD,
        dir_name="test-kanon-skill",
        file_name="SKILL.md",
        required_evidence=REQUIRED_EVIDENCE,
    )


# ---------------------------------------------------------------------------
# Test: token missing
# ---------------------------------------------------------------------------

def test_token_missing_raises_key_error(adapter: PromotionAdapter, valid_candidate: SkillCandidate):
    """Passing an unknown token must raise KeyError immediately."""
    with pytest.raises(KeyError):
        adapter.promote(str(uuid.uuid4()), valid_candidate)


# ---------------------------------------------------------------------------
# Test: token not SUBMITTED
# ---------------------------------------------------------------------------

def test_token_not_submitted_raises_value_error(
    enforcer: SkillBuilderEnforcer,
    adapter: PromotionAdapter,
    oblig_path: Path,
    valid_candidate: SkillCandidate,
):
    """A token that is OPEN (not yet SUBMITTED) must raise ValueError."""
    r = enforcer.enforce(_make_eval("T-OPEN"), task_description="provision birthright")
    open_token = r.skill_obligation_token
    # token is OPEN, not SUBMITTED
    with pytest.raises(ValueError, match="submitted"):
        adapter.promote(open_token, valid_candidate)


# ---------------------------------------------------------------------------
# Test: candidate empty
# ---------------------------------------------------------------------------

def test_empty_candidate_fails_validation(
    enforcer: SkillBuilderEnforcer, adapter: PromotionAdapter, oblig_path: Path
):
    """An empty candidate body must fail Kanon validation; Stele never called."""
    token = _submitted_token(enforcer, "T-EMPTY")
    empty = SkillCandidate(content="", dir_name="test-skill", file_name="SKILL.md")
    result = adapter.promote(token, empty)

    assert result.closure_unblocked is False
    assert result.state is None
    assert len(result.validation_errors) > 0
    assert result.artifact is None


# ---------------------------------------------------------------------------
# Test: invalid SKILL.md (missing required sections)
# ---------------------------------------------------------------------------

def test_invalid_skill_md_fails_validation(
    enforcer: SkillBuilderEnforcer, adapter: PromotionAdapter
):
    """A candidate missing core sections must fail validation; Stele never called."""
    token = _submitted_token(enforcer, "T-INVALID")
    bad = SkillCandidate(
        content="no frontmatter\njust some text\n",
        dir_name="test-skill",
        file_name="SKILL.md",
    )
    result = adapter.promote(token, bad)

    assert result.closure_unblocked is False
    assert result.state is None
    assert any("frontmatter" in e or "missing" in e for e in result.validation_errors)
    assert result.artifact is None


def test_invalid_skill_md_missing_evidence_fails(
    enforcer: SkillBuilderEnforcer, adapter: PromotionAdapter
):
    """Verification section present but required_evidence keys absent → invalid."""
    token = _submitted_token(enforcer, "T-NOEVIDENCE")
    # Has all sections but Verification doesn't mention required_evidence
    no_evidence = """\
---
name: no-evidence-skill
description: A test skill missing required evidence. Use when testing missing evidence validation.
---

# Overview
Skill without the required evidence terms.

# When to Use
Use when confirming that missing evidence is caught.

# Process
1. Run the task.
2. Review output.

# Verification
- task completed
"""
    candidate = SkillCandidate(
        content=no_evidence,
        dir_name="no-evidence-skill",
        file_name="SKILL.md",
        required_evidence=REQUIRED_EVIDENCE,
    )
    result = adapter.promote(token, candidate)

    assert result.closure_unblocked is False
    assert any("required_evidence" in e for e in result.validation_errors)


# ---------------------------------------------------------------------------
# Test: Stele FAILED
# ---------------------------------------------------------------------------

def test_stele_failed_returns_not_unblocked(
    enforcer: SkillBuilderEnforcer, oblig_path: Path, valid_candidate: SkillCandidate
):
    """When Stele returns FAILED, closure stays blocked; obligation stays SUBMITTED."""
    token = _submitted_token(enforcer, "T-SFAIL")
    port = FakeStelePromotionPort(outcome=PromotionState.FAILED)
    adapter = PromotionAdapter(enforcer, port)

    result = adapter.promote(token, valid_candidate)

    assert result.closure_unblocked is False
    assert result.state is PromotionState.FAILED
    assert result.artifact is not None
    assert result.artifact.state is PromotionState.FAILED
    # obligation must NOT have advanced to REDEEMED
    obl = get_obligation(token, oblig_path)
    assert obl is not None
    assert obl.status is ObligationStatus.SUBMITTED


# ---------------------------------------------------------------------------
# Test: Stele INVALIDATED
# ---------------------------------------------------------------------------

def test_stele_invalidated_returns_not_unblocked(
    enforcer: SkillBuilderEnforcer, oblig_path: Path, valid_candidate: SkillCandidate
):
    """When Stele returns INVALIDATED, closure stays blocked; obligation stays SUBMITTED."""
    token = _submitted_token(enforcer, "T-SINV")
    port = FakeStelePromotionPort(outcome=PromotionState.INVALIDATED)
    adapter = PromotionAdapter(enforcer, port)

    result = adapter.promote(token, valid_candidate)

    assert result.closure_unblocked is False
    assert result.state is PromotionState.INVALIDATED
    assert result.artifact is not None
    assert result.artifact.state is PromotionState.INVALIDATED
    obl = get_obligation(token, oblig_path)
    assert obl is not None
    assert obl.status is ObligationStatus.SUBMITTED


# ---------------------------------------------------------------------------
# Test: happy path — the full seam
# ---------------------------------------------------------------------------

def test_happy_path_closure_unblocked(
    enforcer: SkillBuilderEnforcer,
    adapter: PromotionAdapter,
    fake_stele: FakeStelePromotionPort,
    oblig_path: Path,
    valid_candidate: SkillCandidate,
):
    """Full pipeline: SUBMITTED → valid SKILL.md → COMMITTED → REDEEMED → closure_unblocked=True."""
    token = _submitted_token(enforcer, "T-HAPPY")

    result = adapter.promote(token, valid_candidate)

    # Closure gate
    assert result.closure_unblocked is True
    assert result.state is PromotionState.COMMITTED
    assert result.validation_errors == []

    # Stele received the candidate
    assert fake_stele.call_count() == 1
    submitted = fake_stele.calls()[0]
    assert submitted.dir_name == "test-kanon-skill"
    assert submitted.file_name == "SKILL.md"

    # artifact receipt present and coherent
    assert result.artifact is not None
    assert result.artifact.state is PromotionState.COMMITTED
    import hashlib
    expected_hash = hashlib.sha256(valid_candidate.content.encode()).hexdigest()
    assert result.artifact.artifact_hash == expected_hash

    # Obligation is now REDEEMED and closure_blocked=False
    obl = get_obligation(token, oblig_path)
    assert obl is not None
    assert obl.status is ObligationStatus.REDEEMED
    assert obl.closure_blocked is False
    assert obl.redeemed_artifact_hash == result.artifact.artifact_hash


# ---------------------------------------------------------------------------
# Test: idempotency guard — double promote is blocked
# ---------------------------------------------------------------------------

def test_double_promote_blocked(
    enforcer: SkillBuilderEnforcer,
    adapter: PromotionAdapter,
    oblig_path: Path,
    valid_candidate: SkillCandidate,
):
    """A REDEEMED token cannot be promoted again; adapter raises ValueError."""
    token = _submitted_token(enforcer, "T-DOUBLE")
    adapter.promote(token, valid_candidate)  # first promote → REDEEMED

    # Second promote must fail because token is now REDEEMED, not SUBMITTED
    with pytest.raises(ValueError):
        adapter.promote(token, valid_candidate)
