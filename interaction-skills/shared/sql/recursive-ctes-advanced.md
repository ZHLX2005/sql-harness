# Recursive CTEs — pattern catalog

Read when the canonical anchor + UNION ALL + recursive SELECT + termination WHERE isn't the right shape — i.e. you need to descend (find descendants), parameterize across many IDs, or carry a depth counter.

## Walk DOWN: descendants of member 1

```python
rows = query("""
    with recursive recommendeds(memid) as (
        select memid from cd.members where recommendedby = 1
        union all
        select mems.memid
            from recommendeds recs
            inner join cd.members mems on mems.recommendedby = recs.memid
    )
    select recs.memid, mems.firstname, mems.surname
        from recommendeds recs
        inner join cd.members mems on recs.memid = mems.memid
    order by recs.memid
""")
```

## Parametric CTE: chain for any member, flatten across a set

```python
rows = query("""
    with recursive recommenders(recommender, member) as (
        select recommendedby, memid from cd.members
        union all
        select mems.recommendedby, recs.member
            from recommenders recs
            inner join cd.members mems on mems.memid = recs.recommender
            where mems.recommendedby is not null
    )
    select recs.member, recs.recommender, mems.firstname, mems.surname
        from recommenders recs
        inner join cd.members mems on recs.recommender = mems.memid
        where recs.member in (12, 22)
    order by recs.member asc, recs.recommender desc
""")
```

## Depth tracking (carry a depth column)

```python
rows = query("""
    with recursive chain(memid, recommender, depth) as (
        select memid, recommendedby, 0 from cd.members where memid = 27
        union all
        select recs.memid, mems.recommendedby, recs.depth + 1
            from chain recs
            inner join cd.members mems on mems.memid = recs.recommender
            where recs.depth < 10
    )
    select depth, memid, recommender from chain
""")
```

## Performance note

Recursive CTEs are not magic; the planner materializes each iteration. For deep/wide trees, also expect `LIMIT` on outer SELECT to be cheap.
