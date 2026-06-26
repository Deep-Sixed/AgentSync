"""Deterministic pattern/tag resolver.

Scores each rule against a TaskContext across four dimensions:
  task_type, keywords, command_family, file_globs.

Returns matched rules with a numeric score so the orchestrator can decide
whether the pattern layer produced a CONFIDENT match or should fall through
to the capability resolver. No LLM, no network — pure and testable.
"""


import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import EnforcementLevel, MatchedRule

# A rule is "confident" if it matches on at least this many distinct signals,
# OR matches task_type or command_family (the strong, explicit signals).
CONFIDENT_SCORE = 2


@dataclass
class ScoredRule:
    rule: MatchedRule
    score: int
    strong_signal: bool  # matched on task_type or command_family


@dataclass
class PatternResult:
    scored: list[ScoredRule] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return any(s.strong_signal or s.score >= CONFIDENT_SCORE for s in self.scored)

    @property
    def best(self) -> ScoredRule | None:
        if not self.scored:
            return None
        return max(self.scored, key=lambda s: (s.strong_signal, s.score))


def load_rules(rules_path: Path) -> list[dict[str, Any]]:
    """Load the pattern rule list. Missing file -> empty list (no crash)."""
    if not rules_path.exists():
        return []
    data = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    return data.get("rules", []) or []


def _haystack(task) -> str:
    """Lowercased text blob to scan keywords against."""
    parts = [task.task_description or ""]
    if task.task_type:
        parts.append(task.task_type)
    if task.command_family:
        parts.append(task.command_family)
    return " ".join(parts).lower()


def _match_rule(rule: dict[str, Any], task) -> ScoredRule | None:
    match = rule.get("match", {}) or {}
    score = 0
    strong = False

    # task_type — strong explicit signal
    rule_types = [t.lower() for t in (match.get("task_type") or [])]
    if task.task_type and task.task_type.lower() in rule_types:
        score += 1
        strong = True

    # command_family — strong explicit signal
    rule_cmds = [c.lower() for c in (match.get("command_family") or [])]
    if task.command_family and task.command_family.lower() in rule_cmds:
        score += 1
        strong = True

    # keywords — soft signal, scanned across description/type/command
    hay = _haystack(task)
    for kw in (match.get("keywords") or []):
        if kw.lower() in hay:
            score += 1
            break  # one keyword hit is enough to count the dimension

    # file globs — soft signal
    globs = match.get("file_globs") or []
    for f in (task.touched_files or []):
        if any(fnmatch.fnmatch(f, g) for g in globs):
            score += 1
            break

    if score == 0:
        return None

    matched = MatchedRule(
        rule_id=rule["id"],
        skill_expected=bool(rule.get("skill_expected", False)),
        skill_family=rule.get("skill_family"),
        capability_family=rule.get("capability_family"),
        required_evidence=list(rule.get("required_evidence") or []),
        enforcement_level=EnforcementLevel(rule.get("enforcement_level", "none")),
    )
    return ScoredRule(rule=matched, score=score, strong_signal=strong)


def resolve_pattern(task, rules: list[dict[str, Any]]) -> PatternResult:
    """Run the pattern layer. Returns all scored matches (may be empty)."""
    scored = [sr for rule in rules if (sr := _match_rule(rule, task)) is not None]
    scored.sort(key=lambda s: (s.strong_signal, s.score), reverse=True)
    return PatternResult(scored=scored)
