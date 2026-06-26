"""Kanon — SKILL.md schema validator.

Borrows the addyosmani/agent-skills document *anatomy* as a reference spec and
implements it natively. AgentSync is not a fork of that repo; this module
encodes its section contract as our own Python validator.

Three levels, one contract:
  draft     — identity + applicability + procedure + proof present
  promote   — draft + non-empty Verification + required_evidence satisfied
              + kebab-case dir + exact SKILL.md filename + no secret leakage
  hardened  — promote + the polish/teaching sections (Examples,
              Common Rationalizations, Red Flags)

Policy (v1.1-kanon): a newly generated operational skill is NOT blocked for
lacking polish sections. Kanon blocks only on identity, applicability,
procedure, proof, and (at promote) required_evidence + safety.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


class ValidationLevel(str, Enum):
    DRAFT = "draft"
    PROMOTE = "promote"
    HARDENED = "hardened"


# --- section vocabulary -----------------------------------------------------

# Process may appear as either header; either satisfies the requirement.
_PROCESS_ALIASES = ("process", "core process")

CORE_SECTIONS = ("overview", "when to use", "_process_", "verification")
POLISH_SECTIONS = ("examples", "common rationalizations", "red flags")

# Coarse secret-leakage patterns for the promote-level safety check.
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(aws_secret_access_key|secret_access_key)\b\s*[:=]"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"),
    re.compile(r"(?i)\b(api[_-]?key|apikey|password|passwd|token)\b\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),  # slack
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),    # github token
]

_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class SkillValidation:
    level: ValidationLevel
    valid: bool
    errors: List[str] = field(default_factory=list)        # blocking
    warnings: List[str] = field(default_factory=list)      # advisory
    frontmatter: Dict[str, str] = field(default_factory=dict)
    sections: List[str] = field(default_factory=list)      # normalized headers found


# --- public parser (single source of truth for SKILL.md structure) ----------

@dataclass
class ParsedSkillDocument:
    """The one canonical parse of a SKILL.md. Both the validator and the Skill
    Server registry consume this, so they can never disagree about what a skill
    is."""
    frontmatter: Dict[str, str] = field(default_factory=dict)
    body: str = ""
    sections: List[str] = field(default_factory=list)            # normalized headers, in order
    section_text: Dict[str, str] = field(default_factory=dict)   # normalized header -> its text

    def has_section(self, wanted: str) -> bool:
        return _has_section(self.sections, wanted)


def parse_skill_md(text: str) -> ParsedSkillDocument:
    """Parse a SKILL.md into frontmatter + sections. The single shared parser.

    `wanted` lookups via has_section() understand the '_process_' alias
    (Process | Core Process). section_text maps each normalized header to the
    text beneath it.
    """
    fm, body = _parse_frontmatter(text)
    headers = _find_headers(body)
    normalized = [h for h, _ in headers]
    sec_text = {headers[i][0]: _section_text(body, headers, i) for i in range(len(headers))}
    return ParsedSkillDocument(
        frontmatter=fm,
        body=body,
        sections=normalized,
        section_text=sec_text,
    )


# --- parsing internals ------------------------------------------------------

def _parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """Extract simple `key: value` YAML frontmatter delimited by --- fences.

    Deliberately minimal (no nested YAML): a skill's frontmatter is flat
    name/description. Returns (frontmatter, body). Missing fence -> ({}, text).
    """
    m = re.match(r"^\s*---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    block, body = m.group(1), m.group(2)
    fm: Dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition(":")
            fm[k.strip().lower()] = v.strip()
    return fm, body


def _find_headers(body: str) -> List[Tuple[str, int]]:
    """Return [(normalized_header, body_offset)] for every markdown ATX header."""
    out: List[Tuple[str, int]] = []
    for m in re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", body):
        out.append((m.group(1).strip().lower(), m.end()))
    return out


def _section_text(body: str, headers: List[Tuple[str, int]], idx: int) -> str:
    """Text between header idx and the next header (or end of body)."""
    start = headers[idx][1]
    end = headers[idx + 1][1] if idx + 1 < len(headers) else len(body)
    # back up end to the start of the next header line, not its end
    if idx + 1 < len(headers):
        nxt = re.search(r"(?m)^#{1,6}\s+", body[start:])
        if nxt:
            end = start + nxt.start()
    return body[start:end].strip()


def _has_section(normalized: List[str], wanted: str) -> bool:
    if wanted == "_process_":
        return any(h in _PROCESS_ALIASES for h in normalized)
    return any(h == wanted or h.startswith(wanted) for h in normalized)


def _verification_text(body: str, headers: List[Tuple[str, int]]) -> str:
    for i, (h, _) in enumerate(headers):
        if h == "verification" or h.startswith("verification"):
            return _section_text(body, headers, i)
    return ""


# --- public API -------------------------------------------------------------

def validate_skill_md(
    text: str,
    *,
    level: ValidationLevel = ValidationLevel.DRAFT,
    required_evidence: Optional[List[str]] = None,
    dir_name: Optional[str] = None,
    file_name: Optional[str] = None,
) -> SkillValidation:
    """Validate a candidate SKILL.md against the requested level.

    Args:
        text: full SKILL.md content.
        level: draft | promote | hardened.
        required_evidence: evidence keys from the Rule Server decision; each
            must appear in the Verification section at promote/hardened.
        dir_name / file_name: checked at promote+ for kebab-case dir and the
            exact 'SKILL.md' filename.

    Returns a SkillValidation. `valid` is True iff there are no blocking errors.
    """
    required_evidence = required_evidence or []
    doc = parse_skill_md(text)
    fm = doc.frontmatter
    normalized = doc.sections

    errors: List[str] = []
    warnings: List[str] = []

    # --- frontmatter (all levels) ---
    if not fm:
        errors.append("missing YAML frontmatter")
    if "name" not in fm or not fm.get("name"):
        errors.append("missing frontmatter: name")
    if "description" not in fm or not fm.get("description"):
        errors.append("missing frontmatter: description")
    elif len(fm["description"]) < 25 or "use when" not in fm["description"].lower():
        warnings.append("weak/non-specific description (want a 'Use when…' trigger)")

    # --- core sections (all levels) ---
    for sect in CORE_SECTIONS:
        if not _has_section(normalized, sect):
            label = "Process or Core Process" if sect == "_process_" else sect.title()
            errors.append(f"missing required section: {label}")

    # --- verification must be non-empty (all levels block on empty if present;
    #     promote+ also covered above by required-section check) ---
    verif = doc.section_text.get("verification", "")
    if not verif:
        # tolerate 'Verification' variants like 'Verification & Evidence'
        for h, t in doc.section_text.items():
            if h.startswith("verification"):
                verif = t
                break
    if _has_section(normalized, "verification") and not verif:
        errors.append("Verification section is empty")

    # --- polish sections: advisory at draft/promote, required at hardened ---
    for sect in POLISH_SECTIONS:
        present = _has_section(normalized, sect)
        if not present:
            if level == ValidationLevel.HARDENED:
                errors.append(f"missing required section (hardened): {sect.title()}")
            else:
                warnings.append(f"missing advisory section: {sect.title()}")

    # --- promote + hardened gates ---
    if level in (ValidationLevel.PROMOTE, ValidationLevel.HARDENED):
        # required_evidence must be represented in Verification
        verif_lc = verif.lower()
        for ev in required_evidence:
            if ev.lower() not in verif_lc:
                errors.append(f"required_evidence not represented in Verification: {ev}")

        # directory + filename discipline
        if dir_name is not None and not _KEBAB_RE.match(dir_name):
            errors.append(f"skill directory name not kebab-case: {dir_name!r}")
        if file_name is not None and file_name != "SKILL.md":
            errors.append(f"skill filename must be exactly 'SKILL.md', got {file_name!r}")

        # secret leakage
        for pat in _SECRET_PATTERNS:
            if pat.search(text):
                errors.append("possible secret leakage detected in skill body")
                break

    return SkillValidation(
        level=level,
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        frontmatter=fm,
        sections=normalized,
    )
