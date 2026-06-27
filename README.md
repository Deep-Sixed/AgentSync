# AgentSync

AgentSync is a rule-governed skill acquisition loop for AI agents: it checks
task rules, finds reusable skills, forces skill authoring when coverage is
missing, and promotes validated skills back into the shared skill catalog.

One MCP service, registered once with ContextForge.

## Setup

```bash
cd /mnt/jarvis-data/projects/AgentSync
uv sync --extra mcp --extra stele --extra dev
uv run pytest -q
```

## Runtime service

AgentSync runs as a user systemd unit exposing streamable-http MCP on port 8585.
ContextForge federates the service; agents call ContextForge only.

### Install

```bash
mkdir -p ~/.config/agentsync
cp ops/agentsync.env.example ~/.config/agentsync/env
# edit ~/.config/agentsync/env if paths differ

cp ops/systemd/agentsync-mcp.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agentsync-mcp.service
```

### Logs

```bash
journalctl --user -u agentsync-mcp.service -f
journalctl --user -u agentsync-mcp.service --since today
```

Set `AGENTSYNC_LOG_LEVEL=DEBUG` in `~/.config/agentsync/env` for verbose output.

### Restart behavior

- `Restart=always` with `RestartSec=10`
- `ExecStartPre=scripts/bootstrap-storage.sh` ensures storage dirs exist
- `main()` also creates storage parent dirs before serving

```bash
systemctl --user restart agentsync-mcp.service
systemctl --user status agentsync-mcp.service
```

## Mutable storage

| Path | Purpose |
|------|---------|
| `storage/obligations/obligations.jsonl` | Authoritative obligation lifecycle log |
| `storage/obligations/pre_obligations.jsonl` | Non-authoritative rule evaluation records |
| `storage/skills/approved/` | Approved SKILL.md registry |
| `storage/stele/ledger.db` | Stele artifact ledger |
| `storage/stele/artifacts/` | Committed SKILL.md artifact files |

All paths overridable via `AGENTSYNC_*` env vars (see `ops/agentsync.env.example`).

## Backup and restore

```bash
# backup (writes storage/backups/agentsync-YYYYMMDD-HHMMSS.tar.gz)
./scripts/backup-storage.sh

# restore (interactive confirmation; stops service if running)
./scripts/restore-storage.sh storage/backups/agentsync-YYYYMMDD-HHMMSS.tar.gz
```

Backups include obligations, Stele ledger, artifacts, and approved skills.

## ContextForge registration

AgentSync must be registered as a ContextForge gateway named `agentsync-mcp`
pointing at `http://127.0.0.1:8585/mcp` with transport `STREAMABLEHTTP`.

Agent auth uses `ROUTERCORE_MCP_BEARER_TOKEN` (mint via
`/mnt/jarvis-data/projects/EVECOR/bin/evecor-routercore-mcp-bearer`).

## Tests

```bash
uv run pytest -q                          # full gate
uv run pytest tests/test_operational_hardening.py -v
uv run pytest tests/test_agent_facing_a2a_smoke.py -v  # requires ContextForge
```

## Frozen checkpoints

| Tag | Gate |
|-----|------|
| `v1.1-kanon-agent-facing-a2a-smoke` | 95/95 |
| `v1.1-kanon-operational-hardening` | 98/98 |
