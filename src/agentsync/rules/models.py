"""AgentSync Rule Server — stable contracts.

These shapes are the interface. The resolver order may evolve; these models
must not, because Skill Server, Skill Builder Enforcer, and Kanon read them.

Boundary:
  Rule Server   -> decides expectation, emits a pre_obligation (decision record).
  Enforcer      -> confirms gap, mints the authoritative skill_obligation_token.
  Kanon         -> validates candidate SKILL.md, redeems the token.

The `authoritative_skill_obligation_token` field is ALWAYS null on output from
this server. That null is an intentional, tested invariant: the Rule Server is
the decision engine, not the ratchet.
"""


from enum import Enum
from typing import Optional, List

from pydantic import BaseModel, Field, ConfigDict


class SkillMatchStatus(str, Enum):
    FOUND = "found"        # an existing canonical skill covers this task
    MISSING = "missing"    # confirmed no skill covers this task
    UNKNOWN = "unknown"    # caller has not yet checked the Skill Server


class EnforcementLevel(str, Enum):
    BLOCK = "block"        # closure blocked until candidate skill submitted
    ADVISE = "advise"      # agent told to author, but may close
    NONE = "none"          # no authoring obligation


class ResolverPath(str, Enum):
    PATTERN = "pattern"
    CAPABILITY_FALLBACK = "capability_fallback"
    NONE = "none"


class TaskContext(BaseModel):
    """Input to evaluate_task. Only task_id and task_description required."""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )

    task_id: str = Field(..., min_length=1, max_length=200,
                         description="Stable id for this task (e.g. 'JIRA-4821')")
    task_description: str = Field(..., min_length=1,
                                  description="Natural-language description of the task")
    task_type: Optional[str] = Field(default=None,
                                     description="Optional explicit type tag (e.g. 'iam-provisioning')")
    touched_files: Optional[List[str]] = Field(default_factory=list,
                                               description="Paths the task touches, for glob matching")
    command_family: Optional[str] = Field(default=None,
                                          description="Command family if known (e.g. 'sailpoint', 'jamf')")
    skill_match: SkillMatchStatus = Field(default=SkillMatchStatus.UNKNOWN,
                                          description="What the caller knows about existing skill coverage")
    existing_skill_id: Optional[str] = Field(default=None,
                                             description="Canonical skill id, if skill_match=found")


class MatchedRule(BaseModel):
    """A single rule that fired, surfaced for auditability."""
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    skill_expected: bool
    skill_family: Optional[str] = None
    capability_family: Optional[str] = None
    required_evidence: List[str] = Field(default_factory=list)
    enforcement_level: EnforcementLevel = EnforcementLevel.NONE


class RuleEvaluation(BaseModel):
    """Output of evaluate_task. Stable across pattern/capability resolvers."""
    model_config = ConfigDict(extra="forbid")

    task_id: str
    matched_rules: List[MatchedRule] = Field(default_factory=list)
    resolver_path: ResolverPath = Field(..., description="Which layer produced the decision")

    skill_expected: bool
    skill_family: Optional[str] = None
    capability_family: Optional[str] = None
    required_evidence: List[str] = Field(default_factory=list)
    enforcement_level: EnforcementLevel = EnforcementLevel.NONE

    # Loop-control signals consumed by the Enforcer:
    skill_lookup_required: bool = Field(
        default=False,
        description="True when a skill is expected but skill_match=unknown")
    skill_creation_required_if_missing: bool = Field(
        default=False,
        description="True when a confirmed-missing skill would trigger authoring")
    closure_blocked_if_missing_skill: bool = Field(
        default=False,
        description="True when a BLOCK-level obligation would apply on confirmed-missing")

    existing_skill_id: Optional[str] = Field(
        default=None,
        description="Echoed canonical id when skill_match=found")
    pre_obligation_id: Optional[str] = Field(
        default=None,
        description="Id of the decision record written to disk, if one was written")

    # INTENTIONAL INVARIANT: always null from the Rule Server.
    authoritative_skill_obligation_token: Optional[str] = Field(
        default=None,
        description="Always null here. Minted only by the Skill Builder Enforcer.")
