"""Redis driver — redis-py sync client (ConnectionPool).

Not registered for SQL — sql-harness callers don't use query()/execute()/
table() on this workspace. Use the redis.Redis handle directly (it's exposed
as `ws.engine`), or call the redis_* helpers added to sql_harness.helpers
(redis_get / redis_set / redis_keys / redis_info / redis_command).

Mirrors the SshDriver pattern: `make_engine` returns the *native client* (a
redis.Redis instance backed by a ConnectionPool), not a SQLAlchemy Engine. The
list_tables / describe / quote_ident / server_version hooks are explicitly
NotImplementedError — Redis has no schema and no SQL identifiers, so the SQL
helpers are not meaningful here.

URL format:
    redis://[:password@]host[:port][/db]
    rediss://...    (TLS; redis-py supports natively)
    unix://[:password@]/path/to/socket[/db]

Password precedence (highest first):
  1. password embedded in the URL     (redis://:pw@host)
  2. the connection's `password` field (connections.toml: password = "...")
  3. the URL's ACL/user (redis://user:pw@host via ACL on AUTH)

PoolConfig is honored as a *cap* on ConnectionPool sizes (max_connections);
the rest (recycle/pre_ping/echo) are intentionally ignored — redis-py's
connection health check and pool semantics differ from SQLAlchemy's.

`read_only` is enforced client-side via a command-name deny-list (see
_READ_ONLY_BLOCKED). Redis has no server-enforced "READ ONLY" session flag,
so the only available rail is to refuse write verbs at the helper layer.
This is a guard rail, not a security boundary — use a dedicated ACL user
with restricted command categories for a hard limit.
"""

from __future__ import annotations

from urllib.parse import urlparse

import redis
from redis.connection import ConnectionPool

from .readonly import ReadOnlyViolation


# Commands that mutate state — refused on a read-only connection.
# Read commands (GET, HGETALL, LRANGE, SMEMBERS, ZRANGE, SCAN, KEYS, INFO,
# EXISTS, TYPE, TTL, STRLEN, HLEN, LLEN, SCARD, ZCARD, OBJECT, MEMORY USAGE,
# DBSIZE, RANDOMKEY, MULTI without EXEC) all stay allowed.
_READ_ONLY_BLOCKED = frozenset(
    {
        # key/value writes
        "SET", "SETNX", "SETEX", "PSETEX", "MSET", "MSETNX",
        "APPEND", "SETRANGE", "GETSET", "GETRANGE",
        "INCR", "INCRBY", "INCRBYFLOAT", "DECR", "DECRBY",
        # deletes
        "DEL", "UNLINK",
        # hash writes
        "HSET", "HSETNX", "HMSET", "HINCRBY", "HINCRBYFLOAT",
        # list writes
        "LPUSH", "LPUSHX", "RPUSH", "RPUSHX", "LPOP", "RPOP",
        "LINSERT", "LSET", "LREM", "LMOVE", "BLMOVE", "BRPOPLPUSH",
        # set writes
        "SADD", "SPOP", "SREM", "SMOVE",
        # zset writes
        "ZADD", "ZINCRBY", "ZREM", "ZREMRANGEBYSCORE", "ZREMRANGEBYRANK",
        "ZREMRANGEBYLEX", "ZPOPMIN", "ZPOPMAX",
        # generic / meta
        "EXPIRE", "EXPIREAT", "PEXPIRE", "PEXPIREAT",
        "PERSIST", "RENAME", "RENAMENX", "COPY", "MOVE",
        "FLUSHDB", "FLUSHALL", "RANDOMKEY",
        # pubsub / scripting (we deliberately do not allow them read-only)
        "PUBLISH", "EVAL", "EVALSHA", "SCRIPT",
    }
)


