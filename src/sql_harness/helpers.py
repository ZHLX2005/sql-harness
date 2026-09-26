"""Heredoc helpers — auto-imported into the sql-harness exec namespace.

These are thin wrappers over the active workspace's engine. They expect
`set_active(...)` to have been called by `run.py` (or `use_workspace(name)`
within the heredoc).
"""

from __future__ import annotations

import importlib.util
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import analytics as _analytics
from . import paths
from .config import ConnectionConfig
from .manager import SqlHarness, Workspace

# --- Active-harness state (set once per process by run.py / cli.py) ---------

_active: SqlHarness | None = None
_current_workspace: str = ""
_active_connection: str = ""        # the DSN zone (mirrors browser-harness domain)


def set_active(harness: SqlHarness) -> None:
    """Called by run.py after construction. Not for heredoc use.

    Resets the global current-workspace pointer so a fresh process starts
    cleanly (relevant for tests).
    """
    global _active, _current_workspace, _active_connection
    _active = harness
    _current_workspace = ""
    _active_connection = ""


def active() -> SqlHarness:
    if _active is None:
        raise RuntimeError(
            "sql-harness is not initialized; this should be unreachable "
            "from a properly-launched sql-harness process."
        )
    return _active


# --- Connection / zone resolution ------------------------------------------

def active_connection() -> str:
    """Return the currently-active connection name (the DSN zone)."""
    return _active_connection


def _require_connection() -> str:
    if not _active_connection:
        raise RuntimeError(
            "no connection is active; call use_workspace(name) first to set "
            "the DSN zone for scripts/skills/helpers"
        )
    return _active_connection


def _load_zone_helpers(connection: str, target_globals: dict) -> int:
    """Merge a connection's per-zone helpers.py over the current namespace.

    Mirrors browser-harness's auto-load of agent_helpers.py, but scoped per
    DSN. Returns the number of public names merged (0 if no file).
    """
    path = paths.zone_helpers_file(connection)
    if not path.is_file():
        return 0
    spec = importlib.util.spec_from_file_location(
        f"sql_harness_zone_helpers_{connection}", path
    )
    if spec is None or spec.loader is None:
        return 0
    module = importlib.util.module_from_spec(spec)
    # Pre-seed with current namespace so zone helpers can call query()/etc.
    for n, v in target_globals.items():
        if not n.startswith("__"):
            setattr(module, n, v)
    spec.loader.exec_module(module)
    merged = 0
    for name, value in vars(module).items():
        if name.startswith("_"):
            continue
        target_globals[name] = value
        merged += 1
    return merged


# --- Workspace management ---------------------------------------------------

def use_workspace(name: str, _globals: dict | None = None) -> "Workspace | dict":
    """Switch the active workspace (opens engine lazily) + activate its zone.

    Mirrors browser-harness's goto_url: switching context also activates the
    per-DSN isolation zone. Any per-connection `helpers.py` is merged over
    the namespace (overriding the shared base), so its functions become
    callable as bare names in heredoc / run mode.

    Return value (mirrors browser-harness's domain-skills pattern):
      - Default: returns the Workspace object (no info dump).
      - When $BH_SQL_ZONE_SKILLS=1: returns a dict with the Workspace
        + auto-surfaced zone skills + scripts. Agent should read each
        surfaced skill BEFORE touching tables.

    `_globals` is normally left None: per-zone helpers are merged into this
    helpers module's own namespace, which run.py shares as the exec namespace.
    """
    import os as _os
    global _current_workspace, _active_connection
    h = active()
    ws = h.workspace(name)
    _current_workspace = name
    _active_connection = name

    # Merge per-connection helpers over the namespace (zone helpers win).
    # Default target = this module's globals (the exec namespace run.py uses).
    target = _globals if _globals is not None else globals()
    _load_zone_helpers(name, target)

    # Optional auto-surface: when $BH_SQL_ZONE_SKILLS=1, return a dict that
    # includes the zone's skills + scripts so the agent can read them before
    # touching the tables. Mirrors browser-harness's BH_DOMAIN_SKILLS=1 +
    # goto_url returning domain_skills.
    if _os.environ.get("BH_SQL_ZONE_SKILLS") == "1":
        return {
            "workspace": ws,
            "connection": name,
            "driver": ws.driver.name,
            "zone_skills": _list_zone_skills(name, limit=10),
            "zone_scripts": _list_zone_scripts(name, limit=10),
            "hint": (
                "Set $BH_SQL_ZONE_SKILLS=1 to auto-surface these. "
                "Read each zone_skill before querying — it captures non-obvious "
                "schema knowledge. When the flag is off, you get the bare "
                "Workspace object instead."
            ),
        }
    return ws


