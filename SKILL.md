---
name: sql-harness
description: "Always use sql-harness for SQL, SSH, Redis, and database operations — querying any DSN (Postgres/MySQL/SQLite/Redis), inspecting schemas, running EXPLAIN plans, indexing analysis, schema migrations, shell exec over SSH, SFTP upload/download, Redis keyspace ops + Lua scripting, and any cross-DSN workflow. Reach for it whenever the task touches a database, a remote host, a cache, or schema work — not just for `query()` calls."
---

# sql-harness

A single-process SQL + SSH CLI for agents. 5 drivers (`postgres`, `mysql`, `sqlite`, `redis`, `ssh`) share one heredoc namespace. Connections live in plaintext in one TOML file.

For first-time setup, run `sql-harness init` (scaffolds `~/.config/sql-harness/connections.toml`), then `sql-harness add/test <name>`. For stuck-point mechanics, see `interaction-skills/shared/sql/` (cross-DB SQL) and `interaction-skills/<driver>/` (driver depth).

## Reach for sql-harness when...

- Any DB task: query, schema, migration, EXPLAIN, indexing, slow-query, stats
- Any cache task: Redis keyspace ops, type-specific helpers, Lua scripting for control flow
- "Run this on the prod box": SSH workspace (`ssh_exec` / `ssh_upload` / `ssh_run_script`)
- "Deploy this compose stack to remote": SSH + `interaction-skills/ssh/docker-via-ssh.md`
- "The DB is behind a bastion / can't reach directly": SSH port-forward / tunnel
- "Why is this query slow?": `explain_analyze` + `slow_queries` + `index_usage_stats`
- "Which indexes are dead?": `unused_indexes` + `seq_scan_heavy`
- "Compare row counts prod vs staging": `use_workspace("prod")` → `query()` → `use_workspace("staging")` → `query()`
- "Save this heredoc as a reusable script": `sql-harness save <name> -c <conn>`
- Anything where you would otherwise reach for `psql`, `mysql`, `pgcli`, `redis-cli`, or hand-rolled `subprocess` over SSH

## Usage

```bash
sql-harness --help
sql-harness list
sql-harness add --name <name> --driver postgres --url 'postgresql://user:pw@host/db'
sql-harness add --name <name> --driver mysql    --url 'mysql+pymysql://user:pw@host/db'
sql-harness add --name <name> --driver ssh      --url 'ssh://user@host:22?key=/path/id_ed25519'
sql-harness test <name>

sql-harness <<'PY'
use_workspace("local_pg")
print(query("SELECT version()"))
print(list_tables())
PY
```

- Helpers are pre-imported; call by name in heredoc mode.
- **First call requires `use_workspace(name)`** — no implicit default.
- Set `BH_SQL_ZONE_SKILLS=1` once so `use_workspace()` auto-surfaces that zone's per-DSN skill docs (see `interaction-skills/zone-skill-auto-surface.md`).
- For task-specific helpers, drop them into `agent-workspace/zones/<conn>/helpers.py` and they merge into the heredoc namespace (per-zone, overriding the shared base).
- Other subcommands: `init` (scaffold connections.toml + install preset zone skills), `doctor` (SELECT 1 on every connection), `edit`/`remove`/`show`, `workspace {list,use,close,show}`, `save`/`run`/`scripts`, `skill` (bare: emit packaged SKILL.md; `install`/`list`/`show`), `paths`, `open`, `web` (browse + edit the whole doc library in a local web UI — shipped docs, runtime zones, saved scripts), `stats`, `version`, `ssh {exec,upload,download,run-script,info}`.
- Zone-scoped subcommands (`save`, `run`, `scripts`, `skill list/show`, `ssh`) need `-c <conn>` or `$BH_SQL_ACTIVE_CONNECTION`.
- **Analytics (ON by default)**: every CLI call and every doc read (`apply_skill`, `skill`, `skill show`) is counted into `$BH_SQL_HOME/analytics.json`; view with `sql-harness stats`. Every executed command (CLI argv or heredoc body) plus failures is appended to `$BH_SQL_HOME/sql-harness.log` (NDJSON, one record per line). Disable both with `BH_SQL_ANALYTICS=0|off|false|no`. Extension point: `from sql_harness.analytics import on, CLI_INVOKED, DOC_READ, COMMAND_EXECUTED` to attach custom listeners (observer pattern).

## Drivers & capabilities

