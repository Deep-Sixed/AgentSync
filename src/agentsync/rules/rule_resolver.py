"""Rule resolver — the orchestrator.

Order: pattern_match -> capability_fallback -> no_match.
Then the obligation decision maps skill_match status to loop-control signals.

This is the decision engine. It is importable and testable with no MCP,
ContextForge, A2A, or gateway running.
"""


from pathlib import Path

from .capabilities import CapabilityMap, load_capabilities, resolve_capability
from .matcher import load_rules, resolve_pattern
from .models import (
    EnforcementLevel,
    MatchedRule,
    ResolverPath,
    RuleEvaluation,
    SkillMatchStatus,
    TaskContext,
)
from .pre_obligations import write_pre_obligation

DEFAULT_RULES_PATH = Path("storage/rules/rules.yaml")
DEFAULT_CAPABILITIES_PATH = Path("storage/rules/capabilities.yaml")


class RuleResolver:
    """Holds loaded rules + capability map and evaluates tasks."""

    def __init__(
        self,
        rules_path: Path | None = None,
        capabilities_path: Path | None = None,
        pre_obligations_path: Path | None = None,
    ) -> None:
        self._rules = load_rules(rules_path or DEFAULT_RULES_PATH)
        self._caps: CapabilityMap = load_capabilities(capabilities_path or DEFAULT_CAPABILITIES_PATH)
        self._pre_obligations_path = pre_obligations_path

    # --- resolver layers -------------------------------------------------

    def _resolve(self, task: TaskContext):
        """Returns (resolver_path, matched_rules, primary)."""
        pattern = resolve_pattern(task, self._rules)
        if pattern.confident and pattern.best is not None:
            matched = [s.rule for s in pattern.scored]
            return ResolverPath.PATTERN, matched, pattern.best.rule

        # Pattern weak/absent -> capability fallback.
        cap_rule = resolve_capability(task, self._caps)
        if cap_rule is not None:
            return ResolverPath.CAPABILITY_FALLBACK, [cap_rule], cap_rule

        # If pattern had *some* non-confident matches but capability missed,
        # still surface them rather than dropping to NONE silently.
        if pattern.best is not None:
            matched = [s.rule for s in pattern.scored]
            return ResolverPath.PATTERN, matched, pattern.best.rule

        return ResolverPath.NONE, [], None

    # --- public API ------------------------------------------------------

    def evaluate_task(self, task: TaskContext) -> RuleEvaluation:
        resolver_path, matched_rules, primary = self._resolve(task)

        if primary is None:
            # No rule applies. No skill expected, no obligation.
            return RuleEvaluation(
                task_id=task.task_id,
                matched_rules=[],
                resolver_path=ResolverPath.NONE,
                skill_expected=False,
                enforcement_level=EnforcementLevel.NONE,
            )

        skill_expected = primary.skill_expected
        enforcement = primary.enforcement_level
        would_block = skill_expected and enforcement == EnforcementLevel.BLOCK

        ev = RuleEvaluation(
            task_id=task.task_id,
            matched_rules=matched_rules,
            resolver_path=resolver_path,
            skill_expected=skill_expected,
            skill_family=primary.skill_family,
            capability_family=primary.capability_family,
            required_evidence=primary.required_evidence,
            enforcement_level=enforcement,
            existing_skill_id=task.existing_skill_id if task.skill_match == SkillMatchStatus.FOUND else None,
            authoritative_skill_obligation_token=None,  # invariant
        )

        # --- obligation decision (spec steps 5/6/7) ----------------------

        if not skill_expected:
            return ev  # nothing to enforce

        if task.skill_match == SkillMatchStatus.FOUND:
            # Skill exists: no record, no obligation, echo canonical id.
            ev.skill_lookup_required = False
            ev.skill_creation_required_if_missing = False
            ev.closure_blocked_if_missing_skill = False
            return ev

        if task.skill_match == SkillMatchStatus.UNKNOWN:
            # Gap not yet confirmed: signal lookup, do NOT write final record.
            ev.skill_lookup_required = True
            ev.skill_creation_required_if_missing = True
            ev.closure_blocked_if_missing_skill = would_block
            return ev

        # task.skill_match == MISSING: confirmed gap -> write pre_obligation.
        ev.skill_lookup_required = False
        ev.skill_creation_required_if_missing = True
        ev.closure_blocked_if_missing_skill = would_block
        record = write_pre_obligation(
            task_id=task.task_id,
            skill_family=primary.skill_family,
            capability_family=primary.capability_family,
            required_evidence=primary.required_evidence,
            enforcement_level=enforcement.value,
            resolver_path=resolver_path.value,
            matched_rule_ids=[m.rule_id for m in matched_rules],
            path=self._pre_obligations_path,
        )
        ev.pre_obligation_id = record["pre_obligation_id"]
        return ev