def check_redis_command(command: str, *, read_only: bool) -> None:
    """Raise ReadOnlyViolation if `command` is a write verb on a read-only conn.

    `command` is the FIRST argument to redis.Redis.execute_command() (uppercase).
    """
    if not read_only:
        return
    if command.upper() in _READ_ONLY_BLOCKED:
        raise ReadOnlyViolation(
            f"{command.upper()} is not allowed on a read-only connection "
            f"(Redis has no server-enforced READ ONLY flag — this is a "
            f"client-side guard; pair it with an ACL user for a hard limit)"
        )


class _ReadOnlyRedisProxy:
    """Wrap a redis.Redis instance and intercept every command dispatch.

    redis-py routes through `Redis.execute_command(...)` which calls
    `ConnectionPool.get_connection(...)`. Wrapping `execute_command` is the
    one hook that catches every command, including those issued via the
    high-level helpers (`r.get()`, `r.set()`, `r.hset()`, pipelines,
    transactions, pubsub). The cost is one Python frame per call — fine for
    interactive use.

    NOTE: This is a guard rail, not a security boundary. A determined caller
    can bypass it by reaching through `.connection_pool` directly. Use an ACL
    user with restricted command categories for a hard limit.
    """

    def __init__(self, inner: redis.Redis, read_only: bool):
        self._inner = inner
        self._read_only = read_only

    def __getattr__(self, name: str):
        # Forward attribute access; if the user calls a high-level helper
        # like `proxy.set(...)`, redis-py still routes through
        # `execute_command`, which we override below.
        return getattr(self._inner, name)

    def execute_command(self, *args, **kwargs):
        if args:
            check_redis_command(args[0], read_only=self._read_only)
        return self._inner.execute_command(*args, **kwargs)

    def pipeline(self, *args, **kwargs):
        # Pipelines buffer commands client-side; each is dispatched via
        # execute_command when `.execute()` runs. The check above catches
        # every command at that point.
        return self._inner.pipeline(*args, **kwargs)

    def transaction(self, *args, **kwargs):
        # MULTI/EXEC wrapper — same story as pipeline.
        return self._inner.transaction(*args, **kwargs)


def _build_url(url: str, password: str | None) -> str:
    """Inject the standalone `password` if the URL has no embedded credential.

    redis-py accepts the standard ``redis://[:password@]host:port/db`` form;
    a connection configured with both a URL and a `password` field (as
    happens with `sql-harness add --password`) needs them merged.
    """
    parsed = urlparse(url)
    if parsed.password:
        return url  # URL already carries the credential; don't override
    if password and "@" not in parsed.netloc:
        # Re-stitch: scheme://user:<pw>@host:port/db
        user = parsed.username or ""
        # Preserve any user (ACL); inject only the password.
        # urlparse gives .hostname / .port; we rebuild the netloc.
        hostport = parsed.hostname or "localhost"
        if parsed.port:
            hostport += f":{parsed.port}"
        new_netloc = f"{user}:{password}@{hostport}" if user else f":{password}@{hostport}"
        return f"{parsed.scheme}://{new_netloc}{parsed.path}"
    return url