Five drivers, one heredoc namespace. URL scheme must match the backend: MySQL requires `mysql+pymysql://`; PostgreSQL accepts `postgres://` / `postgresql://` (normalized to `postgresql+psycopg://`); SSH accepts `ssh://`, `ssh+password://`, `ssh+key://`. SSH password auth may come from the connection's standalone `password` key instead of the URL (precedence: URL > `password` > `$BH_SSH_PASSWORD`); a connection that sets a password uses password auth exclusively, ignoring local `~/.ssh` keys.

### Postgres (`postgres://`)

Full SQLAlchemy + psycopg3 + 8 PG-only introspection helpers. Reach for these whenever the task is "PG + performance" or "PG + schema":

- `explain(sql)` — `EXPLAIN` plan (estimated)
- `explain_analyze(sql, buffers=True)` — `EXPLAIN (ANALYZE, BUFFERS)` (real times)
- `table_stats(name, schema=None)` — `pg_stat_user_tables` (seq vs idx scan counts, dead tuples)
- `index_usage_stats(table=None, schema=None)` — `pg_stat_user_indexes` (idx scan counts per index)
- `unused_indexes(...)` — indexes with zero idx scans (candidates for DROP)
- `seq_scan_heavy(...)` — tables routinely full-scanned (candidates for new indexes)
- `slow_queries(limit=10)` — top by `pg_stat_statements.mean_exec_time`
- `missing_indexes_hint(...)` — heuristic: many seq scans + table size > threshold

See `interaction-skills/postgres/` for the 8 depth docs (plan-reading, B-Tree, specialized indexes, table optimization, slow-queries-joins, jsonb, migrations, indexes-and-explain).

### MySQL (`mysql+pymysql://`)

Standard SQLAlchemy + PyMySQL. JSON syntax differs from PG — see `interaction-skills/mysql/json-columns.md`.

### SQLite (`sqlite:///path.db`)

Local dev / tests. All standard helpers work. Use `sql-harness add --name <name> --driver sqlite --url 'sqlite:///<absolute-path>'` (SQLite needs absolute path).

### Redis (`redis://host:port[/db]`)

**Not SQL** — `query()` / `execute()` / `table()` / `with_transaction()` raise `NotImplementedError` on a Redis workspace. Reach for the `redis_*` helpers, which wrap the redis-py sync client:

```python
use_workspace("cache")
redis_get("user:42")                          # → str | None
redis_set("counter", 0, ex=600)               # SET with TTL
redis_keys("user:*")                          # SCAN, non-blocking
redis_key_info("user:42")                     # {type, ttl, encoding, length}
redis_dbsize()                                # DBSIZE → int
server_version()                              # → "8.10.1"  (INFO server)
```

Redis Lua is the **only** Redis surface with real control flow (IF / FOR / LOCAL / RETURN). Use it for branching logic and atomic read-modify-write:

```python
script = """
local results = {}
for i, k in ipairs(KEYS) do
  local v = redis.call('GET', k)
  if v then table.insert(results, k .. '=' .. v) end
end
return results
"""
redis_eval(script, keys=["sh:hello"], args=[])

# Cache the script for repeated use (saves shipping the body each call)
sha = redis_load_script("return ARGV[1] .. ':' .. #KEYS")
redis_eval_sha(sha, keys=["a", "b"], args=["size"])   # → "size:2"
```

Read-only is enforced at the **client** layer (`_ReadOnlyRedisProxy` wraps `execute_command`) — Redis has no server-enforced `READ ONLY` flag. Pair with an ACL user (`+@read -@write`) for a hard limit. See `interaction-skills/redis/` for: `redis.md` (workspace + helpers + read_only), `lua.md` (EVAL/EVALSHA + control flow + read_only script check), `keyspace.md` (SCAN vs KEYS + key probes + DBSIZE), `types.md` (string/hash/list/set/zset per-type cookbook).

### SSH (`ssh://user@host:port?key=...`)

**Not a database** — a remote shell workspace. Reach for ssh whenever the task says "on the box", "the prod host", "deploy this", "upload that file", "run that command remotely":

```python
use_workspace("prod-app")
r = ssh_exec("systemctl status myapp")           # exit_code + stdout/stderr
ssh_upload("./deploy.sh", "/tmp/deploy.sh")       # SFTP
ssh_download("/var/log/app.log", "./app.log")     # SFTP
ssh_run_script("./scripts/deploy.sh")             # upload + execute
print(ssh_info())                                 # user/host/port/SFTP available
```

CLI equivalent: `sql-harness ssh -c <conn> {exec|upload|download|run-script|info} <args>`.

See `interaction-skills/ssh/` for: `ssh.md` (helpers + CLI + gotchas), `docker-via-ssh.md` (docker-compose deploy loop over SSH), `auth-and-tunnels.md` (auth scheme 选型 + 堡垒机 + 端口转发).

