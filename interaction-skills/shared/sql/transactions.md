# Transactions

Use `with_transaction()` for atomic multi-statement work. Yields a raw SQLAlchemy `Connection`; `engine.begin()` auto-commits on clean exit and auto-rolls-back on exception.

```python
use_workspace("local_pg")
with with_transaction() as conn:
    conn.execute(text("UPDATE accounts SET balance = balance - :n WHERE id = :id"), {"n": 100, "id": 1})
    conn.execute(text("UPDATE accounts SET balance = balance + :n WHERE id = :id"), {"n": 100, "id": 2})
```

## Detection

- Multiple writes that must succeed/fail together.
- Need a consistent point-in-time read.

## Approach

1. `with with_transaction() as conn:` — yields `conn` bound to a transaction.
2. Use `conn.execute(text(sql), params)` (note: pass `params` to `execute`, not `text()`).
3. Errors propagate → rollback.
4. Clean exit → commit.

## Isolation levels

Pass to the engine via URL or set per-session:

```python
with with_transaction() as conn:
    conn.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
    ...
```

## Deadlock retry

`with_transaction()` does NOT auto-retry on deadlock. Wrap if needed:

```python
import time
for attempt in range(3):
    try:
        with with_transaction() as conn:
            ...
        break
    except Exception as e:
        if "deadlock" in str(e).lower() and attempt < 2:
            time.sleep(0.05 * (2 ** attempt))
            continue
        raise
```

## Gotchas

- **Never f-string/`%`-interpolate user input into SQL** — always pass `params` to `query()`/`execute()`. SQLAlchemy binds them (server-side prepared statements on PG; lets PG cache the plan after a few runs). On MySQL, `%` literals in your SQL must be escaped as `%%` if you ever drop to raw DBAPI.
- `text("SELECT ...").execution_options(...)` is per-statement; transaction-level options belong on the `SET` line.
- Nested `with_transaction()` will create a SAVEPOINT (PostgreSQL) or fail (MySQL). Don't nest.
- A failed statement inside a transaction does NOT auto-rollback the whole transaction in some dialects — call `conn.rollback()` explicitly if you've swallowed the exception.
- **A non-transactional table ignores `ROLLBACK` silently.** On MySQL, check `SELECT @@default_storage_engine` before trusting a rollback: if it is not InnoDB, any table created without an explicit `ENGINE=InnoDB` accepts `BEGIN`/`ROLLBACK` and discards them — the row is still there afterwards and nothing errors. Reach for `with_transaction()` on such tables and you get the *appearance* of atomicity with none of the substance.
