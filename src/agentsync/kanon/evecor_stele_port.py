"""EvecorStelePort — real Stele substrate implementation.

Implements StelePromotionPort against the Stele ledger
(stele.ledger.store.LedgerStore, bound to a stele.archive.store.BlobStore).

Shim rationale
--------------
Stele's canonical write path is ledger_transaction(store, SandboxResult),
designed for untrusted parser output from a sandbox run. A validated
SKILL.md has no sandbox — it is a pre-trusted text blob that Kanon already
validated at PROMOTE level. So this port bypasses ledger_transaction() and
calls the store primitives directly:

    store.create_pending(run_id=, artifact_dir=, artifact_paths=,
                         parser=KANON_PRODUCER, parser_config={...})
    store.seal(record_id)      # archives + verifies the bundle

There is no input Snapshot (Kanon parsed no input document), so the record
carries no source_hash. The "parser" identity names the producer honestly:
Kanon at this AgentSync version, with the validation it applied as config.

State mapping
-------------
Stele SEALED is reported as PromotionState.COMMITTED — the bundle is stored
in the evidence archive and verified, which is what "committed" promised.

run_id threading
----------------
run_id is the Stele join key minted by the Enforcer when the obligation
was created (SkillObligation.run_id). PromotionAdapter passes it in from
obl.run_id. Stele allows exactly one record per run_id, so the record
joins back to the obligation via:

    store.get_by_run_id(obl.run_id) -> ArtifactRecord

A retry with the same run_id reuses that record: SEALED with identical
content is returned as COMMITTED (idempotent), PENDING with identical content
is re-sealed, anything else is FAILED.

Requires the `stele` optional dependency:
    pip install -e ".[stele]"
"""
from __future__ import annotations

import hashlib
import logging
import uuid as _uuid_mod
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING

from agentsync.kanon.models import PromotionArtifact, PromotionState, SkillCandidate

if TYPE_CHECKING:
    from stele.ledger.models import ArtifactRecord
    from stele.ledger.store import LedgerStore

logger = logging.getLogger(__name__)

try:
    from stele.ledger.models import ArtifactState, ParserIdentity
    from stele.ledger.store import DuplicateRunError
    _STELE_IMPORT_ERROR: ImportError | None = None
except ImportError as exc:  # surfaced with its real cause at instantiation
    _STELE_IMPORT_ERROR = exc

KANON_PRODUCER_NAME = "agentsync-kanon"


