# Redis keyspace — SCAN vs KEYS, type probes, cardinality

> **Mechanic guide.** Anything that touches "what keys exist" / "what type
> is this key" / "how big is it" lives here. The Redis equivalent of
> `list_tables()` + `describe()` — but the semantics are different: there
> is no schema, the keyspace is flat, and `KEYS` blocks the server.

## SCAN, not KEYS — always

`KEYS pattern` walks the entire keyspace synchronously: it blocks the
server for the duration of the scan, every other client pauses. Fine on
a 50-key dev box, catastrophic on a 5M-key prod cache.

`SCAN cursor [MATCH pattern] [COUNT hint]` is iterative and non-blocking:
returns a page + the next cursor, and the client loops until cursor=0.
`redis_keys(pattern, count=100)` does the looping for you and returns a
sorted list.

```python
redis_keys("user:*")              # → ["user:1", "user:42", "user:1003", ...]
redis_keys("session:*", count=500)  # bigger page hint; server still controls the actual size
```

`count` is a **hint**, not a cap. The server may return more or fewer
keys per round-trip; you always drive the loop until cursor=0.

## Probe a key without knowing its type

`redis_key_info(key)` returns:

```python
{
    "key": "user:42",
    "type": "hash",            # string | hash | list | set | zset | (none)
    "ttl_seconds": 599,        # None if no TTL set, -1 means "exists, no TTL"
    "encoding": "listpack",    # OBJECT ENCODING; server-side internal layout
    "length": 7,               # type-appropriate: STRLEN / HLEN / LLEN / SCARD / ZCARD
}
```

Use this when you've found a key via `redis_keys(...)` and want to know
what to do with it. The `type` field tells you which typed helper to
reach for; `length` lets you bound work ("if length > 1000, stream
instead of fetching").

## DBSIZE and per-db isolation

`SELECT n` switches logical databases (default 16, configurable).
`redis_dbsize()` returns the key count of the **currently selected**
db, not the total across all dbs. The cloud-redis compose keeps the
default db=0; if you switch with `SELECT 5` via `redis_command`, you
need to remember to switch back, or your next `redis_keys("*")` returns
the wrong slice.

```python
redis_command("SELECT", 5)            # → "OK"  (returns bytes; not auto-decoded by command())
redis_dbsize()                        # counts db 5 only
redis_command("SELECT", 0)            # back to default
```

## MEMORY USAGE — best-effort size per key

```python
redis_command("MEMORY", "USAGE", "user:42")
# → 248  (bytes; or None if MEMORY USAGE was disabled at compile time)
```

Not all servers have MEMORY USAGE enabled (Redis 4.0+ feature, opt-in
via `enable-debug-command yes` in some distros). Catch the
`ResponseError` if you need to support older deployments.

## Detection

- "What keys are in this db?" → `redis_keys(pattern)`
- "What kind of key is this?" → `redis_key_info(key)`
- "How big is this db?" → `redis_dbsize()`
- "How big is this one key?" → `redis_command("MEMORY", "USAGE", key)`

## Approach (one-liner recipe)

```python
redis_keys(pattern)             # SCAN, sorted, non-blocking
redis_key_info(key)             # one key, full probe
redis_dbsize()                  # currently selected db
```

## Gotchas

- **`KEYS *` blocks the server.** Never call it via `redis_command("KEYS", "*")`.
  Use `redis_keys("*")` (SCAN).
- **Sorted alphabetically** by key name for deterministic output. If you
  need insertion order, you're on the wrong data structure — Redis has no
  insertion-order guarantee on keyspace iteration.
- **`redis_key_info` is 4 round-trips per call** (TYPE + TTL + OBJECT +
  cardinality). On a hot loop over thousands of keys, prefer a single
  Lua script that fetches all four — see `interaction-skills/redis/lua.md`.
- **`ttl_seconds = -1` means "key exists, no expiry"**; `None` in this
  helper means "server says no TTL, mapped to None for Python". If you
  need to distinguish, read the raw `client.ttl(key)` — the helper
  collapses -1 and -2 (missing) into None.
- **`OBJECT ENCODING` is a server-internal hint, not a contract.** A
  Redis upgrade can change `encoding` for the same key shape. Don't
  branch behavior on it; treat as diagnostics only.
- **`DBSIZE` is O(1)** on a healthy server (it reads a counter). Safe
  to call from any code path. `redis_keys` is the expensive one.
