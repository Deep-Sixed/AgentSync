"""Obligation store — authoritative append-only log.

Append-only JSONL at storage/obligations/skill_obligations.jsonl. Each lifecycle
transition appends a full SkillObligation snapshot; the CURRENT state of a token
is the latest snapshot for that token. Read tolerates malformed lines.

This is the authoritative obligation record. (Pre-obligations from the Rule
Server live in a separate file and are non-authoritative decision records.)
"""

import json
from pathlib import Path

from .models import SkillObligation

DEFAULT_OBLIGATIONS_PATH = Path("storage/obligations/skill_obligations.jsonl")


def append_obligation(obl: SkillObligation, path: Path | None = None) -> SkillObligation:
    """Append a full obligation snapshot. Returns the obligation written."""
    target = path or DEFAULT_OBLIGATIONS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as f:
        f.write(obl.model_dump_json() + "\n")
    return obl


def _read_snapshots(path: Path) -> list[SkillObligation]:
    if not path.exists():
        return []
    out: list[SkillObligation] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(SkillObligation.model_validate_json(line))
        except Exception:
            continue  # malformed line never crashes the read path
    return out


def current_state(path: Path | None = None) -> dict[str, SkillObligation]:
    """Reconstruct current obligation state: latest snapshot per token."""
    target = path or DEFAULT_OBLIGATIONS_PATH
    state: dict[str, SkillObligation] = {}
    for snap in _read_snapshots(target):
        state[snap.skill_obligation_token] = snap  # last write wins
    return state


def get_obligation(token: str, path: Path | None = None) -> SkillObligation | None:
    """Return the current state of one token, or None if unknown."""
    return current_state(path).get(token)


def find_open_for_task(task_id: str, path: Path | None = None) -> SkillObligation | None:
    """Return an OPEN/SUBMITTED obligation for a task, if one exists.

    Used for idempotency: a task that already has a live obligation must not
    mint a second token.
    """
    for obl in current_state(path).values():
        if obl.task_id == task_id and obl.status.value in ("open", "submitted"):
            return obl
    return None
