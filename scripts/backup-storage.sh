#!/usr/bin/env bash
# Backup AgentSync mutable state: obligations, Stele ledger, artifacts.
#
# Usage:
#   ./scripts/backup-storage.sh [output-dir]
#
# Default output: storage/backups/agentsync-YYYYMMDD-HHMMSS.tar.gz
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORAGE="${AGENTSYNC_STORAGE:-$ROOT/storage}"
OUT_DIR="${1:-$STORAGE/backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$OUT_DIR/agentsync-$STAMP.tar.gz"

mkdir -p "$OUT_DIR"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

copy_if_exists() {
  local src="$1"
  local dest="$2"
  if [[ -e "$src" ]]; then
    mkdir -p "$(dirname "$dest")"
    cp -a "$src" "$dest"
  fi
}

copy_if_exists "$STORAGE/obligations" "$TMP/obligations"
copy_if_exists "$STORAGE/stele/ledger.db" "$TMP/stele/ledger.db"
copy_if_exists "$STORAGE/stele/artifacts" "$TMP/stele/artifacts"
copy_if_exists "$STORAGE/skills/approved" "$TMP/skills/approved"

tar -czf "$ARCHIVE" -C "$TMP" .
echo "backup: $ARCHIVE"
