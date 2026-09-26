# sh_demo — e-commerce test schema (test_pg)

Captured from live `test_pg` (`postgres://...@47.110.80.47:5432/postgres`) during the
first end-to-end CRUD walkthrough. This is the "historical code" the workspace
remembers: read it before re-deriving the schema.

## Connection

- Workspace name: `test_pg`
- Driver: `postgres`
- Schema: `sh_demo` (isolated; safe to `DROP SCHEMA sh_demo CASCADE` and rebuild)

```bash
use_workspace("test_pg")
list_tables(schema="sh_demo")
```

## Tables

### users
| column | type | nullable | default |
|---|---|---|---|
| id | BIGSERIAL | no | — (PK) |
| email | VARCHAR(255) | no | — |
| name | VARCHAR(100) | no | — |
| created_at | TIMESTAMPTZ | no | now() |

### products
| column | type | nullable | default |
|---|---|---|---|
| id | BIGSERIAL | no | — (PK) |
| sku | VARCHAR(40) | no | — |
| name | VARCHAR(200) | no | — |
| price | NUMERIC(10,2) | no | — |
| stock | INT | no | 0 |
| created_at | TIMESTAMPTZ | no | now() |

### orders
| column | type | nullable | default | FK |
|---|---|---|---|---|
| id | BIGSERIAL | no | — (PK) | — |
| user_id | BIGINT | no | — | users(id) |
| status | VARCHAR(20) | no | 'pending' | — |
| total | NUMERIC(12,2) | no | 0 | — |
| created_at | TIMESTAMPTZ | no | now() | — |

### order_items
| column | type | nullable | default | FK |
|---|---|---|---|---|
| id | BIGSERIAL | no | — (PK) | — |
| order_id | BIGINT | no | — | orders(id) ON DELETE CASCADE |
| product_id | BIGINT | no | — | products(id) |
| qty | INT | no | — | — (CHECK qty > 0) |
| unit_price | NUMERIC(10,2) | no | — | — |

## CRUD patterns that work (verified)

### Create
```python
use_workspace("test_pg")
execute(
    "INSERT INTO sh_demo.users (email, name) VALUES (:e, :n)",
    {"e": "alice@x.com", "n": "Alice"},
)
```

### Read (relational)
```python
rows = query("""
    SELECT u.name AS buyer, o.status, o.total
    FROM sh_demo.orders o
    JOIN sh_demo.users u ON u.id = o.user_id
""")
```

### Insert order + items atomically (RETURNING id)
```python
with with_transaction() as conn:
    from sqlalchemy import text
    order_id = conn.execute(
        text("INSERT INTO sh_demo.orders (user_id, status) VALUES (:u, 'pending') RETURNING id"),
        {"u": alice_id},
    ).scalar()
    conn.execute(
        text("INSERT INTO sh_demo.order_items (order_id, product_id, qty, unit_price) VALUES (:o, :p, 3, :up)"),
        {"o": order_id, "p": product_id, "up": price},
    )
```

### Update
```python
execute("UPDATE sh_demo.orders SET status = 'shipped' WHERE status = 'pending'")
```

### Delete
```python
execute("DELETE FROM sh_demo.users WHERE email = 'carol@x.com'")
```

## Notes

- All 4 tables use `BIGSERIAL` PKs (8-byte, future-proof vs `SERIAL`).
- `order_items.order_id` has `ON DELETE CASCADE` — deleting an order removes its items.
- `qty` has a `CHECK (qty > 0)` constraint.
- Money columns use `NUMERIC(10,2)` / `NUMERIC(12,2)` — never FLOAT for money.