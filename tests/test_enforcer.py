"""Tests for the Skill Builder Enforcer (the ratchet).

Proves: covered / obligation_open / no_obligation outcomes, idempotent minting,
the OPEN->SUBMITTED->REDEEMED lifecycle, cancel, invalid-transition guards, and
the end-to-end wire from a real Rule Server evaluation to a minted token.
"""

import uuid
from pathlib import Path

import pytest

from agentsync.rules.models import (
    EnforcementLevel,
    MatchedRule,
    ResolverPath,
    RuleEvaluation,
)
from agentsync.enforcer.enforcer import SkillBuilderEnforcer
from agentsync.enforcer.lookup import SkillLookup
from agentsync.enforcer.models import ObligationStatus
from agentsync.enforcer.store import get_obligation
from agentsync.skills.skill_server import SkillLookupResult, SkillQuery

REPO = Path(__file__).resolve().parents[1]
APPROVED = REPO / "storage" / "skills" / "approved"


# --- test doubles -----------------------------------------------------------

class FoundLookup:
    """Lookup stub that always reports coverage."""
    def find_matching_skill(self, query: SkillQuery) -> SkillLookupResult:
        return SkillLookupResult(status="found", canonical_id="some-skill",
                                 matched_name="some-skill", score=1.0)


class MissingLookup:
    """Lookup stub that always reports a confirmed gap, with a near miss."""
    def find_matching_skill(self, query: SkillQuery) -> SkillLookupResult:
        return SkillLookupResult(status="missing", canonical_id=None,
                                 best_near_match="sailpoint-joiner-provisioning",
                                 score=0.42)


def _expected_eval(task_id="T-1"):
    """A Rule Server evaluation that expects a block-level skill."""
    return RuleEvaluation(
        task_id=task_id,
        matched_rules=[MatchedRule(
            rule_id="iam-provisioning-sailpoint", skill_expected=True,
            skill_family="iam-lifecycle", capability_family="identity-governance",
            required_evidence=["dry_run_output", "rollback_step"],
            enforcement_level=EnforcementLevel.BLOCK,
        )],
        resolver_path=ResolverPath.PATTERN,
        skill_expected=True,
        skill_family="iam-lifecycle",
        capability_family="identity-governance",
        required_evidence=["dry_run_output", "rollback_step"],
        enforcement_level=EnforcementLevel.BLOCK,
        closure_blocked_if_missing_skill=True,
        pre_obligation_id="pre-123",
    )


@pytest.fixture
def oblig_path(tmp_path):
    return tmp_path / "skill_obligations.jsonl"


# --- the three outcomes -----------------------------------------------------

def test_no_obligation_when_skill_not_expected(oblig_path):
    enf = SkillBuilderEnforcer(lookup=MissingLookup(), obligations_path=oblig_path)
    ev = RuleEvaluation(task_id="T-0", resolver_path=ResolverPath.NONE,
                        skill_expected=False)
    r = enf.enforce(ev)
    assert r.outcome == "no_obligation"
    assert r.skill_obligation_token is None
    assert r.closure_blocked is False


def test_covered_when_lookup_found(oblig_path):
    enf = SkillBuilderEnforcer(lookup=FoundLookup(), obligations_path=oblig_path)
    r = enf.enforce(_expected_eval())
    assert r.outcome == "covered"
    assert r.existing_skill_id == "some-skill"
    assert r.skill_obligation_token is None
    assert r.closure_blocked is False
    # nothing minted
    assert not oblig_path.exists() or oblig_path.read_text() == ""


def test_covered_via_real_description_against_real_matcher(oblig_path):
    """Regression: the Enforcer must feed the task DESCRIPTION to the lookup,
    not just task_id+family. A task naming an existing skill must be 'covered',
    never minted. (This bug was masked when the lookup was query-starved.)"""
    enf = SkillBuilderEnforcer(obligations_path=oblig_path, approved_root=APPROVED)
    r = enf.enforce(
        _expected_eval("T-COVERED"),
        task_description="run sailpoint-joiner-provisioning for this joiner",
    )
    assert r.outcome == "covered"
    assert r.existing_skill_id == "sailpoint-joiner-provisioning"
    assert r.skill_obligation_token is None


def test_obligation_minted_when_missing(oblig_path):
    enf = SkillBuilderEnforcer(lookup=MissingLookup(), obligations_path=oblig_path)
    r = enf.enforce(_expected_eval())
    assert r.outcome == "obligation_open"
    assert r.skill_obligation_token is not None
    assert r.run_id is not None                      # Stele join key present
    assert r.closure_blocked is True
    assert r.best_near_match == "sailpoint-joiner-provisioning"
    assert r.required_evidence == ["dry_run_output", "rollback_step"]
    # persisted as OPEN
    obl = get_obligation(r.skill_obligation_token, oblig_path)
    assert obl is not None
    assert obl.status == ObligationStatus.OPEN
    assert obl.pre_obligation_id == "pre-123"
    # token + run_id are valid UUIDs
    uuid.UUID(obl.skill_obligation_token)
    uuid.UUID(obl.run_id)


