# Redis Lua scripting — the only Redis surface with control flow

> **Mechanic guide.** Every Redis command is a single verb — no IF, no loop,
> no locals, no RETURN. **Lua via EVAL / EVALSHA is the only place those
> exist.** This is the canonical home for branching logic, atomic
> read-modify-write, and per-key conditional writes in sql-harness.

## Why Lua, not a pipeline

A pipeline (`ws.engine.pipeline()`) buffers commands and ships them as one
round-trip, but each command is still a single verb: there is no way to
say "if the value is missing, set it; else increment". Lua lets you write
that script and run it server-side as one atomic operation.

## EVAL — run a script

```python
script = """
local results = {}
for i, k in ipairs(KEYS) do
  local v = redis.call('GET', k)
  if v then
    redis.call('SET', 'tag:' .. i, 'tagged:' .. v)
    redis.call('PEXPIRE', 'tag:' .. i, 60000)
    table.insert(results, k .. '=' .. v)
  end
end
return results
"""
redis_eval(script, keys=["sh:hello"], args=[])
# → ['sh:hello=from-sql-harness']
```

Conventions inside a script:

- `KEYS[]` — keys the script will touch (passed positionally as `keys=[...]`).
  Listing them lets Redis route the script to the right shard in Cluster.
- `ARGV[]` — non-key arguments (passed positionally as `args=[...]`).
- `redis.call(name, ...)` — strict; raises on server error.
- `redis.pcall(name, ...)` — returns the error as a table; you handle it.
- Return value is sent back to the client as-is: number, string, list, table.

## EVALSHA — reuse a cached script

Shipped bodies add round-trip cost on every call. `SCRIPT LOAD` returns a
SHA1; `EVALSHA <sha> ...` runs the cached body without re-shipping it.

```python
sha = redis_load_script("return ARGV[1] .. ':' .. #KEYS")
# → "30cfb0861ad022ae9251744bfe0f3a9c6213c869"

print(redis_eval_sha(sha, keys=["a", "b"], args=["size"]))
# → "size:2"
```

If the server flushed its script cache (`NOSCRIPT`), `redis_eval_sha`
raises `RuntimeError("script '...' not in server cache ...")`. Reload
with `redis_load_script(source)` and retry, or fall back to `redis_eval`
once to repopulate the cache.

## Read-only connections refuse scripts that touch redis.call

On a `--read-only` connection, `redis_eval` checks the script body for
`redis.call` / `redis.pcall` (any redis touch = a write potential) and
raises `ReadOnlyViolation` before the script ships. To run a known
read-only script that needs `redis.call` (e.g. a complex HGETALL
projection), reach for `ws.engine.eval(source, ...)` directly — it
bypasses both the wrapper and the body check.

## Detection

- You need branching logic over multiple keys
- You need atomic read-modify-write (compare, conditional update)
- You want to batch a computation server-side without N round-trips
- You're doing "if exists, increment; else create" patterns

## Approach (one-liner recipe)

```python
script = "<Lua source>"
result = redis_eval(script, keys=[...], args=[...])     # or redis_eval_sha(sha, ...)
```

## Gotchas

- **Always pass KEYS as `keys=[...]`, not as ARGV.** Redis Cluster routes by
  keys; passing them as ARGV breaks the routing assumption and silently
  touches the wrong shard.
- **Server-side time limit is 5 s by default** (`lua-time-limit`). Long
  loops block the server. For genuinely big work, batch via SCAN + Lua,
  not "iterate 10M keys in one EVAL".
- **Return values are encoded as Redis types**: number → int, string →
  string, table → array. Nil inside a list becomes `null` in the result.
  All `redis_*` helpers in this harness decode bytes→str and recursively
  decode nested lists for you.
- **Scripts are single-threaded on the server.** During execution, all
  other clients targeting the same keys wait. Keep them short.
- **`redis.call` raises; `redis.pcall` returns errors as tables** — pick
  deliberately. `pcall` is right when you want to recover from a missing
  key inside the script.
- **Use `redis_load_script` once + `redis_eval_sha` many times.** The
  hash is deterministic per source, so the body can stay in your saved
  script under sql-harness while the SHA only lives in the session.
- **Don't `EVAL` a script with `KEYS=[]` that actually touches keys** —
  Redis Cluster will refuse it. Either pass the keys or move the access
  to ARGV (which disables cluster routing — your call).
