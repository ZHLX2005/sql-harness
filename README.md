# sql-harness

A thin, single-process SQL + Redis + SSH CLI for LLM agents. Mirrors browser-harness's structure but targets relational databases (Postgres, MySQL, SQLite) plus Redis (keyspace ops + Lua) and SSH (remote shell + SFTP). No daemon, no cloud backend — engines live in one process.

## Quickstart

```bash
# install (from PyPI)
uv tool install sql-harness        # or: pipx install sql-harness

# or develop locally from a source checkout
uv sync

# scaffold a starter connections.toml
uv run sql-harness init

# add a connection (or edit ~/.config/sql-harness/connections.toml directly)
uv run sql-harness add --name local_pg --driver postgres --url 'postgresql://postgres:postgres@localhost:5432/postgres'

# verify
uv run sql-harness test local_pg

# use it — helpers are pre-imported in heredoc mode
uv run sql-harness <<'PY'
use_workspace("local_pg")
print(query("SELECT version()"))
print(list_tables())
PY

# browse + edit the doc library (SKILL.md, interaction-skills/, your zone skills, connections.toml)
uv run sql-harness web
```

## Architecture

- `SKILL.md` — the agent-facing entry (day-to-day usage; auto-shipped in the wheel)
- `src/sql_harness/` — protected core package (~1k lines: drivers, manager, helpers, web UI)
- `${XDG_CONFIG_HOME:-~/.config}/sql-harness/connections.toml` — plaintext credentials in ONE file (`${env:VAR}` indirection for prod secrets)
- `agent-workspace/zones/<conn>/skills/` — per-DSN skills the agent writes/reads
- `agent-workspace/zones/meta/skills/` — cross-DSN strategy skills (`apply_skill()` fallback)

## Drivers

| Driver | Backend | Package | Status |
|---|---|---|---|
| `postgres` | PostgreSQL | `psycopg[binary]>=3.2` | ✅ |
| `mysql` | MySQL | `pymysql>=1.1` | ✅ |
| `redis` | Redis | `redis>=5.0` | ✅ |
| `sqlite` | SQLite | stdlib (`sqlite3`) | ✅ (test-only) |
| `ssh` / `ssh+password` / `ssh+key` | remote shell + SFTP | `paramiko>=3.4` | ✅ |

## Key env vars

| Var | Purpose |
|---|---|
| `BH_SQL_HOME` | Override state root (default `~/.config/sql-harness`) |
| `BH_SQL_CONFIG_FILE` | Override connections.toml path |
| `BH_SQL_AGENT_WORKSPACE` | Override agent-workspace dir |
| `BH_SQL_ZONE_SKILLS=1` | `use_workspace()` auto-surfaces the zone's skills/scripts |

Full connection-file schema, read-only guard notes, and troubleshooting: read `SKILL.md` (shipped in the package, or `sql-harness skill` prints it).

## What this is NOT

Not a migration framework, not an ORM, not an interactive REPL (`pgcli` exists for that) — this is for *agents*: heredoc scripts with auto-injected helpers, saved per-DSN and re-runnable.

## License

MIT.
