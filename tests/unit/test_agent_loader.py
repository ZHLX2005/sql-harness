"""Unit tests for agent_loader.py — verifies public-name merging + path safety."""

from __future__ import annotations

from pathlib import Path


def test_load_no_file_is_noop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BH_SQL_AGENT_WORKSPACE", str(tmp_path / "nope"))
    from sql_harness.agent_loader import load_agent_helpers

    g: dict = {}
    assert load_agent_helpers(g) == 0


def test_load_merges_public_names(tmp_path: Path, monkeypatch) -> None:
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir(parents=True)
    (ws_dir / "agent_helpers.py").write_text(
        "PUBLIC_NAME = 'public'\n"
        "_private = 'hidden'\n"
        "def my_helper():\n"
        "    return 'hi'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BH_SQL_AGENT_WORKSPACE", str(ws_dir))
    from sql_harness.agent_loader import load_agent_helpers

    g: dict = {}
    load_agent_helpers(g)
    # The agent's own public names are merged (pre-seeded core helpers are too).
    assert g["PUBLIC_NAME"] == "public"
    assert g["my_helper"]() == "hi"
    assert "_private" not in g
    # Core helpers are pre-seeded so agent functions can call them.
    assert "query" in g and "execute" in g and "use_workspace" in g


def test_env_override(tmp_path: Path, monkeypatch) -> None:
    custom = tmp_path / "custom_ws"
    custom.mkdir()
    (custom / "agent_helpers.py").write_text("X = 42\n", encoding="utf-8")
    monkeypatch.setenv("BH_SQL_AGENT_WORKSPACE", str(custom))
    from sql_harness.agent_loader import load_agent_helpers

    g: dict = {}
    load_agent_helpers(g)
    assert g["X"] == 42