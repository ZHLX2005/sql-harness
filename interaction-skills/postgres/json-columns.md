# JSON columns — PostgreSQL (`jsonb`)

Use PostgreSQL's binary `jsonb` type (not `json` — `jsonb` is binary-comparable and indexable).

## Insert

`psycopg3` auto-serializes Python `dict`/`list` to `jsonb`:

```python
execute("INSERT INTO events (data) VALUES (:d)", {"d": {"event": "click", "ts": 1234}})
```

## Query

Use `->` (returns `jsonb`), `->>` (returns `text`), and the containment operator `@>`:

```python
rows = query("SELECT data->>'event' AS event FROM events WHERE data @> :filter",
             {"filter": {"event": "click"}})
```

## Indexing

`jsonb` supports GIN indexes on the whole document (fast for `@>` / `?` / `?&` / `?|`):

```sql
CREATE INDEX idx_events_data ON events USING GIN (data);
```

For paths you hit often, a btree expression index:

```sql
CREATE INDEX idx_events_event ON events ((data->>'event'));
```

## Gotchas

- Prefer `jsonb` over `json` — `json` is text-stored, no index support beyond expression indexes, whitespace-preserved.
- Comparing `data = '{"event":"click"}'::jsonb` works (SQLAlchemy normalizes JSON whitespace).
- `JSONB` aggregates (`jsonb_agg`, `jsonb_object_agg`) exist; for plain text JSON use `string_agg`.
- GIN on `jsonb` is large — measure before adding to high-write tables.

## See also

- `interaction-skills/postgres/specialized-indexes.md` — GIN/GiST/BRIN tradeoffs
- `interaction-skills/mysql/json-columns.md` — MySQL JSON syntax (different operators)