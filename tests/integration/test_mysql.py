"""Integration tests for MySQL — skipped unless $BH_MYSQL_URL is set."""

from __future__ import annotations

import os

import pytest

from sql_harness.config import ConnectionConfig, ConnectionsConfig, PoolConfig
from sql_harness.helpers import query, set_active, use_workspace
from sql_harness.manager import SqlHarness

MYSQL_URL = os.environ.get("BH_MYSQL_URL")

pytestmark = pytest.mark.skipif(not MYSQL_URL, reason="BH_MYSQL_URL not set")


@pytest.fixture()
def mysql_harness():
    cfg = ConnectionsConfig(
        default_workspace="mysql",
        pool_defaults=PoolConfig(size=1),
        connections=[ConnectionConfig(name="mysql", driver="mysql", url=MYSQL_URL)],
    )
    h = SqlHarness(cfg)
    set_active(h)
    yield h
    h.close_all()


def test_select_1(mysql_harness) -> None:
    use_workspace("mysql")
    rows = query("SELECT 1 AS n")
    assert rows == [{"n": 1}]


def test_list_tables(mysql_harness) -> None:
    use_workspace("mysql")
    tables = query("SHOW TABLES")
    assert isinstance(tables, list)