def _agentsync_version() -> str:
    try:
        return metadata.version("agentsync")
    except metadata.PackageNotFoundError:
        return "unknown"


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class EvecorStelePort:
    """Real StelePromotionPort backed by a Stele LedgerStore.

    Args:
        store:         A LedgerStore opened against the target DB and archive.
        artifact_base: Base directory for promotion artifacts.
                       Each call gets its own subdir: artifact_base / run_id /
                       The SKILL.md is written there before create_pending().

    Raises ImportError at instantiation if the `stele` package is missing or
    does not provide the ledger API this port targets.
    """

    def __init__(self, store: LedgerStore, artifact_base: Path) -> None:
        if _STELE_IMPORT_ERROR is not None:
            raise ImportError(
                "EvecorStelePort requires a compatible 'stele' package "
                f"({_STELE_IMPORT_ERROR}). Install with: pip install -e '.[stele]'"
            ) from _STELE_IMPORT_ERROR
        self._store = store
        self._artifact_base = Path(artifact_base)
        self._producer = ParserIdentity(
            name=KANON_PRODUCER_NAME, version=_agentsync_version(),
        )

    def commit_artifact(
        self,
        candidate: SkillCandidate,
        run_id: str,
    ) -> PromotionArtifact:
        """Write candidate to disk, ledger PENDING → SEALED, return receipt.

        Never raises on FAILED outcomes — failure is encoded in
        PromotionArtifact.state per the StelePromotionPort contract.

        Steps:
          1. Reuse an existing record for run_id if one exists (retry path).
          2. Write candidate.content to artifact_base/run_id/<file_name>
          3. store.create_pending(...) with the Kanon producer identity
          4. store.seal(record_id) — archives and verifies the bundle
          5. Return PromotionArtifact(state=COMMITTED)

        On a sealing error: mark the pending record FAILED, return state=FAILED.
        """
        # Validate run_id — obligations always mint UUIDs; catching bad callers early
        # prevents a ledger record with an un-joinable key.
        try:
            _uuid_mod.UUID(run_id)
        except (ValueError, AttributeError, TypeError):
            raise ValueError(
                f"run_id must be a valid UUID string; got {run_id!r}"
            )

        content_hash = _sha256(candidate.content)

        existing = self._store.get_by_run_id(run_id)
        if existing is not None:
            return self._resume(existing, candidate, content_hash)

        skill_path = self._write(candidate, run_id)

        try:
            record = self._store.create_pending(
                run_id=run_id,
                artifact_dir=skill_path.parent,
                artifact_paths=[skill_path],
                parser=self._producer,
                parser_config={
                    "validation_level": "promote",
                    "dir_name": candidate.dir_name,
                    "file_name": candidate.file_name,
                },
            )
        except DuplicateRunError:
            # Lost a race with a concurrent promotion of the same run.
            existing = self._store.get_by_run_id(run_id)
            if existing is None:
                raise
            return self._resume(existing, candidate, content_hash)
        except Exception as exc:
            logger.error(
                "evecor_stele_port: create_pending failed run_id=%s: %r",
                run_id, exc,
            )
            return self._receipt(None, run_id, PromotionState.FAILED, content_hash)

        return self._seal(record)

    # --- helpers -------------------------------------------------------------

    def _write(self, candidate: SkillCandidate, run_id: str) -> Path:
        artifact_dir = self._artifact_base / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        skill_path = artifact_dir / candidate.file_name
        skill_path.write_text(candidate.content, encoding="utf-8")
        return skill_path

    def _seal(self, record: ArtifactRecord) -> PromotionArtifact:
        try:
            record = self._store.seal(record.record_id)
        except Exception as exc:
            logger.error(
                "evecor_stele_port: seal failed record_id=%s run_id=%s: %r",
                record.record_id, record.run_id, exc,
            )
            try:
                self._store.fail(record.record_id, error=repr(exc))
            except Exception:
                pass
            return self._receipt(record, record.run_id, PromotionState.FAILED)

        logger.info(
            "evecor_stele_port: sealed run_id=%s record_id=%s hash=%s…",
            record.run_id, record.record_id, record.artifact_hash[:12],
        )
        return self._receipt(record, record.run_id, PromotionState.COMMITTED)

    def _resume(
        self,
        record: ArtifactRecord,
        candidate: SkillCandidate,
        content_hash: str,
    ) -> PromotionArtifact:
        """Handle a second promotion attempt for a run_id the ledger already has."""
        same_content = record.artifact_manifest == {candidate.file_name: content_hash}

        if same_content and record.state is ArtifactState.SEALED:
            logger.info(
                "evecor_stele_port: run_id=%s already sealed with this content "
                "(record_id=%s); returning it", record.run_id, record.record_id,
            )
            return self._receipt(record, record.run_id, PromotionState.COMMITTED)

        if same_content and record.state is ArtifactState.PENDING:
            # An earlier attempt stopped between create_pending and seal.
            self._write(candidate, record.run_id)
            return self._seal(record)

        logger.error(
            "evecor_stele_port: run_id=%s already has record_id=%s (state=%s, "
            "same_content=%s); refusing to record a second artifact for this run",
            record.run_id, record.record_id, record.state.value, same_content,
        )
        return self._receipt(record, record.run_id, PromotionState.FAILED)

    @staticmethod
    def _receipt(
        record: ArtifactRecord | None,
        run_id: str,
        state: PromotionState,
        fallback_hash: str = "",
    ) -> PromotionArtifact:
        return PromotionArtifact(
            artifact_hash=record.artifact_hash if record is not None else fallback_hash,
            record_id=record.record_id if record is not None else "",
            run_id=run_id,
            state=state,
        )
