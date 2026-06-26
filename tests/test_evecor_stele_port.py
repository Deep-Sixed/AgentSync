"""Tests for EvecorStelePort against a real temporary Stele LedgerStore.

All filesystem writes go to pytest tmp_path.
No EVECOR files are modified.
No files are written outside tmp_path.

Notes on artifact_hash:
  Stele's artifact_hash is sha256_manifest({"SKILL.md": sha256_file(skill_path)}),
  NOT a direct sha256 of the content.  Test 2 verifies the manifest hash;
  the direct content hash is a different value.
"""

import hashlib
import uuid
from pathlib import Path

import pytest

from stele.ledger.hashing import sha256_file, sha256_manifest
from stele.ledger.store import LedgerStore

from agentsync.enforcer.enforcer import SkillBuilderEnforcer
from agentsync.enforcer.models import ObligationStatus
from agentsync.kanon.evecor_stele_port import EvecorStelePort
from agentsync.kanon.models import PromotionState, SkillCandidate
from agentsync.kanon.promotion_adapter import PromotionAdapter
from agentsync.rules.models import EnforcementLevel, MatchedRule, ResolverPath, RuleEvaluation
from agentsync.skills.skill_server import SkillLookupResult, SkillQuery


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

REQUIRED_EVIDENCE = ["dry_run_output", "rollback_step"]

# A minimal candidate for port-level tests — content does not need to satisfy
# the Kanon PROMOTE validator (these tests call the port directly, not the adapter).
SIMPLE_CONTENT = "simple skill content for testing the real stele port"

# A fully valid SKILL.md for the end-to-end adapter test (must pass PROMOTE level).
VALID_SKILL_MD = """\
---
name: test-evecor-stele-skill
description: Real port integration test skill. Use when testing the EvecorStelePort end-to-end pipeline.
---

# Overview
Test skill for the EvecorStelePort real Stele integration lab.

# When to Use
Use when testing the full PromotionAdapter to EvecorStelePort to LedgerStore pipeline.

# Process
1. Submit candidate via PromotionAdapter.promote().
2. EvecorStelePort writes SKILL.md to disk.
3. LedgerStore creates a PENDING record then commits it.
4. PromotionAdapter redeems the obligation token.

# Verification
- dry_run_output confirmed in the ledger artifact manifest
- rollback_step documented for every committed promotion action
"""


class MissingLookup:
    def find_matching_skill(self, query: SkillQuery) -> SkillLookupResult:
        return SkillLookupResult(status="missing", canonical_id=None, score=0.3)


