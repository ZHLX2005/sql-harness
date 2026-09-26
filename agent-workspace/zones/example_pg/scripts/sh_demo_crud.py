"""Saved by `sql-harness save sh_demo_crud`.

Reproduce the full sh_demo CRUD walkthrough end-to-end.
Run:    sql-harness run sh_demo_crud
Helpers (use_workspace, query, execute, ...) are pre-imported.

This script is idempotent: it drops + recreates the sh_demo schema, so it
can be run repeatedly without leaving stale rows.
"""

from sqlalchemy import text

use_workspace("test_pg")


def _exec_many(ddl: str) -> None:
    for stmt in ddl.strip().split(";"):
        if stmt.strip():
            execute(stmt)


# --- DDL: fresh schema every run ---
execute("DROP SCHEMA IF EXISTS sh_demo CASCADE")
execute("CREATE SCHEMA sh_demo")
_exec_many(
    """
CREATE TABLE sh_demo.users (
    id BIGSERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    name VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE sh_demo.products (
    id BIGSERIAL PRIMARY KEY,
    sku VARCHAR(40) NOT NULL,
    name VARCHAR(200) NOT NULL,
    price NUMERIC(10,2) NOT NULL,
    stock INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE sh_demo.orders (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES sh_demo.users(id),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    total NUMERIC(12,2) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE sh_demo.order_items (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES sh_demo.orders(id) ON DELETE CASCADE,
    product_id BIGINT NOT NULL REFERENCES sh_demo.products(id),
    qty INT NOT NULL CHECK (qty > 0),
    unit_price NUMERIC(10,2) NOT NULL
);
"""
)

# --- CREATE: seed users + products ---
for email, name in [("alice@x.com", "Alice"), ("bob@x.com", "Bob"), ("carol@x.com", "Carol")]:
    execute(
        "INSERT INTO sh_demo.users (email, name) VALUES (:e, :n)",
        {"e": email, "n": name},
    )
for sku, name, price, stock in [
    ("A100", "Widget", 9.99, 100),
    ("A200", "Gadget", 19.99, 50),
    ("B300", "Gizmo", 29.99, 25),
]:
    execute(
        "INSERT INTO sh_demo.products (sku, name, price, stock) VALUES (:s, :n, :p, :st)",
        {"s": sku, "n": name, "p": price, "st": stock},
    )

# --- READ + relational insert (order + items atomically) ---
alice = query("SELECT id FROM sh_demo.users WHERE email = 'alice@x.com'")[0]["id"]
widget = query("SELECT id, price FROM sh_demo.products WHERE sku = 'A100'")[0]
with with_transaction() as conn:
    order_id = conn.execute(
        text("INSERT INTO sh_demo.orders (user_id, status) VALUES (:u, 'pending') RETURNING id"),
        {"u": alice},
    ).scalar()
    conn.execute(
        text(
            "INSERT INTO sh_demo.order_items (order_id, product_id, qty, unit_price) "
            "VALUES (:o, :p, 3, :up)"
        ),
        {"o": order_id, "p": widget["id"], "up": widget["price"]},
    )
    conn.execute(
        text("UPDATE sh_demo.orders SET total = 3 * :up WHERE id = :o"),
        {"up": widget["price"], "o": order_id},
    )

# --- UPDATE: ship + decrement stock ---
execute("UPDATE sh_demo.orders SET status = 'shipped' WHERE status = 'pending'")
execute("UPDATE sh_demo.products SET stock = stock - 3 WHERE sku = 'A100'")

# --- DELETE: remove carol ---
execute("DELETE FROM sh_demo.users WHERE email = 'carol@x.com'")

# --- VERIFY (prints for run-mode observers) ---
print("tables:", list_tables(schema="sh_demo"))
print(
    "orders:",
    query(
        """
        SELECT u.name AS buyer, o.status, o.total,
               (SELECT count(*) FROM sh_demo.order_items WHERE order_id = o.id) AS items
        FROM sh_demo.orders o JOIN sh_demo.users u ON u.id = o.user_id
        """
    ),
)
print("widget stock:", query("SELECT stock FROM sh_demo.products WHERE sku = 'A100'"))
print("remaining users:", query("SELECT email FROM sh_demo.users ORDER BY email"))