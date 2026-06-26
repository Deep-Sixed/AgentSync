"""Tests for the AgentSync Rule Server decision engine.

Proves: pattern resolver, capability fallback, no-match, and the three
skill_match obligation branches (found / missing / unknown), plus the
intentional null-token invariant. No MCP, no network.
"""

from pathlib import Path

import pytest

from agentsync.rules.models import (
    EnforcementLevel,
    ResolverPath,
    SkillMatchStatus,
    TaskContext,
)
from agentsync.rules.pre_obligations import read_pre_obligations
from agentsync.rules.rule_resolver import RuleResolver

REPO = Path(__file__).resolve().parents[1]
RULES = REPO / "storage" / "rules" / "rules.yaml"
CAPS = REPO / "storage" / "rules" / "capabilities.yaml"


@pytest.fixture
def resolver(tmp_path):
    """Resolver wired to the real rule files but an isolated obligations log."""
    return RuleResolver(
        rules_path=RULES,
        capabilities_path=CAPS,
        pre_obligations_path=tmp_path / "pre_obligations.jsonl",
    )


# --- pattern resolver -------------------------------------------------------

def test_pattern_match_strong_task_type(resolver):
    task = TaskContext(
        task_id="T-1",
        task_description="Provision birthright access for a new joiner",
        task_type="iam-provisioning",
        skill_match=SkillMatchStatus.UNKNOWN,
    )
    ev = resolver.evaluate_task(task)
    assert ev.resolver_path == ResolverPath.PATTERN
    assert ev.skill_expected is True
    assert ev.skill_family == "iam-lifecycle"
    assert ev.capability_family == "identity-governance"
    assert ev.enforcement_level == EnforcementLevel.BLOCK
    assert "dry_run_output" in ev.required_evidence
    assert any(m.rule_id == "iam-provisioning-sailpoint" for m in ev.matched_rules)


def test_pattern_match_via_keywords_and_files(resolver):
    task = TaskContext(
        task_id="T-2",
        task_description="Update the Jamf smart-group scope for ADE enrollment",
        touched_files=["jamf/profiles/enroll.mobileconfig"],
        skill_match=SkillMatchStatus.UNKNOWN,
    )
    ev = resolver.evaluate_task(task)
    assert ev.resolver_path == ResolverPath.PATTERN
    assert ev.skill_family == "apple-device-management"
    assert ev.capability_family == "endpoint-management"


def test_pattern_skill_not_expected_readonly(resolver):
    task = TaskContext(
        task_id="T-3",
        task_description="Audit and report current entitlement assignments",
        task_type="audit",
        skill_match=SkillMatchStatus.UNKNOWN,
    )
    ev = resolver.evaluate_task(task)
    # 'audit' is a readonly task_type -> skill not expected, no enforcement.
    assert ev.skill_expected is False
    assert ev.enforcement_level == EnforcementLevel.NONE
    assert ev.closure_blocked_if_missing_skill is False


# --- capability fallback ----------------------------------------------------

def test_capability_fallback_when_pattern_misses(resolver):
    # 'saviynt' isn't a pattern keyword, but it's a capability hint via task_type.
    task = TaskContext(
        task_id="T-4",
        task_description="Reconcile accounts in the access platform",
        task_type="saviynt",
        skill_match=SkillMatchStatus.UNKNOWN,
    )
    ev = resolver.evaluate_task(task)
    assert ev.resolver_path == ResolverPath.CAPABILITY_FALLBACK
    assert ev.capability_family == "identity-governance"
    assert ev.skill_family == "iam-lifecycle"
    assert ev.skill_expected is True


def test_capability_fallback_advise_level(resolver):
    task = TaskContext(
        task_id="T-5",
        task_description="Adjust the model routing weights",
        command_family="litellm",
        skill_match=SkillMatchStatus.UNKNOWN,
    )
    ev = resolver.evaluate_task(task)
    assert ev.resolver_path == ResolverPath.CAPABILITY_FALLBACK
    assert ev.capability_family == "ai-infrastructure"
    assert ev.enforcement_level == EnforcementLevel.ADVISE
    # advise -> expected, but not closure-blocking
    assert ev.closure_blocked_if_missing_skill is False
    assert ev.skill_creation_required_if_missing is True