def _make_eval(task_id: str = "T-EVECOR") -> RuleEvaluation:
    return RuleEvaluation(
        task_id=task_id,
        matched_rules=[MatchedRule(
            rule_id="iam-evecor-test",
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


@pytest.fixture
def artifact_base(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


@pytest.fixture
def store(tmp_path: Path) -> LedgerStore:
    return LedgerStore(tmp_path / "stele.db")


@pytest.fixture
def port(store: LedgerStore, artifact_base: Path) -> EvecorStelePort:
    return EvecorStelePort(store, artifact_base)


@pytest.fixture
def simple_candidate() -> SkillCandidate:
    return SkillCandidate(content=SIMPLE_CONTENT, dir_name="test-skill")


@pytest.fixture
def run_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# 1. Basic commit
# ---------------------------------------------------------------------------

def test_evecor_port_commits_candidate_skill(
    port: EvecorStelePort,
    simple_candidate: SkillCandidate,
    run_id: str,
    artifact_base: Path,
) -> None:
    artifact = port.commit_artifact(simple_candidate, run_id)

    assert artifact.state is PromotionState.COMMITTED
    assert artifact.run_id == run_id
    assert artifact.record_id != ""
    assert artifact.artifact_hash != ""

    skill_path = artifact_base / run_id / "SKILL.md"
    assert skill_path.exists(), f"SKILL.md not written at {skill_path}"
    assert skill_path.read_text(encoding="utf-8") == SIMPLE_CONTENT


# ---------------------------------------------------------------------------
# 2. artifact_hash matches the written file (manifest hash, not raw content hash)
# ---------------------------------------------------------------------------

def test_evecor_port_hash_matches_written_content(
    port: EvecorStelePort,
    simple_candidate: SkillCandidate,
    run_id: str,
    artifact_base: Path,
) -> None:
    artifact = port.commit_artifact(simple_candidate, run_id)

    skill_path = artifact_base / run_id / "SKILL.md"
    # Stele artifact_hash = sha256_manifest({"SKILL.md": sha256_file(path)})
    # This is the manifest hash — NOT sha256(content) directly.
    expected = sha256_manifest({"SKILL.md": sha256_file(skill_path)})
    assert artifact.artifact_hash == expected

    # Confirm it differs from a direct sha256 of the raw content (documents the contract).
    direct_hash = hashlib.sha256(SIMPLE_CONTENT.encode()).hexdigest()
    assert artifact.artifact_hash != direct_hash


# ---------------------------------------------------------------------------
# 3. run_id join key preserved exactly
# ---------------------------------------------------------------------------

def test_evecor_port_preserves_join_key(
    port: EvecorStelePort,
    simple_candidate: SkillCandidate,
) -> None:
    expected = str(uuid.uuid4())
    artifact = port.commit_artifact(simple_candidate, expected)
    assert artifact.run_id == expected


# ---------------------------------------------------------------------------
# 4. Duplicate content behavior is explicit
# ---------------------------------------------------------------------------

def test_evecor_port_duplicate_content_behavior_is_explicit(
    port: EvecorStelePort,
    simple_candidate: SkillCandidate,
) -> None:
    """Same content, different run_ids → both return COMMITTED (duplicate_policy='ignore').

    Locked behavior:
      - First commit writes the artifact and creates the COMMITTED record.
      - Second commit hits the duplicate guard (same artifact_hash already committed)
        and returns the existing record rather than raising DuplicateArtifactError.
      - Both calls return state=COMMITTED and preserve their own run_id.
      - Both calls share the same artifact_hash (content is identical).
    """
    rid1 = str(uuid.uuid4())
    rid2 = str(uuid.uuid4())

    a1 = port.commit_artifact(simple_candidate, rid1)
    a2 = port.commit_artifact(simple_candidate, rid2)

    assert a1.state is PromotionState.COMMITTED
    assert a2.state is PromotionState.COMMITTED

    # Each call preserves its own join key
    assert a1.run_id == rid1
    assert a2.run_id == rid2

    # Identical content → identical artifact_hash
    assert a1.artifact_hash == a2.artifact_hash

    # record_id may be the same (existing record returned for duplicate) — that's fine
    # The important thing is both callers get COMMITTED, not FAILED


# ---------------------------------------------------------------------------
# 5. Invalid run_id fails cleanly — port must not mint a replacement UUID
# ---------------------------------------------------------------------------

def test_evecor_port_invalid_run_id_fails_cleanly(
    port: EvecorStelePort,
    simple_candidate: SkillCandidate,
) -> None:
    """A non-UUID run_id must raise ValueError — never silently replaced."""
    with pytest.raises(ValueError, match="UUID"):
        port.commit_artifact(simple_candidate, "not-a-uuid")


def test_evecor_port_empty_run_id_fails_cleanly(
    port: EvecorStelePort,
    simple_candidate: SkillCandidate,
) -> None:
    with pytest.raises(ValueError, match="UUID"):
        port.commit_artifact(simple_candidate, "")


# ---------------------------------------------------------------------------
# 6. End-to-end: PromotionAdapter + real EvecorStelePort redeems token
# ---------------------------------------------------------------------------

def test_promotion_adapter_with_real_evecor_port_redeems_token(
    tmp_path: Path,
) -> None:
    """Full pipeline with real LedgerStore — no Fake anywhere.

    Enforcer SUBMITTED obligation
      → Kanon validates SKILL.md (PROMOTE level)
      → EvecorStelePort writes file + LedgerStore.create_pending + commit
      → PromotionAdapter redeems token
      → closure_unblocked=True
    """
    oblig_path = tmp_path / "obligations.jsonl"
    enforcer = SkillBuilderEnforcer(lookup=MissingLookup(), obligations_path=oblig_path)

    # Mint and submit obligation
    result = enforcer.enforce(
        _make_eval("T-E2E-REAL"),
        task_description="provision birthright entitlement set",
    )
    token = result.skill_obligation_token
    obl = enforcer.get_obligation(token)
    enforcer.submit_candidate(token, "staging/T-E2E-REAL/SKILL.md")

    # Wire the real port
    store = LedgerStore(tmp_path / "stele.db")
    real_port = EvecorStelePort(store, tmp_path / "artifacts")
    adapter = PromotionAdapter(enforcer, real_port)

    candidate = SkillCandidate(
        content=VALID_SKILL_MD,
        dir_name="test-evecor-stele-skill",
        file_name="SKILL.md",
        required_evidence=REQUIRED_EVIDENCE,
    )

    promote_result = adapter.promote(token, candidate)

    # Closure gate
    assert promote_result.closure_unblocked is True
    assert promote_result.state is PromotionState.COMMITTED
    assert promote_result.validation_errors == []

    # Join-key seam: artifact.run_id must match the obligation's run_id
    assert promote_result.artifact is not None
    assert promote_result.artifact.run_id == obl.run_id

    # Obligation is REDEEMED and proof is attached
    redeemed = enforcer.get_obligation(token)
    assert redeemed.status is ObligationStatus.REDEEMED
    assert redeemed.closure_blocked is False
    assert redeemed.redeemed_artifact_hash == promote_result.artifact.artifact_hash

    # Stele ledger confirms the record
    records = store.find_by_run_id(obl.run_id)
    assert len(records) == 1
    assert records[0].artifact_hash == promote_result.artifact.artifact_hash
