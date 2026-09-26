"""SQLite driver — useful for local dev + tests.

Added as a built-in 4th driver so unit tests can use a real SQL backend
without needing a Postgres or MySQL server.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, inspect

from .readonly import install_guard


class SqliteDriver:
    name = "sqlite"

    def make_engine(
        self, url: str, pool, password: str | None = None, read_only: bool = False
    ) -> Engine:
        # SQLite ignores most pool settings; StaticPool is fine for in-memory.
        # `password` is ignored too — SQLite has no auth.
        if not url.startswith("sqlite:"):
            raise ValueError(f"SQLite URL must start with sqlite: (got {url[:30]!r})")
        engine = create_engine(url, future=True)
        if read_only:
            # No session-level rail exists here. For a file database the real
            # boundary is the URL: `sqlite:///file:path?mode=ro&uri=true` makes
            # the OS open it read-only. That is a URL change, so it is not done
            # behind the user's back — the guard below is the only rail for
            # sqlite, and `ATTACH` is refused by the verb allow-list.
            install_guard(engine, session_sql=None)
        return engine

    def list_tables(self, engine: Engine, schema: str | None) -> list[str]:
        insp = inspect(engine)
        return sorted(insp.get_table_names(schema=schema))

    def describe(self, engine: Engine, table: str, schema: str | None) -> list[dict]:
        insp = inspect(engine)
        cols = insp.get_columns(table, schema=schema)
        return [
            {
                "name": c["name"],
                "type": str(c["type"]),
                "nullable": bool(c.get("nullable", True)),
                "default": str(c.get("default")) if c.get("default") is not None else None,
            }
            for c in cols
        ]

    def quote_ident(self, ident: str) -> str:
        return '"' + ident.replace('"', '""') + '"'