def _list_zone_skills(connection: str, limit: int = 10) -> list[str]:
    """List skill filenames in the connection's zone (cap at `limit`)."""
    d = paths.zone_skills_dir(connection)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("*.md"))[:limit]


def _list_zone_scripts(connection: str, limit: int = 10) -> list[str]:
    """List saved script filenames in the connection's zone (cap at `limit`)."""
    d = paths.zone_scripts_dir(connection)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.py"))[:limit]


def use_workspace_info(name: str, _globals: dict | None = None) -> dict:
    """Always returns a dict describing the activated zone (forces the surface).

    Use from heredocs when you want to see what skills/scripts exist for the
    DSN you just switched to, regardless of the BH_SQL_ZONE_SKILLS env var.
    Mirrors browser-harness's goto_url returning domain skills.
    """
    use_workspace(name, _globals=_globals)
    return {
        "connection": name,
        "driver": current_workspace().driver.name,
        "skills": list_skills(),
        "scripts": list_scripts(),
    }


def current_workspace() -> Workspace:
    """Return the currently-active workspace (must already be opened)."""
    h = active()
    if not _current_workspace:
        raise RuntimeError(
            "no workspace is active; call use_workspace(name) first "
            "(config has default_workspace=" + repr(h.default_workspace_name) + ")"
        )
    ws = h.get_workspace(_current_workspace)
    if ws is None:
        # Stale pointer — reopen.
        return use_workspace(_current_workspace)
    return ws


def workspaces() -> list[str]:
    """List names of all open workspaces."""
    return active().list_workspaces()


def connection_info() -> dict:
    """Return dict: name, driver, url (masked), pool settings, status."""
    ws = current_workspace()
    pool = ws.engine.pool
    # SQLAlchemy's pool.size is a property; NullPool has size() as int attr.
    size_attr = getattr(pool, "size", None)
    size = size_attr() if callable(size_attr) else size_attr
    checked = getattr(pool, "checkedout", lambda: None)()
    overflow = getattr(pool, "overflow", lambda: None)()
    return {
        "name": ws.config.connection.name,
        "driver": ws.driver.name,
        "url": ws.config.connection.masked_url(),
        "application_name": ws.config.connection.application_name,
        # Surfaced so a caller can tell whether writes will be refused before
        # writing — the guard's error is the fallback, not the only signal.
        "read_only": ws.config.connection.read_only,
        "pool": {
            "size": size,
            "checked_out": checked,
            "overflow": overflow,
        },
    }


def dispose() -> None:
    """Close every engine."""
    active().close_all()


# --- Query helpers ----------------------------------------------------------

def query(sql: str, params: dict | list | None = None) -> list[dict]:
    """Run a SELECT and return rows as list[dict] (column name -> value)."""
    ws = current_workspace()
    with ws.engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        cols = result.keys()
        return [dict(zip(cols, row)) for row in result.fetchall()]


def execute(sql: str, params: dict | list | None = None) -> dict:
    """Run a write statement; return rowcount + lastrowid/oid info."""
    ws = current_workspace()
    with ws.engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        info: dict[str, Any] = {"rowcount": result.rowcount}
        # INSERTs expose lastrowid (MySQL) / inserted_primary_key (PG via RETURNING).
        # Both can raise on non-INSERT statements or on drivers that lack the
        # attribute (psycopg3 cursor has no `lastrowid`), so probe defensively.
        try:
            lrid = result.lastrowid
        except Exception:
            lrid = None
        if lrid is not None:
            info["lastrowid"] = lrid
        try:
            ipk = result.inserted_primary_key
        except Exception:
            ipk = None
        if ipk:
            info["inserted_primary_key"] = list(ipk)
        return info


