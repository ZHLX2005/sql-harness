# B-Tree Index Scan Types

How PG's planner picks between Index Scan, Bitmap Heap Scan, and Index Only Scan — and how to nudge it toward the cheapest one. The choice is driven by **selectivity** (distinct values / total rows), so measure it before indexing.

## Approach

```python
use_workspace("local_pg")

# 0. Measure selectivity BEFORE indexing. Index wins when a predicate filters
#    out ~90%+ of rows (selectivity near 1). A boolean/status column (~0.5,
#    ~0.1) never meets that bar — skip it; the planner will prefer Seq Scan.
for r in query("""
    SELECT ROUND(COUNT(DISTINCT title)::NUMERIC / COUNT(*), 2) AS sel_title,
           ROUND(COUNT(DISTINCT publication_date)::NUMERIC / COUNT(*), 2) AS sel_pub_date
      FROM book"""):
    print(r)  # title ~1.00 (worth indexing); publication_date ~0.04 (weak alone)

# 1. Single-col B-Tree on a high-selectivity column (title -> selectivity ~1).
execute("CREATE INDEX idx_book_title ON book (title)")

#    Equality on a selective column -> Index Scan (reads index, then heap row).
for r in explain("SELECT isbn, title FROM book WHERE title = 'Patterns of Enterprise Application Architecture'"):
    print(r["QUERY PLAN"])

# 2. Low-selectivity column (publication_date, selectivity ~0.04) returning many rows -> Bitmap.
execute("CREATE INDEX idx_book_pub_date ON book (publication_date)")
for r in query("EXPLAIN ANALYZE SELECT isbn, publication_date FROM book WHERE publication_date > '2019-01-01'"):
    print(r["QUERY PLAN"])

# 3. Multicolumn index: combine two weak columns into a strong one. Each of
#    publication_date and rating is ~0.04 alone, but the PAIR is ~1.0 — one
#    index read beats bitmap-ANDing two separate single-col indexes.
execute("DROP INDEX idx_book_pub_date")
execute("CREATE INDEX idx_book_pub_date_rating ON book (publication_date, rating)")

#    Both predicates hit one index -> Bitmap Heap Scan over a single Bitmap Index Scan.
for r in query("EXPLAIN ANALYZE SELECT isbn FROM book WHERE publication_date > '2019-01-01' AND rating > 4.9"):
    print(r["QUERY PLAN"])

# 4. Index Only Scan: SELECT only indexed columns AND the visibility map is clean.
query("VACUUM book")  # sets all-visible bits -> Heap Fetches: 0
for r in query("EXPLAIN ANALYZE SELECT publication_date, rating FROM book WHERE publication_date = '1994-11-10'"):
    print(r["QUERY PLAN"])

# 5. Index the FK so joins switch from Hash Join to Nested Loop + Index Scan.
execute("CREATE INDEX idx_fk_book_publisher ON book (publisher_id)")
for r in query("EXPLAIN ANALYZE SELECT b.isbn FROM book b JOIN publisher p ON p.publisher_id = b.publisher_id WHERE b.publication_date = '1994-11-10'"):
    print(r["QUERY PLAN"])
```

## What you see

- **Index Scan** — `Index Scan using idx_...`: chosen for selective predicates (rule of thumb: returns <~10–15% of rows, selectivity > ~0.85). Descends the btree, then fetches each matching heap row. Shows `Index Cond` (indexed part) and may add `Filter` / `Rows Removed by Filter` (unindexed part).
- **Bitmap Heap Scan** — always two nodes: `Bitmap Index Scan` builds a bitmap of matching TIDs, `Bitmap Heap Scan` walks each heap page once (no per-row random I/O). Used when many rows match or selectivity is poor (<~0.85). `BitmapAnd` / `BitmapOr` combine multiple indexes. Look for `Recheck Cond` and `Heap Blocks: exact=N`.
- **Index Only Scan** — `Index Only Scan using idx_...`: answers the query from the index alone. Requires (a) every selected column is in the index, and (b) the visibility map marks the relevant pages all-visible so PG can trust the index entries without visiting the heap. Watch `Heap Fetches:` — `0` means truly index-only; a high number means the visibility map is stale and it's secretly reading the heap anyway.

