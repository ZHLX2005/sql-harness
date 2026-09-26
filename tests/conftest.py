"""Test fixtures for sql-harness."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point all sql-harness env vars at a fresh tmp dir for the test."""
    home = tmp_path / "sh"
    home.mkdir()
    monkeypatch.setenv("BH_SQL_HOME", str(home))
    monkeypatch.setenv("BH_SQL_AGENT_WORKSPACE", str(home / "agent-workspace"))
    monkeypatch.setenv("BH_SQL_CONFIG_FILE", str(home / "connections.toml"))
    return home