def table(name: str, schema: str | None = None, limit: int | None = None) -> list[dict]:
    """SELECT * FROM <name> (small tables only — use streaming for big ones)."""
    ws = current_workspace()
    qid = ws.driver.quote_ident(name)
    sql = f"SELECT * FROM {qid}"
    if schema:
        sql = f"SELECT * FROM {ws.driver.quote_ident(schema)}.{qid}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return query(sql)


def list_tables(schema: str | None = None) -> list[str]:
    """List all tables and views in the active workspace."""
    return current_workspace().driver.list_tables(current_workspace().engine, schema)


def describe(table: str, schema: str | None = None) -> list[dict]:
    """Return column metadata for the given table."""
    return current_workspace().driver.describe(
        current_workspace().engine, table, schema
    )


def explain(sql: str) -> list[dict]:
    """Run EXPLAIN on the given SQL and return the plan rows."""
    ws = current_workspace()
    if ws.driver.name in ("postgres", "mysql", "sqlite"):
        prefix = "EXPLAIN"
    else:
        raise NotImplementedError(f"explain not supported for driver {ws.driver.name}")
    return query(f"{prefix} {sql}")


@contextmanager
def with_transaction():
    """Run statements inside a transaction with auto-commit/rollback.

    Use the yielded connection for raw control:

        with with_transaction() as conn:
            conn.execute(text("..."))

    Two limits worth knowing before trusting this with a migration:

    - **DDL is not covered on MySQL.** PostgreSQL and SQLite roll back
      CREATE/DROP/ALTER inside a transaction, but MySQL implicitly commits
      them, so a failed `drop + create` leaves neither table. Guard DDL with
      a backup or a reversible plan. See `interaction-skills/shared/sql/migrations.md`.
    - **A non-transactional storage engine ignores the rollback silently.**
      On MySQL, a table created where `@@default_storage_engine` is MyISAM
      accepts BEGIN/ROLLBACK but discards them — writes persist and nothing
      raises. Write `ENGINE=InnoDB` explicitly when rollback matters.
    """
    ws = current_workspace()
    with ws.engine.begin() as conn:
        yield conn
    # `engine.begin()` auto-commits on clean exit; auto-rollback on exception.


# --- Skill / script helpers (per-DSN zone) ---------------------------------

def apply_skill(name: str) -> str:
    """Read a skill markdown from the ACTIVE zone, with zones/meta as fallback.

    Two-layer fallback (decision P): the active zone wins if it has the skill;
    otherwise zones/meta/skills/ (cross-DSN strategy knowledge, populated by
    `sql-harness init`) supplies the answer. This collapses the legacy
    global `agent-workspace/skills/` layer into a regular zone so the lookup
    is single-shape: zone or meta.
    """
    conn = _require_connection()
    zone_path = paths.zone_skills_dir(conn) / f"{name}.md"
    if zone_path.is_file():
        _analytics.emit(_analytics.DOC_READ, {"doc": f"{name}.md", "via": "apply_skill"})
        return zone_path.read_text(encoding="utf-8")
    meta_path = paths.zone_skills_dir("meta") / f"{name}.md"
    if meta_path.is_file():
        _analytics.emit(_analytics.DOC_READ, {"doc": f"{name}.md", "via": "apply_skill"})
        return meta_path.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"no skill named {name!r} in zone {conn!r} or zones/meta"
    )


def list_skills() -> list[str]:
    """List skills in the ACTIVE zone UNION zones/meta (cross-DSN fallback)."""
    conn = _require_connection()
    seen: set[str] = set()
    for d in (paths.zone_skills_dir(conn), paths.zone_skills_dir("meta")):
        if d.is_dir():
            seen.update(p.stem for p in d.glob("*.md"))
    return sorted(seen)


def list_scripts() -> list[str]:
    """List saved-script names in the ACTIVE connection's zone."""
    conn = _require_connection()
    d = paths.zone_scripts_dir(conn)
    return sorted(p.stem for p in d.glob("*.py")) if d.is_dir() else []


# --- Server version (handy diagnostic) -------------------------------------

def server_version() -> str:
    """Return the backend's reported version string."""
    ws = current_workspace()
    fn = getattr(ws.driver, "server_version", None)
    if fn is None:
        return f"{ws.driver.name} (no server_version helper)"
    return fn(ws.engine)