class RedisDriver:
    name = "redis"

    def make_engine(
        self, url: str, pool, password: str | None = None, read_only: bool = False
    ):
        """Return a redis.Redis instance backed by a ConnectionPool.

        `pool.size` (from PoolConfig) caps `max_connections`; pool.recycle /
        pool.pre_ping / pool.echo are accepted for API parity but not wired
        (redis-py's health check is server-driven via PING, not per-checkout).
        """
        if not url.startswith(("redis://", "rediss://", "unix://")):
            raise ValueError(
                f"Redis URL must start with redis://, rediss://, or unix:// "
                f"(got {url[:30]!r})"
            )
        merged_url = _build_url(url, password)
        # Cap pool by sql-harness's PoolConfig.size when set; redis-py default
        # is unlimited (None), which is fine for short-lived tests but risky
        # in long-running agent sessions.
        max_connections = getattr(pool, "size", None) or None
        connection_pool = ConnectionPool.from_url(
            merged_url, max_connections=max_connections
        )
        client = redis.Redis(connection_pool=connection_pool)
        if read_only:
            return _ReadOnlyRedisProxy(client, read_only=True)
        return client

    # --- SQL-shaped hooks intentionally not implemented ---------------------
    #
    # Redis is not a SQL store. The list_tables / describe / quote_ident /
    # server_version hooks on the Driver protocol assume a schema model
    # (named tables, columns with types, identifiers that need quoting,
    # a SELECT-able version() function). Forcing a Redis driver to fake
    # these would mislead callers — list_tables("users") can't run.
    #
    # Use the redis_* helpers in sql_harness.helpers instead.

    def list_tables(self, engine, schema: str | None) -> list[str]:
        raise NotImplementedError(
            "Redis has no tables; use redis_keys(engine, pattern='*') or "
            "list_tables_keyspace(engine) instead."
        )

    def describe(self, engine, table: str, schema: str | None) -> list[dict]:
        raise NotImplementedError(
            "Redis has no schema; for one key use redis_key_info(engine, key); "
            "for many, iterate redis_keys(engine, pattern=key)."
        )

    def quote_ident(self, ident: str) -> str:
        raise NotImplementedError(
            "Redis has no SQL identifiers; keys are passed verbatim to "
            "redis_get / redis_set / etc."
        )

    def server_version(self, engine) -> str:
        # The protocol calls `driver.server_version(engine)` from
        # helpers.server_version(). Redis has a real answer (INFO server),
        # so provide it — it's the one useful override.
        info = engine.info(section="server") or {}
        return str(info.get("redis_version") or "unknown")


# --- Convenience helpers re-exported by sql_harness.helpers ----------------
#
# These keep the redis_* verbs at the driver layer (so SSH / Mongo / etc. can
# later borrow the same shape), while the heredoc-visible wrappers live in
# helpers.py to live alongside query() / execute() / ssh_exec().


def key_info(client: redis.Redis, key: str) -> dict:
    """Return a dict describing one key: type, ttl_seconds, encoding, length.

    For strings: length is STRLEN. For hashes/sets/zsets: cardinal (HLEN etc).
    MEMORY USAGE is best-effort — omitted if the server lacks the command.
    """
    info: dict = {"key": key, "type": None, "ttl_seconds": None,
                  "encoding": None, "length": None}
    info["type"] = client.type(key).decode() if isinstance(
        client.type(key), bytes
    ) else client.type(key)
    ttl = client.ttl(key)
    info["ttl_seconds"] = ttl if ttl >= 0 else None
    try:
        enc = client.object("encoding", key)
        if isinstance(enc, bytes):
            enc = enc.decode()
        info["encoding"] = enc
    except redis.ResponseError:
        info["encoding"] = None
    if info["type"] == "string":
        info["length"] = client.strlen(key)
    elif info["type"] == "hash":
        info["length"] = client.hlen(key)
    elif info["type"] == "list":
        info["length"] = client.llen(key)
    elif info["type"] == "set":
        info["length"] = client.scard(key)
    elif info["type"] == "zset":
        info["length"] = client.zcard(key)
    return info


def scan_keys(client: redis.Redis, pattern: str = "*", count: int = 100) -> list[str]:
    """Return keys matching `pattern` via SCAN (non-blocking, unlike KEYS).

    `count` is the SCAN hint per round-trip; the actual page size is up to
    the server. Returns sorted by name for deterministic output.
    """
    keys: list[str] = []
    cursor = 0
    while True:
        cursor, batch = client.scan(cursor=cursor, match=pattern, count=count)
        for k in batch:
            keys.append(k.decode() if isinstance(k, bytes) else k)
        if cursor == 0:
            break
    return sorted(keys)


