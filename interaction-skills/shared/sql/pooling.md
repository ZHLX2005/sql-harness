# Pooling

Default pool: `QueuePool(size=5, recycle=3600, pre_ping=True)`.

## When to tune `pool_size`

- **1 connection**: one-shot CLI sessions, batch jobs that run serially.
- **2-5**: typical interactive sessions; one query at a time, but the pool handles "fetching + metadata" parallelism.
- **10+**: batch jobs that fan out parallel queries (each helper call still uses one connection at a time, but you may have multiple concurrent call sites).

Each workspace has its own pool — `pool_size=5` per workspace means 5 connections to *that* database, not 5 total.

## `pre_ping`

`pre_ping=True` runs `SELECT 1` on every checkout. Costs ~1ms. Worth it if:
- The DB restarts or fails over behind a load balancer.
- Long-lived idle connections (over 1 hour) may have been killed by `idle_in_transaction_session_timeout` or `wait_timeout`.

Turn off only if you've measured the round-trip overhead and your DB has no idle-timeout.

## `pool_recycle`

Number of seconds before a connection is recycled (closed + reopened). Default 3600s.

Tune down if:
- Behind a load balancer with shorter idle timeout (e.g. AWS RDS Proxy defaults to 1800s).
- Behind a NAT that drops idle TCP after 5 minutes.

```python
execute("ALTER ROLE my_user SET idle_in_transaction_session_timeout = '60s'")
```

## `NullPool` vs `QueuePool`

`NullPool` (no pooling): open + close per query. Slower but no stale-connection risk.

```python
# config.toml
pool_size = 0                   # 0 → NullPool
```

Use `NullPool` for:
- Serverless/edge functions.
- Strict compliance environments (no persistent connections).

## Gotchas

- A connection in `with_transaction()` is checked out for the duration. Calling `query()` mid-transaction will block until the transaction ends (if `pool_size=1`) or grab a second connection (if larger).
- Pool stats: `connection_info()["pool"]` shows `size`, `checked_out`, `overflow`. Watch `overflow` for sizing pressure.
- On `engine.dispose()`, all connections close; re-opening is lazy.
- Don't share engines across workspaces — each workspace is its own pool, and cross-workspace reuse defeats isolation.