"""PostgreSQL driver (psycopg 3 via SQLAlchemy)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, inspect, text

from .readonly import install_guard

# `SELECT ... INTO t` creates a table in PostgreSQL (unlike MySQL, where INTO
# means OUTFILE). Spaced so `FROM into_log` is not a false positive. The FOR
# variants take row locks.
_PG_FORBIDDEN = frozenset(
    {
        " INTO ",
        "FOR UPDATE",
        "FOR NO KEY UPDATE",
        "FOR SHARE",
        "FOR KEY SHARE",
    }
)


class PostgresDriver:
    name = "postgres"

    def make_engine(
        self, url: str, pool, password: str | None = None, read_only: bool = False
    ) -> Engine:
        # `password` is ignored: credentials live in the URL for SQL backends.
        # Accept all three SQLAlchemy-recognized PostgreSQL URL prefixes:
        #   postgresql+psycopg://  (explicit dialect — preferred)
        #   postgresql://          (SQLAlchemy maps to default psycopg driver)
        #   postgres://            (short alias SQLAlchemy also accepts)
        # Normalize to the explicit dialect form for determinism.
        if url.startswith("postgresql+psycopg://"):
            pass
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        elif url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        else:
            raise ValueError(
                f"PostgreSQL URL must start with postgres:// or postgresql:// "
                f"(got {url[:30]!r})"
            )
        engine = create_engine(url, **pool.sqlalchemy_kwargs())
        if read_only:
            install_guard(
                engine,
                # `#` is the bitwise-XOR operator in PostgreSQL, not a comment —
                # treating it as one would swallow real code and hide a write.
                hash_comments=False,
                backslash_escapes=False,
                extra_forbidden=_PG_FORBIDDEN,
                # Server-enforced rail: writes then fail with
                # "cannot execute ... in a read-only transaction".
                session_sql="SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
            )
        return engine

    def list_tables(self, engine: Engine, schema: str | None) -> list[str]:
        insp = inspect(engine)
        return sorted(
            insp.get_table_names(schema=schema) + insp.get_view_names(schema=schema)
        )

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
        # PostgreSQL standard: double-quote, escape embedded double-quotes.
        return '"' + ident.replace('"', '""') + '"'

    def server_version(self, engine: Engine) -> str:
        with engine.connect() as conn:
            return str(conn.execute(text("SELECT version()")).scalar())