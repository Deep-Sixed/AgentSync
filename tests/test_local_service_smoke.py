"""Local service smoke tests — full AgentSync loop, no ContextForge.

Proves the complete pipeline with all real components:

  Rule Server evaluates task
    ↓ skill_expected=True
  Skill Server returns missing (real approved/ tree)
    ↓ outcome=obligation_open
  Enforcer mints skill_obligation_token
    ↓
  Candidate SKILL.md submitted
    ↓ SUBMITTED
  Kanon validates at PROMOTE level
    ↓ valid=True
  EvecorStelePort commits through real LedgerStore
    ↓ ArtifactState.SEALED (reported as PromotionState.COMMITTED)
  PromotionAdapter redeems token
    ↓
  closure_unblocked=True

All storage in pytest tmp_path. Real rules.yaml, real approved/ skills,
real LedgerStore (SQLite). No mocks. No Fakes. No ContextForge.
No writes outside tmp_path or the AgentSync repo storage/.
"""

from pathlib import Path

import pytest

pytest.importorskip("stele", reason="needs the [stele] extra")

from stele.archive.store import BlobStore
from stele.ledger.store import LedgerStore

from agentsync.enforcer.enforcer import SkillBuilderEnforcer
from agentsync.enforcer.models import ObligationStatus
from agentsync.kanon.evecor_stele_port import EvecorStelePort
from agentsync.kanon.models import PromotionState, SkillCandidate
from agentsync.kanon.promotion_adapter import PromotionAdapter
from agentsync.rules.models import SkillMatchStatus, TaskContext
from agentsync.rules.rule_resolver import RuleResolver

# ---------------------------------------------------------------------------
# Repo-relative paths (real storage, read-only in tests)
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]
RULES_PATH = REPO / "storage" / "rules" / "rules.yaml"
CAPS_PATH = REPO / "storage" / "rules" / "capabilities.yaml"
APPROVED_PATH = REPO / "tests" / "fixtures" / "approved"

# ---------------------------------------------------------------------------
# Task descriptions
#
# MISSING_TASK_DESC: triggers iam-provisioning-sailpoint (contains "birthright"
#   + "entitlement") but does NOT name any approved skill — confirmed missing.
# COVERED_TASK_DESC: names the existing approved skill by id — confirmed covered.
# READONLY_TASK_DESC: triggers readonly-investigation (skill_expected=False).
# ---------------------------------------------------------------------------

MISSING_TASK_DESC = "Provision a brand new birthright entitlement set for a new employee identity"
COVERED_TASK_DESC = "run sailpoint-joiner-provisioning for a new joiner"
READONLY_TASK_DESC = "inspect and audit the current entitlement report"

# Required evidence from iam-provisioning-sailpoint in rules.yaml (all three required).
REQUIRED_EVIDENCE = ["dry_run_output", "rollback_step", "approval_reference"]

# SKILL.md candidate for the smoke promotion.
# Must satisfy Kanon PROMOTE level with all three required_evidence items.
VALID_SKILL_MD = """\
---
name: new-birthright-provisioning-skill
description: Provisions birthright access for a new employee via SailPoint. Use when an IAM provisioning task requires a new birthright entitlement workflow not covered by existing skills.
---

# Overview
Provisions the full birthright entitlement set for a new employee identity
via SailPoint IdentityIQ, covering joiner events where no existing skill applies.

# When to Use
Use when an IAM provisioning task requires authoring a new birthright entitlement
workflow that is confirmed missing from the approved skill registry.

# Process
1. Resolve the identity from the HR event feed and confirm no existing skill applies.
2. Select the appropriate birthright entitlement set for the identity role.
3. Execute the provisioning workflow in dry-run mode and capture dry_run_output.
4. Review dry_run_output for unexpected or over-provisioned grants.
5. Obtain approval_reference from the access request system before applying.
6. Apply the entitlement set, recording the rollback_step for every granted item.

# Verification
- dry_run_output reviewed and attached to the access request ticket
- approval_reference captured from the provisioning system before any live apply
- rollback_step documented for every granted birthright entitlement
"""

SKILL_DIR_NAME = "new-birthright-provisioning-skill"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_resolver(tmp_path: Path) -> RuleResolver:
    return RuleResolver(
        rules_path=RULES_PATH,
        capabilities_path=CAPS_PATH,
        pre_obligations_path=tmp_path / "pre_obligations.jsonl",
    )


def _make_enforcer(tmp_path: Path) -> SkillBuilderEnforcer:
    return SkillBuilderEnforcer(
        approved_root=APPROVED_PATH,
        obligations_path=tmp_path / "obligations.jsonl",
    )


# ---------------------------------------------------------------------------
# Smoke 1: covered task — no obligation minted
# ---------------------------------------------------------------------------

def test_smoke_covered_task_no_obligation(tmp_path: Path) -> None:
    """A task the real matcher covers with an existing skill must NOT mint a token."""
    resolver = _make_resolver(tmp_path)
    enforcer = _make_enforcer(tmp_path)

    evaluation = resolver.evaluate_task(TaskContext(
        task_id="SMOKE-COVERED",
        task_description=COVERED_TASK_DESC,
        task_type="iam-provisioning",
    ))
    assert evaluation.skill_expected is True

    result = enforcer.enforce(evaluation, task_description=COVERED_TASK_DESC)

    assert result.outcome == "covered"
    assert result.closure_blocked is False
    assert result.skill_obligation_token is None
    assert result.existing_skill_id == "sailpoint-joiner-provisioning"


