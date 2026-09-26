"""Integration tests for PostgreSQL — skipped unless $BH_PG_URL is set.

Set BH_PG_URL to a live PostgreSQL URL (any valid one; SQLite OK for smoke)
to enable these tests. Format: postgresql://user:pass@host:port/db
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from sql_harness.config import ConnectionConfig, ConnectionsConfig, PoolConfig
from sql_harness.manager import SqlHarness
from sql_harness.helpers import (
    describe,
    list_tables,
    query,
    set_active,
    use_workspace,
    with_transaction,
)

PG_URL = os.environ.get("BH_PG_URL")

pytestmark = pytest.mark.skipif(not PG_URL, reason="BH_PG_URL not set")


@pytest.fixture()
def pg_harness():
    cfg = ConnectionsConfig(
        default_workspace="pg",
        pool_defaults=PoolConfig(size=1),
        connections=[ConnectionConfig(name="pg", driver="postgres", url=PG_URL)],
    )
    h = SqlHarness(cfg)
    set_active(h)
    yield h
    h.close_all()


def test_select_1(pg_harness) -> None:
    use_workspace("pg")
    rows = query("SELECT 1 AS n")
    assert rows == [{"n": 1}]


def test_list_and_describe(pg_harness) -> None:
    use_workspace("pg")
    tables = list_tables()
    assert isinstance(tables, list)
    # If we have a known table, describe it.
    if "pg_class" in tables:
        cols = describe("pg_class")
        names = {c["name"] for c in cols}
        assert "relname" in names


def test_with_transaction_commit(pg_harness) -> None:
    use_workspace("pg")
    # Use a temp table that we drop ourselves; rollback-safe.
    with pg_harness.workspace("pg").engine.begin() as conn:
        conn.execute(text("CREATE TEMP TABLE sh_test (n INT)"))
    with with_transaction() as conn:
        conn.execute(text("INSERT INTO sh_test (n) VALUES (1), (2), (3)"))
    rows = query("SELECT SUM(n) AS total FROM sh_test")
    assert rows[0]["total"] == 6