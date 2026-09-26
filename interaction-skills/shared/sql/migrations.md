# Migrations

## Reading a SQL file

```python
from pathlib import Path
sql = Path("migrations/0001_init.sql").read_text(encoding="utf-8")

with with_transaction() as conn:
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(text(stmt))
```

## Approach (in production)

1. Read the migration file.
2. Always wrap in a transaction (DDL is transactional on PostgreSQL, NOT on MySQL).
3. For destructive changes: stage in 2-3 migrations (add new column → backfill → drop old column).
4. For long-running migrations: use `CREATE INDEX CONCURRENTLY` (PG) or `pt-online-schema-change` (MySQL).

## Schema diffing

Use a tool like `alembic` (PG/MySQL via SQLAlchemy) or `sqitch` for repeatable migrations — sql-harness is for *running* migrations, not authoring them.

## Gotchas

- PostgreSQL DDL is transactional. MySQL DDL is NOT — an error mid-migration leaves the schema half-applied.
- `CREATE INDEX CONCURRENTLY` cannot run inside a transaction. Use `engine.connect()` (not `engine.begin()`).
- Adding a `NOT NULL` column without a default locks the table. Add nullable → backfill → add NOT NULL in a separate ALTER.
- On MySQL, `ALTER TABLE ... ADD COLUMN ... NOT NULL DEFAULT X` is online for INSTANT ADD COLUMN (MySQL 8.0.12+). Verify your server version.