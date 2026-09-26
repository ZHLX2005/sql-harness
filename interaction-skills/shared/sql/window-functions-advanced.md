# Window functions — function catalog

Read when the basic `ROW_NUMBER() OVER (PARTITION BY g ORDER BY k)` pattern isn't enough — i.e. navigation (previous/next rows), running aggregates with N-row frames, ranking with ties, or first/last-by-partition.

## Ranking with ties

`RANK() OVER (ORDER BY k DESC)` shares a rank on ties; `DENSE_RANK()` does not skip a number after a tie:

```python
query("""
  SELECT facid, SUM(slots) AS total,
         RANK() OVER (ORDER BY SUM(slots) DESC) AS rk
    FROM cd.bookings GROUP BY facid
""")
```

**`RANK()` vs `DENSE_RANK()` vs `ROW_NUMBER()`** — pick deliberately:

- `ROW_NUMBER()` — unique 1..N (ties broken arbitrarily).
- `RANK()` — ties share a rank; next rank skips. Two players tied for 2nd → `1,2,2,4`.
- `DENSE_RANK()` — ties share a rank; next rank is sequential. Two players tied for 2nd → `1,2,2,3`.

## Top-N per group

Compute the rank in a subquery, then `WHERE rank <= N`:

```python
query("""
  SELECT name, rank FROM (
    SELECT f.name,
           RANK() OVER (ORDER BY SUM(CASE WHEN b.memid = 0
                                          THEN b.slots * f.guestcost
                                          ELSE b.slots * f.membercost
                                     END) DESC) AS rank
      FROM cd.bookings b
      JOIN cd.facilities f ON b.facid = f.facid
     GROUP BY f.name
  ) t WHERE rank <= 3 ORDER BY rank
""")
```

## Rolling sum / average

Give the `ORDER BY` an explicit frame. `ROWS BETWEEN 14 PRECEDING AND CURRENT ROW` = this row + 14 before. Window runs AFTER `GROUP BY`, so aggregate per-day revenue first, then average:

```python
query("""
  SELECT d::date,
         AVG(rev) OVER (ORDER BY d ROWS BETWEEN 14 PRECEDING AND CURRENT ROW) AS avgrev
    FROM (
      SELECT CAST(b.starttime AS date) AS d,
             SUM(CASE WHEN b.memid = 0 THEN b.slots * f.guestcost
                      ELSE b.slots * f.membercost END) AS rev
        FROM cd.bookings b
        JOIN cd.facilities f ON b.facid = f.facid
       GROUP BY CAST(b.starttime AS date)
    ) days
""")
```

## FIRST_VALUE / LAST_VALUE

Return another row from the same partition. Earliest booking per facility:

```python
query("""
  SELECT DISTINCT facid,
         FIRST_VALUE(starttime) OVER (PARTITION BY facid ORDER BY starttime) AS first_booked
    FROM cd.bookings
""")
```

## LAG / LEAD

Compare to the previous/next row in the ordered partition:

```python
query("""
  SELECT starttime, slots,
         LAG(slots)    OVER (ORDER BY starttime) AS prev_slots,
         slots - LAG(slots) OVER (ORDER BY starttime) AS delta
    FROM cd.bookings WHERE memid = 28 ORDER BY starttime
""")
```

## Gotchas

- **MySQL < 8.0 has no window functions** — must rewrite with `GROUP BY` + self-join, or upgrade. MySQL 8.0+ matches PG; prefer `ROWS` over `RANGE` for frames (RANGE on ties is implementation-defined).
- **Window runs after `GROUP BY`.** Put grouping keys in `PARTITION BY`, not in `OVER()`. Aggregates inside `OVER()` see the post-`GROUP BY` rows, not raw rows.
- **`LAST_VALUE`** with default frame returns the running last (not the partition's actual last). Add `ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING` to see the true tail.
- **`LAG(col)` on the first row is NULL**, not the first value — handle the null or seed with `LAG(col, 1, 0)`.
