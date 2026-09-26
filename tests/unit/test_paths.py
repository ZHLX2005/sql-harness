"""Unit tests for paths.py — XDG-style resolution + env overrides."""

from __future__ import annotations

from pathlib import Path

from sql_harness import paths


def test_home_dir_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BH_SQL_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Either ~/.config or the OS equivalent
    result = paths.home_dir()
    assert result.name == "sql-harness"


def test_home_dir_bh_sql_home(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "custom"
    monkeypatch.setenv("BH_SQL_HOME", str(target))
    assert paths.home_dir() == target


def test_home_dir_xdg(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BH_SQL_HOME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert paths.home_dir() == tmp_path / "sql-harness"


def test_config_file_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BH_SQL_HOME", str(tmp_path))
    assert paths.config_file() == tmp_path / "connections.toml"


def test_config_file_override(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "my-config.toml"
    monkeypatch.setenv("BH_SQL_CONFIG_FILE", str(target))
    assert paths.config_file() == target


def test_workspace_dir_override(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "my-workspace"
    monkeypatch.setenv("BH_SQL_AGENT_WORKSPACE", str(target))
    assert paths.workspace_dir() == target


def test_ensure_private_dir(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested"
    result = paths.ensure_private_dir(target)
    assert result.is_dir()