"""Promotion installs into the approved catalog and closes the loop.

Runs without Stele: promotion goes through FakeStelePromotionPort. All
writes go to pytest tmp_path.

  enforce  → obligation_open
  submit   → SUBMITTED
  promote  → installed at approved/<dir_name>/SKILL.md, index reloaded, REDEEMED
  enforce  → covered by the installed skill (no second obligation)
"""

from pathlib import Path

import pytest

from agentsync.enforcer.enforcer import SkillBuilderEnforcer
from agentsync.enforcer.models import ObligationStatus
from agentsync.kanon.models import PromotionState, SkillCandidate
from agentsync.kanon.promotion_adapter import PromotionAdapter
from agentsync.kanon.stele_port import FakeStelePromotionPort
from agentsync.rules.models import EnforcementLevel, MatchedRule, ResolverPath, RuleEvaluation
from agentsync.skills.registry import SkillConflictError, install_approved_skill
from agentsync.skills.skill_server import SkillServer

REQUIRED_EVIDENCE = ["dry_run_output", "rollback_step"]
DIR_NAME = "vendor-offboarding-runbook"
TASK_DESCRIPTION = "revoke all entitlements for a departing contractor"

VALID_SKILL_MD = """\
---
name: vendor-offboarding-runbook
description: Offboard a vendor account safely. Use when a contractor leaves.
---

# Overview
Removes access for a departing contractor.

# When to Use
Use when a contractor engagement ends.

# Process
1. Export current entitlements.
2. Revoke them.

# Verification
- dry_run_output attached before revocation
- rollback_step recorded for each revoked entitlement
"""


def _eval(task_id: str) -> RuleEvaluation:
    return RuleEvaluation(
        task_id=task_id,
        matched_rules=[MatchedRule(
            rule_id="iam-test",
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
def approved(tmp_path: Path) -> Path:
    root = tmp_path / "approved"
    root.mkdir()
    return root


@pytest.fixture
def server(approved: Path) -> SkillServer:
    return SkillServer(approved_root=approved)


@pytest.fixture
def enforcer(tmp_path: Path, server: SkillServer) -> SkillBuilderEnforcer:
    return SkillBuilderEnforcer(lookup=server, obligations_path=tmp_path / "obligations.jsonl")


def _submitted_token(enforcer: SkillBuilderEnforcer, task_id: str) -> str:
    result = enforcer.enforce(_eval(task_id), task_description=TASK_DESCRIPTION)
    assert result.outcome == "obligation_open"
    enforcer.submit_candidate(result.skill_obligation_token, f"staging/{task_id}/SKILL.md")
    return result.skill_obligation_token


def _candidate(content: str = VALID_SKILL_MD) -> SkillCandidate:
    return SkillCandidate(
        content=content, dir_name=DIR_NAME, required_evidence=REQUIRED_EVIDENCE,
    )


def test_promotion_installs_skill_and_closes_loop(
    enforcer: SkillBuilderEnforcer, server: SkillServer, approved: Path,
) -> None:
    token = _submitted_token(enforcer, "T-LOOP")
    adapter = PromotionAdapter(
        enforcer, FakeStelePromotionPort(),
        approved_root=approved, on_installed=server.reload,
    )

    result = adapter.promote(token, _candidate())

    assert result.closure_unblocked is True
    installed = approved / DIR_NAME / "SKILL.md"
    assert installed.read_text(encoding="utf-8") == VALID_SKILL_MD
    assert not list((approved / DIR_NAME).glob(".*.tmp"))

    obl = enforcer.get_obligation(token)
    assert obl.status is ObligationStatus.REDEEMED
    assert obl.redeemed_skill_id == DIR_NAME

    # Served by the same index the Enforcer uses — no restart needed.
    assert DIR_NAME in {s.canonical_id for s in server.list_skills()}

    # Re-enforcing the task is covered by the skill it produced.
    again = enforcer.enforce(_eval("T-LOOP"), task_description=TASK_DESCRIPTION)
    assert again.outcome == "covered"
    assert again.existing_skill_id == DIR_NAME
    assert again.skill_obligation_token is None


def test_redeemed_task_reobligates_if_skill_removed(
    enforcer: SkillBuilderEnforcer, server: SkillServer, approved: Path,
) -> None:
    token = _submitted_token(enforcer, "T-GONE")
    PromotionAdapter(
        enforcer, FakeStelePromotionPort(),
        approved_root=approved, on_installed=server.reload,
    ).promote(token, _candidate())

    (approved / DIR_NAME / "SKILL.md").unlink()
    server.reload()

    again = enforcer.enforce(_eval("T-GONE"), task_description=TASK_DESCRIPTION)
    assert again.outcome == "obligation_open"
    assert again.skill_obligation_token != token


def test_conflicting_dir_name_rejected_before_stele(
    enforcer: SkillBuilderEnforcer, server: SkillServer, approved: Path,
) -> None:
    install_approved_skill(DIR_NAME, "existing different skill", approved)
    token = _submitted_token(enforcer, "T-CONFLICT")
    port = FakeStelePromotionPort()
    adapter = PromotionAdapter(enforcer, port, approved_root=approved)

    result = adapter.promote(token, _candidate())

    assert result.closure_unblocked is False
    assert result.state is None
    assert any("already exists" in e for e in result.validation_errors)
    assert port.call_count() == 0
    assert (approved / DIR_NAME / "SKILL.md").read_text(encoding="utf-8") == "existing different skill"
    assert enforcer.get_obligation(token).status is ObligationStatus.SUBMITTED


def test_reinstalling_identical_content_is_allowed(approved: Path) -> None:
    install_approved_skill(DIR_NAME, VALID_SKILL_MD, approved)
    path = install_approved_skill(DIR_NAME, VALID_SKILL_MD, approved)
    assert path.read_text(encoding="utf-8") == VALID_SKILL_MD

    with pytest.raises(SkillConflictError):
        install_approved_skill(DIR_NAME, "other", approved)


def test_failed_commit_installs_nothing(
    enforcer: SkillBuilderEnforcer, approved: Path,
) -> None:
    token = _submitted_token(enforcer, "T-FAIL")
    adapter = PromotionAdapter(
        enforcer, FakeStelePromotionPort(outcome=PromotionState.FAILED),
        approved_root=approved,
    )

    result = adapter.promote(token, _candidate())

    assert result.closure_unblocked is False
    assert not (approved / DIR_NAME).exists()
    assert enforcer.get_obligation(token).status is ObligationStatus.SUBMITTED


def test_adapter_without_approved_root_does_not_install(
    enforcer: SkillBuilderEnforcer, approved: Path,
) -> None:
    token = _submitted_token(enforcer, "T-NOROOT")
    result = PromotionAdapter(enforcer, FakeStelePromotionPort()).promote(token, _candidate())

    assert result.closure_unblocked is True
    assert not (approved / DIR_NAME).exists()
    assert enforcer.get_obligation(token).redeemed_skill_id is None


@pytest.mark.parametrize("bad", ["", "..", "../escape", "a/b"])
def test_install_rejects_paths_outside_approved(approved: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        install_approved_skill(bad, VALID_SKILL_MD, approved)
