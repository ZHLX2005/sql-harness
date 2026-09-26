# JSON columns — MySQL (`JSON`)

MySQL 5.7+ has a native `JSON` type. PyMySQL serializes Python `dict`/`list` to `JSON`.

## Insert

SQLAlchemy handles the conversion when passing a dict as a parameter:

```python
execute("INSERT INTO events (data) VALUES (:d)", {"d": {"event": "click"}})
```

## Query

Use `JSON_EXTRACT(col, '$.path')` or the `->` / `->>` shorthand (MySQL 8.0+):

```python
rows = query("SELECT data->>'$.event' AS event FROM events WHERE JSON_EXTRACT(data, '$.event') = :e",
             {"e": "click"})
```

Note `->>` in MySQL returns `TEXT` (unquoted). In PostgreSQL `->>` returns `text` of a jsonb path. The `'$.event'` path syntax is MySQL-specific.

## Indexing

MySQL 8.0+ supports functional indexes on JSON expressions:

```sql
CREATE INDEX idx_events_event ON events ((CAST(data->>'$.event' AS CHAR(64))));
```

Pre-8.0: index a generated column instead.

## Gotchas

- `JSON_EXTRACT` returns JSON; `->>` returns the unquoted scalar. For comparisons, match the operator.
- `->>` shorthand requires MySQL 8.0+. On 5.7 use `JSON_UNQUOTE(JSON_EXTRACT(...))`.
- MySQL `JSON` does NOT auto-compress (unlike PG `jsonb` TOAST). Large docs blow up row size.
- Whitespace handling: MySQL normalizes on input but may reformat on output (PG normalizes the same way).

## See also

- `interaction-skills/postgres/json-columns.md` — PostgreSQL `jsonb` syntax (`@>`, `?`, no `$.path`)