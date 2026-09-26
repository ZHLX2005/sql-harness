# Timeouts & cancellation

A query that runs forever is worse than a query that fails fast.

## Server-side timeouts

### PostgreSQL

```sql
SET statement_timeout = '5s';        -- per-session
SET statement_timeout = '5s';        -- per-transaction with SET LOCAL
```

Use `SET LOCAL` inside a transaction so the timeout only applies for that statement:

```python
with with_transaction() as conn:
    conn.execute(text("SET LOCAL statement_timeout = '5s'"))
    rows = conn.execute(text("SELECT pg_sleep(10)")).fetchall()   # raises after 5s
```

### MySQL

```sql
SET SESSION MAX_EXECUTION_TIME = 5000;     -- milliseconds, MySQL 5.7+
SET SESSION MAX_EXECUTION_TIME = 5000;
```

`MAX_EXECUTION_TIME` only affects SELECT statements, not writes.

## Cancellation

### PostgreSQL

Cancel the running query from another connection:

```sql
SELECT pg_cancel_backend(<pid>);
```

Get the PID:

```sql
SELECT pid FROM pg_stat_activity WHERE state = 'active' AND query LIKE '%pg_sleep%';
```

### MySQL

```sql
KILL QUERY <connection_id>;
```

Get the connection ID:

```sql
SELECT ID, INFO FROM information_schema.PROCESSLIST WHERE INFO LIKE '%pg_sleep%';
```

## Gotchas

- `statement_timeout` includes network round-trip + parse + plan + execute. Don't set it below your slowest expected query.
- A timed-out statement may leave locks if it was inside a transaction — always rollback after timeout.
- `pg_cancel_backend` signals politely; the query may take up to a second to actually stop.
- `KILL QUERY` on MySQL kills the current statement but leaves the connection.