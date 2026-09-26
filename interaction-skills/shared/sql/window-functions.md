# Window functions

Compute per-row values — rank, running totals, previous/next — without collapsing rows like `GROUP BY`. The `OVER (...)` clause runs last, after `WHERE` and aggregation, so it sees the final result set.

> Function catalog (LAG/LEAD, FIRST_VALUE, RANK/DENSE_RANK, rolling frames): see `references/window-functions-catalog.md`.

> Cross-DB: PostgreSQL and MySQL 8.0+ both support `OVER (...)`. MySQL < 8.0 does NOT. Related: `recursive-ctes.md` (when the result feeds a recursive tree), `joins.md` (self-join rewrites that windows replace).

## Detection

- "Per-row aggregate that keeps other columns" — `row_number`, `rank`, running sum, prev/next value.
- "Top N per group", "first/last per group", "number rows 1..N by some key".
- Replacing a self-join or correlated subquery that pulled an aggregate alongside the row.

## Approach

```python
use_workspace("local_pg")
```

**Numbering within groups** — `ROW_NUMBER() OVER (PARTITION BY g ORDER BY k)`. First booking per member from `cd.bookings`:

```python
rows = query("""
  SELECT memid, starttime FROM (
    SELECT memid, starttime,
           ROW_NUMBER() OVER (PARTITION BY memid ORDER BY starttime) AS rn
      FROM cd.bookings
  ) t WHERE rn = 1
""")
```

## Gotchas

- **MySQL < 8.0 has no window functions** — must rewrite with `GROUP BY` + self-join, or upgrade.
- **Window functions are NOT allowed in `WHERE`.** Wrap in a subquery / CTE and filter the alias: `WHERE rn <= 3` must reference the outer query.
- **Default frame with `ORDER BY` is `RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`.** On a `k` with ties this lumps all peers together — always write `ROWS BETWEEN N PRECEDING AND CURRENT ROW` when you want "physical N preceding".
- **`RANK()` vs `DENSE_RANK()`** — pick deliberately. Two players tied for 2nd: `RANK` gives 1,2,2,**4**; `DENSE_RANK` gives 1,2,2,**3**.
