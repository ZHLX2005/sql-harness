"""Unit tests for analytics.py — CLI call counters + doc-read tracking.

Analytics are ON by default (BH_SQL_ANALYTICS unset = enabled); set
BH_SQL_ANALYTICS=0|off|false|no to disable. Data lives in
$BH_SQL_HOME/analytics.json (JSON).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sql_harness import analytics


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point all sql-harness env vars at a fresh tmp dir for the test."""
    home = tmp_path / "sh"
    home.mkdir()
    monkeypatch.setenv("BH_SQL_HOME", str(home))
    monkeypatch.setenv("BH_SQL_AGENT_WORKSPACE", str(home / "agent-workspace"))
    monkeypatch.setenv("BH_SQL_CONFIG_FILE", str(home / "connections.toml"))
    return home


def _run_cli(*args: str, env: dict | None = None, input: str | None = None):
    """Run sql-harness in a subprocess (isolated BH_SQL_HOME via env)."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "sql_harness.run", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=full_env,
        input=input,
    )


# --- enable / disable -------------------------------------------------------


def test_enabled_by_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BH_SQL_ANALYTICS", raising=False)
    assert analytics.analytics_enabled() is True


@pytest.mark.parametrize("value", ["0", "off", "false", "no", "OFF", "False"])
def test_disabled_by_off_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("BH_SQL_ANALYTICS", value)
    assert analytics.analytics_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "on", "yes", "anything-else"])
def test_enabled_by_on_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("BH_SQL_ANALYTICS", value)
    assert analytics.analytics_enabled() is True


# --- recording --------------------------------------------------------------


def test_record_cli_accumulates(isolated_home: Path) -> None:
    analytics.record_cli("list")
    analytics.record_cli("list")
    analytics.record_cli("test")
    stats = analytics.load_stats()
    assert stats["total_runs"] == 3
    assert stats["by_subcommand"] == {"list": 2, "test": 1}
    assert (isolated_home / "analytics.json").exists()


def test_record_doc_read_appends(isolated_home: Path) -> None:
    analytics.record_doc_read("gost-tunnel.md", "apply_skill")
    analytics.record_doc_read("gost-tunnel.md", "apply_skill")
    analytics.record_doc_read("SKILL.md", "skill")
    stats = analytics.load_stats()
    assert stats["doc_reads"]["total"] == 3
    assert stats["doc_reads"]["by_doc"] == {"gost-tunnel.md": 2, "SKILL.md": 1}
    assert stats["doc_reads"]["by_via"] == {"apply_skill": 2, "skill": 1}
    recent = stats["doc_reads"]["recent"]
    assert len(recent) == 3
    assert recent[0]["doc"] == "SKILL.md"
    assert recent[0]["via"] == "skill"


def test_doc_reads_capped_at_500(isolated_home: Path) -> None:
    for i in range(510):
        analytics.record_doc_read(f"doc{i % 10}.md", "apply_skill")
    raw = json.loads((isolated_home / "analytics.json").read_text(encoding="utf-8"))
    assert len(raw["doc_reads"]) == 500
    stats = analytics.load_stats()
    assert stats["doc_reads"]["total"] == 500


def test_corrupt_analytics_file_recovers(isolated_home: Path) -> None:
    (isolated_home / "analytics.json").write_text("{not json!!", encoding="utf-8")
    analytics.record_cli("list")  # must not raise
    stats = analytics.load_stats()
    assert stats["total_runs"] == 1
    assert stats["by_subcommand"] == {"list": 1}


def test_disabled_writes_nothing(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BH_SQL_ANALYTICS", "0")
    analytics.record_cli("list")
    analytics.record_doc_read("x.md", "apply_skill")
    assert not (isolated_home / "analytics.json").exists()
    stats = analytics.load_stats()
    assert stats["enabled"] is False


# --- CLI integration (subprocess) -------------------------------------------


def test_cli_stats_subcommand_reports_calls(tmp_path: Path) -> None:
    env = {"BH_SQL_HOME": str(tmp_path)}
    assert _run_cli("init", env=env).returncode == 0
    assert _run_cli("list", env=env).returncode == 0
    p = _run_cli("stats", env=env)
    assert p.returncode == 0
    data = json.loads(p.stdout)
    # init + list + stats itself were each invoked once
    assert data["total_runs"] == 3
    assert data["by_subcommand"]["init"] == 1
    assert data["by_subcommand"]["list"] == 1
    assert data["by_subcommand"]["stats"] == 1


def test_cli_heredoc_apply_skill_tracks_doc_read(tmp_path: Path) -> None:
    env = {
        "BH_SQL_HOME": str(tmp_path),
        "BH_SQL_AGENT_WORKSPACE": str(tmp_path / "agent-workspace"),
    }
    # Seed a meta skill so apply_skill resolves (needs an active workspace).
    assert _run_cli("init", env=env).returncode == 0
    meta = tmp_path / "agent-workspace" / "zones" / "meta" / "skills"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "foo.md").write_text("# foo\n\nknowledge\n", encoding="utf-8")

    body = "use_workspace('local_pg')\nprint(apply_skill('foo'))\n"
    p = _run_cli(input=body, env=env)
    assert p.returncode == 0, p.stderr

    p = _run_cli("stats", env=env)
    data = json.loads(p.stdout)
    assert data["total_runs"] == 3  # init + heredoc + stats
    assert data["by_subcommand"].get("heredoc") == 1
    assert data["doc_reads"]["by_doc"] == {"foo.md": 1}
    assert data["doc_reads"]["by_via"] == {"apply_skill": 1}


def test_help_lists_stats_subcommand(tmp_path: Path) -> None:
    p = _run_cli("--help", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0
    assert "stats" in p.stdout


# --- event-bus interface (observer pattern) ---------------------------------


def test_on_emit_delivers_payload(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BH_SQL_ANALYTICS", raising=False)
    received: list[dict] = []
    analytics.on(analytics.CLI_INVOKED, received.append)
    try:
        analytics.emit(analytics.CLI_INVOKED, {"subcommand": "list"})
        assert received == [{"subcommand": "list"}]
    finally:
        analytics.off(analytics.CLI_INVOKED, received.append)


def test_emit_swallows_handler_errors(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing listener must not break other listeners or the caller."""
    monkeypatch.delenv("BH_SQL_ANALYTICS", raising=False)
    received: list[str] = []

    def boom(payload: dict) -> None:
        raise RuntimeError("listener bug")

    def ok(payload: dict) -> None:
        received.append("ok")

    analytics.on(analytics.CLI_INVOKED, boom)
    analytics.on(analytics.CLI_INVOKED, ok)
    try:
        analytics.emit(analytics.CLI_INVOKED, {"subcommand": "list"})  # no raise
        assert received == ["ok"]
    finally:
        analytics.off(analytics.CLI_INVOKED, boom)
        analytics.off(analytics.CLI_INVOKED, ok)