# --- Lua scripting ----------------------------------------------------------
#
# Redis Lua (via EVAL / EVALSHA) is the ONE Redis surface with real control
# flow — IF / FOR / WHILE / LOCAL / RETURN — so it's where "agent scripts"
# naturally land. Every redis-py call inside a Lua block goes through
# `redis.call(...)` / `redis.pcall(...)` and is subject to the same
# read_only guard as a top-level command, because each call still routes
# through `execute_command` on the wrapped client.
#
# The helpers below are intentionally tiny: redis-py's own `eval` already
# does the work. We only (a) normalize bytes↔str return values, (b) attach
# read_only checking at the *outer* call level, and (c) keep the EVALSHA
# cache hot so a script re-run avoids shipping the body.

# Lua-keywords that signal a write inside a script. Best-effort — Lua has
# many ways to call redis.call('SET', ...) (variables, table indexing,
# concatenation), so a regex on source is not a real security boundary.
# This is a *guard rail*; use an ACL user for a hard limit.
_LUA_WRITE_TOKENS = (
    "redis.call",
    "redis.pcall",
)


def _lua_calls_writes(source: str) -> bool:
    """Return True if `source` mentions redis.call / redis.pcall.

    Cheap substring check on the script body. Does not parse Lua — false
    positives are safe (they just trigger a read-only violation), false
    negatives require bypassing the outer check too (EVALSHA via cache).
    """
    src = source.lower()
    return any(tok in src for tok in _LUA_WRITE_TOKENS)


def eval_script(
    client: redis.Redis,
    source: str,
    keys: list[str] | None = None,
    args: list | None = None,
    *,
    read_only: bool = False,
) -> object:
    """Run a Lua script with redis.eval; normalize the return value.

    `keys` are KEYS[] in the script (key-locking granularity); `args` are
    ARGV[]. Pass `read_only=True` to reject scripts that touch
    `redis.call(...)` / `redis.pcall(...)` (any Redis call = a write
    potential). If you have a read-only script that needs `redis.call`,
    use raw `client.eval(source, len(keys), *keys, *args)` instead.
    """
    if read_only and _lua_calls_writes(source):
        raise ReadOnlyViolation(
            "Lua script uses redis.call/pcall — not allowed on a read-only "
            "connection. Use raw client.eval(...) to bypass, or relax the "
            "read_only flag."
        )
    keys = keys or []
    args = args or []
    result = client.eval(source, len(keys), *keys, *args)
    return _decode(result)


def load_script(client: redis.Redis, source: str) -> str:
    """SCRIPT LOAD; return the SHA1 (hex str). Use with eval_script_sha."""
    sha = client.script_load(source)
    return sha.decode() if isinstance(sha, bytes) else sha


def eval_script_sha(
    client: redis.Redis,
    sha: str,
    keys: list[str] | None = None,
    args: list | None = None,
) -> object:
    """EVALSHA — run a previously-loaded script without re-shipping the body.

    Falls back to EVAL automatically if the server has flushed its script
    cache (NOSCRIPT response). redis-py's Script class does this for you;
    this helper exposes it without forcing a Callable wrapper.
    """
    keys = keys or []
    args = args or []
    try:
        result = client.evalsha(sha, len(keys), *keys, *args)
    except redis.exceptions.NoScriptError:
        # Cache miss: caller must have the original source. We raise with
        # the SHA so they know which one to reload — cheaper than guessing.
        raise RuntimeError(
            f"script {sha!r} not in server cache (NOSCRIPT). Reload with "
            f"eval_script(...) or call script_load(source) again."
        )
    return _decode(result)


def _decode(value):
    """Recursively turn redis-py bytes replies into native Python types.

    redis-py's default behavior is bytes for string replies; in a heredoc
    session that's almost never what an agent wants to read. Keep ints,
    floats, lists, None, and bool alone; decode bytes (and bytes inside
    lists/tuples) as utf-8 with `errors='replace'` so a binary blob can't
    crash the printing.
    """
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("utf-8", errors="replace")
    if isinstance(value, (list, tuple)):
        return [_decode(v) for v in value]
    return value
