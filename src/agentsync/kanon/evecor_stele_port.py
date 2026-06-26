"""EvecorStelePort — real Stele substrate implementation.

Implements StelePromotionPort against EVECOR's Stele ledger
(stele.ledger.store.LedgerStore).

Shim rationale
--------------
Stele's canonical write path is ledger_transaction(store, SandboxResult),
designed for untrusted parser output from a bubblewrap sandbox run.
A validated SKILL.md has no sandbox — it is a pre-trusted text blob that
Kanon already validated at PROMOTE level. So this port bypasses
ledger_transaction() and calls the store primitives directly:

    store.create_pending(run_id, artifact_dir, artifact_paths, ...)
    store.commit(record_id)

This is honest: the dishonesty in a dummy SandboxResult (exit_code=0,
wall_time=0, empty stdout/stderr) is replaced by the honest absence of a
SandboxResult entirely. The ledger gets a real artifact_dir with a real
SKILL.md file, a real sha256 manifest, and the correct run_id.

run_id threading
----------------
run_id is the Stele join key minted by the Enforcer when the obligation
was created (SkillObligation.run_id). PromotionAdapter passes it in from
obl.run_id. This port uses it as the LedgerStore.run_id so the resulting
ArtifactRecord joins back to the obligation via:

    store.find_by_run_id(obl.run_id) → [ArtifactRecord]

Requires the `stele` optional dependency:
    pip install -e ".[stele]"
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agentsync.kanon.models import PromotionArtifact, PromotionState, SkillCandidate

if TYPE_CHECKING:
    from stele.ledger.store import LedgerStore

logger = logging.getLogger(__name__)

try:
    from stele.ledger.store import DuplicateArtifactError, LedgerStore as _LedgerStore
    from stele.ledger.models import ArtifactState
    _STELE_AVAILABLE = True
except ImportError:
    _STELE_AVAILABLE = False


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


class EvecorStelePort:
    """Real StelePromotionPort backed by EVECOR's Stele LedgerStore.

    Args:
        store:         A LedgerStore instance opened against the target DB.
        artifact_base: Base directory for promotion artifacts.
                       Each call gets its own subdir: artifact_base / run_id /
                       The SKILL.md is written there before create_pending().

    Raises ImportError at instantiation if the `stele` package is not installed.
    """

    def __init__(self, store: LedgerStore, artifact_base: Path) -> None:
        if not _STELE_AVAILABLE:
            raise ImportError(
                "EvecorStelePort requires the 'stele' package. "
                "Install with: pip install -e '.[stele]'"
            )
        self._store = store
        self._artifact_base = Path(artifact_base)

    def commit_artifact(
        self,
        candidate: SkillCandidate,
        run_id: str,
    ) -> PromotionArtifact:
        """Write candidate to disk, ledger PENDING → COMMITTED, return receipt.

        Never raises on FAILED outcomes — failure is encoded in
        PromotionArtifact.state per the StelePromotionPort contract.

        Steps:
          1. Write candidate.content to artifact_base/run_id/<file_name>
          2. store.create_pending(run_id, artifact_dir, [skill_path], ...)
             duplicate_policy="ignore" — idempotent for identical content
          3. store.commit(record_id) — re-verifies file exists before COMMITTED
          4. Return PromotionArtifact(state=COMMITTED)

        On any store error: mark pending record FAILED, return state=FAILED.
        """
        source_hash = _sha256(candidate.content)

        # Step 1: write artifact to disk
        artifact_dir = self._artifact_base / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        skill_path = artifact_dir / candidate.file_name
        skill_path.write_text(candidate.content, encoding="utf-8")

        # Step 2: create pending ledger record
        record = None
        try:
            record = self._store.create_pending(
                run_id=run_id,
                artifact_dir=artifact_dir,
                artifact_paths=[skill_path],
                source_path=str(skill_path),
                source_hash=source_hash,
                duplicate_policy="ignore",
            )
        except Exception as exc:
            logger.error(
                "evecor_stele_port: create_pending failed run_id=%s: %r",
                run_id, exc,
            )
            return PromotionArtifact(
                artifact_hash=source_hash,
                record_id="",
                run_id=run_id,
                state=PromotionState.FAILED,
            )

        # Step 3: commit pending → committed
        try:
            record = self._store.commit(record.record_id)
        except Exception as exc:
            logger.error(
                "evecor_stele_port: commit failed record_id=%s run_id=%s: %r",
                record.record_id, run_id, exc,
            )
            try:
                self._store.fail(record.record_id, error=repr(exc))
            except Exception:
                pass
            return PromotionArtifact(
                artifact_hash=record.artifact_hash,
                record_id=record.record_id,
                run_id=run_id,
                state=PromotionState.FAILED,
            )

        logger.info(
            "evecor_stele_port: committed run_id=%s record_id=%s hash=%s…",
            run_id, record.record_id, record.artifact_hash[:12],
        )
        return PromotionArtifact(
            artifact_hash=record.artifact_hash,
            record_id=record.record_id,
            run_id=run_id,
            state=PromotionState.COMMITTED,
        )
