# Joins — advanced patterns

Read when the basic `JOIN ... ON` pattern alone isn't enough — i.e. 3+ tables, self-referential tables, or correlated subqueries you suspect should be joins.

## Self-join (members ⨝ members)

When a column references the same table (`recommendedby` → `memid`), alias it twice and join to itself:

```python
# Members who have recommended someone (no duplicates)
query("""
    SELECT DISTINCT recs.firstname, recs.surname
      FROM cd.members mems
      JOIN cd.members recs ON recs.memid = mems.recommendedby
     ORDER BY recs.surname, recs.firstname
""")
```

## Multi-table join (3+ tables)

Joins are LEFT-associative; each join's output becomes the next join's LEFT input:

```python
# Members who used a tennis court, with full name
query("""
    SELECT DISTINCT mems.firstname || ' ' || mems.surname AS member,
                    facs.name AS facility
      FROM cd.members mems
      JOIN cd.bookings bks ON mems.memid = bks.memid
      JOIN cd.facilities facs ON bks.facid = facs.facid
     WHERE bks.facid IN (0, 1)
     ORDER BY member
""")
```

`||` is PostgreSQL concat; MySQL needs `CONCAT(a, ' ', b)`.

## JOIN vs subquery rewrite

Same answer, two paths — pick by readability:

```python
# Correlated subquery (one lookup per outer row)
query("""
    SELECT DISTINCT mems.firstname || ' ' || mems.surname AS member,
           (SELECT recs.firstname || ' ' || recs.surname
              FROM cd.members recs WHERE recs.memid = mems.recommendedby) AS recommender
      FROM cd.members mems
""")

# Same result via LEFT JOIN — usually clearer and faster
query("""
    SELECT DISTINCT mems.firstname || ' ' || mems.surname AS member,
           recs.firstname || ' ' || recs.surname AS recommender
      FROM cd.members mems
      LEFT JOIN cd.members recs ON recs.memid = mems.recommendedby
""")
```

Rule of thumb: if the subquery references the outer query (`WHERE x = mems.foo`), a JOIN is almost always cleaner. Use a subquery in `FROM` (inline view) when you want to filter on a derived column before the outer `WHERE`.