# --- PostgreSQL performance helpers -----------------------------------------
#
# Wrap PostgreSQL's statistics catalogs (pg_stat_user_tables,
# pg_stat_user_indexes, pg_constraint, pg_stat_statements) and EXPLAIN ANALYZE.
# Each guards on `ws.driver.name == "postgres"` and raises NotImplementedError
# otherwise — same pattern as explain(). Mined from the upstream
# "PostgreSQL Performance Essentials" guide; see interaction-skills/postgres/ and
# interaction-skills/shared/sql/.

def explain_analyze(sql: str, buffers: bool = True, format: str = "text") -> list[dict]:
    """Run EXPLAIN (ANALYZE) on ``sql`` and return the plan rows.

    WARNING: ANALYZE *executes* the statement. Pass only read-only SELECTs,
    or wrap writes in a transaction you roll back::

        with with_transaction() as conn:
            explain_analyze("DELETE FROM accounts WHERE ...")  # then rollback

    ``buffers`` adds Buffer hit/miss detail. ``format`` is one of
    text|json|xml|yaml. Output mirrors :func:`explain`: a list of row dicts;
    for ``format='text'`` each row is ``{'QUERY PLAN': '<line>'}``.
    """
    ws = current_workspace()
    if ws.driver.name != "postgres":
        raise NotImplementedError(
            f"explain_analyze not supported for driver {ws.driver.name}"
        )
    fmt = format.upper()
    if fmt not in ("TEXT", "JSON", "XML", "YAML"):
        raise ValueError(f"unsupported EXPLAIN format: {format!r}")
    opts = ["ANALYZE"]
    if buffers:
        opts.append("BUFFERS")
    opts.append(f"FORMAT {fmt}")  # whitelisted; safe to interpolate
    return query(f"EXPLAIN ({', '.join(opts)}) {sql}")


def table_stats(name: str, schema: str | None = None) -> list[dict]:
    """Return size + live/dead row estimates for one table (pg_stat_user_tables).

    Columns: schemaname, table_name, live_rows, dead_rows, total_size_bytes,
    total_size, table_size, last_vacuum, last_analyze, vacuum_count,
    analyze_count.
    """
    ws = current_workspace()
    if ws.driver.name != "postgres":
        raise NotImplementedError(
            f"table_stats not supported for driver {ws.driver.name}"
        )
    return query(
        """
        SELECT s.schemaname,
               s.relname                                    AS table_name,
               s.n_live_tup                                 AS live_rows,
               s.n_dead_tup                                 AS dead_rows,
               pg_total_relation_size(s.relid)             AS total_size_bytes,
               pg_size_pretty(pg_total_relation_size(s.relid)) AS total_size,
               pg_size_pretty(pg_relation_size(s.relid))   AS table_size,
               s.last_vacuum,
               s.last_analyze,
               s.vacuum_count,
               s.analyze_count
          FROM pg_stat_user_tables s
         WHERE s.relname = :name
           AND (:schema IS NULL OR s.schemaname = :schema)
        """,
        {"name": name, "schema": schema},
    )


def index_usage_stats(table: str | None = None, schema: str | None = None) -> list[dict]:
    """Per-index scan counts from pg_stat_user_indexes.

    Columns: schemaname, table_name, index_name, idx_scan, idx_tup_read,
    idx_tup_fetch. Rows with ``idx_scan = 0`` are drop candidates
    (see :func:`unused_indexes`).
    """
    ws = current_workspace()
    if ws.driver.name != "postgres":
        raise NotImplementedError(
            f"index_usage_stats not supported for driver {ws.driver.name}"
        )
    return query(
        """
        SELECT s.schemaname,
               s.relname      AS table_name,
               s.indexrelname AS index_name,
               s.idx_scan,
               s.idx_tup_read,
               s.idx_tup_fetch
          FROM pg_stat_user_indexes s
         WHERE (:table IS NULL OR s.relname = :table)
           AND (:schema IS NULL OR s.schemaname = :schema)
         ORDER BY s.schemaname, s.relname, s.indexrelname
        """,
        {"table": table, "schema": schema},
    )


