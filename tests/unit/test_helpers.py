"""Unit tests for helpers.py — uses SQLite-in-memory as a stand-in backend."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from sql_harness.config import ConnectionConfig, ConnectionsConfig, PoolConfig
from sql_harness.helpers import (
    apply_skill,
    connection_info,
    current_workspace,
    describe,
    dispose,
    execute,
    explain,
    list_skills,
    list_tables,
    query,
    set_active,
    use_workspace,
    with_transaction,
    workspaces,
)
from sql_harness.manager import SqlHarness


@pytest.fixture()
def harness_with_table():
    """SqlHarness pointed at a fresh SQLite-in-memory with one table."""
    cfg = ConnectionsConfig(
        default_workspace="",                 # force explicit use_workspace()
        pool_defaults=PoolConfig(size=1),
        connections=[
            ConnectionConfig(
                name="t",
                driver="sqlite",
                url="sqlite:///:memory:",
            ),
        ],
    )
    h = SqlHarness(cfg)
    ws = h.workspace("t")
    with ws.engine.begin() as conn:
        conn.execute(text("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)"))
        conn.execute(text("INSERT INTO people (name, age) VALUES ('alice', 30)"))
        conn.execute(text("INSERT INTO people (name, age) VALUES ('bob', 25)"))
    set_active(h)
    yield h
    h.close_all()


def test_use_workspace_then_query(harness_with_table) -> None:
    use_workspace("t")
    rows = query("SELECT name, age FROM people ORDER BY age")
    assert rows == [{"name": "bob", "age": 25}, {"name": "alice", "age": 30}]


def test_query_without_workspace_raises() -> None:
    # Fresh harness, no workspace opened.
    h = SqlHarness(
        ConnectionsConfig(
            connections=[
                ConnectionConfig(name="x", driver="sqlite", url="sqlite:///:memory:"),
            ],
        )
    )
    set_active(h)
    with pytest.raises(RuntimeError, match="no workspace is active"):
        query("SELECT 1")
    h.close_all()


def test_execute_returns_rowcount(harness_with_table) -> None:
    use_workspace("t")
    info = execute("INSERT INTO people (name, age) VALUES (:n, :a)", {"n": "carol", "a": 40})
    assert info["rowcount"] == 1


def test_list_tables_and_describe(harness_with_table) -> None:
    use_workspace("t")
    tables = list_tables()
    assert "people" in tables
    cols = describe("people")
    names = [c["name"] for c in cols]
    assert set(names) == {"id", "name", "age"}


def test_workspaces_and_connection_info(harness_with_table) -> None:
    use_workspace("t")
    assert workspaces() == ["t"]
    info = connection_info()
    assert info["name"] == "t"
    assert info["url"].startswith("sqlite:")      # SQLite URL has no password


def test_with_transaction_commit(harness_with_table) -> None:
    use_workspace("t")
    with with_transaction() as conn:
        conn.execute(text("INSERT INTO people (name, age) VALUES ('dave', 50)"))
    rows = query("SELECT name FROM people WHERE name = 'dave'")
    assert len(rows) == 1


def test_with_transaction_rollback(harness_with_table) -> None:
    use_workspace("t")
    try:
        with with_transaction() as conn:
            conn.execute(text("INSERT INTO people (name, age) VALUES ('eve', 60)"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    rows = query("SELECT name FROM people WHERE name = 'eve'")
    assert rows == []


def test_explain(harness_with_table) -> None:
    use_workspace("t")
    plan = explain("SELECT * FROM people")
    assert isinstance(plan, list)
    # SQLite EXPLAIN returns plan rows (at least one).
    assert len(plan) >= 1


def test_list_skills_empty_when_dir_missing(harness_with_table, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BH_SQL_AGENT_WORKSPACE", str(tmp_path / "no_skills"))
    use_workspace("t")                       # activate the zone
    assert list_skills() == []


def test_apply_skill_missing_raises(harness_with_table, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BH_SQL_AGENT_WORKSPACE", str(tmp_path / "no_skills"))
    use_workspace("t")                       # activate the zone
    with pytest.raises(FileNotFoundError):
        apply_skill("does-not-exist")


def test_dispose_closes_all(harness_with_table) -> None:
    use_workspace("t")
    assert workspaces() == ["t"]
    dispose()
    assert workspaces() == []


# --- PostgreSQL performance helpers (driver guard) + run_sql_file ------------

from sql_harness.helpers import (  # noqa: E402  (appended import block)
    explain_analyze,
    index_usage_stats,
    missing_indexes_hint,
    run_sql_file,
    seq_scan_heavy,
    slow_queries,
    table_stats,
    unused_indexes,
)


@pytest.mark.parametrize("fn,args", [
    (explain_analyze,      ("SELECT 1",)),
    (table_stats,          ("people",)),
    (index_usage_stats,    ()),
    (unused_indexes,       ()),
    (seq_scan_heavy,       ()),
    (slow_queries,         ()),
    (missing_indexes_hint, ()),
])
def test_pg_helpers_not_supported_on_sqlite(harness_with_table, fn, args) -> None:
    """Every PG-only helper raises NotImplementedError on a non-postgres driver."""
    use_workspace("t")
    with pytest.raises(NotImplementedError, match="not supported for driver sqlite"):
        fn(*args)


def test_run_sql_file_executes_multi_statement(harness_with_table, tmp_path) -> None:
    """run_sql_file splits on top-level ';' and ignores ';' inside strings/comments."""
    use_workspace("t")
    sql_file = tmp_path / "fixture.sql"
    sql_file.write_text(
        "-- a comment line\n"
        "CREATE TABLE notes (id int, body text);\n"
        "INSERT INTO notes VALUES (1, 'semi;colon in string');  -- trailing comment\n"
        "/* block ; comment */ SELECT id, body FROM notes ORDER BY id;\n",
        encoding="utf-8",
    )
    rows = run_sql_file(str(sql_file))
    assert rows == [{"id": 1, "body": "semi;colon in string"}]


def test_run_sql_file_returns_empty_for_write_only(harness_with_table, tmp_path) -> None:
    use_workspace("t")
    sql_file = tmp_path / "writes.sql"
    sql_file.write_text(
        "CREATE TABLE w (n int);\nINSERT INTO w VALUES (1),(2);\n", encoding="utf-8"
    )
    assert run_sql_file(str(sql_file)) == []