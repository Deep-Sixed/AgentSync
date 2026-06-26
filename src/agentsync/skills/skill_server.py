"""Skill Server — the serving + lookup half of the AgentSync loop.

Contract (tool surface from skillsmcp, native implementation):
    list_skills()                  -> list[SkillSummary]
    read_skill_file(canonical_id)  -> SkillFile | None
    activate_skill(canonical_id)   -> ActivatedSkill | None
    find_matching_skill(query)     -> SkillLookupResult

LOCKED matching policy (conservative — bias toward "missing"):
  exact canonical_id        -> found, score 1.0
  exact frontmatter name    -> found, score 1.0
  content/family/keyword    -> score contributors only
  FOUND_THRESHOLD     = 0.82 -> at/above: found
  NEAR_MISS_THRESHOLD = 0.35 -> in [0.35, 0.82): missing + best_near_match
  below 0.35                 -> missing, no meaningful match

A false "found" lets an agent skip authoring with a recipe that doesn't fit,
which is worse than a false "missing". So "found" must be earned.

Hard boundaries: reads only approved/; never promotes; never mints obligations;
never modifies files. This is not Kanon.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .registry import ApprovedSkill, load_approved_skills

# Scoring weights (handoff-locked).
W_EXACT_ID = 1.0
W_EXACT_NAME = 1.0
W_NAME_TOKEN_OVERLAP_CAP = 0.35
W_DESC_TOKEN_OVERLAP_CAP = 0.35
W_SKILL_FAMILY = 0.15
W_CAPABILITY_FAMILY = 0.10

FOUND_THRESHOLD = 0.82
NEAR_MISS_THRESHOLD = 0.35

_WORD_RE = re.compile(r"[a-z0-9]+")


class SkillSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_id: str
    name: str
    description: str
    skill_family: Optional[str] = None
    capability_family: Optional[str] = None
    path: str


class SkillFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_id: str
    path: str
    content: str


class ActivatedSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_id: str
    name: str
    description: str
    content: str
    activation_status: str = "activated"


class SkillLookupResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str                              # "found" | "missing"
    canonical_id: Optional[str] = None       # set only when found
    matched_name: Optional[str] = None       # set only when found
    match_reason: Optional[str] = None
    score: float = 0.0
    best_near_match: Optional[str] = None    # near-miss canonical_id when missing


class SkillQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    text: str = Field(..., min_length=1, description="Task description or skill query")
    skill_family: Optional[str] = Field(default=None, description="Family hint from the Rule Server")
    capability_family: Optional[str] = Field(default=None, description="Capability hint from the Rule Server")
    canonical_id: Optional[str] = Field(default=None, description="Direct id lookup, if the caller has one")


def _tokens(s: str) -> set:
    return set(_WORD_RE.findall(s.lower()))


def _capped_overlap(a: set, b: set, per_hit: float, cap: float) -> float:
    if not a or not b:
        return 0.0
    return min(len(a & b) * per_hit, cap)


class SkillServer:
    """Holds the loaded approved-skill index and answers the four tools."""

    def __init__(self, approved_root: Optional[Path] = None) -> None:
        self._approved_root = approved_root
        self._skills: Dict[str, ApprovedSkill] = load_approved_skills(approved_root)

    def reload(self) -> None:
        """Re-scan disk (e.g. after Kanon promotes a new skill)."""
        self._skills = load_approved_skills(self._approved_root)

    def list_skills(self) -> List[SkillSummary]:
        return [
            SkillSummary(
                canonical_id=s.canonical_id,
                name=s.name,
                description=s.description,
                skill_family=s.skill_family,
                capability_family=s.capability_family,
                path=str(s.path),
            )
            for s in self._skills.values()
        ]

    def read_skill_file(self, canonical_id: str) -> Optional[SkillFile]:
        s = self._skills.get(canonical_id)
        if s is None:
            return None
        return SkillFile(canonical_id=s.canonical_id, path=str(s.path), content=s.raw)

    def activate_skill(self, canonical_id: str) -> Optional[ActivatedSkill]:
        s = self._skills.get(canonical_id)
        if s is None:
            return None
        return ActivatedSkill(
            canonical_id=s.canonical_id,
            name=s.name,
            description=s.description,
            content=s.raw,
            activation_status="activated",
        )

    def find_matching_skill(self, query: SkillQuery) -> SkillLookupResult:
        # 1. Direct id lookup is decisive.
        if query.canonical_id and query.canonical_id in self._skills:
            s = self._skills[query.canonical_id]
            return SkillLookupResult(
                status="found", canonical_id=s.canonical_id, matched_name=s.name,
                match_reason="exact canonical_id", score=W_EXACT_ID,
            )

        q_text_lc = query.text.lower()
        q_tokens = _tokens(query.text)

        best: Optional[ApprovedSkill] = None
        best_score = 0.0
        best_reason = ""

        for s in self._skills.values():
            # 2. Exact frontmatter name present in query -> decisive 1.0.
            if s.name and s.name.lower() in q_text_lc:
                return SkillLookupResult(
                    status="found", canonical_id=s.canonical_id, matched_name=s.name,
                    match_reason="exact frontmatter name", score=W_EXACT_NAME,
                )

            # 3. Otherwise accumulate contributing signals.
            score = 0.0
            reasons: List[str] = []

            name_overlap = _capped_overlap(
                q_tokens, _tokens(s.name), 0.12, W_NAME_TOKEN_OVERLAP_CAP)
            if name_overlap:
                score += name_overlap
                reasons.append("name token overlap")

            desc_overlap = _capped_overlap(
                q_tokens, _tokens(s.description), 0.10, W_DESC_TOKEN_OVERLAP_CAP)
            if desc_overlap:
                score += desc_overlap
                reasons.append("description token overlap")

            if query.skill_family and s.skill_family == query.skill_family:
                score += W_SKILL_FAMILY
                reasons.append("skill_family match")

            if query.capability_family and s.capability_family == query.capability_family:
                score += W_CAPABILITY_FAMILY
                reasons.append("capability_family match")

            if score > best_score:
                best_score, best, best_reason = score, s, "; ".join(reasons)

        # 4. Threshold decision.
        if best is not None and best_score >= FOUND_THRESHOLD:
            return SkillLookupResult(
                status="found", canonical_id=best.canonical_id, matched_name=best.name,
                match_reason=best_reason, score=round(best_score, 3),
            )

        if best is not None and best_score >= NEAR_MISS_THRESHOLD:
            return SkillLookupResult(
                status="missing", canonical_id=None, matched_name=None,
                match_reason=f"near match: {best_reason}",
                score=round(best_score, 3), best_near_match=best.canonical_id,
            )

        return SkillLookupResult(status="missing", canonical_id=None, score=0.0)