def unused_indexes(table: str | None = None, schema: str | None = None) -> list[dict]:
    """Indexes never scanned (``idx_scan = 0``) — drop candidates.

    Excludes unique indexes (needed for constraints) and expression indexes
    (``indkey`` contains 0), which may legitimately show zero scans.
    Columns: schemaname, table_name, index_name, times_used,
    index_size_bytes, index_size, index_ddl.
    """
    ws = current_workspace()
    if ws.driver.name != "postgres":
        raise NotImplementedError(
            f"unused_indexes not supported for driver {ws.driver.name}"
        )
    return query(
        """
        SELECT s.schemaname,
               s.relname      AS table_name,
               s.indexrelname AS index_name,
               s.idx_scan     AS times_used,
               pg_relation_size(s.indexrelid)                 AS index_size_bytes,
               pg_size_pretty(pg_relation_size(s.indexrelid)) AS index_size,
               idx.indexdef   AS index_ddl
          FROM pg_stat_user_indexes s
          JOIN pg_index i       ON i.indexrelid = s.indexrelid
          JOIN pg_indexes idx   ON idx.schemaname = s.schemaname
                               AND idx.indexname = s.indexrelname
         WHERE s.idx_scan = 0
           AND 0 <> ALL(i.indkey)
           AND NOT i.indisunique
           AND (:table IS NULL OR s.relname = :table)
           AND (:schema IS NULL OR s.schemaname = :schema)
         ORDER BY pg_relation_size(s.indexrelid) DESC
        """,
        {"table": table, "schema": schema},
    )


def seq_scan_heavy(table: str | None = None, schema: str | None = None) -> list[dict]:
    """Tables being full-scanned a lot, ranked by rows touched (pg_stat_user_tables).

    High ``seq_tup_read`` with ``seq_scan > 0`` hints a missing index.
    Columns: schemaname, table_name, seq_scan, seq_tup_read, idx_scan,
    idx_tup_fetch.
    """
    ws = current_workspace()
    if ws.driver.name != "postgres":
        raise NotImplementedError(
            f"seq_scan_heavy not supported for driver {ws.driver.name}"
        )
    return query(
        """
        SELECT s.schemaname,
               s.relname     AS table_name,
               s.seq_scan,
               s.seq_tup_read,
               s.idx_scan,
               s.idx_tup_fetch
          FROM pg_stat_user_tables s
         WHERE s.seq_scan > 0
           AND (:table IS NULL OR s.relname = :table)
           AND (:schema IS NULL OR s.schemaname = :schema)
         ORDER BY s.seq_tup_read DESC
        """,
        {"table": table, "schema": schema},
    )


def slow_queries(limit: int = 10) -> list[dict]:
    """Top-N slow queries from pg_stat_statements by mean exec time.

    Requires the ``pg_stat_statements`` extension *and*
    ``shared_preload_libraries='pg_stat_statements'``; if absent the backend
    raises. Columns: short_query, calls, total_exec_time_ms, mean_exec_time_ms,
    percent_of_total. (PG 13+ column names.)
    """
    ws = current_workspace()
    if ws.driver.name != "postgres":
        raise NotImplementedError(
            f"slow_queries not supported for driver {ws.driver.name}"
        )
    return query(
        """
        SELECT SUBSTRING(query, 1, 80)                                       AS short_query,
               calls,
               ROUND(total_exec_time::numeric, 2)                            AS total_exec_time_ms,
               ROUND(mean_exec_time::numeric, 2)                             AS mean_exec_time_ms,
               ROUND((100 * total_exec_time
                      / NULLIF(SUM(total_exec_time) OVER (), 0))::numeric, 2) AS percent_of_total
          FROM pg_stat_statements
         ORDER BY mean_exec_time DESC
         LIMIT :limit
        """,
        {"limit": int(limit)},
    )


