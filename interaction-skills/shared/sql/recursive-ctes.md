# Recursive CTEs

Use `WITH RECURSIVE` to walk a self-referential table to arbitrary depth (employee chains, org trees, parent/child taxonomies). Works in PostgreSQL and MySQL 8+ — both require the `RECURSIVE` keyword.

> More patterns (walk-down trees, parameterized traversal, depth tracking): see `references/recursive-ctes-patterns.md`.

## Detection

- Self-FK (column references PK of the same table) — `cd.members.recommendedby -> cd.members.memid`.
- Need rows at unknown depth up to N levels.
- "All ancestors of X" / "all descendants of X" / "flatten a tree into a member×ancestor table."

## Approach

Three-part anatomy: **anchor SELECT (initial row(s)) + `UNION ALL` + recursive SELECT (joins CTE to itself) + termination `WHERE` inside the recursive SELECT.**

### Walk UP: upward recommendation chain for member 27

```python
rows = query("""
    with recursive recommenders(recommender) as (
        select recommendedby from cd.members where memid = 27
        union all
        select mems.recommendedby
            from recommenders recs
            inner join cd.members mems on mems.memid = recs.recommender
            where mems.recommendedby is not null
    )
    select recs.recommender, mems.firstname, mems.surname
        from recommenders recs
        inner join cd.members mems on recs.recommender = mems.memid
    order by recs.recommender desc
""")
```

## Gotchas

- Termination: the recursive SELECT must produce an empty row-set eventually. For UP-walks, guard `where mems.recommendedby is not null` (NULL halts recursion).
- `UNION` deduplicates (extra sort + cost). Prefer `UNION ALL` unless you need dedup; `recommenders(recommender, member)` style can otherwise drop valid chains.
- Cycle safety: real schemas may have cycles. Add a `depth < N` guard OR a visited-set column. Without it, a corrupted FK loops forever.
- LIMIT outside the CTE stops output but the CTE still runs to completion — put the cap *inside* the recursive SELECT to short-circuit.
- Recursive CTEs are not magic; the planner materializes each iteration. For deep/wide trees, also expect `LIMIT` on outer SELECT to be cheap.
- Cross-DB: PG and MySQL 8+ both require `RECURSIVE`. SQLite needs `WITH RECURSIVE`. SQL Server uses just `WITH` (the `RECURSIVE` keyword is reserved-but-accepted). Don't drop the keyword "just to be safe."
- CTE visibility: the recursive SELECT only sees rows added by the previous iteration, not the whole CTE mid-flight.
- vs fixed-depth self-join: if you know the max depth (1 or 2), a plain self-join is cheaper and index-friendly. Reach for recursion at depth 3+ or unknown depth.
