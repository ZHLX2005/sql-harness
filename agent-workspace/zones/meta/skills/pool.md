# Connection pooling — strategy

> **Strategy stub.** Sizing rules, `pre_ping`, `pool_recycle`, `NullPool` and pool
> gotchas all live in the mechanic: `interaction-skills/shared/sql/pooling.md`. Read that
> first. This file owns only the shipped default block below.

## `[pool_defaults]`

Every workspace pool starts from these conservative defaults (`pool_size=5` means
5 connections to *that* database, per workspace — not 5 total). Tune per workload
using the mechanic's sizing rules.

```toml
[pool_defaults]
size = 5
recycle = 3600
pre_ping = true
echo = false
```

**Recycle-timing rule:** when a load balancer / proxy kills idle connections
(AWS RDS Proxy = 1800s; PostgreSQL `idle_in_transaction_session_timeout`), set
`recycle` to (their timeout) × 0.8 so connections recycle *before* being killed
externally.

## Read full guide

`interaction-skills/shared/sql/pooling.md` — when to tune `pool_size` (1 / 2-5 / 10+), when
`pre_ping` and `pool_recycle` matter, `NullPool` vs `QueuePool`, and pool-stat
gotchas.
