# Workspace isolation

One connection = one workspace. Never share engines across workspaces.

## Why

Different connections have different pool sizes, different app names, different credentials. Conflating them:
- Makes pool stats meaningless (which DB is checked out?).
- Risks cross-database SQL injection (`SELECT * FROM other_db.users`).
- Breaks transaction isolation (a query in workspace A could read uncommitted data from workspace B if they share a connection).

## Lifecycle

```python
use_workspace("prod_pg")        # opens lazily
use_workspace("staging_pg")    # both workspaces now open
# ... do work ...
dispose()                       # close all
```

`use_workspace()` is idempotent — calling it again returns the cached workspace.

## Multi-database workflows

```python
# Compare row counts between prod and staging
use_workspace("prod_pg")
prod_count = query("SELECT count(*) AS n FROM events")[0]["n"]

use_workspace("staging_pg")
staging_count = query("SELECT count(*) AS n FROM events")[0]["n"]

print(f"prod={prod_count} staging={staging_count}")
```

Each `query()` call grabs its workspace's connection and returns it to the pool on context exit.

## Transactions across workspaces

Don't. A single transaction is bound to one connection, so moving data between
workspaces is two separate transactions (`query` from A → `execute` into B) — one
per workspace, and NOT atomic. If you need atomicity, you need a single DB. See
`interaction-skills/shared/sql/transactions.md` for the transaction mechanics.

## Gotchas

- `workspaces()` shows only currently-open workspaces, not all configured connections.
- `connection_info()` returns the *current* workspace's info. Switch with `use_workspace()` first.
- Switching workspaces mid-transaction is not allowed — finish or rollback the current transaction first.