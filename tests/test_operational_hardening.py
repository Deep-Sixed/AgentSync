"""Operational hardening smoke — storage bootstrap, backup/restore scripts."""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "scripts" / "bootstrap-storage.sh"
BACKUP = REPO / "scripts" / "backup-storage.sh"
RESTORE = REPO / "scripts" / "restore-storage.sh"


def test_bootstrap_storage_creates_dirs(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    result = subprocess.run(
        [str(BOOTSTRAP)],
        env={**os.environ, "AGENTSYNC_STORAGE": str(storage)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (storage / "obligations").is_dir()
    assert (storage / "skills" / "approved").is_dir()
    assert (storage / "stele" / "artifacts").is_dir()
    assert (storage / "stele" / "archive").is_dir()


def test_backup_and_restore_roundtrip(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    subprocess.run(
        [str(BOOTSTRAP)],
        env={**os.environ, "AGENTSYNC_STORAGE": str(storage)},
        check=True,
    )

    obl_file = storage / "obligations" / "obligations.jsonl"
    obl_file.write_text('{"token":"t1"}\n', encoding="utf-8")
    ledger = storage / "stele" / "ledger.db"
    ledger.write_bytes(b"sqlite-test")
    artifact_dir = storage / "stele" / "artifacts" / "run-1"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    archive_blob = storage / "stele" / "archive" / "blobs" / "ab" / "abcd"
    archive_blob.parent.mkdir(parents=True)
    archive_blob.write_bytes(b"blob")

    backup_dir = tmp_path / "backups"
    result = subprocess.run(
        [str(BACKUP), str(backup_dir)],
        env={**os.environ, "AGENTSYNC_STORAGE": str(storage)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "backup:" in result.stdout
    archives = list(backup_dir.glob("agentsync-*.tar.gz"))
    assert len(archives) == 1

    # Wipe mutable state
    shutil.rmtree(storage)

    restore = subprocess.run(
        [str(RESTORE), str(archives[0])],
        input="RESTORE\n",
        env={**os.environ, "AGENTSYNC_STORAGE": str(storage)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "restore complete" in restore.stdout
    assert obl_file.read_text(encoding="utf-8") == '{"token":"t1"}\n'
    assert ledger.read_bytes() == b"sqlite-test"
    assert (artifact_dir / "SKILL.md").read_text(encoding="utf-8") == "# skill"
    assert archive_blob.read_bytes() == b"blob"


def test_main_ensure_storage_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("stele", reason="the server imports stele at startup")
    pytest.importorskip("mcp", reason="needs the [mcp] extra")
    monkeypatch.setenv("AGENTSYNC_PRE_OBLIGATIONS_PATH", str(tmp_path / "obligations/pre.jsonl"))
    monkeypatch.setenv("AGENTSYNC_OBLIGATIONS_PATH", str(tmp_path / "obligations/obligations.jsonl"))
    monkeypatch.setenv("AGENTSYNC_APPROVED_ROOT", str(tmp_path / "skills/approved"))
    monkeypatch.setenv("AGENTSYNC_STELE_DB_PATH", str(tmp_path / "stele/ledger.db"))
    monkeypatch.setenv("AGENTSYNC_ARTIFACTS_BASE", str(tmp_path / "stele/artifacts"))
    monkeypatch.setenv("AGENTSYNC_STELE_ARCHIVE_ROOT", str(tmp_path / "stele/archive"))

    import agentsync.mcp.server as server

    importlib.reload(server)
    server._ensure_storage_dirs()

    assert (tmp_path / "obligations").is_dir()
    assert (tmp_path / "skills" / "approved").is_dir()
    assert (tmp_path / "stele" / "artifacts").is_dir()
    assert (tmp_path / "stele" / "archive").is_dir()

    # Reload with defaults so later tests are unaffected
    for key in (
        "AGENTSYNC_PRE_OBLIGATIONS_PATH",
        "AGENTSYNC_OBLIGATIONS_PATH",
        "AGENTSYNC_APPROVED_ROOT",
        "AGENTSYNC_STELE_DB_PATH",
        "AGENTSYNC_ARTIFACTS_BASE",
        "AGENTSYNC_STELE_ARCHIVE_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    importlib.reload(server)
