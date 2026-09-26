# Indexes & EXPLAIN

Before adding an index, prove the query is slow without one.

> PostgreSQL depth: `postgres/plan-reading.md`, `postgres/btree-indexes.md`, `postgres/specialized-indexes.md`, `postgres/table-optimization.md`, `postgres/slow-queries-joins.md`. This doc is the cross-DB intro (PG + MySQL).

## Detection
- Slow query reported by user.
- Want to verify a new index is actually used.

## Approach

```python
use_workspace("local_pg")
plan = explain("SELECT * FROM events WHERE user_id = 42 AND created_at > '2024-01-01'")
for row in plan:
    print(row)
```

## Approach (PostgreSQL)

```python
plan = query("EXPLAIN (ANALYZE, BUFFERS) SELECT ...")  # for full plan + runtime
```

`EXPLAIN ANALYZE` actually runs the query — destructive statements need to be wrapped:

```python
query("BEGIN")
query("EXPLAIN ANALYZE DELETE FROM ...")            # safe inside transaction; never commits
query("ROLLBACK")
```

## Approach (MySQL)

```python
plan = query("EXPLAIN SELECT ...")
# or
plan = query("EXPLAIN ANALYZE SELECT ...")          # MySQL 8.0+
```

## Reading the output

See `postgres/plan-reading.md` for full depth. The cross-DB truth is the 3 plan
types: `Seq Scan` (PG) / `ALL` (MySQL), `Index Scan` / `ref`, `Bitmap Heap Scan`
(PG when many rows match). PG-specific cost analysis (`cost=N..M`, BUFFERS,
ANALYZE runtime deltas) lives in the postgres doc.

## Adding an index

See `postgres/btree-indexes.md` for full depth (column order, partial indexes,
expression indexes, `EXPLAIN`-driven iteration). The cross-DB mechanic line:

```python
execute("CREATE INDEX CONCURRENTLY idx_events_user_created ON events(user_id, created_at)")
# CONCURRENTLY = PG; doesn't lock the table for writes.
# MySQL equivalent: ALTER TABLE ... ADD INDEX (online in InnoDB; pt-online-schema-change for huge tables).
```

## Gotchas

- `CONCURRENTLY` can fail and leave an INVALID index — drop and retry.
- MySQL `ALTER TABLE ... ADD INDEX` is online in InnoDB but can stall on huge tables; consider `pt-online-schema-change`.
- An index slows writes. Add only when reads benefit.
- `EXPLAIN ANALYZE` on a write in MySQL runs the write — wrap in a transaction and rollback.