"""Integration tests for the PostgreSQL performance helpers.

Skipped unless BH_PG_URL is set. Exercises explain_analyze, table_stats,
index_usage_stats, unused_indexes, and missing_indexes_hint against a throwaway
schema (created/dropped per test). slow_queries / seq_scan_heavy depend on
shared_preload_libraries / stats-counter warmup and are exercised via the docs.
"""
from __future__ import annotations

import os
import random

import pytest
from sqlalchemy import text

from sql_harness.config import ConnectionConfig, ConnectionsConfig, PoolConfig
from sql_harness.helpers import (
    explain_analyze,
    execute,
    index_usage_stats,
    missing_indexes_hint,
    query,
    set_active,
    table_stats,
    unused_indexes,
    use_workspace,
)
from sql_harness.manager import SqlHarness

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


@pytest.fixture()
def opt_schema(pg_harness):
    """Throwaway schema so the stats views only see our objects."""
    use_workspace("pg")
    name = f"sh_opt_{os.getpid()}_{random.randint(1000, 9999)}"
    eng = pg_harness.workspace("pg").engine
    with eng.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{name}"'))
    yield name
    with eng.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{name}" CASCADE'))


def test_explain_analyze_text(opt_schema) -> None:
    sc = opt_schema
    execute(f'CREATE TABLE "{sc}".t (n int)')
    execute(f'INSERT INTO "{sc}".t VALUES (1), (2), (3)')
    rows = explain_analyze(f'SELECT * FROM "{sc}".t WHERE n = 1')
    assert isinstance(rows, list) and rows
    joined = "\n".join(r.get("QUERY PLAN", "") for r in rows)
    assert "Execution Time" in joined            # ANALYZE actually ran it


def test_explain_analyze_json_format(opt_schema) -> None:
    import json
    sc = opt_schema
    execute(f'CREATE TABLE "{sc}".t (n int)')
    execute(f'INSERT INTO "{sc}".t VALUES (1)')
    rows = explain_analyze(f'SELECT * FROM "{sc}".t', format="json")
    assert len(rows) == 1
    plan = json.loads(rows[0]["QUERY PLAN"])
    assert isinstance(plan, list) and "Plan" in plan[0]


def test_table_stats(opt_schema) -> None:
    sc = opt_schema
    execute(f'CREATE TABLE "{sc}".people (id int PRIMARY KEY, name text)')
    execute(
        f'INSERT INTO "{sc}".people VALUES (1,:a),(2,:b),(3,:c),(4,:d),(5,:e)',
        {"a": "a", "b": "b", "c": "c", "d": "d", "e": "e"},
    )
    execute(f'ANALYZE "{sc}".people')
    rows = table_stats("people", schema=sc)
    assert len(rows) == 1
    assert rows[0]["live_rows"] == 5
    assert "total_size_bytes" in rows[0]


def test_index_usage_stats(opt_schema) -> None:
    sc = opt_schema
    execute(f'CREATE TABLE "{sc}".idx_demo (id int, email text)')
    execute(f'CREATE INDEX email_idx ON "{sc}".idx_demo (email)')
    # enough rows that the index is selective; then ANALYZE + a qualifying read
    execute(
        f'INSERT INTO "{sc}".idx_demo SELECT g, \'user\' || g || \'@x\' '
        f"FROM generate_series(1, 200) AS g"
    )
    execute(f'ANALYZE "{sc}".idx_demo')
    query(f'SELECT * FROM "{sc}".idx_demo WHERE email = \'user42@x\'')
    rows = index_usage_stats(table="idx_demo", schema=sc)
    assert any(r["index_name"] == "email_idx" for r in rows)


def test_unused_indexes(opt_schema) -> None:
    sc = opt_schema
    execute(f'CREATE TABLE "{sc}".ui (id int PRIMARY KEY, tag text)')
    execute(f'CREATE INDEX ui_tag_idx ON "{sc}".ui (tag)')   # never queried -> idx_scan 0
    execute(f'ANALYZE "{sc}".ui')
    rows = unused_indexes(table="ui", schema=sc)
    names = [r["index_name"] for r in rows]
    assert "ui_tag_idx" in names
    assert not any("pkey" in n for n in names)               # PK (unique) excluded


def test_missing_indexes_hint_before(opt_schema) -> None:
    sc = opt_schema
    execute(f'CREATE TABLE "{sc}".parent (pid int PRIMARY KEY)')
    execute(
        f'CREATE TABLE "{sc}".child (cid int PRIMARY KEY, '
        f'pid int REFERENCES "{sc}".parent(pid))'
    )
    rows = missing_indexes_hint(table="child", schema=sc)
    child_rows = [r for r in rows if r["table_name"] == "child"]
    assert child_rows
    assert child_rows[0]["create_sql"].startswith("CREATE INDEX ON")
    assert "pid" in child_rows[0]["fk_columns"]


def test_missing_indexes_hint_covered(opt_schema) -> None:
    sc = opt_schema
    execute(f'CREATE TABLE "{sc}".parent (pid int PRIMARY KEY)')
    execute(
        f'CREATE TABLE "{sc}".child (cid int PRIMARY KEY, '
        f'pid int REFERENCES "{sc}".parent(pid))'
    )
    execute(f'CREATE INDEX child_pid_idx ON "{sc}".child (pid)')   # covers the FK
    rows = missing_indexes_hint(table="child", schema=sc)
    assert not any(r["table_name"] == "child" for r in rows)
