# Aggregations — advanced patterns

Read when the simple `SUM/COUNT/AVG(col)` shape isn't enough — i.e. conditional sums (`SUM(CASE WHEN)`), per-month / per-year bucketing, or subtotals + grand totals (GROUPING SETS / ROLLUP / CUBE).

## Conditional aggregate (`SUM(CASE WHEN ...)`)

"count(*) WHERE predicate" / "sum(col) for one side only" via `SUM(CASE WHEN condition THEN value ELSE 0 END)`:

```python
rows = query("""
    SELECT facs.name,
           sum(CASE WHEN memid = 0 THEN slots*facs.guestcost
                    ELSE slots*facs.membercost END) AS revenue
    FROM cd.bookings bks
    INNER JOIN cd.facilities facs ON bks.facid = facs.facid
    GROUP BY facs.name
    ORDER BY revenue
""")
```

`SUM(CASE WHEN ... THEN 1 ELSE 0 END)` is the portable "conditional count". `COUNT_IF` (MySQL 8 / PG 16+) and `FILTER (WHERE ...)` (PG) are nicer but less portable — stick with the `CASE` form unless the target is fixed.

## GROUP BY multiple keys

Multiple `GROUP BY` keys = one row per **combination**. Bucket a timestamp with `EXTRACT(month from ...)` (or `DATE_TRUNC('month', ...)` for year-aware buckets):

```python
# Per-facility per-month totals
rows = query("""
    SELECT facid,
           extract(month FROM starttime) AS month,
           sum(slots) AS total_slots
    FROM cd.bookings
    WHERE starttime >= '2012-01-01' AND starttime < '2013-01-01'
    GROUP BY facid, month
    ORDER BY facid, month
""")
```

`DATE_TRUNC('month', starttime)` is the safer alternative when the same month can recur across years (Jan 2023 vs Jan 2024). **`EXTRACT(month FROM ts)` drops the year** — fine inside a year-bounded query, wrong if the range crosses years.

## Integer division portability

`slots / 2` truncates on PG; `slots / 2.0` doesn't. MySQL auto-casts. For hours-from-half-hour-slots, write the divisor as `2.0` for portability.

## GROUPING SETS / ROLLUP / CUBE (PG + MySQL 8.0+)

Subtotals + grand totals in one pass:

- `GROUPING SETS ((a), (b), ())` — explicit combinations.
- `ROLLUP (a, b)` — hierarchical subtotals down the chain + grand total.
- `CUBE (a, b)` — every combination + grand total.

On PG these return NULL for the rolled-up key; `GROUPING(col)` returns 1 for that NULL. Use `COALESCE(col, 'Total')` to label.

```sql
-- Per-facility per-month + grand total in one shot:
SELECT facid,
       date_trunc('month', starttime) AS month,
       sum(slots) AS total
  FROM cd.bookings
 GROUP BY ROLLUP (facid, month)
 ORDER BY facid NULLS LAST, month NULLS LAST;
```