# --- no match ---------------------------------------------------------------

def test_no_match_returns_none_path(resolver):
    task = TaskContext(
        task_id="T-6",
        task_description="Water the office plants",
        skill_match=SkillMatchStatus.UNKNOWN,
    )
    ev = resolver.evaluate_task(task)
    assert ev.resolver_path == ResolverPath.NONE
    assert ev.skill_expected is False
    assert ev.matched_rules == []


# --- obligation branches (spec steps 5/6/7) ---------------------------------

def test_skill_match_unknown_requires_lookup_no_record(resolver, tmp_path):
    task = TaskContext(
        task_id="T-7",
        task_description="Provision SailPoint access",
        task_type="iam-provisioning",
        skill_match=SkillMatchStatus.UNKNOWN,
    )
    ev = resolver.evaluate_task(task)
    assert ev.skill_lookup_required is True
    assert ev.closure_blocked_if_missing_skill is True   # block-level rule
    assert ev.pre_obligation_id is None                  # not yet confirmed missing
    # nothing written to disk on UNKNOWN
    assert read_pre_obligations(tmp_path / "pre_obligations.jsonl") == []


def test_skill_match_found_no_obligation(resolver, tmp_path):
    task = TaskContext(
        task_id="T-8",
        task_description="Provision SailPoint access",
        task_type="iam-provisioning",
        skill_match=SkillMatchStatus.FOUND,
        existing_skill_id="iam-lifecycle/sailpoint-joiner@v3",
    )
    ev = resolver.evaluate_task(task)
    assert ev.skill_lookup_required is False
    assert ev.closure_blocked_if_missing_skill is False
    assert ev.existing_skill_id == "iam-lifecycle/sailpoint-joiner@v3"
    assert ev.pre_obligation_id is None
    assert read_pre_obligations(tmp_path / "pre_obligations.jsonl") == []


def test_skill_match_missing_writes_pre_obligation(resolver, tmp_path):
    task = TaskContext(
        task_id="T-9",
        task_description="Provision SailPoint access",
        task_type="iam-provisioning",
        skill_match=SkillMatchStatus.MISSING,
    )
    ev = resolver.evaluate_task(task)
    assert ev.skill_creation_required_if_missing is True
    assert ev.closure_blocked_if_missing_skill is True
    assert ev.pre_obligation_id is not None              # record written
    records = read_pre_obligations(tmp_path / "pre_obligations.jsonl")
    assert len(records) == 1
    rec = records[0]
    assert rec["task_id"] == "T-9"
    assert rec["record_type"] == "PRE_OBLIGATION"
    assert rec["pre_obligation_id"] == ev.pre_obligation_id
    assert "iam-provisioning-sailpoint" in rec["matched_rule_ids"]


# --- the intentional invariant ----------------------------------------------

@pytest.mark.parametrize("status", list(SkillMatchStatus))
def test_authoritative_token_always_null(resolver, status):
    """The Rule Server NEVER mints the authoritative token, on any path."""
    task = TaskContext(
        task_id="T-INV",
        task_description="Provision SailPoint access",
        task_type="iam-provisioning",
        skill_match=status,
        existing_skill_id="x" if status == SkillMatchStatus.FOUND else None,
    )
    ev = resolver.evaluate_task(task)
    assert ev.authoritative_skill_obligation_token is None


def test_malformed_obligation_line_does_not_crash_read(tmp_path):
    path = tmp_path / "pre_obligations.jsonl"
    path.write_text(
        '{"pre_obligation_id":"ok","task_id":"A"}\n'
        "{broken json not parseable\n"
        "\n",
        encoding="utf-8",
    )
    records = read_pre_obligations(path)
    assert len(records) == 1
    assert records[0]["task_id"] == "A"
