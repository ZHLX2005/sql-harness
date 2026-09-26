"""MySQL driver (PyMySQL via SQLAlchemy)."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, inspect, text

from .readonly import install_guard

# Dangerous even inside an otherwise read-only statement: file writes and row
# locks. MySQL's `#` is a comment and `\'` escapes inside strings.
_MYSQL_FORBIDDEN = frozenset(
    {
        "INTO OUTFILE",
        "INTO DUMPFILE",
        "FOR UPDATE",
        "FOR SHARE",
        "LOCK IN SHARE MODE",
    }
)


class MysqlDriver:
    name = "mysql"

    def make_engine(
        self, url: str, pool, password: str | None = None, read_only: bool = False
    ) -> Engine:
        # `password` is ignored: credentials live in the URL for SQL backends.
        if not url.startswith("mysql+pymysql://"):
            raise ValueError(
                f"MySQL URL must start with mysql+pymysql:// (got {url[:30]!r})"
            )
        engine = create_engine(url, **pool.sqlalchemy_kwargs())
        if read_only:
            install_guard(
                engine,
                hash_comments=True,
                backslash_escapes=True,
                extra_forbidden=_MYSQL_FORBIDDEN,
                # Server-enforced rail: MySQL then rejects DML *and* DDL with
                # 1792 "Cannot execute statement in a READ ONLY transaction".
                session_sql="SET SESSION TRANSACTION READ ONLY",
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
        # MySQL: backticks; escape embedded backticks.
        return "`" + ident.replace("`", "``") + "`"

    def server_version(self, engine: Engine) -> str:
        with engine.connect() as conn:
            return str(conn.execute(text("SELECT VERSION()")).scalar())