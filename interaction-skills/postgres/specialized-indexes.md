# Specialized PostgreSQL Indexes

Interaction skill for choosing and verifying the indexes a plain B-Tree cannot replace: Unique (duplicates + `ON CONFLICT` upserts), Partial (`WHERE`), Expression (`lower()`), GIN (trigram `LIKE`, jsonb, arrays), Hash (equality only), plus a plain index on the **referencing side of a FK**. Each targets a predicate or workload a B-Tree handles poorly or not at all.

## Detection

```python
use_workspace("pg")
list_tables()
describe("book")

# Which indexes exist? Spot UNIQUE, WHERE, expression, USING gin|hash
query("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'book'")

# Are they used? idx_scan = 0 -> dead weight (reset stats first)
query("SELECT indexrelname, idx_scan FROM pg_stat_user_indexes WHERE relname = 'book'")

# Find unindexed FKs: a FK does NOT auto-create an index on the referencing
# column. Returns one row per missing index.
query("""
SELECT c.conname, c.conrelid::regclass AS tbl, c.confrelid::regclass AS ref,
       ARRAY_AGG(a.attname) AS cols
  FROM pg_constraint c
  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
 WHERE c.contype = 'f'
   AND NOT EXISTS (SELECT 1 FROM pg_index i
                    WHERE i.indrelid = c.conrelid
                      AND c.conkey <@ STRING_TO_ARRAY(i.indkey::text, ' ')::SMALLINT[])
 GROUP BY c.conname, c.conrelid, c.confrelid
""")

# Confirm a planner choice by index name, before vs after creating
[print(r["QUERY PLAN"]) for r in explain("SELECT isbn FROM book WHERE title LIKE 'Patterns%'")]
```

## Approach

```python
use_workspace("pg")

# --- Unique: UNIQUE/PK constraints auto-create a B-Tree unique index (_key /
# _pkey suffix), so hand-writing CREATE UNIQUE INDEX is rare. The reason to:
# you need a unique arbiter for ON CONFLICT on a column with no constraint.
execute("CREATE UNIQUE INDEX IF NOT EXISTS book_isbn_key ON book (isbn)")
# isbn already has a UNIQUE constraint -> ON CONFLICT (isbn) arbitrates on it.
query("INSERT INTO book (isbn, title, publication_date) "
      "VALUES ('978-1449373320','X','2020-01-01') "
      "ON CONFLICT (isbn) DO NOTHING")

# --- FK referencing column: a FK does NOT auto-index it. Joins and cascading
# DELETE/UPDATE on the parent seq-scan the child until you add an index.
execute("CREATE INDEX idx_fk_book_publisher ON book (publisher_id)")

# --- Partial: index only hot rows. Query predicate MUST logically imply the
# index predicate or the planner skips it.
execute("CREATE INDEX idx_book_high_rating ON book (publication_date) WHERE rating >= 4")

# --- Expression: index the function result; query must use the IDENTICAL expr.
execute("CREATE INDEX idx_book_lower_title ON book (LOWER(title))")

# --- GIN trigram: accelerate LIKE/ILIKE on text. Extension first, then opclass.
execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
execute("CREATE INDEX idx_book_title_trgm ON book USING gin (title gin_trgm_ops)")

# --- Hash: equality-only, smaller than B-Tree on very large sets.
execute("CREATE INDEX idx_book_title_hash ON book USING HASH (title)")

# Verify each plan. explain() runs plain EXPLAIN -> one "QUERY PLAN" line per row.
def plan(sql):
    for r in explain(sql):
        print(r["QUERY PLAN"])

plan("SELECT isbn FROM book WHERE isbn = '978-1449373320'")                                   # unique
plan("SELECT isbn FROM book WHERE publication_date > '2019-01-01' AND rating >= 4")          # partial USED
plan("SELECT isbn FROM book WHERE publication_date > '2019-01-01' AND rating > 3")           # partial SKIPPED
plan("SELECT isbn FROM book WHERE LOWER(title) = 'patterns of enterprise application architecture'")  # expression
plan("SELECT isbn FROM book WHERE title LIKE 'Patterns%'")                                   # gin trigram
plan("SELECT isbn FROM book WHERE title = 'Patterns of Enterprise Application Architecture'")  # hash
```

What each plan signals:
- **Unique** -> `Index Scan using book_isbn_key`; uniqueness is enforcement, not a different scan type. This same index is what `ON CONFLICT (isbn)` locks against.
- **FK index** -> without `idx_fk_book_publisher` the join plan is `Hash Join` + `Seq Scan on book`; after, it becomes a `Nested Loop` with `Index Scan using idx_fk_book_publisher` driven per parent row.
- **Partial** -> plan names the partial index (`Bitmap Index Scan on idx_book_high_rating`); cond lists only the indexed column — the `rating >= 4` predicate is proven by the index definition, not re-checked.
- **Expression** -> `Bitmap Index Scan on idx_book_lower_title`, cond is `(lower((title)::text) = ...)`. The function call appearing literally in the cond is the match signal.
- **GIN (trigram)** -> `Bitmap Index Scan on idx_book_title_trgm`, cond uses `~~` (the LIKE operator). Often shows `Rows Removed by Index Recheck` — GIN is lossy and rechecks heap rows.
- **Hash** -> `Index Scan using idx_book_title_hash`. Looks like a B-Tree scan; confirm via `pg_indexes.indexdef` (`USING hash`).

## Gotchas

- **FK != indexed.** Declaring `REFERENCES` creates the constraint but no index on the referencing column. Joins and cascading `DELETE`/`UPDATE` on the parent seq-scan the child until you add one. Exceptions: tiny child tables (seq scan is cheaper anyway), or you never join/cascade on the key.
- **Unique as concurrency guard.** `INSERT ... ON CONFLICT (col) DO ...` needs a unique index on `col` to arbitrate; without one PG errors. A `UNIQUE` constraint already provides it — the manual `CREATE UNIQUE INDEX` is for upserts on a non-constraint column.
- **Partial needs logical implication.** Index `WHERE rating >= 4` serves `rating >= 4` or `rating > 5` (all result rows qualify), but NOT `rating > 3` (selects rows in (3,4) never indexed) — planner falls back. Partial also saves space + writes: only qualifying rows are tracked.
- **Expression index needs the identical expression.** An index on `LOWER(title)` is invisible to `WHERE title = '...'` or `WHERE title ILIKE '...'`. Match function, argument, and collation exactly.
- **GIN is for multi-value, not scalar equality.** GIN wins on arrays/jsonb/tsvector and trigram `LIKE`; for plain `=` on a scalar, B-Tree beats it (Index Only Scan + uniqueness). GIN is bigger and slower to update — one index entry per component value — and lossy, so expect heap rechecks.
- **Hash is equality-only, never unique, WAL-logged only since PG10.** No `>`, `<`, `BETWEEN`, `ORDER BY`; cannot back PK/UNIQUE. Pre-PG10 hash indexes were crash-unsafe (unlogged) — the reason old advice said to avoid them.
- **Unique implies B-Tree.** Only B-Tree can be unique; `UNIQUE`/`PRIMARY KEY` auto-creates it, so hand-writing `CREATE UNIQUE INDEX` is mainly for `ON CONFLICT` arbiters on a non-constraint column.