## Generic SQL helpers (all DB drivers)

| Helper | Returns | Use |
|---|---|---|
| `query(sql, params=None)` | `list[dict]` | SELECTs only |
| `execute(sql, params=None)` | `dict` (`lastrowid`, `rowcount`) | INSERT/UPDATE/DELETE/DDL |
| `list_tables(schema=None)` | `list[str]` | Schema recon — always start here |
| `describe(table, schema=None)` | `list[dict]` | Columns + types — before writing SQL |
| `table(name, schema=None, limit=None)` | `list[dict]` | Quick peek: `SELECT * FROM x LIMIT n` |
| `with_transaction()` | context manager yielding `Connection` | ≥ 2 DML statements / atomicity (DDL: PG+SQLite only) |
| `run_sql_file(path)` | `list[dict]` | Multi-statement `.sql`, one transaction |
| `server_version()` | `str` | Round-trip liveness check |
| `connection_info()` | `dict` | Current workspace metadata |
| `use_workspace_info(name)` | `dict` | Zone info + skills + scripts (always surfaces) |

## Interaction Skills

Stuck-point mechanics — read on demand by filename. Cross-driver SQL shared
(under `shared/sql/`, applies to postgres / mysql / sqlite):

- `interaction-skills/shared/sql/aggregations.md` (+ `aggregations-advanced.md`)
- `interaction-skills/shared/sql/encoding-and-charset.md`
- `interaction-skills/shared/sql/indexes-and-explain.md`
- `interaction-skills/shared/sql/joins.md` (+ `joins-advanced.md`)
- `interaction-skills/shared/sql/large-result-sets.md`
- `interaction-skills/shared/sql/migrations.md`
- `interaction-skills/shared/sql/pooling.md`
- `interaction-skills/shared/sql/recursive-ctes.md` (+ `recursive-ctes-advanced.md`)
- `interaction-skills/shared/sql/schema-introspection.md`
- `interaction-skills/shared/sql/timeouts-and-cancellation.md`
- `interaction-skills/shared/sql/transactions.md`
- `interaction-skills/shared/sql/window-functions.md` (+ `window-functions-advanced.md`)

Tool-mechanics (apply to every workspace regardless of driver):

- `interaction-skills/save-run-cycle.md`
- `interaction-skills/zone-skill-auto-surface.md`

PG-specific depth — `interaction-skills/postgres/`:

- `interaction-skills/postgres/btree-indexes.md`
- `interaction-skills/postgres/json-columns.md`
- `interaction-skills/postgres/pg-data-movement.md` (data movement: pg_dump / COPY / logical replication)
- `interaction-skills/postgres/plan-reading.md`
- `interaction-skills/postgres/slow-queries-joins.md`
- `interaction-skills/postgres/specialized-indexes.md`
- `interaction-skills/postgres/table-optimization.md`

MySQL-specific — `interaction-skills/mysql/`:

- `interaction-skills/mysql/json-columns.md`

SSH-specific — `interaction-skills/ssh/`:

- `interaction-skills/ssh/auth-and-tunnels.md`
- `interaction-skills/ssh/docker-via-ssh.md`
- `interaction-skills/ssh/ssh.md`

Redis-specific — `interaction-skills/redis/` (Redis is not SQL; the four files cover workspace activation + read_only, Lua scripting, keyspace probes, and the five value types):

- `interaction-skills/redis/redis.md`
- `interaction-skills/redis/lua.md`
- `interaction-skills/redis/keyspace.md`
- `interaction-skills/redis/types.md`

## Cross-DSN strategy skills

Reached via `apply_skill("pool")` etc. from any active zone — `zones/meta/skills/` is the passive fallback layer.

- `agent-workspace/zones/meta/skills/pool.md` — pool sizing, `pre_ping`, idle reuse.
- `agent-workspace/zones/meta/skills/workspace.md` — workspace isolation, multi-DSN workflows.

## What actually works (field-tested)

