# AgentSync

AgentSync is a rule-governed skill acquisition loop for AI agents: it checks
task rules, finds reusable skills, forces skill authoring when coverage is
missing, and promotes validated skills back into the shared skill catalog.

One MCP service, registered once with ContextForge.

> **Repo status:** this is the reference implementation for a private
> deployment. The MCP server imports the promotion substrate (`stele`) at
> startup; see [Stele substrate](#stele-substrate) and
> [Local deployment](#local-deployment-deep-sixed) before attempting to run it.

## Stele substrate

The promotion step records validated `SKILL.md` artifacts in a **Stele
ledger** — Stele's `LedgerStore` bound to its evidence archive (`BlobStore`),
driven **in-process as a library dependency**, not a vendored copy and not a
separate service. Once sealed, the skill is installed into the approved
catalog (`storage/skills/approved/<dir_name>/SKILL.md`) and the Skill Server
index is reloaded, so the next lookup for the task finds it.

- `EvecorStelePort` (`src/agentsync/kanon/evecor_stele_port.py`) implements the
  `StelePromotionPort` seam against `stele.ledger.store.LedgerStore`.
- It bypasses Stele's `ledger_transaction(store, SandboxResult)` write path —
  a promoted `SKILL.md` is a pre-trusted blob with no sandbox run — and calls
  the store primitives directly: `create_pending(parser=agentsync-kanon, ...)`
  then `seal(...)`. A sealed record is reported as promotion state `committed`.
- The obligation's `run_id` is the Stele run id: one ledger record per
  obligation, found with `store.get_by_run_id(run_id)`.
- The `storage/stele/ledger.db` schema (`artifact_records`), the evidence
  archive under `storage/stele/archive/`, and the artifact files under
  `storage/stele/artifacts/` are owned by Stele, not by AgentSync.
- Stele is an **optional dependency** (`[stele]` extra). Tests run without it
  via `FakeStelePromotionPort`; the live MCP server requires it (it imports
  `stele.ledger.store` at module load and fails loud if absent).

Stele is pinned to the commit whose ledger API the port targets
(`pyproject.toml`):

```toml
stele = ["stele @ git+https://github.com/Deep-Sixed/Stele@10bbf3f24a4cd4b15fc501c57ce58bb2fcf7b54a"]
```

Moving the pin means re-running `tests/test_evecor_stele_port.py` against the
new Stele.

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
| `storage/skills/approved/` | AgentSync | Approved SKILL.md registry (Kanon installs promoted skills here) |
| `storage/stele/ledger.db` | Stele | Stele `LedgerStore` artifact ledger (`artifact_records`) |
| `storage/stele/archive/` | Stele | Stele evidence archive (`BlobStore`) the ledger points into |
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

Backups include obligations, the Stele ledger and its evidence archive,
artifacts, and approved skills.

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

The suite runs on a clean clone. Tests read seed skills from the committed
`tests/fixtures/approved/` tree, never from the git-ignored
`storage/skills/approved/`, and write only to pytest's `tmp_path`.

- Without the `[stele]` extra, the Stele port, local-service and MCP-service
  tests skip ("needs the [stele] extra"); with it, they run.
- The `test_agent_facing_a2a_smoke.py` and `test_contextforge_smoke.py` suites
  skip unless ContextForge is running and a valid
  `ROUTERCORE_MCP_BEARER_TOKEN` is available.

## Frozen checkpoints

Last tagged gate: **`v1.1-kanon-operational-hardening` — 98/98**, on the
reference machine with ContextForge running.

| Tag | Gate | Note |
|-----|------|------|
| `v1.1-kanon-operational-hardening` | 98/98 | current |
| `v1.1-kanon-agent-facing-a2a-smoke` | 95/95 | historical |

## Local deployment (Deep-Sixed)

Concrete paths for the reference deployment on JARVIS:

```bash
# project root
cd /mnt/jarvis-data/projects/AgentSync

# Stele source: the [stele] extra pins a Deep-Sixed/Stele commit; to run
# against the local checkout instead:
#   uv pip install -e /mnt/jarvis-data/projects/Stele

# mint the ContextForge agent bearer
ROUTERCORE_MCP_BEARER_TOKEN="$(/mnt/jarvis-data/projects/EVECOR/bin/evecor-routercore-mcp-bearer)"
```
