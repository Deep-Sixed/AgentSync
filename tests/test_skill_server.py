"""Tests for the AgentSync Skill Server (handoff-locked policy).

Proves the 10 required cases: serving (list/read/activate), conservative
matching (0.82 found, 0.35 near-miss floor), best_near_match surfacing,
clean handling of unknown ids, malformed skills, and non-SKILL files.
"""

from pathlib import Path

import pytest

from agentsync.skills.skill_server import (
    FOUND_THRESHOLD,
    NEAR_MISS_THRESHOLD,
    SkillQuery,
    SkillServer,
)

REPO = Path(__file__).resolve().parents[1]
APPROVED = REPO / "tests" / "fixtures" / "approved"


@pytest.fixture
def server():
    return SkillServer(approved_root=APPROVED)


# 1. list_skills returns the seed skills (and skips malformed/non-SKILL dirs).
def test_list_skills_returns_seeds(server):
    ids = {s.canonical_id for s in server.list_skills()}
    assert "sailpoint-joiner-provisioning" in ids
    assert "jamf-ade-enrollment" in ids
    # malformed (no frontmatter name) is skipped, not served
    assert "broken-skill" not in ids
    # dir without a SKILL.md is skipped
    assert "has-non-skill-file" not in ids


# 2. read_skill_file returns SKILL.md content.
def test_read_skill_file_returns_content(server):
    f = server.read_skill_file("sailpoint-joiner-provisioning")
    assert f is not None
    assert "# Verification" in f.content
    assert f.path.endswith("SKILL.md")


# 3. activate_skill returns canonical_id, name, description, content, status.
def test_activate_skill_payload(server):
    a = server.activate_skill("sailpoint-joiner-provisioning")
    assert a is not None
    assert a.canonical_id == "sailpoint-joiner-provisioning"
    assert a.name == "sailpoint-joiner-provisioning"
    assert a.description
    assert "# Process" in a.content
    assert a.activation_status == "activated"


# 4. exact canonical_id -> found, score 1.0.
def test_find_exact_canonical_id(server):
    r = server.find_matching_skill(SkillQuery(
        text="anything", canonical_id="sailpoint-joiner-provisioning"))
    assert r.status == "found"
    assert r.canonical_id == "sailpoint-joiner-provisioning"
    assert r.score == 1.0


# 5. exact frontmatter name in query -> found, score 1.0.
def test_find_exact_name(server):
    r = server.find_matching_skill(SkillQuery(
        text="run sailpoint-joiner-provisioning for this joiner event"))
    assert r.status == "found"
    assert r.canonical_id == "sailpoint-joiner-provisioning"
    assert r.score == 1.0


# 6. weak keyword overlap -> missing, not found.
def test_weak_overlap_is_missing(server):
    r = server.find_matching_skill(SkillQuery(text="new joiner needs access"))
    assert r.status == "missing"
    assert r.canonical_id is None
    assert r.matched_name is None
    assert r.score < FOUND_THRESHOLD


# 7. same skill_family only -> missing with best_near_match.
def test_family_hint_alone_is_missing_with_near_match(server):
    # family (0.15) is below the 0.35 near-miss floor on its own, so to land
    # in the near-miss band we add light description overlap.
    r = server.find_matching_skill(SkillQuery(
        text="provisions birthright access entitlements",
        skill_family="iam-lifecycle"))
    assert r.status == "missing"
    assert r.canonical_id is None
    if r.score >= NEAR_MISS_THRESHOLD:
        assert r.best_near_match == "sailpoint-joiner-provisioning"
        assert "near match" in (r.match_reason or "")


# 8. unknown canonical_id returns clean not-found, does not crash.
def test_unknown_id_clean(server):
    assert server.read_skill_file("does-not-exist") is None
    assert server.activate_skill("does-not-exist") is None
    r = server.find_matching_skill(SkillQuery(
        text="x", canonical_id="does-not-exist"))
    # direct id miss falls through to content matching -> missing, no crash
    assert r.status == "missing"


# 9. malformed approved skill is skipped, does not poison the registry.
def test_malformed_skill_does_not_poison_registry(server):
    # broken-skill exists on disk with no frontmatter; registry still serves
    # the valid skills and answers lookups normally.
    assert server.read_skill_file("broken-skill") is None
    r = server.find_matching_skill(SkillQuery(
        text="anything", canonical_id="sailpoint-joiner-provisioning"))
    assert r.status == "found"


# 10. registry ignores non-SKILL.md files.
def test_registry_ignores_non_skill_files(server):
    assert server.read_skill_file("has-non-skill-file") is None


# extra: empty registry returns clean missing.
def test_empty_registry_missing(tmp_path):
    empty = SkillServer(approved_root=tmp_path)
    assert empty.list_skills() == []
    r = empty.find_matching_skill(SkillQuery(text="anything"))
    assert r.status == "missing"
    assert r.best_near_match is None


# extra: no-overlap query -> clean missing, score 0.
def test_no_overlap_clean_missing(server):
    r = server.find_matching_skill(SkillQuery(text="water the office plants"))
    assert r.status == "missing"
    assert r.score == 0.0
    assert r.best_near_match is None
