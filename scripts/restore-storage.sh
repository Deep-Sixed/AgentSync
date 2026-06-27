#!/usr/bin/env bash
# Restore AgentSync mutable state from a backup archive.
#
# Usage:
#   ./scripts/restore-storage.sh /path/to/agentsync-YYYYMMDD-HHMMSS.tar.gz
#
# Stops the user service if active, restores files, then leaves restart to operator.
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 /path/to/agentsync-backup.tar.gz" >&2
  exit 1
fi

ARCHIVE="$1"
if [[ ! -f "$ARCHIVE" ]]; then
  echo "error: archive not found: $ARCHIVE" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORAGE="${AGENTSYNC_STORAGE:-$ROOT/storage}"

echo "This will overwrite mutable state under: $STORAGE"
echo "  archive: $ARCHIVE"
read -r -p "Type RESTORE to continue: " confirm
if [[ "$confirm" != "RESTORE" ]]; then
  echo "aborted"
  exit 1
fi

if systemctl --user is-active --quiet agentsync-mcp.service 2>/dev/null; then
  echo "stopping agentsync-mcp.service..."
  systemctl --user stop agentsync-mcp.service
  STOPPED=1
else
  STOPPED=0
fi

"$ROOT/scripts/bootstrap-storage.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
tar -xzf "$ARCHIVE" -C "$TMP"

restore_tree() {
  local name="$1"
  if [[ -d "$TMP/$name" ]]; then
    rm -rf "$STORAGE/$name"
    mkdir -p "$(dirname "$STORAGE/$name")"
    cp -a "$TMP/$name" "$STORAGE/$name"
    echo "restored: $STORAGE/$name"
  fi
}

restore_tree "obligations"
if [[ -d "$TMP/stele" ]]; then
  mkdir -p "$STORAGE/stele"
  [[ -f "$TMP/stele/ledger.db" ]] && cp -a "$TMP/stele/ledger.db" "$STORAGE/stele/ledger.db" && echo "restored: $STORAGE/stele/ledger.db"
  [[ -d "$TMP/stele/artifacts" ]] && rm -rf "$STORAGE/stele/artifacts" && cp -a "$TMP/stele/artifacts" "$STORAGE/stele/artifacts" && echo "restored: $STORAGE/stele/artifacts"
fi
restore_tree "skills/approved"

if [[ "$STOPPED" -eq 1 ]]; then
  echo "start with: systemctl --user start agentsync-mcp.service"
fi

echo "restore complete"
