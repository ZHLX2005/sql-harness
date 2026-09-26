"""Unit tests for the read-only statement guard (drivers/readonly.py).

The bypass cases are the point of this file: a guard that only stops `INSERT`
is worthless, so each entry below is a dodge that would defeat a naive
keyword match.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from sql_harness.config import PoolConfig
from sql_harness.drivers.mysql import _MYSQL_FORBIDDEN
from sql_harness.drivers.postgres import _PG_FORBIDDEN
from sql_harness.drivers.readonly import (
    ReadOnlyViolation,
    check_statement,
    install_guard,
)
from sql_harness.drivers.sqlite import SqliteDriver


# --- allowed -----------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select id, name from users where active = 1",
        "SHOW TABLES",
        "DESCRIBE users",
        "EXPLAIN SELECT * FROM users",
        "WITH recent AS (SELECT 1) SELECT * FROM recent",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        # Keywords inside literals and comments are not statements.
        "SELECT * FROM t WHERE note = 'DROP TABLE users'",
        "SELECT 1 -- DROP TABLE users",
        "SELECT /* DROP TABLE users */ 1",
    ],
)
def test_read_statements_are_allowed(sql: str) -> None:
    check_statement(sql)  # must not raise


# --- refused -----------------------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "CREATE TABLE t (id int)",
        "ALTER TABLE t ADD c int",
        "TRUNCATE t",
        "GRANT ALL ON *.* TO x",
        "REVOKE ALL ON *.* FROM x",
        # Not covered by an INSERT/UPDATE/DELETE deny-list — the reason this
        # guard allow-lists instead of deny-lists.
        "CALL some_procedure()",
        "DO SLEEP(1)",
        "LOAD DATA INFILE 'x' INTO TABLE t",
        "HANDLER t OPEN",
        # MySQL's `/!...*/` comments are executed by the server, so a guard
        # that treated them as comments would be defeated by this.
        "/*!50000 DROP TABLE t */",
        "/*! DELETE FROM t */",
        # Multi-statement: the second one must not ride along on the first.
        "SELECT 1; DROP TABLE t",
        # A CTE in front of a write.
        "WITH x AS (SELECT 1) DELETE FROM t",
        "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x",
        # PostgreSQL executes the inner statement of EXPLAIN ANALYZE.
        "EXPLAIN ANALYZE DELETE FROM t",
        # The escape from the *server-side* rail — refused here too, though
        # this is exactly the hole that makes the guard a rail, not a wall.
        "SET SESSION TRANSACTION READ WRITE",
    ],
)
def test_write_statements_are_refused(sql: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        check_statement(sql)


def test_unknown_verb_is_refused() -> None:
    """Fail closed: an unrecognised statement is refused, not permitted."""
    with pytest.raises(ReadOnlyViolation):
        check_statement("VACUUM FULL users")


# --- dialect-specific hazards ------------------------------------------------

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * INTO OUTFILE '/tmp/x' FROM t",
        "SELECT * INTO DUMPFILE '/tmp/x' FROM t",
        "SELECT * FROM t FOR UPDATE",
        "SELECT * FROM t LOCK IN SHARE MODE",
    ],
)
def test_mysql_extra_forbidden(sql: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        check_statement(sql, hash_comments=True, extra_forbidden=_MYSQL_FORBIDDEN)


@pytest.mark.parametrize(
    "sql",
    [
        # SELECT ... INTO creates a table in PostgreSQL.
        "SELECT * INTO new_table FROM t",
        "SELECT * FROM t FOR UPDATE",
        "SELECT * FROM t FOR NO KEY UPDATE",
    ],
)
def test_postgres_extra_forbidden(sql: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        check_statement(sql, extra_forbidden=_PG_FORBIDDEN)


def test_hash_is_a_comment_in_mysql_but_xor_in_postgres() -> None:
    """The one dialect switch that is a bypass if you get it wrong.

    MySQL: `#` comments out the rest of the line, so the DROP is not executed.
    PostgreSQL: `#` is bitwise XOR, so the DROP is live and must be caught —
    stripping it as a comment would hide the write.
    """
    sql = "SELECT 1 # ; DROP TABLE t"

    check_statement(sql, hash_comments=True)  # MySQL: genuinely a comment

    with pytest.raises(ReadOnlyViolation):
        check_statement(sql, hash_comments=False)  # PostgreSQL: real code


def test_postgres_into_does_not_match_lookalike_identifiers() -> None:
    """`INTO` is matched with its surrounding spaces, so `into_log` is fine."""
    check_statement("SELECT * FROM into_log", extra_forbidden=_PG_FORBIDDEN)


# --- engine integration ------------------------------------------------------

def test_guard_is_attached_to_the_engine_not_the_helpers() -> None:
    """The guard hooks `before_cursor_execute`, so it covers raw `conn.execute`.

    This is what makes it a driver-layer rail rather than a `query()`/`execute()`
    convention: any path to the engine is inspected.
    """
    engine = SqliteDriver().make_engine("sqlite://", PoolConfig(), read_only=True)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
            with pytest.raises(ReadOnlyViolation):
                conn.execute(text("CREATE TABLE t (id int)"))
    finally:
        engine.dispose()


def test_engine_is_unrestricted_without_the_flag() -> None:
    """Default stays permissive — read-only is opt-in per connection."""
    engine = SqliteDriver().make_engine("sqlite://", PoolConfig())
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE t (id int)"))
            conn.commit()
    finally:
        engine.dispose()


def test_install_guard_can_be_detached_from_session_sql() -> None:
    """`session_sql=None` (sqlite, which has no session rail) still guards."""
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    try:
        install_guard(engine, session_sql=None)
        with engine.connect() as conn:
            with pytest.raises(ReadOnlyViolation):
                conn.execute(text("DROP TABLE anything"))
    finally:
        engine.dispose()
