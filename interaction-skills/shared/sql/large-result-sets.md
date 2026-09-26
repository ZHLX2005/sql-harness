# Large result sets

`query()` loads every row into memory. For >10k rows, stream.

## Detection
- Result set expected to exceed ~10,000 rows.
- Latency-sensitive pagination.

## Approach

```python
use_workspace("local_pg")
ws = current_workspace()
with ws.engine.connect().execution_options(stream_results=True) as conn:
    result = conn.execute(text("SELECT id, payload FROM big_table WHERE created_at > :t"),
                          {"t": "2024-01-01"})
    for row in result:
        process(row)
```

`stream_results=True` uses server-side cursors (PostgreSQL) / unbuffered cursors (MySQL). The connection stays checked out for the duration — pair with a transaction if you need isolation.

## Gotchas

- A streamed result holds the connection until fully consumed. Don't loop and `query()` mid-stream.
- On PostgreSQL, the cursor needs a transaction (`with conn.begin():` or `with with_transaction():`).
- On MySQL, unbuffered queries can't run additional queries on the same connection until consumed.
- For aggregation, prefer running the aggregation server-side (e.g. `SELECT count(*) ...`) over streaming.