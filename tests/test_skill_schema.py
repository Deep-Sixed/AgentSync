"""Tests for Kanon's SKILL.md validator.

Proves: draft / promote / hardened levels, core-required vs advisory split,
required_evidence gate, kebab-case + filename discipline, secret detection.
Fixtures are written in AgentSync's own domain (iam-lifecycle) using the
borrowed addyosmani anatomy — not copied from their repo.
"""

import textwrap

import pytest

from agentsync.skills.skill_schema import (
    ValidationLevel,
    validate_skill_md,
)


# A well-formed operational skill in AgentSync's domain. Has the four core
# sections + Verification carrying the evidence the iam rule requires, but
# deliberately OMITS the polish sections (Examples / Rationalizations / Red Flags)
# to exercise the advisory path.
CORE_SKILL = textwrap.dedent("""\
    ---
    name: sailpoint-joiner-provisioning
    description: Provisions birthright access for a new joiner in SailPoint IdentityIQ. Use when an HR joiner event requires baseline entitlements.
    ---

    # Overview
    Grants the birthright entitlement set to a new identity via the joiner workflow.

    # When to Use
    When a joiner event fires and the identity has no baseline access yet.

    # Process
    1. Resolve the identity's role from the HR feed.
    2. Run the birthright provisioning workflow in dry-run.
    3. Review the dry_run_output for unexpected grants.
    4. Apply, capturing the approval_reference.
    5. Record the rollback_step for the granted set.

    # Verification
    - dry_run_output attached and reviewed
    - rollback_step documented for every granted entitlement
    - approval_reference captured from the access request
    """)

# Same skill, fully hardened: adds the polish sections.
HARDENED_SKILL = CORE_SKILL + textwrap.dedent("""\

    # Examples
    Joiner JIRA-4821: granted finance-base, captured AR-9912.

    # Common Rationalizations
    "I'll skip the dry-run, it's just a joiner." — No. Dry-run catches over-grants.

    # Red Flags
    - Provisioning without an approval_reference.
    - Granting entitlements not in the birthright set.
    """)

IAM_EVIDENCE = ["dry_run_output", "rollback_step", "approval_reference"]


# --- draft level ------------------------------------------------------------

def test_draft_accepts_core_skill():
    r = validate_skill_md(CORE_SKILL, level=ValidationLevel.DRAFT)
    assert r.valid is True
    assert r.errors == []
    assert r.frontmatter["name"] == "sailpoint-joiner-provisioning"


def test_draft_warns_on_missing_polish_but_does_not_block():
    r = validate_skill_md(CORE_SKILL, level=ValidationLevel.DRAFT)
    assert r.valid is True
    joined = " ".join(r.warnings).lower()
    assert "examples" in joined
    assert "common rationalizations" in joined
    assert "red flags" in joined


def test_draft_blocks_missing_verification():
    no_verif = CORE_SKILL.replace("# Verification", "# Notes")
    r = validate_skill_md(no_verif, level=ValidationLevel.DRAFT)
    assert r.valid is False
    assert any("Verification" in e for e in r.errors)


def test_draft_blocks_missing_frontmatter_name():
    broken = CORE_SKILL.replace("name: sailpoint-joiner-provisioning\n", "")
    r = validate_skill_md(broken, level=ValidationLevel.DRAFT)
    assert r.valid is False
    assert any("name" in e for e in r.errors)


def test_process_alias_core_process_accepted():
    alt = CORE_SKILL.replace("# Process", "# Core Process")
    r = validate_skill_md(alt, level=ValidationLevel.DRAFT)
    assert r.valid is True


def test_empty_verification_blocks():
    empty_verif = CORE_SKILL.rsplit("# Verification", 1)[0] + "# Verification\n"
    r = validate_skill_md(empty_verif, level=ValidationLevel.DRAFT)
    assert r.valid is False
    assert any("empty" in e.lower() for e in r.errors)


# --- promote level ----------------------------------------------------------

def test_promote_passes_with_evidence_and_discipline():
    r = validate_skill_md(
        CORE_SKILL,
        level=ValidationLevel.PROMOTE,
        required_evidence=IAM_EVIDENCE,
        dir_name="sailpoint-joiner-provisioning",
        file_name="SKILL.md",
    )
    assert r.valid is True, r.errors


def test_promote_blocks_when_evidence_missing_from_verification():
    # Verification lacks 'approval_reference'
    missing_ev = CORE_SKILL.replace(
        "- approval_reference captured from the access request\n", ""
    )
    r = validate_skill_md(
        missing_ev,
        level=ValidationLevel.PROMOTE,
        required_evidence=IAM_EVIDENCE,
        dir_name="sailpoint-joiner-provisioning",
        file_name="SKILL.md",
    )
    assert r.valid is False
    assert any("approval_reference" in e for e in r.errors)


def test_promote_blocks_non_kebab_dir():
    r = validate_skill_md(
        CORE_SKILL,
        level=ValidationLevel.PROMOTE,
        required_evidence=IAM_EVIDENCE,
        dir_name="SailPoint_Joiner",
        file_name="SKILL.md",
    )
    assert r.valid is False
    assert any("kebab" in e for e in r.errors)


def test_promote_blocks_wrong_filename():
    r = validate_skill_md(
        CORE_SKILL,
        level=ValidationLevel.PROMOTE,
        required_evidence=IAM_EVIDENCE,
        dir_name="sailpoint-joiner-provisioning",
        file_name="skill.md",
    )
    assert r.valid is False
    assert any("SKILL.md" in e for e in r.errors)


def test_promote_blocks_secret_leakage():
    leaky = CORE_SKILL + "\nexport API_KEY=sk-live-abcdef1234567890\n"
    r = validate_skill_md(
        leaky,
        level=ValidationLevel.PROMOTE,
        required_evidence=IAM_EVIDENCE,
        dir_name="sailpoint-joiner-provisioning",
        file_name="SKILL.md",
    )
    assert r.valid is False
    assert any("secret" in e.lower() for e in r.errors)


# --- hardened level ---------------------------------------------------------

def test_hardened_requires_polish_sections():
    # CORE_SKILL lacks polish -> hardened must block
    r = validate_skill_md(
        CORE_SKILL,
        level=ValidationLevel.HARDENED,
        required_evidence=IAM_EVIDENCE,
        dir_name="sailpoint-joiner-provisioning",
        file_name="SKILL.md",
    )
    assert r.valid is False
    assert any("hardened" in e.lower() for e in r.errors)


def test_hardened_passes_full_skill():
    r = validate_skill_md(
        HARDENED_SKILL,
        level=ValidationLevel.HARDENED,
        required_evidence=IAM_EVIDENCE,
        dir_name="sailpoint-joiner-provisioning",
        file_name="SKILL.md",
    )
    assert r.valid is True, r.errors
    assert r.warnings == [] or all("polish" not in w for w in r.warnings)
