# Table-level optimization

Interaction skill for the four 表优化 levers: index budget (don't over-index), planner statistics (`ANALYZE`/autovacuum), physical row order (`CLUSTER`), and declarative partitioning (`RANGE`/`LIST`/`HASH`) with partition pruning. Reach for these before adding another index.

## Detection

```python
use_workspace("pg")  # workspace wired to the book schema

# Over-indexed? idx_scan = 0 on a non-unique, non-expression index -> drop it.
# Every index slows INSERT/UPDATE/DELETE, so an unused one is pure write cost.
for r in query("""
    SELECT s.relname AS table_name, s.indexrelname AS index_name,
           s.idx_scan AS times_used,
           pg_size_pretty(pg_relation_size(s.indexrelid)) AS index_size
      FROM pg_stat_user_indexes s
      JOIN pg_index i ON s.indexrelid = i.indexrelid
     WHERE s.idx_scan = 0
       AND 0 <> ALL(i.indkey)     -- skip expression indexes (indkey has 0)
       AND NOT i.indisunique      -- skip unique/PK (kept for enforcement)
     ORDER BY pg_relation_size(s.indexrelid) DESC
"""):
    print(r)

# Bloat + stale stats in one view. n_dead_tup = rows killed by UPDATE/DELETE
# not yet reclaimed; n_mod_since_analyze = changes the planner hasn't seen yet.
for r in query("""
    SELECT relname, last_vacuum, last_analyze,
           n_dead_tup, n_mod_since_analyze
      FROM pg_stat_user_tables
     ORDER BY n_mod_since_analyze DESC
"""):
    print(r)

# Is a table partitioned? what partitions exist?
for r in query("SELECT inhrelid::regclass FROM pg_inherits "
               "WHERE inhparent = 'booking'::regclass"):
    print(r)
```

Read the signals: stale stats surface in `EXPLAIN` as a wild `rows=N` estimate next to a `Seq Scan` that `EXPLAIN ANALYZE` contradicts — run `ANALYZE`. High `n_dead_tup` relative to live rows means UPDATE/DELETE bloat — `VACUUM`. A plan that names partitions other than the one the predicate targets means pruning failed.

## Approach

```python
# --- 1. Statistics: ANALYZE after every bulk load so the planner sees real counts ---
execute("ANALYZE book")            # sample book, refresh pg_statistic
execute("VACUUM ANALYZE book")     # also reclaim dead tuples left by UPDATE/DELETE

# --- 2. VACUUM reclaims dead tuples. VACUUM FULL is a different beast: it REWRITES
#        the whole table under ACCESS EXCLUSIVE lock (blocks reads too). Avoid it;
#        prefer plain VACUUM + pg_repack, reserve FULL for emergencies only.
execute("VACUUM book")

# --- 3. CLUSTER: physically reorder the heap to match an index (range scans win) ---
execute("CREATE INDEX IF NOT EXISTS book_publication_date_idx ON book (publication_date)")
execute("CLUSTER book USING book_publication_date_idx")  # rewrites heap; one-time

# --- 4. Declarative partitioning (PG 10+): pruning + cheap data lifecycle ---
execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")

# Parent: PARTITION BY RANGE/LIST/HASH. PK/UNIQUE MUST include the partition key.
execute("""
CREATE TABLE booking (
    booking_id  UUID DEFAULT uuid_generate_v4(),
    customer_id UUID NOT NULL,
    amount      NUMERIC(10,2) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (booking_id, created_at)
) PARTITION BY RANGE (created_at)
""")

execute("CREATE TABLE booking_y2024 PARTITION OF booking "
        "FOR VALUES FROM ('2024-01-01') TO ('2025-01-01')")
execute("CREATE TABLE booking_y2025 PARTITION OF booking "
        "FOR VALUES FROM ('2025-01-01') TO ('2026-01-01')")

# Index on parent -> one index per partition automatically (smaller, hotter in cache).
execute("CREATE INDEX idx_booking_created_at ON booking (created_at)")

# Data lifecycle win: retire a range by DROPping a partition (instant, no dead tuples),
# instead of a slow DELETE that just leaves bloat for VACUUM to chase.
execute("DROP TABLE booking_y2024")

# Verify pruning: predicate hits one range -> only that partition is scanned.
for r in query("SHOW enable_partition_pruning"):  # must be 'on' (default)
    print(r)
for r in query("""  -- plan should name ONLY booking_y2025; others absent entirely
    EXPLAIN
    SELECT date_trunc('month', created_at) AS m, COUNT(*)
      FROM booking WHERE created_at >= '2025-01-01' GROUP BY m
"""):
    print(r["QUERY PLAN"])
```

## Gotchas

- **ANALYZE after every big load.** Bulk `INSERT`/`COPY` outrun autovacuum's threshold (`autovacuum_analyze_scale_factor * rows + autovacuum_analyze_threshold`); until `ANALYZE` runs the planner trusts stale row counts and picks `Seq Scan` / wrong join order. Autovacuum is on by default — never disable it, tune the scale factor for huge tables.
- **VACUUM FULL is expensive.** It rewrites the whole table under `ACCESS EXCLUSIVE` lock (no reads, no writes) and needs free disk equal to the table size. Plain `VACUUM` does not shrink the file, only marks space reusable — pair with `pg_repack` to reclaim space without the hard lock.
- **CLUSTER is one-time and locks the table.** It rewrites the whole heap — run off-hours on large tables, with enough free disk for a second copy. Subsequent INSERTs/UPDATEs do NOT stay clustered; re-run as needed. Effect also decays as new rows append out of order.
- **Partition key must be in every PK/UNIQUE.** `PRIMARY KEY (booking_id, created_at)` — not just `booking_id` — because PG enforces uniqueness only per-partition. A global unique constraint is impossible declaratively; enforce one via a trigger or separate lookup table.
- **Over-indexing write cost.** Each non-unique index adds an entry per INSERT and per UPDATE of an indexed column; 8 indexes ≈ 8x write amplification. Drop `idx_scan = 0` indexes found above; prefer one well-ordered multicolumn index over several single-column ones.
- **Partition only pays when the table exceeds memory.** Splitting a 1GB table into 10 partitions adds per-partition planning/index overhead for no benefit; the win kicks in when scans stop fitting `shared_buffers` and pruning eliminates most partitions.
- **Pruning needs a value the planner can see.** `WHERE created_at = NOW()` prunes only at execution time (runtime pruning — look for `Subplans Removed` in EXPLAIN); a predicate wrapped in a non-foldable function defeats pruning entirely.