# --- idempotency ------------------------------------------------------------

def test_minting_is_idempotent_per_task(oblig_path):
    enf = SkillBuilderEnforcer(lookup=MissingLookup(), obligations_path=oblig_path)
    r1 = enf.enforce(_expected_eval("T-DUP"))
    r2 = enf.enforce(_expected_eval("T-DUP"))
    assert r1.skill_obligation_token == r2.skill_obligation_token
    assert "idempotent" in (r2.note or "")


# --- lifecycle --------------------------------------------------------------

def test_full_lifecycle_open_submitted_redeemed(oblig_path):
    enf = SkillBuilderEnforcer(lookup=MissingLookup(), obligations_path=oblig_path)
    token = enf.enforce(_expected_eval("T-LC")).skill_obligation_token

    sub = enf.submit_candidate(token, "staging/reviews/T-LC/SKILL.md")
    assert sub.status == ObligationStatus.SUBMITTED
    assert sub.candidate_path.endswith("SKILL.md")

    red = enf.redeem(token, artifact_hash="deadbeef" * 8)
    assert red.status == ObligationStatus.REDEEMED
    assert red.closure_blocked is False
    assert red.redeemed_artifact_hash == "deadbeef" * 8


def test_cancel_from_open(oblig_path):
    enf = SkillBuilderEnforcer(lookup=MissingLookup(), obligations_path=oblig_path)
    token = enf.enforce(_expected_eval("T-CANCEL")).skill_obligation_token
    c = enf.cancel(token, reason="covering skill appeared")
    assert c.status == ObligationStatus.CANCELLED
    assert c.closure_blocked is False


# --- invalid transitions ----------------------------------------------------

def test_cannot_redeem_unsubmitted(oblig_path):
    enf = SkillBuilderEnforcer(lookup=MissingLookup(), obligations_path=oblig_path)
    token = enf.enforce(_expected_eval("T-BAD")).skill_obligation_token
    with pytest.raises(ValueError):
        enf.redeem(token, artifact_hash="x")  # still OPEN, not SUBMITTED


def test_unknown_token_raises(oblig_path):
    enf = SkillBuilderEnforcer(lookup=MissingLookup(), obligations_path=oblig_path)
    with pytest.raises(KeyError):
        enf.submit_candidate("not-a-real-token", "x")


def test_double_redeem_blocked(oblig_path):
    enf = SkillBuilderEnforcer(lookup=MissingLookup(), obligations_path=oblig_path)
    token = enf.enforce(_expected_eval("T-DR")).skill_obligation_token
    enf.submit_candidate(token, "x/SKILL.md")
    enf.redeem(token, artifact_hash="h")
    with pytest.raises(ValueError):
        enf.redeem(token, artifact_hash="h")  # already REDEEMED


# --- end-to-end from a REAL Rule Server -------------------------------------

def test_end_to_end_rule_server_to_token(oblig_path, tmp_path):
    """Real Rule Server decision -> real Enforcer -> minted token, no stubs
    except the lookup (which uses the real conservative matcher via in-process)."""
    from agentsync.rules.rule_resolver import RuleResolver
    from agentsync.rules.models import SkillMatchStatus, TaskContext

    rules = REPO / "storage" / "rules" / "rules.yaml"
    caps = REPO / "storage" / "rules" / "capabilities.yaml"
    resolver = RuleResolver(rules_path=rules, capabilities_path=caps,
                            pre_obligations_path=tmp_path / "pre.jsonl")

    # A task the rules say needs a skill, with a description the conservative
    # matcher will NOT cover (so the Enforcer should mint).
    ev = resolver.evaluate_task(TaskContext(
        task_id="E2E-1",
        task_description="Provision a brand new birthright entitlement set",
        task_type="iam-provisioning",
        skill_match=SkillMatchStatus.MISSING,
    ))
    assert ev.skill_expected is True
    assert ev.authoritative_skill_obligation_token is None  # rule server never mints

    # Real in-process lookup against the real approved/ tree.
    enf = SkillBuilderEnforcer(obligations_path=oblig_path, approved_root=APPROVED)
    r = enf.enforce(ev, task_description="Provision a brand new birthright entitlement set")
    # The matcher is conservative; this task text doesn't name a skill, so missing.
    assert r.outcome == "obligation_open"
    assert r.skill_obligation_token is not None
    assert r.run_id is not None