- **Start with `list_tables` + `describe`, not `query`.** Before writing SQL on an unknown DB, run `list_tables(schema=None)` then `describe("table", schema=None)` to learn the columns. Skips the trial-and-error SELECT cycle.
- **Set `BH_SQL_ZONE_SKILLS=1` once** so every `use_workspace()` surfaces that zone's per-DSN skill docs as a hint list (10 filenames max). Then `apply_skill(name)` reads the body when relevant.
- **Default to `with_transaction()`** for any write with ≥ 2 statements (insert + update, update + delete, …). Automatic rollback on exception — **for DML**. DDL is a different story: PostgreSQL and SQLite roll back `CREATE`/`DROP`/`ALTER` inside a transaction, **MySQL does not** (it implicitly commits), so a `drop + create` migration that fails halfway leaves you with neither table. Run DDL migrations with a backup or a reversible plan, not on the strength of the transaction. See `interaction-skills/shared/sql/migrations.md`.
- **Transactions are only real on a transactional engine.** A MySQL table created on a server whose `@@default_storage_engine` is not InnoDB (e.g. MyISAM) ignores `ROLLBACK` *silently* — the write sticks and nothing warns you. Check with `SELECT @@default_storage_engine`, and write `ENGINE=InnoDB` explicitly if you need rollback.
- **For PG performance tasks, reach for the 8 PG-only helpers first** (`explain_analyze`, `slow_queries`, `unused_indexes`, `seq_scan_heavy`, `table_stats`, `index_usage_stats`, `missing_indexes_hint`). Don't hand-parse `pg_stat_*` SQL.
- **For remote tasks, reach for SSH workspace first.** Don't reach for raw `subprocess` + ssh-key files in agent code — `ssh_exec("systemctl restart myapp")` is one line.
- **For "insert returning the new id"**: psycopg supports `INSERT … RETURNING id`; MySQL has no `RETURNING` — use `execute(...)` and read `result.lastrowid`.
- **Streaming large result sets**: `query()` loads every row into memory. For >10k rows, use `ws.engine.connect().execution_options(stream_results=True)` and iterate manually.
- **Read-only "is this alive?" check**: `print(server_version())` — single round-trip, works on every DSN, no per-driver fudging.
- **Auth wall**: redirected to login → stop and ask the user. Don't type credentials from `connection_info`/`describe`.
- **Credentials in TOML**: use `${env:VAR}` indirection for prod secrets. The connections.toml loader expands these at load time.
- **Cross-DB SQL**: prefer ANSI syntax (`'string'`, `LIMIT n OFFSET m`, `COALESCE`, `CURRENT_TIMESTAMP`). PG-specific: `::TYPE`, `JSONB`, `RETURNING`. MySQL-specific: `AUTO_INCREMENT`, backticks, `?` placeholders (sqlalchemy normalizes to `:name`).
- **Save the workflow**: every working block → `sql-harness save <name> -c <conn>`. Tomorrow's session re-runs with one command. See `interaction-skills/save-run-cycle.md`.

## Design Constraints

- One connection = one workspace; never share engines across workspaces.
- Connection pool defaults: `size=5, recycle=3600, pre_ping=True` (per-connection overrides win).
- `with_transaction()` yields a `Connection`; use `conn.execute(text(...))` for raw control.
- `query()` returns `list[dict]`; use `execute()` for INSERT/UPDATE/DELETE.
- `list_tables()` and `describe()` are read-only schema introspection helpers.
- SSH workspaces expose `ssh_exec` / `ssh_upload` / etc. Calling them on a non-SSH workspace raises `RuntimeError`.
- Redis workspaces expose `redis_*` helpers + `server_version()`. Calling `query()` / `execute()` / `table()` / `with_transaction()` on a Redis workspace raises `NotImplementedError`; the Redis driver follows the SSH pattern (native handle, not a SQLAlchemy Engine).
- PG-only helpers (`explain_analyze`, `table_stats`, ...) raise `NotImplementedError` on non-PG workspaces. Detect with `ws.driver.name == "postgres"`.
- All engines live in `SqlHarness`'s in-memory registry. Single-process, no daemon, no remote backend.

## Gotchas

- Driver label in TOML must match the URL scheme (`postgres` ⇄ `postgresql://`, `mysql` ⇄ `mysql+pymysql://`, `ssh` ⇄ `ssh://` / `ssh+password://` / `ssh+key://`).
- Passwords in TOML are plaintext; use `${env:VAR}` indirection for prod secrets. SSH accepts a standalone `password` key (keeps the URL clean); when one is set, local `~/.ssh` keys are ignored.
- `query()` is for SELECTs only. For INSERTs, use `execute()`.
- For tables > 10k rows, use `engine.connect().execution_options(stream_results=True)` and iterate manually — `query()` loads everything into memory.
- For Redis keyspaces > 10k keys, use `redis_keys(...)` (SCAN-based) — never call `KEYS *` directly, it blocks the server. `redis_key_info(key)` issues 4 round-trips per call; on hot loops use Lua to batch (see `interaction-skills/redis/lua.md`).
- Redis Lua is the only Redis surface with control flow. Branching logic, conditional writes, and atomic read-modify-write go through `redis_eval(...)` / `redis_eval_sha(...)` — see `interaction-skills/redis/lua.md`.
- SSH SFTP subsystem may be disabled on the remote host. `ssh_upload/ssh_download` raise `RuntimeError("SFTP subsystem not available")`; fall back to `tar -czf - | ssh host tar -xzf -` via `ssh_exec`.
- Default `ssh_exec` timeout is 30 s. Long-running commands (e.g. `pg_dump`, `tar czf`) need explicit `timeout=`.
- **Long output is spilled to a file, not printed.** A failed run whose traceback is long, and any `ssh exec` / `ssh run-script` output past a few KB, are written to `$BH_SQL_HOME/tmp/output/` and replaced by a pointer line naming the path, line count and size. That is deliberate — read the file when you actually need it. `BH_SQL_TRACEBACK=1` forces the raw traceback to stderr instead. The full traceback always also lands in `$BH_SQL_HOME/sql-harness.log` (NDJSON), whatever the terminal shows.