def test_off_removes_handler(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BH_SQL_ANALYTICS", raising=False)
    received: list[dict] = []
    analytics.on(analytics.DOC_READ, received.append)
    analytics.off(analytics.DOC_READ, received.append)
    analytics.emit(analytics.DOC_READ, {"doc": "x.md", "via": "apply_skill"})
    assert received == []


def test_emit_does_not_dispatch_when_disabled(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BH_SQL_ANALYTICS", "0")
    received: list[dict] = []
    analytics.on(analytics.CLI_INVOKED, received.append)
    try:
        analytics.emit(analytics.CLI_INVOKED, {"subcommand": "list"})
        assert received == []
    finally:
        analytics.off(analytics.CLI_INVOKED, received.append)


def test_record_cli_emits_event_to_listeners(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """record_* are the built-in listeners; custom listeners share the bus."""
    monkeypatch.delenv("BH_SQL_ANALYTICS", raising=False)
    received: list[dict] = []
    analytics.on(analytics.CLI_INVOKED, received.append)
    try:
        analytics.record_cli("test")
        assert received == [{"subcommand": "test"}]
    finally:
        analytics.off(analytics.CLI_INVOKED, received.append)


# --- command execution log (NDJSON, append-only) ----------------------------


def _read_log(isolated_home: Path) -> list[dict]:
    path = isolated_home / "sql-harness.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_command_executed_event_appends_ndjson(isolated_home: Path) -> None:
    analytics.emit(
        analytics.COMMAND_EXECUTED,
        {"argv": ["ssh", "exec", "docker ps"], "code": None, "ok": True,
         "exit_code": 0, "error": None},
    )
    records = _read_log(isolated_home)
    assert len(records) == 1
    r = records[0]
    assert r["argv"] == ["ssh", "exec", "docker ps"]
    assert r["ok"] is True and r["exit_code"] == 0
    assert "ts" in r


def test_command_executed_records_failure_with_error(isolated_home: Path) -> None:
    analytics.emit(
        analytics.COMMAND_EXECUTED,
        {"argv": ["list"], "code": None, "ok": False, "exit_code": 2,
         "error": "no connection named 'x'"},
    )
    r = _read_log(isolated_home)[0]
    assert r["ok"] is False and r["exit_code"] == 2
    assert r["error"] == "no connection named 'x'"


def test_command_executed_keeps_full_history(isolated_home: Path) -> None:
    for i in range(3):
        analytics.emit(
            analytics.COMMAND_EXECUTED,
            {"argv": [f"cmd{i}"], "code": None, "ok": True, "exit_code": 0, "error": None},
        )
    assert len(_read_log(isolated_home)) == 3  # append-only, never truncated


def test_command_log_disabled_with_analytics(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BH_SQL_ANALYTICS", "0")
    analytics.emit(
        analytics.COMMAND_EXECUTED,
        {"argv": ["list"], "code": None, "ok": True, "exit_code": 0, "error": None},
    )
    assert not (isolated_home / "sql-harness.log").exists()


def test_cli_logs_executed_commands(tmp_path: Path) -> None:
    env = {"BH_SQL_HOME": str(tmp_path)}
    assert _run_cli("init", env=env).returncode == 0
    assert _run_cli("list", env=env).returncode == 0
    log_path = tmp_path / "sql-harness.log"
    records = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines()]
    assert [r["argv"][0] for r in records] == ["init", "list"]
    assert all(r["ok"] is True for r in records)


def test_cli_logs_heredoc_with_code_and_failure(tmp_path: Path) -> None:
    env = {"BH_SQL_HOME": str(tmp_path)}
    p = _run_cli(
        input="print('hello')\n1/0\n", env=env
    )  # heredoc body + runtime error
    assert p.returncode == 1
    log_path = tmp_path / "sql-harness.log"
    records = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    r = records[0]
    assert r["argv"] == [] and r["code"] == "print('hello')\n1/0\n"
    assert r["ok"] is False and r["exit_code"] == 1
    assert "ZeroDivisionError" in r["error"]
