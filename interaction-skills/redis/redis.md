# Redis mechanic — how to talk to a Redis workspace

> **Mechanic guide.** The Redis driver is **not SQL**: `query()` / `execute()` /
> `table()` / `with_transaction()` do not work. Use the `redis_*` helpers below,
> or reach for `redis_eval(...)` when you need real control flow (the only
> Redis surface with IF / FOR / LOCAL / RETURN — see `interaction-skills/redis/lua.md`).

## Activation

```python
use_workspace("cache")                    # driver=redis in connections.toml
print(server_version())                   # → "8.10.1"  (INFO server)
print(redis_dbsize())                     # total keys in the selected db
```

Calling any `redis_*` helper against a non-Redis workspace raises
`RuntimeError("workspace 'X' is a Y workspace, not a Redis workspace...")`.

## Why no `query()` / `execute()`?

`query()` and `execute()` build SQL via `text(sql)` and route through a
SQLAlchemy `Connection`. A Redis `redis.Redis` instance has no SQL parser.
The driver raises `NotImplementedError` on `list_tables` / `describe` /
`quote_ident` for the same reason — Redis has no schema and no identifiers.

If you catch yourself reaching for `query("GET k")`, **stop**: use
`redis_get("k")`. If you need a verb with no typed helper, use the
`redis_command(*args)` escape hatch (passes through to redis-py's
`execute_command` and is therefore caught by the read-only guard).

## Typed helpers

```python
# key/value
redis_get(key)                        # → str | None
redis_set(key, value, ex=None)        # SET, optional TTL seconds → bool
redis_delete(*keys)                   # DEL → count

# keyspace
redis_keys(pattern="*", count=100)    # SCAN-based, non-blocking → list[str]
redis_key_info(key)                   # {type, ttl_seconds, encoding, length}
redis_dbsize()                        # DBSIZE → int

# escape hatch (use when no typed helper fits)
redis_command("HGETALL", "user:1")    # raw execute_command(*args)
redis_command("LRANGE", "queue", 0, -1)
redis_command("ZADD", "scores", "1", "alice")
```

`redis_command` is the one helper that always reaches the server; the
read-only guard intercepts there before the bytes go out.

## Five types, one driver

Each Redis type has a natural adapter. Pick the one your data shape needs —
do not force SQL-shaped helpers.

| Redis type | Use for | Helper |
|---|---|---|
| string | counters, JSON blobs, locks | `redis_set / redis_get` (+ `INCR` via `redis_command`) |
| hash | object-shaped records (user:1 → {name, age, …}) | `redis_command("HSET", ...)` / `HGETALL` |
| list | queues, recent-N buffers | `redis_command("LPUSH/RPUSH/LRANGE", ...)` |
| set | unique tags, membership | `redis_command("SADD/SMEMBERS", ...)` |
| zset | leaderboards, time-indexed | `redis_command("ZADD/ZRANGE", ...)` |

`redis_key_info(key)` returns `{type, ttl_seconds, encoding, length}` so an
agent can probe an unknown key without knowing its type upfront — the
`length` field is the type-appropriate cardinality (`STRLEN` / `HLEN` /
`LLEN` / `SCARD` / `ZCAR`).

## Read-only (`read_only = true`)

Redis has no server-side `READ ONLY` session flag. sql-harness enforces
read-only at the **client** layer: every command is intercepted by
`_ReadOnlyRedisProxy` (wrapping `execute_command`) and matched against a
write-verb deny-list before the bytes leave the Python process.

What it refuses on a `--read-only` connection:

- key/value writes: `SET SETNX SETEX MSET APPEND INCR DECR …`
- deletes: `DEL UNLINK`
- hash / list / set / zset writes: `HSET LPUSH SADD ZADD …` and friends
- TTL / rename / flush: `EXPIRE PERSIST RENAME COPY FLUSHDB FLUSHALL …`
- scripting: `EVAL EVALSHA SCRIPT PUBLISH` (use raw `client.eval(...)` to bypass)

What it still allows: `GET HGET LRANGE SMEMBERS ZRANGE SCAN KEYS INFO
EXISTS TYPE TTL STRLEN HLEN LLEN SCARD ZCARD OBJECT MEMORY DBSIZE`.
Per-type coverage is in `interaction-skills/redis/types.md`; the Lua
bypass is in `interaction-skills/redis/lua.md`.

**This is a guard rail, not a security boundary.** A determined caller can
bypass it (reach through `.connection_pool` directly). For a hard limit,
pair it with a Redis ACL user whose allowed-commands list excludes writes
(`+@read -@write -@dangerous`).

## Detection

- Caller has activated a workspace whose `connection.driver == "redis"`
- They want to GET/SET keys, list the keyspace, or run a Lua script
- They have noticed `query()` does not work and want the right helper

## Approach (one-liner recipe)

```python
use_workspace("<redis-connection>")
redis_set(key, value)                  # or redis_get / redis_keys / redis_eval
```

## Gotchas

- **`query()` / `execute()` / `table()` / `with_transaction()` raise NotImplementedError** on a Redis workspace. Don't try to bend SQL onto it.
- **`SCAN`, not `KEYS`** for anything beyond a few hundred keys. `KEYS` blocks the server. `redis_keys(...)` is SCAN-based.
- **The handle is a `redis.Redis`, not a SQLAlchemy `Engine`.** `ws.engine.ping()` and `ws.engine.dbsize()` work (used by `test`); `ws.engine.connect()` does not.
- **String replies come back as `bytes`** from raw redis-py. All `redis_*` helpers in this harness decode UTF-8 (`errors="replace"` for binary blobs). If you go through `redis_command` directly, decode yourself.
- **`redis_eval` is the only place with control flow.** Branching over many keys, conditional writes, atomic read-modify-write — all of it goes through Lua. See `interaction-skills/redis/lua.md`.
- **`redis_keys` returns sorted by name** for deterministic output. Pages internally with SCAN; `count=` is the hint, not a hard cap.
- **`maxmemory-policy noeviction`** in the cloud-redis compose: writes that would exceed `maxmemory` fail with `OOM` rather than evicting. Pick keys / TTL deliberately.
- **Pipelines and transactions** work on the raw handle (`ws.engine.pipeline()` / `.transaction()`); each command inside still routes through `execute_command`, so the read-only guard catches writes inside a pipeline.
- **Avoid `KEYS *` on production-sized databases.** It blocks the server for the full scan; `SCAN` does not. The `redis_keys()` helper uses SCAN, not KEYS, even when you pass `"*"`.
