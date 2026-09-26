# Aggregations (GROUP BY / HAVING)

Aggregate functions (`COUNT`, `SUM`, `AVG`, `MIN`, `MAX`) collapse many rows into one. `GROUP BY` partitions rows into buckets so the aggregate is computed per bucket. `HAVING` filters the bucket results after aggregation. Works the same on PostgreSQL + MySQL.

> Advanced (conditional aggregates, GROUPING SETS, ROLLUP/CUBE, bucket binning): see `references/aggregations-advanced.md`.

## Detection

- "How many...", "total...", "per X" (group), "top N by aggregate", or "only show groups where..." (HAVING).
- Need a scalar from many rows (`COUNT(*)`) or one row per category (`GROUP BY`).
- Need to filter on the *result* of an aggregate — that's HAVING, not WHERE.

## Approach

Pick the smallest thing that works; escalate only if you need it.

```python
# Scalar aggregate over the whole table
rows = query("SELECT count(*) FROM cd.facilities")
# => [{'count': 9}]

# COUNT(*) vs COUNT(col) vs COUNT(DISTINCT col)
#   count(*)            -> rows (NULLs included)
#   count(col)          -> non-NULL values of col
#   count(distinct col) -> distinct non-NULL values
rows = query("SELECT count(*) AS rows, count(guestcost) AS priced, count(distinct name) AS uniq FROM cd.facilities")

# GROUP BY one key
rows = query("""
    SELECT facid, sum(slots) AS total_slots
    FROM cd.bookings
    GROUP BY facid
    ORDER BY facid
""")

# WHERE filters rows before aggregation; HAVING filters groups after it.
# Find facilities with > 1000 slots booked:
rows = query("""
    SELECT facid, sum(slots) AS total_slots
    FROM cd.bookings
    GROUP BY facid
    HAVING sum(slots) > 1000
    ORDER BY facid
""")
```

### Post-aggregation filtering

When `HAVING` would force you to repeat a big expression, wrap the inner query and filter with `WHERE`:

```python
# "revenue < 1000" — alias not usable in HAVING on PG strict mode.
# Either repeat the sum() in HAVING, or wrap in a subquery:
rows = query("""
    SELECT name, revenue FROM (
        SELECT facs.name,
               sum(CASE WHEN memid = 0 THEN slots*facs.guestcost
                        ELSE slots*facs.membercost END) AS revenue
        FROM cd.bookings bks
        INNER JOIN cd.facilities facs ON bks.facid = facs.facid
        GROUP BY facs.name
    ) AS agg
    WHERE revenue < 1000
    ORDER BY revenue
""")
```

## Gotchas

- **PG strict GROUP BY.** Every non-aggregated column in `SELECT` must appear in `GROUP BY`. `SELECT facid, count(*) FROM cd.facilities` errors out — DB doesn't know which `facid` to pair with the count. MySQL with `ONLY_FULL_GROUP_BY` off used to be lenient (returned an arbitrary value); treat that as a bug-magnet, not a feature — write the `GROUP BY` explicitly.
- **Aliases aren't visible in HAVING on PG.** `HAVING revenue < 1000` fails with `column "revenue" does not exist`. Repeat the expression or wrap in a subquery. MySQL accepts the alias; portability still favors the subquery form.
- **`COUNT(*)` counts all rows; `COUNT(col)` ignores NULLs.** Use `COUNT(*)` for "rows in a group"; use `COUNT(col)` for "rows with a value".
- **HAVING runs AFTER aggregation; WHERE runs BEFORE.** If the predicate references an aggregate, it belongs in HAVING. Don't try `WHERE count(*) > 5` — it errors. If it filters rows, it belongs in WHERE — pushing row filters into HAVING forces them to be re-checked per group.
- **`GROUP BY` extra columns for portability.** When grouping by a 1:1 pair (e.g. `facid, name` where `facid` is the PK), PG infers the redundancy; MySQL often doesn't. Group on every non-aggregated column you `SELECT`.
