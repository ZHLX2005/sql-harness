# Redis types — string, hash, list, set, zset

> **Mechanic guide.** Redis has five value types, each with a use case that
> maps cleanly onto a helper. This is the per-type cookbook — pick the
> type your data shape actually wants, not the type that feels familiar
> from SQL. Read-only write-refusal is in `interaction-skills/redis/redis.md`
> §Read-only; this file is about usage, not enforcement.

## string — counters, JSON blobs, locks

```python
redis_set("counter:requests", 0)
redis_command("INCR", "counter:requests")          # → 1 (int)
redis_command("INCRBY", "counter:requests", 10)    # → 11

# JSON blob
import json
redis_set("user:1:profile", json.dumps({"name": "alice", "age": 30}), ex=600)
profile = json.loads(redis_get("user:1:profile"))
```

`INCR` / `INCRBY` / `DECR` are atomic on the server — no read-modify-write
race. For conditional counters ("only if below threshold"), use Lua
(see `interaction-skills/redis/lua.md`).

## hash — object-shaped records

A hash maps `field → value` inside one key. Use when your record has a
fixed-ish shape and you'll touch individual fields independently.

```python
redis_command("HSET", "user:1", "name", "alice", "age", 30, "city", "shenzhen")
# → 3  (fields added)
user = redis_command("HGETALL", "user:1")
# → {"name": "alice", "age": "30", "city": "shenzhen"}  (note: age is str)
redis_command("HINCRBY", "user:1", "age", 1)
# → "31"
```

All hash field values are strings on the wire — `HINCRBY` returns the
new count as a string. Cast before comparing.

## list — queues, recent-N buffers

A list is a linked list of strings. `LPUSH` + `RPOP` gives FIFO
queue semantics; `LPUSH` + `LRANGE 0 N-1` gives "latest N" caches.

```python
redis_command("RPUSH", "queue:jobs", "job-1", "job-2", "job-3")
redis_command("LPOP", "queue:jobs")              # → "job-1" (and removes)
redis_command("LRANGE", "queue:jobs", 0, -1)     # → ["job-2", "job-3"]
```

`LPOP` / `RPOP` with no count return a single element and remove it.
`LPOP key count` returns an array of N (Redis 6.2+) — for "drain N at
once", use the count form rather than a loop.

## set — unique tags, membership

```python
redis_command("SADD", "tags:post:42", "redis", "lua", "agent")
redis_command("SISMEMBER", "tags:post:42", "redis")    # → 1
redis_command("SMEMBERS", "tags:post:42")              # → ["redis", "lua", "agent"]
redis_command("SCARD", "tags:post:42")                 # → 3
```

For intersections ("posts tagged both 'redis' and 'lua'"):

```python
redis_command("SINTER", "tag:redis", "tag:lua")
# → ["post:42"]
```

## zset — leaderboards, time-indexed entries

A zset is a set where every member has a score. Sort by score; rank by
position.

```python
redis_command("ZADD", "scores", 100, "alice", 85, "bob", 92, "carol")
redis_command("ZREVRANGE", "scores", 0, 2, "WITHSCORES")
# → ["alice", "100", "carol", "92", "bob", "85"]  (highest first)
redis_command("ZSCORE", "scores", "alice")        # → "100"
redis_command("ZRANK", "scores", "alice")         # → 0  (highest = rank 0)
```

Use a Unix timestamp as the score for "events ordered by time":
`ZADD ts:events 1700000000 "evt-1"`. Then `ZRANGEBYSCORE ts:events
start end` becomes a time-range query.

## Detection

- One scalar value (counter, blob, lock) → string
- Object with named fields, accessed individually → hash
- Ordered sequence with push/pop semantics → list
- Unordered unique collection → set
- Sorted ranking or time-indexed data → zset

## Approach (one-liner recipe)

```python
use_workspace("<redis>")
redis_command("<VERB>", key, *args)   # escape hatch for everything
# or a typed helper if it fits:
redis_get / redis_set / redis_keys / redis_key_info / redis_dbsize / redis_eval
```

## Gotchas

- **All reply values from `redis_command` are bytes** — strings come back
  as `b"..."`, not `"..."`. The `redis_get` helper decodes for you; raw
  `redis_command` does not.
- **Hash field values are strings.** `HINCRBY` returns the new count as
  a string. Numeric comparisons need an explicit `int(...)`.
- **`LPOP key count`** is Redis 6.2+. Older servers return an error
  when you pass a count. Use a loop if you're on 6.0 or earlier.
- **Sets are unordered; zsets are ordered by score.** `SMEMBERS` returns
  whatever order the server wants; `ZRANGE` is deterministic.
- **Zset scores are float64** — precision drops for very large integers
  (above 2^53). Use a Unix-ms timestamp, not a nanosecond clock, unless
  you accept rounding.
- **`SINTER` / `SUNION` / `SDIFF`** all return sets as arrays; the largest
  set dominates the cost. Don't intersect two 10M-member sets in a hot
  loop — paginate or pre-compute.
