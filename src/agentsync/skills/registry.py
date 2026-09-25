"""Skill registry.

Loads approved skills from storage/skills/approved/<canonical-id>/SKILL.md.
The directory name IS the canonical_id (kebab-case, enforced at promotion by
Kanon). Frontmatter parsing is shared with the schema validator so there is
exactly one parser in the codebase.
"""

from dataclasses import dataclass, field
from pathlib import Path

from .skill_schema import parse_skill_md

DEFAULT_APPROVED_ROOT = Path("storage/skills/approved")
SKILL_FILENAME = "SKILL.md"


@dataclass
class ApprovedSkill:
    canonical_id: str          # = directory name
    name: str                  # frontmatter name
    description: str
    skill_family: str | None
    capability_family: str | None
    path: Path                 # path to the SKILL.md
    raw: str = field(repr=False, default="")  # full file text (for read/activate)


def load_approved_skills(approved_root: Path | None = None) -> dict[str, ApprovedSkill]:
    """Scan the approved tree. Returns {canonical_id: ApprovedSkill}.

    Missing root -> empty. Directories without a SKILL.md are skipped.
    The directory name is authoritative for canonical_id; a mismatched
    frontmatter name is allowed (name is human-facing) but logged via the
    skill object so callers can surface drift if they want.
    """
    root = approved_root or DEFAULT_APPROVED_ROOT
    out: dict[str, ApprovedSkill] = {}
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


class SkillConflictError(Exception):
    """An approved skill with this canonical_id already exists with other content."""


def approved_skill_conflict(
    canonical_id: str, content: str, approved_root: Path | None = None,
) -> str | None:
    """Return why `content` cannot be installed as `canonical_id`, or None.

    Installing identical content again is allowed (idempotent retry); replacing
    an existing approved skill with different content is not.
    """
    skill_file = (approved_root or DEFAULT_APPROVED_ROOT) / canonical_id / SKILL_FILENAME
    if skill_file.exists() and skill_file.read_text(encoding="utf-8") != content:
        return f"approved skill {canonical_id!r} already exists with different content"
    return None


def install_approved_skill(
    canonical_id: str, content: str, approved_root: Path | None = None,
) -> Path:
    """Write content to approved/<canonical_id>/SKILL.md atomically.

    Called by Kanon after Stele seals the candidate; the Skill Server itself
    never writes here. Raises SkillConflictError instead of overwriting a
    different approved skill.
    """
    if not canonical_id or canonical_id in (".", "..") or Path(canonical_id).name != canonical_id:
        raise ValueError(f"canonical_id must be a single directory name: {canonical_id!r}")
    root = approved_root or DEFAULT_APPROVED_ROOT
    conflict = approved_skill_conflict(canonical_id, content, root)
    if conflict:
        raise SkillConflictError(conflict)

    skill_dir = root / canonical_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / SKILL_FILENAME
    tmp = skill_dir / f".{SKILL_FILENAME}.tmp"
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(skill_file)
    return skill_file
