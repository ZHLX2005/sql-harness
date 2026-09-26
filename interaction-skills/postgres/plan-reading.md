# Plan Reading & Index Selectivity

PG's planner picks Seq Scan vs Index Scan from a cost model fed by table stats and predicate selectivity. Before adding an index, read the EXPLAIN plan: decode the node line, compare estimated vs actual rows, and spot filters that throw away most of a full scan.

## Detection

Read each node line in two halves — the estimate half (always present) and the actual half (only with `ANALYZE`):

- `(cost=0.00..3917.09 rows=27 width=57)` — planner estimate: `startup..total` cost (arbitrary units, lower node wins), estimated `rows`, avg `width` in bytes.
- `(actual time=0.008..8.575 rows=1 loops=1)` — only from `ANALYZE`: real `startup..total` ms, actual `rows` returned, `loops` (multiply time×loops when loops>1).
- **Estimated rows wildly off actual rows** (e.g. estimated `rows=27` vs actual `rows=1`, or the reverse) → stale stats. Run `ANALYZE book;` and re-plan.
- **Seq Scan GOOD**: small/lookup table, or query returns a large proportion of the table (full sequential read beats random index probes). Startup cost is always `0.00`.
- **Seq Scan BAD**: large table + `Filter: (col = ...)` + `Rows Removed by Filter: 100006` to keep ~1 → that filter wants an index. Scanning the whole heap to return <15% of rows is the classic miss.
- **Index Scan** (`Index Cond: (col = ...)`): descends the B-Tree, fetches heap rows by TID (random I/O). Chosen when the indexed predicate is selective enough that random access beats a full sequential read.
- **Bitmap Index Scan + Bitmap Heap Scan**: two-node plan — index builds an in-memory TID bitmap, heap reads those pages sequentially. Bridges moderate selectivity (poor-ish column, but manageable match count). If `Heap Blocks: exact=N` approaches total table blocks, it's basically full-scanning and the index barely helps.
- **Index Scan with `Rows Removed by Filter` >> matched rows** → index covers the seek column but not the filter; build a multicolumn index.
- High `seq_tup_read` / `seq_scan` in `pg_stat_user_tables` → tables routinely full-scanned, candidates for indexing.

## Approach

```python
use_workspace("local_pg")

# 1. EXPLAIN = planner estimate only (never executes).
#    EXPLAIN ANALYZE also RUNS the query → adds actual time/rows/loops + Planning/Execution Time.
#    EXPLAIN (ANALYZE, BUFFERS) adds Shared Blocks hit/read (cache vs disk). FORMAT JSON for tooling.
#    ANALYZE has side effects on writes → use plain EXPLAIN or BEGIN; ... ; ROLLBACK.
for r in query("EXPLAIN (ANALYZE, BUFFERS) SELECT isbn, title, publication_date, rating FROM book WHERE publication_date = '1994-11-10'"):
    print(r["QUERY PLAN"])  # Seq Scan + Filter + Rows Removed by Filter: 100006 → index this

# 2. Selectivity = COUNT(DISTINCT col) / COUNT(*). >0.85 favors Index Scan;
#    <~0.05 and a single value matches >15% of rows → Seq Scan.
for r in query("""
    SELECT 'rating'          AS col, ROUND(COUNT(DISTINCT rating)::NUMERIC / COUNT(*), 2) FROM book
    UNION ALL SELECT 'publication_date', ROUND(COUNT(DISTINCT publication_date)::NUMERIC / COUNT(*), 2) FROM book
    UNION ALL SELECT 'isbn',             ROUND(COUNT(DISTINCT isbn)::NUMERIC / COUNT(*), 2) FROM book
"""):
    print(r)

# 3. Low selectivity + wide range (>15% of rows) → Seq Scan;
#    narrow range (small %) → Bitmap Scan once an index exists.
execute("CREATE INDEX idx_book_pub_date ON book (publication_date)")
for r in explain("SELECT isbn, publication_date FROM book WHERE publication_date > '2019-01-01'"):
    print(r["QUERY PLAN"])

# 4. High-selectivity column → Index Scan.
execute("CREATE INDEX idx_book_title ON book (title)")
for r in explain("SELECT isbn, title FROM book WHERE title = 'Patterns of Enterprise Application Architecture'"):
    print(r["QUERY PLAN"])

# 5. Tables needing indexes, ranked by full-scan traffic.
for r in query("""
    SELECT relname, seq_scan, seq_tup_read, idx_scan, idx_tup_fetch
      FROM pg_stat_user_tables
     WHERE seq_scan > 0
     ORDER BY seq_tup_read DESC
"""):
    print(r)
```

## Gotchas

- **`ANALYZE` drives the estimates**: stale stats → wrong scan choice AND a `rows=` estimate that diverges from actual. Re-run `ANALYZE` after bulk loads; `ANALYZE book` refreshes one table.
- **Selectivity is per-value, not per-query**: `rating` (~0.04, 5 distinct values) returns ~20% of rows for any single value → Seq Scan. The same low-selectivity column for a rare value would suit an index, but PG plans off the column's MCV stats, not the specific constant — so `rows=` can still mispredict per-value (the estimated `rows=27` vs actual `rows=1` case).
- **`EXPLAIN ANALYZE` has side effects**: it runs the statement, so `UPDATE`/`DELETE`/`INSERT`/`MERGE` actually mutate data. Use plain `EXPLAIN` or wrap in `BEGIN; ... ROLLBACK;`.
- **`BUFFERS` needs `ANALYZE`**: `EXPLAIN (ANALYZE, BUFFERS)` prints `Shared Blocks: hit=X read=Y` — `hit` is cache, `read` is disk. High `read` on a repeated query → cold cache or working set larger than RAM.
- **Cost params default to HDD**: `random_page_cost=4` vs `seq_page_cost=1`. On SSDs lower `random_page_cost` to ~1.1 so PG picks Index Scans more readily.
- **`pg_stat_user_tables` resets on restart**: capture a baseline after warmup, not right after reboot. `SELECT pg_stat_reset();` zeroes counters on demand.