def missing_indexes_hint(table: str | None = None, schema: str | None = None) -> list[dict]:
    """Foreign-key columns with no covering index — suggests CREATE INDEX.

    Returns a ready-to-run ``CREATE INDEX`` for each FK whose leading columns
    aren't covered by any valid index. Columns: schema_name, table_name,
    fk_constraint_name, referenced_table, fk_columns, create_sql.
    """
    ws = current_workspace()
    if ws.driver.name != "postgres":
        raise NotImplementedError(
            f"missing_indexes_hint not supported for driver {ws.driver.name}"
        )
    return query(
        """
        SELECT n.nspname                                 AS schema_name,
               cl.relname                                AS table_name,
               c.conname                                 AS fk_constraint_name,
               refcl.relname                             AS referenced_table,
               ARRAY_AGG(a.attname ORDER BY u.ord)       AS fk_columns,
               ('CREATE INDEX ON '
                  || quote_ident(n.nspname) || '.' || quote_ident(cl.relname)
                  || ' (' || STRING_AGG(quote_ident(a.attname), ', ' ORDER BY u.ord) || ');'
               )                                         AS create_sql
          FROM pg_constraint c
          JOIN pg_class cl     ON cl.oid = c.conrelid
          JOIN pg_namespace n  ON n.oid = cl.relnamespace
          JOIN pg_class refcl  ON refcl.oid = c.confrelid
          JOIN LATERAL UNNEST(c.conkey) WITH ORDINALITY AS u(attnum, ord) ON TRUE
          JOIN pg_attribute a  ON a.attrelid = c.conrelid AND a.attnum = u.attnum
         WHERE c.contype = 'f'
           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND NOT EXISTS (
                SELECT 1
                  FROM pg_index i
                 WHERE i.indrelid = c.conrelid
                   AND i.indisvalid
                   AND (i.indkey::int2[])[1:array_length(c.conkey::int2[], 1)]
                       = c.conkey::int2[]
           )
           AND (:table IS NULL
                OR (cl.relname = :table AND (:schema IS NULL OR n.nspname = :schema)))
         GROUP BY n.nspname, cl.relname, c.conname, c.conrelid,
                  c.confrelid, c.conkey, refcl.relname
         ORDER BY n.nspname, cl.relname, c.conname
        """,
        {"table": table, "schema": schema},
    )


# --- Multi-statement file execution -----------------------------------------

def run_sql_file(path: str) -> list[dict]:
    """Read a ``.sql`` file, execute every statement, return the last result set.

    Splits on top-level ``;`` (respecting single-quoted strings, double-quoted
    identifiers, ``--`` / ``/* */`` comments, and PG ``$tag$`` dollar-quoting),
    then runs every statement inside one transaction on the active workspace.
    Ideal for loading schema/seed fixtures or replaying a saved practice
    query::

        run_sql_file("path/to/schema.sql")
        run_sql_file("path/to/seed.sql")
        rows = run_sql_file("path/to/answer.sql")

    Returns the rows of the final statement if it produced a result set;
    otherwise an empty list.
    """
    sql_text = Path(path).read_text(encoding="utf-8")
    ws = current_workspace()
    last_rows: list[dict] = []
    with ws.engine.begin() as conn:
        for stmt in _split_sql(sql_text):
            result = conn.execute(text(stmt))
            if result.returns_rows:
                cols = result.keys()
                last_rows = [dict(zip(cols, row)) for row in result.fetchall()]
            else:
                last_rows = []
    return last_rows


def _split_sql(sql: str) -> list[str]:
    """Split SQL into top-level statements on ``;``.

    Respects single-quoted strings (``''`` escape), double-quoted identifiers
    (``""`` escape), ``--`` line comments, ``/* */`` block comments, and PG
    ``$tag$...$tag$`` dollar-quoting. Whitespace/comment-only chunks are dropped.
    """
    stmts: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        # -- line comment: skip to end of line (leave '\n' as whitespace)
        if c == "-" and nxt == "-":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        # /* block comment */
        if c == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        # 'single-quoted string'
        if c == "'":
            buf.append(c)
            i += 1
            while i < n:
                ch = sql[i]
                buf.append(ch)
                i += 1
                if ch == "'":
                    if i < n and sql[i] == "'":  # '' escaped quote
                        buf.append(sql[i])
                        i += 1
                        continue
                    break
            continue
        # "double-quoted identifier"
        if c == '"':
            buf.append(c)
            i += 1
            while i < n:
                ch = sql[i]
                buf.append(ch)
                i += 1
                if ch == '"':
                    if i < n and sql[i] == '"':  # "" escaped quote
                        buf.append(sql[i])
                        i += 1
                        continue
                    break
            continue
        # $tag$...$tag$ dollar-quoting (PG)
        if c == "$":
            m = re.match(r"\$[A-Za-z_0-9]*\$", sql[i:])
            if m:
                tag = m.group(0)
                buf.append(tag)
                i += len(tag)
                end = sql.find(tag, i)
                if end == -1:
                    buf.extend(sql[i:])
                    i = n
                else:
                    buf.extend(sql[i:end + len(tag)])
                    i = end + len(tag)
                continue
        # statement separator
        if c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