# ---------------------------------------------------------------------------
# Smoke 2: read-only task — no obligation, closure never blocked
# ---------------------------------------------------------------------------

def test_smoke_readonly_task_no_skill_expected(tmp_path: Path) -> None:
    """A read-only investigation task must not generate any skill obligation."""
    resolver = _make_resolver(tmp_path)
    enforcer = _make_enforcer(tmp_path)

    evaluation = resolver.evaluate_task(TaskContext(
        task_id="SMOKE-READONLY",
        task_description=READONLY_TASK_DESC,
        task_type="investigation",
    ))

    assert evaluation.skill_expected is False

    result = enforcer.enforce(evaluation, task_description=READONLY_TASK_DESC)

    assert result.outcome == "no_obligation"
    assert result.closure_blocked is False
    assert result.skill_obligation_token is None


# ---------------------------------------------------------------------------
# Smoke 3: full loop — rule → missing → obligation → candidate → promote → redeemed
# ---------------------------------------------------------------------------

def test_smoke_full_loop_obligation_to_redemption(tmp_path: Path) -> None:
    """Full AgentSync pipeline end-to-end with real components, no Fakes.

    Step 1: Rule Server identifies skill gap
    Step 2: Enforcer confirms missing and mints skill_obligation_token
    Step 3: Agent submits candidate SKILL.md → SUBMITTED
    Step 4: PromotionAdapter validates → EvecorStelePort commits → token redeemed
    Step 5: Verify closure_unblocked, obligation REDEEMED, Stele join key intact
    """
    resolver = _make_resolver(tmp_path)
    enforcer = _make_enforcer(tmp_path)

    # ---- Step 1: Rule Server identifies skill gap --------------------------------
    evaluation = resolver.evaluate_task(TaskContext(
        task_id="SMOKE-FULL-1",
        task_description=MISSING_TASK_DESC,
        task_type="iam-provisioning",
        skill_match=SkillMatchStatus.UNKNOWN,
    ))

    assert evaluation.skill_expected is True, (
        "Rule Server must identify a skill gap for this task — "
        "check that rules.yaml matches 'birthright'/'entitlement'"
    )

    # ---- Step 2: Enforcer confirms missing and mints token -----------------------
    enforce_result = enforcer.enforce(
        evaluation,
        task_description=MISSING_TASK_DESC,
    )

    assert enforce_result.outcome == "obligation_open", (
        f"Expected 'obligation_open', got {enforce_result.outcome!r}. "
        "The real approved/ tree may now cover this task description — "
        "update MISSING_TASK_DESC to a description the matcher won't cover."
    )
    assert enforce_result.closure_blocked is True
    token = enforce_result.skill_obligation_token
    assert token is not None

    obl = enforcer.get_obligation(token)
    assert obl is not None
    assert obl.status is ObligationStatus.OPEN
    assert set(REQUIRED_EVIDENCE).issubset(set(obl.required_evidence)), (
        f"real rules.yaml required_evidence mismatch: "
        f"expected {REQUIRED_EVIDENCE}, got {obl.required_evidence}"
    )

    # ---- Step 3: Agent submits candidate SKILL.md --------------------------------
    enforcer.submit_candidate(token, f"staging/SMOKE-FULL-1/{SKILL_DIR_NAME}/SKILL.md")
    assert enforcer.get_obligation(token).status is ObligationStatus.SUBMITTED

    # ---- Step 4: Kanon validates → EvecorStelePort commits → token redeemed ------
    store = LedgerStore(tmp_path / "stele.db", BlobStore(tmp_path / "stele-archive"))
    port = EvecorStelePort(store, tmp_path / "artifacts")
    adapter = PromotionAdapter(enforcer, port)

    candidate = SkillCandidate(
        content=VALID_SKILL_MD,
        dir_name=SKILL_DIR_NAME,
        file_name="SKILL.md",
        required_evidence=obl.required_evidence,
    )

    promote_result = adapter.promote(token, candidate)

    assert promote_result.closure_unblocked is True, (
        f"Promotion failed — validation_errors: {promote_result.validation_errors} "
        f"state: {promote_result.state} note: {promote_result.note!r}"
    )
    assert promote_result.state is PromotionState.COMMITTED
    assert promote_result.validation_errors == []

    # ---- Step 5: Verify closure gate, join key, and Stele ledger -----------------
    artifact = promote_result.artifact
    assert artifact is not None
    assert artifact.state is PromotionState.COMMITTED

    # Join key: artifact.run_id must equal the obligation's run_id
    assert artifact.run_id == obl.run_id, (
        "Join-key seam broken: artifact.run_id does not match obligation.run_id"
    )

    # Obligation is REDEEMED, closure unblocked, proof attached
    redeemed = enforcer.get_obligation(token)
    assert redeemed.status is ObligationStatus.REDEEMED
    assert redeemed.closure_blocked is False
    assert redeemed.redeemed_artifact_hash == artifact.artifact_hash

    # Stele ledger confirms the record via run_id join
    record = store.get_by_run_id(obl.run_id)
    assert record is not None, f"No Stele record for run_id={obl.run_id}"
    assert record.artifact_hash == artifact.artifact_hash

    # SKILL.md written to expected path inside tmp_path
    skill_path = tmp_path / "artifacts" / obl.run_id / "SKILL.md"
    assert skill_path.exists()
    assert skill_path.read_text(encoding="utf-8") == VALID_SKILL_MD
