"""Unit tests for manager.py — uses SQLite-in-memory via SQLAlchemy."""

from __future__ import annotations

from sqlalchemy import text

from sql_harness.config import ConnectionConfig, ConnectionsConfig, PoolConfig
from sql_harness.manager import SqlHarness


def _sqlite_config() -> ConnectionsConfig:
    return ConnectionsConfig(
        default_workspace="mem",
        pool_defaults=PoolConfig(size=2),
        connections=[
            ConnectionConfig(
                name="mem",
                driver="sqlite",
                url="sqlite:///:memory:",
            ),
        ],
    )


def test_workspace_lazy_open() -> None:
    h = SqlHarness(_sqlite_config())
    assert h.list_workspaces() == []
    ws = h.workspace("mem")
    assert h.list_workspaces() == ["mem"]
    assert h.has_workspace("mem")
    h.close_all()
    assert h.list_workspaces() == []


def test_workspace_idempotent() -> None:
    h = SqlHarness(_sqlite_config())
    a = h.workspace("mem")
    b = h.workspace("mem")
    assert a is b
    h.close_all()


def test_get_unknown_raises() -> None:
    h = SqlHarness(_sqlite_config())
    try:
        h.config.get("nope")
        assert False, "expected KeyError"
    except KeyError:
        pass
    finally:
        h.close_all()


def test_default_workspace_name() -> None:
    h = SqlHarness(_sqlite_config())
    assert h.default_workspace_name == "mem"


def test_close_workspace_unknown_is_noop() -> None:
    h = SqlHarness(_sqlite_config())
    h.close_workspace("nope")        # should not raise
    h.close_all()


def test_skills_no_dir(tmp_path, monkeypatch) -> None:
    """SqlHarness.apply_skill/list_skills is the legacy class-level API
    (decision C in the P-plan): it still reads workspace_dir()/skills/ unchanged.
    The canonical entry is the module-level helpers.py:apply_skill, which uses
    the zones/meta fallback. See test_helpers.py for the new behavior."""
    monkeypatch.setenv("BH_SQL_AGENT_WORKSPACE", str(tmp_path / "isolated_ws"))
    h = SqlHarness(_sqlite_config())
    assert h.list_skills() == []
    h.close_all()


def test_apply_skill_path_traversal_blocked(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BH_SQL_AGENT_WORKSPACE", str(tmp_path / "ws"))
    (tmp_path / "ws" / "skills").mkdir(parents=True)
    h = SqlHarness(_sqlite_config())
    for bad in ("../etc", "..\\etc", "sub/file", ".hidden"):
        try:
            h.apply_skill(bad)
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_select_1_via_sqlite() -> None:
    """End-to-end: open SQLite workspace, SELECT 1."""
    h = SqlHarness(_sqlite_config())
    ws = h.workspace("mem")
    with ws.engine.connect() as conn:
        row = conn.execute(text("SELECT 1")).scalar()
    assert row == 1
    h.close_all()