# --- SSH helpers -----------------------------------------------------------
#
# Analogous to the SQL helpers above, but for SSH workspaces (driver=ssh).
# The active workspace's .engine is a `_SshHandle` exposing .client / .sftp /
# .exec / .upload / .download. We guard on the driver name and raise a clear
# error if called against a DB workspace.

def _ssh_handle():
    """Return the active workspace's SSH handle, or raise if not an SSH workspace."""
    ws = current_workspace()
    if ws.driver.name != "ssh":
        raise RuntimeError(
            f"workspace {ws.config.connection.name!r} is a {ws.driver.name} workspace, "
            "not an SSH workspace. Call use_workspace('<ssh-name>') first, or add "
            "a connection with driver='ssh' in connections.toml."
        )
    return ws.engine  # the _SshHandle


def ssh_exec(command: str, timeout: float = 30.0) -> dict:
    """Run a shell command on the active SSH workspace.

    Returns the same dict shape as `query()`: `{stdout, stderr, exit_code, ok}`.
    """
    handle = _ssh_handle()
    result = handle.exec(command, timeout=timeout)
    return result.to_dict()


def ssh_upload(local_path: str, remote_path: str) -> None:
    """Copy a local file to the active SSH workspace."""
    _ssh_handle().upload(local_path, remote_path)


def ssh_download(remote_path: str, local_path: str) -> None:
    """Copy a file from the active SSH workspace to local disk."""
    _ssh_handle().download(remote_path, local_path)


def ssh_run_script(
    local_script_path: str,
    remote_dir: str = "/tmp",
    interpreter: str = "bash",
) -> dict:
    """Upload a local script and execute it remotely.

    Convenience: equivalent to `ssh_upload + ssh_exec "bash <remote_path>"`.
    Returns the command result dict.
    """
    handle = _ssh_handle()
    from .drivers.ssh import run_remote_script

    result = run_remote_script(handle, local_script_path, remote_dir, interpreter)
    return result.to_dict()


def ssh_info() -> dict:
    """Return basic info about the active SSH workspace (user, host, port, transport)."""
    handle = _ssh_handle()
    transport = handle.client.get_transport()
    return {
        "connection": current_workspace().config.connection.name,
        "user": handle.client.get_transport().get_username() if transport else None,
        "host": transport.sock.getpeername()[0] if transport else None,
        "port": transport.sock.getpeername()[1] if transport else None,
        "sftp_available": handle.sftp is not None,
    }


# --- Redis helpers ----------------------------------------------------------
#
# Analogous to the SSH helpers above, but for Redis workspaces (driver=redis).
# The active workspace's `.engine` is a `redis.Redis` (or the read-only
# proxy wrapping it) from drivers/redis.py. We guard on the driver name and
# raise a clear error if called against a non-Redis workspace.
#
# Why these wrappers (instead of letting heredoc users call `ws.engine.get(...)`
# directly): same reason SSH has ssh_exec — keep the cross-DSN ergonomics
# consistent, normalize bytes↔str returns, surface the read_only check at
# one place, and let agent scripts stay short.


def _redis_handle():
    """Return the active workspace's Redis handle, or raise if not a Redis workspace.

    Returns the underlying redis.Redis instance. The read-only check is
    installed as a method-level guard on the handle itself (see
    drivers/redis._ReadOnlyRedisProxy), so individual commands are caught
    before they reach the server.
    """
    ws = current_workspace()
    if ws.driver.name != "redis":
        raise RuntimeError(
            f"workspace {ws.config.connection.name!r} is a {ws.driver.name} "
            "workspace, not a Redis workspace. Call use_workspace('<redis-name>') "
            "first, or add a connection with driver='redis' in connections.toml."
        )
    return ws.engine


