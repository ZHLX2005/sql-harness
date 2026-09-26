"""Integration test: pgexercises practice module + run_sql_file.

Skipped unless BH_PG_URL is set AND practice/pgexercises fixtures exist.
The practice dir was removed in v0.3.0 (docs consolidation), so this module
is dormant unless the fixtures are restored. run_sql_file itself has unit
coverage in tests/unit/test_helpers.py.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from sql_harness.config import ConnectionConfig, ConnectionsConfig, PoolConfig
from sql_harness.helpers import query, run_sql_file, set_active, use_workspace
from sql_harness.manager import SqlHarness

PG_URL = os.environ.get("BH_PG_URL")
PRACTICE_DIR = Path(__file__).resolve().parents[2] / "practice" / "pgexercises"
pytestmark = pytest.mark.skipif(
    (not PG_URL) or (not PRACTICE_DIR.is_dir()),
    reason="BH_PG_URL not set or practice/pgexercises removed (v0.3.0)",
)


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


def _load(pg_harness) -> None:
    use_workspace("pg")
    run_sql_file(str(PRACTICE_DIR / "schema.sql"))
    run_sql_file(str(PRACTICE_DIR / "seed.sql"))


def test_schema_seed_load_with_expected_counts(pg_harness) -> None:
    _load(pg_harness)
    assert query("SELECT count(*) AS n FROM cd.facilities")[0]["n"] == 9
    assert query("SELECT count(*) AS n FROM cd.members")[0]["n"] == 15
    assert query("SELECT count(*) AS n FROM cd.bookings")[0]["n"] == 35


def test_run_problem_file_returns_rows(pg_harness) -> None:
    _load(pg_harness)
    basic = sorted(PRACTICE_DIR.glob("basic_01_*.sql"))[0]   # select * from cd.facilities
    rows = run_sql_file(str(basic))
    assert len(rows) == 9


def test_join_problem_runs(pg_harness) -> None:
    _load(pg_harness)
    join_file = sorted(PRACTICE_DIR.glob("joins_01_*.sql"))[0]
    rows = run_sql_file(str(join_file))
    assert isinstance(rows, list)
    # Every joins_01 answer is a SELECT over members/bookings — non-empty here.
    assert rows
