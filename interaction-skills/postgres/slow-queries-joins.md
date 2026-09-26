# Slow queries & join strategy

Find slow queries via `pg_stat_statements` (and `pg_stat_activity` for what's running right now), then rewrite correlated subqueries / N+1 application-loop shapes as joins. Postgres is good at joins — a 5-join plan is normal, and one set-based JOIN usually beats N queries fired in an app loop.

## Detection

```python
use_workspace("local_pg")

# pg_stat_statements must be preloaded (postgresql.conf:
# shared_preload_libraries + restart) before it records anything.
print(query("SHOW shared_preload_libraries"))
execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")

# 1. Top 20 by total exec time, each row's % of the grand total.
for r in query("""
    SELECT SUBSTRING(query, 1, 60) AS short_query,
           ROUND((100 * total_exec_time
                / SUM(total_exec_time) OVER ())::NUMERIC, 2) AS pct,
           ROUND(total_exec_time::numeric, 2) AS total_exec_time,
           calls,
           ROUND(mean_exec_time::numeric, 2) AS mean,
           query
      FROM pg_stat_statements
     ORDER BY total_exec_time DESC
     LIMIT 20
"""):
    print(r)

# 2. What's running RIGHT NOW. Catches a stuck query not yet in the totals.
for r in query("""
    SELECT pid, state, now() - query_start AS runtime, LEFT(query, 80) AS q
      FROM pg_stat_activity
     WHERE state <> 'idle'
     ORDER BY runtime DESC
"""):
    print(r)
execute("SELECT pg_cancel_backend(<pid>)")      # soft: cancel query, keep connection
execute("SELECT pg_terminate_backend(<pid>)")   # hard: kill the connection

# 3. Log every statement slower than 250ms (SUSET; set in postgresql.conf +
#    reload to make permanent). -1 disables, 0 logs everything.
execute("SET log_min_duration_statement = 250")

# 4. Tables hit by sequential scans — candidates for a new index.
for r in query("""
    SELECT relname, seq_scan, seq_tup_read, idx_scan
      FROM pg_stat_user_tables
     WHERE seq_scan > 0
     ORDER BY seq_tup_read DESC
"""):
    print(r)
```

Spot it: high `mean_exec_time` with non-trivial `calls`; `total_exec_time` a large % of the sum. In the plan, `Nested Loop` with `loops` in the thousands over an inner `Seq Scan` = missing FK index; a `Seq Scan` removing ~all rows (`Rows Removed by Filter`) = a filter that defeats the index.

## Approach

The "use more joins" point is counter-intuitive but right: normalize + one join plan beats denormalize + N app-loop queries (the N+1 shape). The slow case is a join that **explodes** row counts — so filter *before* joining, not after.

```python
# Slow: correlated subquery in SELECT list — re-executes per outer row.
slow = """
  SELECT b.title,
         (SELECT a.full_name
            FROM book_author ba
            JOIN author a ON a.author_id = ba.author_id
           WHERE ba.book_id = b.book_id) AS author
    FROM book b
   WHERE b.title LIKE '%Patterns%'
"""
for row in explain(slow):          # EXPLAIN prepended automatically
    print(row)

# Fast: single join over the book_author junction.
fast = """
  SELECT b.title, a.full_name
    FROM book b
    JOIN book_author ba ON b.book_id   = ba.book_id
    JOIN author a       ON a.author_id = ba.author_id
   WHERE b.title LIKE '%Patterns%'
"""
for row in explain(fast):
    print(row)
```

Node types: **Hash Join** hashes the smaller side and probes with the larger (O(N+M), default for two sizable unsorted inputs); **Nested Loop** looks up the inner per outer row (fast for few outer rows, catastrophic if the outer is large and the inner isn't indexed); **Merge Join** merges two inputs sorted on the key (good for large equi-joins). With an index on the FK (`book_author.book_id`) the planner takes Nested Loop + Index Scan; without it, or when both sides are large, it takes a Hash Join.

JOIN vs EXISTS vs IN: use **JOIN** when you need columns from both sides (planner reorders inner joins freely); **EXISTS** for "does a related row exist?" (semi-join, short-circuits, no duplication); **IN** only for small lists — a huge literal `IN (...)` is brittle, prefer `= ANY(array)` or a join.

## Gotchas

- `pg_stat_statements` needs `shared_preload_libraries = 'pg_stat_statements'` in `postgresql.conf` **+ restart**. `CREATE EXTENSION` alone records nothing.
- `pg_cancel_backend(pid)` asks a query to stop (transaction rolls back, connection lives); `pg_terminate_backend(pid)` kills the connection. Don't terminate yourself.
- `SET log_min_duration_statement` is per-session and SUSET; for permanent logging set it in `postgresql.conf` and reload. `-1` disables; `0` logs everything (noisy).
- Join slowdowns, by frequency: missing index on the join/FK column; joining then filtering (filter *first* to shrink inputs before the join); `OR` conditions the planner can't turn into an index scan — rewrite as `UNION` of two indexed queries.
- Join hints (`enable_hashjoin`, `enable_nestloop`, …) are almost never needed — fix stats or the missing index. The planner reorders inner joins; it cannot reorder outer joins.
- Stale stats after a big load cause "suddenly slow" plans — run `ANALYZE` (autovacuum normally handles this).
- `EXPLAIN` only estimates; `EXPLAIN ANALYZE` actually runs the statement. For writes, wrap in `with_transaction()` and let it roll back.
