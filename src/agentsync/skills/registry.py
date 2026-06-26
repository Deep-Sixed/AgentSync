"""Skill registry.

Loads approved skills from storage/skills/approved/<canonical-id>/SKILL.md.
The directory name IS the canonical_id (kebab-case, enforced at promotion by
Kanon). Frontmatter parsing is shared with the schema validator so there is
exactly one parser in the codebase.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .skill_schema import parse_skill_md

DEFAULT_APPROVED_ROOT = Path("storage/skills/approved")
SKILL_FILENAME = "SKILL.md"


@dataclass
class ApprovedSkill:
    canonical_id: str          # = directory name
    name: str                  # frontmatter name
    description: str
    skill_family: Optional[str]
    capability_family: Optional[str]
    path: Path                 # path to the SKILL.md
    raw: str = field(repr=False, default="")  # full file text (for read/activate)


def load_approved_skills(approved_root: Optional[Path] = None) -> Dict[str, ApprovedSkill]:
    """Scan the approved tree. Returns {canonical_id: ApprovedSkill}.

    Missing root -> empty. Directories without a SKILL.md are skipped.
    The directory name is authoritative for canonical_id; a mismatched
    frontmatter name is allowed (name is human-facing) but logged via the
    skill object so callers can surface drift if they want.
    """
    root = approved_root or DEFAULT_APPROVED_ROOT
    out: Dict[str, ApprovedSkill] = {}
    if not root.exists():
        return out

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_file = child / SKILL_FILENAME
        if not skill_file.exists():
            continue
        raw = skill_file.read_text(encoding="utf-8")
        try:
            doc = parse_skill_md(raw)
        except Exception:
            # A malformed skill must not poison the whole registry. Skip it.
            continue
        fm = doc.frontmatter
        # A skill with no frontmatter name is structurally invalid as a served
        # skill; skip rather than serve a nameless recipe.
        if not fm.get("name"):
            continue
        out[child.name] = ApprovedSkill(
            canonical_id=child.name,
            name=fm.get("name", child.name),
            description=fm.get("description", ""),
            skill_family=fm.get("skill_family"),
            capability_family=fm.get("capability_family"),
            path=skill_file,
            raw=raw,
        )
    return out
