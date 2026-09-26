# Joins

Combine rows across tables on a related column. Reach for this when a question needs data from two or more tables together (who booked what, who recommended whom).

> Advanced patterns (correlated subquery → join, EXISTS vs IN, 3+ tables): see `references/joins-advanced.md`.

## Detection

- Question references fields from >1 table (member name + booking time).
- One table has a foreign key into another (`bookings.memid` → `members.memid`).
- Need "rows with NO match" → `LEFT JOIN` + `IS NULL` check.
- Same result expressible via subquery: pick whichever reads cleaner.

## Join types (cross-DB)

| Type                   | Returns                                              |
| ---------------------- | ---------------------------------------------------- |
| `INNER JOIN`           | Rows where `ON` matches in BOTH tables                |
| `LEFT [OUTER] JOIN`    | All left rows; right columns `NULL` when no match    |
| `RIGHT [OUTER] JOIN`   | Mirror of LEFT (rare — rewrite as LEFT, swap sides)  |

- `LEFT OUTER JOIN` ≡ `LEFT JOIN`; `OUTER` is noise but legal in both PG and MySQL.
- MySQL `RIGHT JOIN` works but rewriting as `LEFT JOIN` (swap sides) is easier to read.

## Inner join basics

Club schema: `cd.members (memid, firstname, surname, recommendedby)`, `cd.bookings (memid, facid, starttime, slots)`, `cd.facilities (facid, name, membercost, guestcost)`.

```python
use_workspace("local_pg")
# David's bookings
query("""
    SELECT bks.starttime
      FROM cd.bookings bks
      JOIN cd.members mems ON mems.memid = bks.memid
     WHERE mems.firstname = 'David' AND mems.surname = 'Farrell'
""")
```

## LEFT JOIN to find rows with NO match

`INNER JOIN` drops unmatched rows. To find the OPPOSITE (members who never booked), use `LEFT JOIN` + `WHERE right.id IS NULL`:

```python
# All members with their recommender (NULL if none)
query("""
    SELECT mems.firstname AS memfname, mems.surname AS memsname,
           recs.firstname AS recfname, recs.surname AS recsname
      FROM cd.members mems
      LEFT JOIN cd.members recs ON recs.memid = mems.recommendedby
     ORDER BY mems.surname, mems.firstname
""")

# Members who have NOT booked anything
query("""
    SELECT mems.firstname, mems.surname
      FROM cd.members mems
      LEFT JOIN cd.bookings bks ON bks.memid = mems.memid
     WHERE bks.memid IS NULL
""")
```

## Gotchas

- Always alias tables — self-joins REQUIRE it, multi-joins benefit.
- `LEFT JOIN ... WHERE right.col = X` turns the LEFT back into an inner join (the `WHERE` runs after). Move predicates into `ON`.
- `SELECT *` on joined tables returns ambiguous columns; project only what you need.
- An unfiltered `LEFT JOIN` to a 1-to-many table explodes row counts — `DISTINCT` or aggregate AFTER the join, never before.
- MySQL pre-8 has no CTEs — subqueries in `FROM` still work but are uglier than `WITH ... AS`.