def redis_info(section: str | None = None) -> dict:
    """Return the Redis INFO dict (parsed). Pass `section` to limit (e.g. 'server')."""
    handle = _redis_handle()
    raw = handle.info(section=section) if section else handle.info()
    # redis-py returns string values; coerce common numerics for ergonomics.
    return {k: _coerce_info_value(v) for k, v in raw.items()}


def _coerce_info_value(v):
    """Best-effort int/float coercion for INFO values; keep strings otherwise."""
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        if v.isdigit():
            return int(v)
        try:
            return float(v)
        except ValueError:
            return v
    return v


def redis_keys(pattern: str = "*", count: int = 100) -> list[str]:
    """SCAN-based key listing (non-blocking; preferred over KEYS on big DBs)."""
    from .drivers.redis import scan_keys
    return scan_keys(_redis_handle(), pattern=pattern, count=count)


def redis_key_info(key: str) -> dict:
    """Return {key, type, ttl_seconds, encoding, length} for one key."""
    from .drivers.redis import key_info
    return key_info(_redis_handle(), key)


def redis_get(key: str) -> str | None:
    """GET; returns the string value or None for missing keys."""
    raw = _redis_handle().get(key)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")
    return raw


def redis_set(key: str, value: str | bytes | int | float, ex: int | None = None) -> bool:
    """SET with optional EX (seconds). Returns True on OK."""
    return bool(_redis_handle().set(key, value, ex=ex))


def redis_delete(*keys: str) -> int:
    """DEL one or more keys; returns number deleted."""
    return int(_redis_handle().delete(*keys))


def redis_command(*args, **kwargs):
    """Low-level escape hatch: pass through to execute_command.

    The first arg is the command name (e.g. 'HGETALL', 'LRANGE', 'ZADD').
    Use this when no typed helper above fits. The read-only guard (if the
    connection is read_only) intercepts here and raises ReadOnlyViolation
    before the command reaches the server.
    """
    handle = _redis_handle()
    # If the handle is a read-only proxy, route through it; the proxy's
    # execute_command does the check. For a plain redis.Redis, this is
    # just the standard method.
    method = getattr(handle, "execute_command", None)
    if method is None:
        # Plain redis.Redis exposes execute_command the same way.
        return handle.execute_command(*args, **kwargs)
    return method(*args, **kwargs)


# --- Lua scripting (Redis) --------------------------------------------------
#
# Redis Lua (EVAL / EVALSHA) is the one Redis surface with real control flow
# — IF / FOR / WHILE / LOCAL / RETURN — so it's where "agent scripts" with
# branching naturally land. See drivers/redis.py:eval_script for the
# underlying implementation.

def redis_eval(
    source: str,
    keys: list[str] | None = None,
    args: list | None = None,
) -> object:
    """Run a Lua script via EVAL. `keys` → KEYS[], `args` → ARGV[].

    Refuses to run scripts that mention `redis.call` / `redis.pcall` on a
    read-only connection (those are the only ways a Lua script touches the
    server). Use raw `ws.engine.eval(...)` to bypass the read-only check
    if you have a known read-only script that needs redis.call.
    """
    from .drivers.redis import eval_script
    handle = _redis_handle()
    ws = current_workspace()
    return eval_script(
        handle,
        source,
        keys=keys,
        args=args,
        read_only=ws.config.connection.read_only,
    )


def redis_eval_sha(sha: str, keys: list[str] | None = None, args: list | None = None) -> object:
    """EVALSHA — run a previously-loaded script without re-shipping the body.

    Raises RuntimeError on NOSCRIPT (server cache miss); reload with
    `redis_eval(source, ...)` or `redis_load_script(source)` then retry.
    """
    from .drivers.redis import eval_script_sha
    return eval_script_sha(_redis_handle(), sha, keys=keys, args=args)


def redis_load_script(source: str) -> str:
    """SCRIPT LOAD; return the SHA1 (hex str) for use with redis_eval_sha."""
    from .drivers.redis import load_script
    return load_script(_redis_handle(), source)


def redis_dbsize() -> int:
    """DBSIZE — total keys in the current logical database."""
    return int(_redis_handle().dbsize())