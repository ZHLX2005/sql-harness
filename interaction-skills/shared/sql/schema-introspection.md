# Schema introspection

Read schema metadata before writing queries that depend on it.

## Approach

```python
use_workspace("local_pg")

# All tables + views
tables = list_tables()

# All tables in a specific schema (PostgreSQL)
tables = list_tables(schema="public")

# Column metadata
cols = describe("users")
# [{'name': 'id', 'type': 'INTEGER', 'nullable': False, 'default': None}, ...]
```

## Detection
- Unfamiliar schema (third-party DB, prod clone).
- Column type drift after a migration.
- Foreign-key walk: `query("SELECT ... FROM information_schema...")`.

## Manual SQL fallback

```python
# PostgreSQL
query("""
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = :t
""", {"t": "users"})

# MySQL
query("""
    SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
    FROM information_schema.COLUMNS
    WHERE TABLE_NAME = :t
""", {"t": "users"})
```

## Gotchas

- `describe()` returns `default` as a stringified SQL fragment (e.g. `'nextval('users_id_seq')'::regclass`) — not always parseable.
- On PostgreSQL, `list_tables()` includes views by default. Filter explicitly if needed.
- Schema names are case-sensitive on PostgreSQL (lowercase by convention); case-insensitive on MySQL (depends on `lower_case_table_names`).