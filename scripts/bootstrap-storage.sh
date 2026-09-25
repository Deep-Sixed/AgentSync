#!/usr/bin/env bash
# Ensure AgentSync mutable storage directories exist before service start.
set -euo pipefail

ROOT="${AGENTSYNC_ROOT:-/mnt/jarvis-data/projects/AgentSync}"
STORAGE="${AGENTSYNC_STORAGE:-$ROOT/storage}"

mkdir -p \
  "$STORAGE/obligations" \
  "$STORAGE/skills/approved" \
  "$STORAGE/stele/artifacts" \
  "$STORAGE/stele/archive"

exit 0
