"""Reusable helpers for the sh_demo schema.

These functions are auto-imported into the sql-harness heredoc/run namespace
(mirrors browser-harness's agent_helpers.py auto-load). After `use_workspace`,
call them by name — no import needed.

Example heredoc:

    use_workspace("test_pg")
    seed_users([("dave@x.com", "Dave")])
    print(list_orders())
"""

from sqlalchemy import text

__all__ = [
    "seed_users",
    "seed_products",
    "place_order",
    "ship_pending_orders",
    "list_orders",
    "user_by_email",
    "product_by_sku",
]


def seed_users(rows):
    """Insert users. `rows` = iterable of (email, name). Returns count inserted."""
    n = 0
    for email, name in rows:
        execute(
            "INSERT INTO sh_demo.users (email, name) VALUES (:e, :n)",
            {"e": email, "n": name},
        )
        n += 1
    return n


def seed_products(rows):
    """Insert products. `rows` = iterable of (sku, name, price, stock)."""
    n = 0
    for sku, name, price, stock in rows:
        execute(
            "INSERT INTO sh_demo.products (sku, name, price, stock) "
            "VALUES (:s, :n, :p, :st)",
            {"s": sku, "n": name, "p": price, "st": stock},
        )
        n += 1
    return n


def place_order(user_email, items):
    """Create an order atomically. `items` = list of (sku, qty).

    Looks up user + product IDs, inserts order + items, sets total, decrements
    stock. Runs inside a single transaction.
    """
    user = user_by_email(user_email)
    if user is None:
        raise ValueError(f"no user with email {user_email!r}")
    with with_transaction() as conn:
        order_id = conn.execute(
            text(
                "INSERT INTO sh_demo.orders (user_id, status) "
                "VALUES (:u, 'pending') RETURNING id"
            ),
            {"u": user["id"]},
        ).scalar()
        total = 0
        for sku, qty in items:
            prod = product_by_sku(sku)
            if prod is None:
                raise ValueError(f"no product with sku {sku!r}")
            line = qty * prod["price"]
            total += line
            conn.execute(
                text(
                    "INSERT INTO sh_demo.order_items "
                    "(order_id, product_id, qty, unit_price) "
                    "VALUES (:o, :p, :q, :up)"
                ),
                {"o": order_id, "p": prod["id"], "q": qty, "up": prod["price"]},
            )
            conn.execute(
                text("UPDATE sh_demo.products SET stock = stock - :q WHERE id = :p"),
                {"q": qty, "p": prod["id"]},
            )
        conn.execute(
            text("UPDATE sh_demo.orders SET total = :t WHERE id = :o"),
            {"t": total, "o": order_id},
        )
    return order_id


def ship_pending_orders():
    """Mark all pending orders as shipped. Returns rowcount."""
    return execute("UPDATE sh_demo.orders SET status = 'shipped' WHERE status = 'pending'")[
        "rowcount"
    ]


def list_orders():
    """Return orders joined to users with item counts."""
    return query(
        """
        SELECT u.name AS buyer, o.status, o.total,
               (SELECT count(*) FROM sh_demo.order_items WHERE order_id = o.id) AS items
        FROM sh_demo.orders o
        JOIN sh_demo.users u ON u.id = o.user_id
        ORDER BY o.id
        """
    )


def user_by_email(email):
    """Return the user row for `email`, or None."""
    rows = query("SELECT * FROM sh_demo.users WHERE email = :e", {"e": email})
    return rows[0] if rows else None


def product_by_sku(sku):
    """Return the product row for `sku`, or None."""
    rows = query("SELECT * FROM sh_demo.products WHERE sku = :s", {"s": sku})
    return rows[0] if rows else None