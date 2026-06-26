"""Pre-obligation records.

The Rule Server writes a *decision record* when policy says a skill is expected
and the gap is not yet confirmed-found. This is NOT the authoritative
skill_obligation_token — that is minted by the Skill Builder Enforcer after it
confirms the gap against the Skill Server.

A pre_obligation says: "According to policy, this task should have a skill."
It is append-only and tolerant of malformed lines on read.
"""


import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_PRE_OBLIGATIONS_PATH = Path("storage/obligations/pre_obligations.jsonl")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_pre_obligation(
    *,
    task_id: str,
    skill_family: Optional[str],
    capability_family: Optional[str],
    required_evidence: List[str],
    enforcement_level: str,
    resolver_path: str,
    matched_rule_ids: List[str],
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Append one pre_obligation decision record. Returns the written record."""
    target = path or DEFAULT_PRE_OBLIGATIONS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "pre_obligation_id": str(uuid.uuid4()),
        "record_type": "PRE_OBLIGATION",
        "timestamp": _now_iso(),
        "task_id": task_id,
        "skill_family": skill_family,
        "capability_family": capability_family,
        "required_evidence": required_evidence,
        "enforcement_level": enforcement_level,
        "resolver_path": resolver_path,
        "matched_rule_ids": matched_rule_ids,
        # Made explicit so no downstream reader mistakes this for the real token:
        "authoritative_skill_obligation_token": None,
    }
    with target.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return record


def read_pre_obligations(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read all decision records. Missing file -> []. Malformed lines skipped."""
    target = path or DEFAULT_PRE_OBLIGATIONS_PATH
    if not target.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
