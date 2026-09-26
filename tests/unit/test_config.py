"""Unit tests for config.py — TOML load/save, env expansion, masking."""

from __future__ import annotations

import os
from pathlib import Path

from sql_harness.config import (
    ConnectionConfig,
    ConnectionsConfig,
    PoolConfig,
    example_config,
    load,
    save,
)


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    cfg = load(tmp_path / "nope.toml")
    assert cfg.connections == []
    assert cfg.default_workspace == ""


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    cfg = example_config()
    p = tmp_path / "connections.toml"
    save(cfg, p)
    loaded = load(p)
    assert loaded.names() == cfg.names()
    assert loaded.default_workspace == "local_pg"
    assert loaded.connections[0].url == cfg.connections[0].url


def test_env_expansion_in_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PROD_PG_URL", "postgresql://u:p@h/d")
    p = tmp_path / "c.toml"
    p.write_text(
        '[[connections]]\n'
        'name = "prod"\n'
        'driver = "postgres"\n'
        'url = "${env:PROD_PG_URL}"\n',
        encoding="utf-8",
    )
    cfg = load(p)
    assert cfg.get("prod").url == "postgresql://u:p@h/d"


def test_env_expansion_missing_var_is_empty(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    p = tmp_path / "c.toml"
    p.write_text(
        '[[connections]]\n'
        'name = "x"\n'
        'driver = "postgres"\n'
        'url = "postgresql://u:${env:MISSING_VAR}@h/d"\n',
        encoding="utf-8",
    )
    cfg = load(p)
    assert cfg.get("x").url == "postgresql://u:@h/d"


def test_masked_url() -> None:
    c = ConnectionConfig(
        name="x", driver="postgres",
        url="postgresql+psycopg://user:secret@localhost/db",
    )
    assert ":***@" in c.masked_url()
    assert "secret" not in c.masked_url()


def test_standalone_password_roundtrip(tmp_path: Path) -> None:
    """The `password` field survives save → load (SSH-style credential)."""
    cfg = ConnectionsConfig(
        default_workspace="",
        connections=[
            ConnectionConfig(
                name="iot",
                driver="ssh",
                url="ssh://113.44.193.72:22",
                password="Iot66688",
            ),
        ],
    )
    p = tmp_path / "c.toml"
    save(cfg, p)
    assert 'password = "Iot66688"' in p.read_text(encoding="utf-8")
    loaded = load(p)
    assert loaded.get("iot").password == "Iot66688"
    assert loaded.get("iot").url == "ssh://113.44.193.72:22"


def test_standalone_password_env_expansion(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("IOT_SSH_PASSWORD", "s3cret")
    p = tmp_path / "c.toml"
    p.write_text(
        '[[connections]]\n'
        'name = "iot"\n'
        'driver = "ssh"\n'
        'url = "ssh://113.44.193.72:22"\n'
        'password = "${env:IOT_SSH_PASSWORD}"\n',
        encoding="utf-8",
    )
    assert load(p).get("iot").password == "s3cret"


def test_password_omitted_when_empty(tmp_path: Path) -> None:
    """No empty `password = ""` noise in the written TOML."""
    cfg = ConnectionsConfig(
        default_workspace="",
        connections=[
            ConnectionConfig(name="pg", driver="postgres", url="postgresql://localhost"),
        ],
    )
    p = tmp_path / "c.toml"
    save(cfg, p)
    assert "password" not in p.read_text(encoding="utf-8")


def test_masked_password() -> None:
    assert ConnectionConfig(name="a", driver="ssh", url="ssh://h").masked_password() == ""
    assert (
        ConnectionConfig(
            name="a", driver="ssh", url="ssh://h", password="pw"
        ).masked_password()
        == "***"
    )


def test_pool_defaults_propagate() -> None:
    cfg = ConnectionsConfig(
        default_workspace="",
        pool_defaults=PoolConfig(size=20, recycle=7200),
        connections=[
            ConnectionConfig(
                name="only",
                driver="postgres",
                url="postgresql://localhost",
            ),
        ],
    )
    cfg.apply_pool_defaults()
    assert cfg.get("only").pool.size == 20
    assert cfg.get("only").pool.recycle == 7200


def test_pool_per_connection_overrides_defaults() -> None:
    cfg = ConnectionsConfig(
        default_workspace="",
        pool_defaults=PoolConfig(size=5, recycle=3600),
        connections=[
            ConnectionConfig(
                name="custom",
                driver="postgres",
                url="postgresql://localhost",
                pool=PoolConfig(size=20, recycle=1800),
            ),
        ],
    )
    cfg.apply_pool_defaults()
    # apply_pool_defaults replaces pure-default instances; the custom one stays.
    assert cfg.get("custom").pool.size == 20


def test_sqlalchemy_kwargs() -> None:
    p = PoolConfig(size=3, recycle=600, pre_ping=False, echo=True)
    kwargs = p.sqlalchemy_kwargs()
    assert kwargs == {
        "pool_size": 3,
        "pool_recycle": 600,
        "pool_pre_ping": False,
        "echo": True,
    }


def test_postgres_driver_accepts_short_alias() -> None:
    """PostgresDriver must accept postgres://, postgresql://, postgresql+psycopg://."""
    from sql_harness.drivers.postgres import PostgresDriver

    d = PostgresDriver()
    # We can't open a real engine here; just verify make_engine reaches
    # create_engine without raising ValueError on URL-prefix validation.
    # create_engine itself is lazy — it doesn't connect until first use.
    for url in (
        "postgres://u:p@localhost/db",
        "postgresql://u:p@localhost/db",
        "postgresql+psycopg://u:p@localhost/db",
    ):
        eng = d.make_engine(url, PoolConfig())
        assert eng is not None
        eng.dispose()


def test_postgres_driver_rejects_wrong_scheme() -> None:
    from sql_harness.drivers.postgres import PostgresDriver

    d = PostgresDriver()
    import pytest

    with pytest.raises(ValueError, match="must start with postgres"):
        d.make_engine("mysql://u:p@localhost/db", PoolConfig())