Multicolumn index — the leftmost-prefix rule for `(a, b, c)`:
- **Usable prefixes**: `{a}`, `{a, b}`, `{a, b, c}` all hit the index. `{b}` alone, `{c}` alone, and `{b, c}` do NOT — planner falls back to Seq Scan. Put the filtered / high-selectivity column first.
- **One multicolumn index beats two single-col indexes** when the columns are individually weak: each single index has bad selectivity, so the planner bitmap-ANDs them (two index reads + an AND node). The combined index has good selectivity (the `(publication_date, rating)` pair is ~1.0) — one read, no AND.
- **Two singles win** when each column is already selective on its own and the combined predicate is rare — they're cheaper to maintain and reusable across different predicate combinations.
- Predicate order in `WHERE` is irrelevant — the planner reorders. But `OR` can't use one multicolumn index; each side needs its own index, then bitmap-OR'd.

## Detection

- `EXPLAIN ANALYZE` shows `Seq Scan` on a big table for a selective predicate -> missing (or unusable) index.
- `Index Scan` with a large `Rows Removed by Filter` -> index column doesn't carry the filter; add the filter column to the index (or build a multicolumn index).
- `Index Only Scan` with `Heap Fetches > 0` right after writes -> `VACUUM` the table to refresh the visibility map.
- Joins showing `Hash Join` on a large child table where the parent gets updated/deleted -> missing FK index.
- `COUNT(DISTINCT col)::NUMERIC / COUNT(*)` near 0 (boolean, status, low-cardinality) -> an index won't help; the planner ignores it or pays more than a Seq Scan. Consider a partial index instead.
- Find unindexed FKs directly:
```python
for r in query("""
  SELECT c.conname, c.conrelid::REGCLASS AS tbl, ARRAY_AGG(a.attname) AS cols
    FROM pg_constraint c JOIN pg_attribute a
      ON a.attrelid = c.conrelid AND a.attnum = any(c.conkey)
   WHERE c.contype = 'f'
     AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid
                     AND conkey <@ STRING_TO_ARRAY(i.indkey::text,' ')::SMALLINT[])
   GROUP BY c.conname, c.conrelid, c.confrelid"""):
    print(r)
```

## Gotchas

- **Low-cardinality columns defeat B-Tree**: a boolean (2 values) or status enum has selectivity ~0.5 / ~0.1 — the index returns most of the table, so the planner rightly prefers Seq Scan. Use a **partial** index (`WHERE is_active`) instead, or no index at all.
- **Leftmost prefix**: a multicolumn index `(a, b, c)` is dead weight for `WHERE b = ?`, `WHERE c = ?`, or `WHERE b = ? AND c = ?`. Order columns by selectivity and query usage, filtered-first.
- **Visibility map goes stale on writes**: `UPDATE`/`DELETE` clears all-visible bits, so Index Only Scan degrades to heap fetches until autovacuum (or a manual `VACUUM`) rebuilds it. A query that was fast yesterday can slow down today.
- **Bitmap isn't always a win**: if the bitmap covers most of the table, PG reverts to Seq Scan. Don't index a column with a handful of distinct values just to "help."
- **Each index taxes writes**: `INSERT`/`UPDATE`/`DELETE` must maintain every index. Over-indexing trades read speed for write latency + disk. Drop unused ones (`pg_stat_user_indexes.idx_scan = 0`).
- **`OR` defeats multicolumn indexes**: rewrite as `UNION` of two indexed queries, or give each side its own single-col index and let bitmap-OR combine them.
- **FK indexes aren't automatic**: PG creates indexes for PK/UNIQUE only, never for FK columns. A missing FK index silently forces Seq Scans / Hash Joins on the child table.
