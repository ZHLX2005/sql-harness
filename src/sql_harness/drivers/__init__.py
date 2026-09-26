"""Driver abstraction for sql-harness.

A Driver wraps a SQLAlchemy Engine with dialect-specific helpers
(`list_tables`, `describe`, `quote_ident`). Adding a new backend
means adding one file here implementing the `Driver` protocol.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine


class Driver(Protocol):
    """Protocol every backend implements."""

    name: str

    def make_engine(
        self,
        url: str,
        pool: "PoolConfig",
        password: str | None = None,
        read_only: bool = False,
    ) -> Engine:
        """Create a SQLAlchemy Engine with the given pool config.

        `password` is the connection's standalone secret (ConnectionConfig.password).
        Drivers whose URL already carries credentials (SQL backends) ignore it;
        SSH uses it when no password is embedded in the URL.

        `read_only` installs that dialect's read-only rails (see
        `drivers/readonly.py`): a statement allow-list on every
        `before_cursor_execute`, plus the server-side session setting where the
        backend has one. Drivers with no notion of statements (SSH, Redis)
        accept it for API parity.
        """

    def list_tables(self, engine: Engine, schema: str | None) -> list[str]:
        """Return table names (and views) visible in the given schema."""

    def describe(
        self, engine: Engine, table: str, schema: str | None
    ) -> list[dict]:
        """Return column metadata: [{name, type, nullable, default}, ...]."""

    def quote_ident(self, ident: str) -> str:
        """Safely quote an identifier for the dialect."""


def get_driver(name: str) -> Driver:
    """Look up a driver by its short name."""
    if name == "postgres":
        from .postgres import PostgresDriver

        return PostgresDriver()
    if name == "mysql":
        from .mysql import MysqlDriver

        return MysqlDriver()
    if name == "redis":
        from .redis import RedisDriver

        return RedisDriver()
    if name == "sqlite":
        from .sqlite import SqliteDriver

        return SqliteDriver()
    if name in ("ssh", "ssh+password", "ssh+key"):
        from .ssh import SshDriver

        return SshDriver()
    raise ValueError(
        f"unknown driver: {name!r} (known: postgres, mysql, redis, sqlite, ssh)"
    )