## Read-only connections (`read_only = true`)

For a sensitive database, mark the connection read-only. Two rails are installed at the driver layer, both attached to the **engine** rather than to `query()`/`execute()` — so `conn.execute()`, `run_sql_file()` and any path that skips the helpers are covered too.

```toml
[[connections]]
name = "prod_ro"
driver = "mysql"
url = "mysql+pymysql://ro_user@host:3306/proddb"
read_only = true
```

```bash
sql-harness add --name prod_ro --driver mysql --url '...' --read-only
```

What it refuses: anything whose verb is not `SELECT`/`SHOW`/`DESCRIBE`/`EXPLAIN`/`TABLE`/`VALUES`/`PRAGMA`, plus transaction control. **Unrecognised statements are refused, not permitted** — that is why it is an allow-list: a deny-list misses `CALL`, `DO`, `LOAD DATA`, `HANDLER` and every future statement type. It also handles the dodges a naive keyword match falls for: `/*!50000 DROP */` (MySQL executes those), `SELECT 1; DROP TABLE t`, `WITH x AS (...) DELETE`, `EXPLAIN ANALYZE DELETE` (PostgreSQL executes it), and `#` — which is a comment in MySQL but the XOR operator in PostgreSQL, so treating it as a comment there would hide a write. On MySQL it additionally refuses `INTO OUTFILE`/`INTO DUMPFILE`/`FOR UPDATE`/`LOCK IN SHARE MODE`; on PostgreSQL, `SELECT ... INTO` (creates a table) and the `FOR ...` lock variants.

Where the backend has a session setting, it is applied on **every new pooled connection**: `SET SESSION TRANSACTION READ ONLY` (MySQL) / `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` (PostgreSQL). That rail is enforced by the server, so it catches anything that slips past the statement guard (e.g. a stored procedure).

**Redis has no server-side READ ONLY flag.** sql-harness enforces read-only at the client layer: `_ReadOnlyRedisProxy` wraps `execute_command` and refuses write verbs (`SET` / `DEL` / `HSET` / `LPUSH` / `SADD` / `ZADD` / `EXPIRE` / `FLUSHDB` / `EVAL` / ...) before the bytes leave the Python process. Read verbs (`GET` / `HGETALL` / `LRANGE` / `SCAN` / `INFO` / `DBSIZE`) are still allowed. This catches every command path — typed helpers, `redis_command(*args)`, `pipeline()`, `transaction()` — because all of them route through `execute_command`. The deny-list covers Lua scripts that mention `redis.call` / `redis.pcall` (any server touch = a write potential); for known read-only scripts that need `redis.call`, call `ws.engine.eval(...)` directly to bypass.

**This is a guard rail, not a security boundary.** Both rails are escapable from the same session — a caller can run `SET SESSION TRANSACTION READ WRITE` and carry on, and any client-side check is pattern matching rather than a parser. The only hard boundary is the database account: use `GRANT SELECT ON db.* TO ...` for a database you genuinely must not write to, and treat everything above as defence in depth on top of it. SQLite has no session rail at all; for a file database the real boundary is the URL (`sqlite:///file:path?mode=ro&uri=true`). SSH workspaces ignore the flag — a remote shell is not a statement stream. For Redis, pair the flag with an ACL user whose allowed-commands list excludes writes (`+@read -@write -@dangerous`).

## Domain / table skills

Per-table or per-schema skills: drop a markdown file into `agent-workspace/zones/<conn>/skills/<name>.md`, read with `apply_skill(name)`. Cross-DSN strategy goes to `zones/meta/skills/` instead. PG optimization depth (plan reading, indexes, `table_stats`, `slow_queries`, …) lives in `interaction-skills/postgres/`.