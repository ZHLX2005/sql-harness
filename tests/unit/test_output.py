"""Unit tests for output.py — spill policy and error rendering."""

from __future__ import annotations

from pathlib import Path

import pytest

from sql_harness.output import (
    SPILL_KEEP,
    render_exception,
    spill,
    write_spill,
)


def _spill_dir(home: Path) -> Path:
    return home / "tmp" / "output"


def test_short_text_is_returned_unchanged(isolated_home: Path) -> None:
    """The common case must stay byte-identical — no file, no pointer."""
    assert spill("hello", "test") == "hello"
    assert not _spill_dir(isolated_home).exists()


def test_long_text_is_spilled_with_a_pointer(isolated_home: Path) -> None:
    """Above the threshold: content goes to disk, a pointer takes its place."""
    body = "x" * 5000 + "\n"
    out = spill(body, "test")
    assert out.startswith("full output (")
    assert "lines," in out and "KB" in out

    written = list(_spill_dir(isolated_home).glob("test-*.log"))
    assert len(written) == 1
    assert written[0].read_text(encoding="utf-8") == body
    assert str(written[0]) in out


def test_write_spill_is_unconditional(isolated_home: Path) -> None:
    """The primitive ignores length — callers have already made the call."""
    pointer = write_spill("tiny", "test")
    assert "->" in pointer and "1 lines" in pointer


def test_two_spills_in_the_same_second_do_not_collide(
    isolated_home: Path,
) -> None:
    """The timestamp has one-second resolution, so names need disambiguating."""
    first = write_spill("a", "same")
    second = write_spill("b", "same")
    assert first != second
    assert len(list(_spill_dir(isolated_home).glob("same-*.log"))) == 2


def test_spills_are_pruned_to_the_keep_limit(isolated_home: Path) -> None:
    """Spill files are scratch: a long session must not grow the dir forever."""
    for i in range(SPILL_KEEP + 5):
        write_spill(f"body {i}", "many")
    assert len(list(_spill_dir(isolated_home).glob("many-*.log"))) == SPILL_KEEP


def test_short_exception_prints_whole(isolated_home: Path) -> None:
    """A few readable lines hide nothing, so summarizing would only add a read."""
    try:
        raise ValueError("tiny")
    except ValueError as exc:
        out = render_exception(exc)

    assert "Traceback (most recent call last)" in out
    assert "ValueError: tiny" in out
    assert not _spill_dir(isolated_home).exists()


def _deep_exception(depth: int = 8) -> BaseException:
    """Build an exception whose formatted traceback is genuinely long.

    Deep *recursion* does not work for this: Python 3.13+ collapses repeated
    frames, so 40 recursive calls format as 17 lines. Chaining distinct
    exceptions does — each `from` layer prints its own block.
    """
    exc: BaseException = ValueError("base")
    for i in range(depth):
        try:
            raise exc
        except Exception as inner:
            try:
                raise RuntimeError(f"layer {i}") from inner
            except Exception as outer:
                exc = outer
    return exc


def test_long_exception_summarizes_and_spills(isolated_home: Path) -> None:
    """A long stack is summarized down to the actionable line."""
    try:
        raise _deep_exception()
    except Exception as exc:
        out = render_exception(exc)

    assert out.startswith("sql-harness: RuntimeError")
    assert "full traceback (" in out
    assert str(_spill_dir(isolated_home)) in out
    # The stack itself went to the file, not to stderr.
    assert "Traceback (most recent call last)" not in out


def test_dbapi_errno_is_surfaced() -> None:
    """`(errno, message)` is the DBAPI convention; the errno is the searchable bit.

    Synthesized because sqlite reports no integer errno and unit tests have no
    MySQL server — pymysql's shape is what a real 1064 produces. Tested on the
    formatter directly, since a hand-raised exception is too short to trip the
    summarize threshold and would only exercise the pass-through.
    """
    from sql_harness.output import _summarize

    pymysql_err = pytest.importorskip("pymysql").err
    sqlalchemy_exc = pytest.importorskip("sqlalchemy").exc

    orig = pymysql_err.ProgrammingError(1064, "You have an error in your SQL syntax")
    wrapper = sqlalchemy_exc.ProgrammingError("SELECT * FORM users", {}, orig)

    headline, message, sql = _summarize(wrapper)
    assert headline == "ProgrammingError (1064)"
    assert message == "You have an error in your SQL syntax"
    assert sql == "SELECT * FORM users"


def test_chained_dbapi_error_is_unwrapped() -> None:
    """`raise ... from e` buries the driver error; the report must still find it."""
    from sql_harness.output import _summarize

    sqlalchemy_exc = pytest.importorskip("sqlalchemy").exc

    try:
        try:
            raise sqlalchemy_exc.OperationalError("SELECT 1", {}, ValueError("boom"))
        except Exception as inner:
            raise RuntimeError("outer") from inner
    except RuntimeError as exc:
        headline, message, sql = _summarize(exc)

    # The buried driver error wins over the wrapper the user raised.
    assert headline == "ValueError"
    assert message == "boom"
    assert sql == "SELECT 1"


def test_non_dbapi_exception_keeps_its_type() -> None:
    """No driver error anywhere in the chain -> the original type survives."""
    from sql_harness.output import _summarize

    try:
        raise KeyError("missing-column")
    except KeyError as exc:
        headline, message, sql = _summarize(exc)

    assert headline == "KeyError"
    assert "missing-column" in message
    assert sql is None
