# AgentSync

AgentSync is a rule-governed skill acquisition loop for AI agents: it checks
task rules, finds reusable skills, forces skill authoring when coverage is
missing, and promotes validated skills back into the shared skill catalog.

One MCP service, registered once with ContextForge.

> **Repo status:** this is the reference implementation for a private
> deployment. It is **not yet clone-and-run** for outside operators — the
> promotion substrate (`stele`) resolves from a local path and the MCP server
> imports it at startup. See [Stele substrate](#stele-substrate) and
> [Local deployment](#local-deployment-deep-sixed) before attempting to run it.

## Stele substrate

The promotion step commits validated `SKILL.md` artifacts to a **Stele
ledger** — the real EVECOR Stele `LedgerStore`, driven **in-process as a
library dependency**, not a vendored copy and not a separate service.

- `EvecorStelePort` (`src/agentsync/kanon/evecor_stele_port.py`) implements the
  `StelePromotionPort` seam against `stele.ledger.store.LedgerStore`.
- It bypasses Stele's `ledger_transaction(store, SandboxResult)` write path —
  a promoted `SKILL.md` is a pre-trusted blob with no sandbox run — and calls
  the store primitives directly: `create_pending(...)` then `commit(...)`.
- The `storage/stele/ledger.db` schema (`artifact_records`) and the artifact
  files under `storage/stele/artifacts/` are owned by Stele, not by AgentSync.
- Stele is an **optional dependency** (`[stele]` extra). Tests run without it
  via `FakeStelePromotionPort`; the live MCP server requires it (it imports
  `stele.ledger.store` at module load and fails loud if absent).

Stele is currently sourced from a local path (`pyproject.toml`):

```toml
stele = ["stele @ file:///mnt/jarvis-data/projects/Stele"]
```

Outside operators must repoint this at their own Stele checkout, index, or
wheel before `--extra stele` or the MCP server will work.

## Setup

```bash
git clone https://github.com/Deep-Sixed/AgentSync.git
cd AgentSync

# core + tests run without Stele (promotion is exercised via the fake port)
uv sync --extra mcp --extra dev
uv run pytest -q

# to run the live MCP service you also need a Stele install (see above)
uv sync --extra mcp --extra stele --extra dev
```

## Runtime service

AgentSync runs as a user systemd unit exposing streamable-http MCP on port 8585.
ContextForge federates the service; agents call ContextForge only.

### Install

```bash
mkdir -p ~/.config/agentsync
cp ops/agentsync.env.example ~/.config/agentsync/env
# edit ~/.config/agentsync/env — set paths for your deployment

cp ops/systemd/agentsync-mcp.service ~/.config/systemd/user/
# edit the copied unit — WorkingDirectory / ExecStart paths are deployment-specific
systemctl --user daemon-reload
systemctl --user enable --now agentsync-mcp.service
```

> The shipped `ops/systemd/agentsync-mcp.service` and `ops/agentsync.env.example`
> contain absolute paths for the reference deployment. Edit them for yours.

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

| Path | Owner | Purpose |
|------|-------|---------|
| `storage/obligations/obligations.jsonl` | AgentSync | Authoritative obligation lifecycle log |
| `storage/obligations/pre_obligations.jsonl` | AgentSync | Non-authoritative rule evaluation records |
| `storage/skills/approved/` | AgentSync | Approved SKILL.md registry |
| `storage/stele/ledger.db` | Stele | Stele `LedgerStore` artifact ledger (`artifact_records`) |
| `storage/stele/artifacts/` | Stele | Committed SKILL.md artifact files |

All runtime paths overridable via `AGENTSYNC_*` env vars (see
`ops/agentsync.env.example`). Live `*.db`, `*.jsonl`, and artifact files are
git-ignored — the tree ships empty dirs (`.gitkeep`) plus the `rules.yaml` /
`capabilities.yaml` fixtures only.

## Backup and restore

```bash
# backup (writes storage/backups/agentsync-YYYYMMDD-HHMMSS.tar.gz)
./scripts/backup-storage.sh

# restore (interactive confirmation; stops service if running)
./scripts/restore-storage.sh storage/backups/agentsync-YYYYMMDD-HHMMSS.tar.gz
```

Backups include obligations, the Stele ledger, artifacts, and approved skills.

## ContextForge registration

Register AgentSync as a ContextForge gateway named `agentsync-mcp` pointing at
`http://127.0.0.1:8585/mcp` with transport `STREAMABLEHTTP`.

Agent-facing auth uses a bearer token supplied via the
`ROUTERCORE_MCP_BEARER_TOKEN` environment variable. The token is consumed by
the smoke tests and MCP clients — it is **not** read by the AgentSync server
itself. Mint it with whatever bearer tooling fronts your ContextForge /
RouterCore deployment, then export it before running the agent-facing tests.

## Tests

```bash
uv run pytest -q                          # full gate
uv run pytest tests/test_operational_hardening.py -v
uv run pytest tests/test_agent_facing_a2a_smoke.py -v  # requires ContextForge
```

The `test_agent_facing_a2a_smoke.py` and `test_contextforge_smoke.py` suites
require a running ContextForge and a valid `ROUTERCORE_MCP_BEARER_TOKEN`; the
rest run standalone.

## Frozen checkpoints

Current validated gate: **`v1.1-kanon-operational-hardening` — 98/98**
(HEAD is this tag plus docs-only commits).

| Tag | Gate | Note |
|-----|------|------|
| `v1.1-kanon-operational-hardening` | 98/98 | current |
| `v1.1-kanon-agent-facing-a2a-smoke` | 95/95 | historical |

## Local deployment (Deep-Sixed)

Concrete paths for the reference deployment on JARVIS:

```bash
# project root
cd /mnt/jarvis-data/projects/AgentSync

# Stele source (pyproject [stele] extra)
#   stele @ file:///mnt/jarvis-data/projects/Stele

# mint the ContextForge agent bearer
ROUTERCORE_MCP_BEARER_TOKEN="$(/mnt/jarvis-data/projects/EVECOR/bin/evecor-routercore-mcp-bearer)"
```
