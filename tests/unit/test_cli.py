"""Unit tests for cli.py — argparse parsing + cmd_* dispatch (no real DB)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _run_cli(
    *args: str, env: dict | None = None, input: str | None = None
) -> subprocess.CompletedProcess:
    """Run sql-harness in a subprocess; capture UTF-8 output.

    `input` (when given) is piped to the child's stdin as UTF-8 — used to
    exercise `save`, which reads its heredoc body from stdin.
    """
    import os

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


def test_help_lists_all_subcommands(tmp_path: Path) -> None:
    p = _run_cli("--help", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0
    for sub in [
        "list", "add", "edit", "remove", "show", "test",
        "workspace", "skill", "web", "init", "version", "doctor",
    ]:
        assert sub in p.stdout


def test_version_json(tmp_path: Path) -> None:
    p = _run_cli("version", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0
    data = json.loads(p.stdout)
    assert "sql-harness" in data
    assert "psycopg" in data
    assert "sqlalchemy" in data


def test_init_writes_example(tmp_path: Path) -> None:
    p = _run_cli("init", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0
    data = json.loads(p.stdout)
    assert data["existed"] is False
    assert (tmp_path / "connections.toml").exists()


def test_init_idempotent(tmp_path: Path) -> None:
    _run_cli("init", env={"BH_SQL_HOME": str(tmp_path)})
    p = _run_cli("init", env={"BH_SQL_HOME": str(tmp_path)})
    data = json.loads(p.stdout)
    assert data["existed"] is True


def test_list_shows_example_connections(tmp_path: Path) -> None:
    _run_cli("init", env={"BH_SQL_HOME": str(tmp_path)})
    p = _run_cli("list", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0
    data = json.loads(p.stdout)
    names = [c["name"] for c in data["connections"]]
    assert names == ["local_pg", "local_mysql"]


def test_show_masks_password(tmp_path: Path) -> None:
    _run_cli("init", env={"BH_SQL_HOME": str(tmp_path)})
    p = _run_cli("show", "local_pg", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0
    data = json.loads(p.stdout)
    assert "***" in data["url"]
    # Original is postgresql+psycopg://postgres:postgres@...; the password
    # is the second occurrence of "postgres" in the URL — verify it's masked.
    assert "postgres:postgres" not in data["url"]


def test_show_unknown_returns_2(tmp_path: Path) -> None:
    _run_cli("init", env={"BH_SQL_HOME": str(tmp_path)})
    p = _run_cli("show", "nope", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 2


def test_remove_then_list(tmp_path: Path) -> None:
    _run_cli("init", env={"BH_SQL_HOME": str(tmp_path)})
    p = _run_cli("remove", "local_mysql", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0
    p2 = _run_cli("list", env={"BH_SQL_HOME": str(tmp_path)})
    names = [c["name"] for c in json.loads(p2.stdout)["connections"]]
    assert "local_mysql" not in names


def test_workspace_list_empty(tmp_path: Path) -> None:
    p = _run_cli("workspace", "list", env={"BH_SQL_HOME": str(tmp_path)})
    data = json.loads(p.stdout)
    assert data["open"] == []


def test_skill_list_empty(tmp_path: Path) -> None:
    # skill list is zone-scoped; --connection selects the DSN zone.
    p = _run_cli("skill", "-c", "test_pg", "list", env={"BH_SQL_HOME": str(tmp_path)})
    data = json.loads(p.stdout)
    assert data["connection"] == "test_pg"
    assert data["skills"] == []


def test_skill_list_requires_connection(tmp_path: Path) -> None:
    p = _run_cli("skill", "list", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 2
    assert "no active connection" in p.stderr


def test_skill_emit_outputs_packaged_skill_md(tmp_path: Path) -> None:
    """Bare `sql-harness skill` emits the packaged SKILL.md to stdout."""
    p = _run_cli("skill", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0
    body = p.stdout
    assert body.startswith("---")
    assert "name: sql-harness" in body
    assert "description:" in body
    assert "# sql-harness" in body


def test_skill_emit_is_installable(tmp_path: Path) -> None:
    """The emitted body is valid YAML-frontmattered markdown (installable)."""
    p = _run_cli("skill", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0
    lines = p.stdout.splitlines()
    assert lines[0].strip() == "---"
    # Find closing frontmatter delimiter.
    close = next(i for i, ln in enumerate(lines[1:], start=1) if ln.strip() == "---")
    frontmatter = "\n".join(lines[1:close])
    assert "name:" in frontmatter
    assert "description:" in frontmatter


def test_doctor_no_connections(tmp_path: Path) -> None:
    """Doctor on an empty config reports no results and exits 0."""
    p = _run_cli("doctor", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0
    data = json.loads(p.stdout)
    assert data["results"] == []


def test_doctor_reports_unreachable(tmp_path: Path) -> None:
    """Doctor against an unreachable connection returns ok=False, exit 1."""
    # connect_timeout=2 keeps psycopg from hanging on connection retry.
    cfg = tmp_path / "connections.toml"
    cfg.write_text(
        '[[connections]]\n'
        'name = "bogus"\n'
        'driver = "postgres"\n'
        'url = "postgresql://nobody:nobody@127.0.0.1:1/none?connect_timeout=2"\n',
        encoding="utf-8",
    )
    p = _run_cli("doctor", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 1
    data = json.loads(p.stdout)
    assert len(data["results"]) == 1
    assert data["results"][0]["ok"] is False


def test_doctor_with_sqlite(tmp_path: Path) -> None:
    """Doctor against a working SQLite connection returns ok=True, exit 0."""
    cfg = tmp_path / "connections.toml"
    cfg.write_text(
        '[[connections]]\n'
        'name = "mem"\n'
        'driver = "sqlite"\n'
        'url = "sqlite:///:memory:"\n',
        encoding="utf-8",
    )
    p = _run_cli("doctor", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0
    data = json.loads(p.stdout)
    assert data["results"][0]["ok"] is True
    assert "latency_ms" in data["results"][0]


def test_paths_lists_global_dirs(tmp_path: Path) -> None:
    """`paths` prints every global folder the CLI involves, with existence."""
    p = _run_cli("paths", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0
    data = json.loads(p.stdout)
    names = {r["name"] for r in data["global"]}
    # The key folders a user wants to open must all be present.
    assert {"home", "config_file", "agent_workspace", "meta_skills",
            "package_root", "package_source"} <= names
    # config_file should point inside the isolated BH_SQL_HOME.
    cf = next(r for r in data["global"] if r["name"] == "config_file")
    assert str(tmp_path) in cf["path"]


def test_paths_zone_with_connection(tmp_path: Path) -> None:
    """`paths -c CONN` adds the zone section with per-DSN dirs."""
    cfg = tmp_path / "connections.toml"
    cfg.write_text(
        '[[connections]]\n'
        'name = "mem"\n'
        'driver = "sqlite"\n'
        'url = "sqlite:///:memory:"\n',
        encoding="utf-8",
    )
    p = _run_cli("paths", "-c", "mem", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0
    data = json.loads(p.stdout)
    assert data["zone"]["connection"] == "mem"
    znames = {d["name"] for d in data["zone"]["dirs"]}
    assert {"zone_dir", "zone_scripts", "zone_skills", "zone_helpers"} == znames


def test_open_scripts_requires_connection(tmp_path: Path) -> None:
    """`open scripts` without --connection errors with guidance."""
    p = _run_cli("open", "scripts", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 2
    assert "zone-scoped" in p.stderr


def _write_sqlite_cfg(tmp_path: Path) -> None:
    """Seed an isolated BH_SQL_HOME with a single sqlite connection."""
    (tmp_path / "connections.toml").write_text(
        '[[connections]]\n'
        'name = "mem"\n'
        'driver = "sqlite"\n'
        'url = "sqlite:///:memory:"\n',
        encoding="utf-8",
    )


def test_save_preserves_utf8_stdin(tmp_path: Path) -> None:
    """`save` reads stdin as UTF-8 even on Windows (cp936 default locale).

    Regression: non-ASCII bytes in the heredoc body were decoded into
    surrogates and write_text(utf-8) raised UnicodeEncodeError.
    """
    _write_sqlite_cfg(tmp_path)
    body = "# 服务器内存检查\nprint('ok')\n"
    p = _run_cli(
        "save", "utf8", "-c", "mem", input=body, env={"BH_SQL_HOME": str(tmp_path)}
    )
    assert p.returncode == 0, p.stderr
    saved = Path(json.loads(p.stdout)["path"])
    assert "服务器内存检查" in saved.read_text(encoding="utf-8")


def test_run_namespace_exposes_sys(tmp_path: Path) -> None:
    """`run` exec namespace includes `sys`, matching heredoc mode.

    Regression: saved scripts that used `sys.stdout.write` without
    `import sys` (as ssh.md documents) failed with NameError.
    """
    _write_sqlite_cfg(tmp_path)
    body = "sys.stdout.write('ok-from-sys')\n"  # relies on pre-imported sys
    p = _run_cli(
        "save", "usesys", "-c", "mem", input=body, env={"BH_SQL_HOME": str(tmp_path)}
    )
    assert p.returncode == 0, p.stderr
    p2 = _run_cli("run", "usesys", "-c", "mem", env={"BH_SQL_HOME": str(tmp_path)})
    assert p2.returncode == 0, p2.stderr
    assert "ok-from-sys" in p2.stdout


# --- standalone password + SSH-aware probing -------------------------------


def test_show_and_list_do_not_leak_standalone_password(tmp_path: Path) -> None:
    (tmp_path / "connections.toml").write_text(
        '[[connections]]\n'
        'name = "iot"\n'
        'driver = "ssh"\n'
        'url = "ssh://113.44.193.72:22"\n'
        'password = "Iot66688"\n',
        encoding="utf-8",
    )
    show = _run_cli("show", "iot", env={"BH_SQL_HOME": str(tmp_path)})
    assert show.returncode == 0, show.stderr
    assert "Iot66688" not in show.stdout
    assert json.loads(show.stdout)["password_set"] is True

    listed = _run_cli("list", env={"BH_SQL_HOME": str(tmp_path)})
    assert listed.returncode == 0, listed.stderr
    assert "Iot66688" not in listed.stdout
    assert json.loads(listed.stdout)["connections"][0]["password_set"] is True


def test_add_accepts_password_flag(tmp_path: Path) -> None:
    p = _run_cli(
        "add", "--name", "iot", "--driver", "ssh",
        "--url", "ssh://113.44.193.72:22",
        "--password", "Iot66688",
        env={"BH_SQL_HOME": str(tmp_path)},
    )
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["password_set"] is True
    written = (tmp_path / "connections.toml").read_text(encoding="utf-8")
    assert 'password = "Iot66688"' in written


class _FakeCmdResult:
    command = "echo sql-harness-ok"
    stdout = "sql-harness-ok\n"
    stderr = ""
    exit_code = 0
    ok = True


class _FakeSshDriver:
    name = "ssh"


class _FakeSshEngine:
    sftp = object()

    def exec(self, command: str, timeout: float = 30.0):
        return _FakeCmdResult()


class _FakeSshWorkspace:
    driver = _FakeSshDriver()
    engine = _FakeSshEngine()


def test_probe_ssh_workspace_uses_remote_echo() -> None:
    """Regression: doctor/test called engine.connect() on SSH handles."""
    from sql_harness.cli import _probe_workspace

    r = _probe_workspace(_FakeSshWorkspace())
    assert r["ok"] is True
    assert r["probe"] == "echo sql-harness-ok"
    assert r["stdout"] == "sql-harness-ok"
    assert r["sftp_available"] is True


def test_probe_ssh_workspace_reports_failure() -> None:
    from sql_harness.cli import _probe_workspace

    class _FailingEngine(_FakeSshEngine):
        def exec(self, command: str, timeout: float = 30.0):
            class R:
                command = "echo sql-harness-ok"
                stdout = ""
                stderr = "boom"
                exit_code = 1
                ok = False

            return R()

    ws = type("WS", (), {"driver": _FakeSshDriver(), "engine": _FailingEngine()})()
    r = _probe_workspace(ws)
    assert r["ok"] is False
    assert r["exit_code"] == 1
    assert r["stderr"] == "boom"


def test_probe_sql_workspace_selects_1() -> None:
    from sqlalchemy import create_engine

    from sql_harness.cli import _probe_workspace

    ws = type(
        "WS",
        (),
        {
            "driver": type("D", (), {"name": "sqlite"})(),
            "engine": create_engine("sqlite://"),
        },
    )()
    r = _probe_workspace(ws)
    assert r["ok"] is True
    assert r["select_1"] == 1


# --- stream encoding + long-output spill ------------------------------------


# U+02B1 and U+21C4 are outside cp936, the codec a Windows console defaults to.
_NON_GBK = "ʱ ⇄ 服务器"


def test_heredoc_non_ascii_stdout_exits_zero(tmp_path: Path) -> None:
    """Non-ASCII printed to stdout must not abort the run.

    Regression: stdout kept the locale codec (GBK/CP936 on Windows) while only
    stdin was reconfigured, so printing a char outside GBK raised
    UnicodeEncodeError and killed the whole heredoc. The parent decodes UTF-8,
    so a clean round-trip proves the child encoded UTF-8 too.
    """
    p = _run_cli(
        input=f"print({_NON_GBK!r})\n", env={"BH_SQL_HOME": str(tmp_path)}
    )
    assert p.returncode == 0, p.stderr
    assert _NON_GBK in p.stdout


def test_list_emits_non_ascii_description(tmp_path: Path) -> None:
    """The `_emit` JSON path must survive non-ASCII from user-authored TOML.

    `json.dumps(..., ensure_ascii=False)` writes the raw characters, so this
    covers every subcommand that reports config back (list/show/paths).
    """
    (tmp_path / "connections.toml").write_text(
        '[[connections]]\n'
        'name = "mem"\n'
        'driver = "sqlite"\n'
        'url = "sqlite:///:memory:"\n'
        f'description = "{_NON_GBK}"\n',
        encoding="utf-8",
    )
    p = _run_cli("list", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["connections"][0]["description"] == _NON_GBK


def test_short_output_is_not_spilled(tmp_path: Path) -> None:
    """Guard the threshold: ordinary output stays inline, with no pointer."""
    p = _run_cli("list", env={"BH_SQL_HOME": str(tmp_path)})
    assert p.returncode == 0, p.stderr
    assert "tmp" not in p.stdout and "output" not in p.stdout
    assert not (tmp_path / "tmp" / "output").exists()


def test_db_error_is_concise_on_stderr_but_complete_in_log(
    tmp_path: Path,
) -> None:
    """A DB failure: short and actionable on stderr, whole in the log.

    The compressed form exists to surface what a ~70-line SQLAlchemy stack
    buries — the driver's own message, the SQL, and the user's line — while
    the analytics NDJSON stays the complete, traceable record.
    """
    _write_sqlite_cfg(tmp_path)
    p = _run_cli(
        input="use_workspace('mem')\nquery('SELECT * FROM nope')\n",
        env={"BH_SQL_HOME": str(tmp_path)},
    )
    assert p.returncode == 1
    assert "OperationalError" in p.stderr
    assert "no such table: nope" in p.stderr
    assert "sql: SELECT * FROM nope" in p.stderr
    assert "at:  <string>:2" in p.stderr
    assert "site-packages" not in p.stderr
    assert p.stderr.count("\n") <= 8

    # The full traceback survives in the log — this is the "traceable" contract.
    records = [
        json.loads(line)
        for line in (tmp_path / "sql-harness.log")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    # The log stores the payload flattened (plus a "ts"), not wrapped in an
    # event envelope — "error" is the field that marks a command.executed row.
    executed = [r for r in records if "error" in r]
    assert executed and executed[-1]["ok"] is False
    assert "Traceback (most recent call last)" in executed[-1]["error"]
    assert "site-packages" in executed[-1]["error"]

    # And the spill file named on stderr really holds the whole stack.
    spill_dir = tmp_path / "tmp" / "output"
    spilled = list(spill_dir.glob("*.log"))
    assert len(spilled) == 1
    body = spilled[0].read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in body
    assert str(spilled[0]) in p.stderr


def test_traceback_env_restores_full_stack(tmp_path: Path) -> None:
    """BH_SQL_TRACEBACK=1 opts back into the raw traceback.

    An env var rather than a flag because in heredoc mode every argument is
    routed to the CLI subparser, so a flag could never reach this path.
    """
    p = _run_cli(
        input="raise RuntimeError('boom')\n",
        env={"BH_SQL_HOME": str(tmp_path), "BH_SQL_TRACEBACK": "1"},
    )
    assert p.returncode == 1
    assert "Traceback (most recent call last)" in p.stderr
    assert "RuntimeError: boom" in p.stderr


def test_long_error_spills_but_short_one_prints_whole(tmp_path: Path) -> None:
    """Length decides: a screenful goes to a file, a few lines stay inline."""
    _write_sqlite_cfg(tmp_path)
    long = _run_cli(
        input="use_workspace('mem')\nquery('SELECT * FROM nope')\n",
        env={"BH_SQL_HOME": str(tmp_path)},
    )
    assert "full traceback" in long.stderr

    short = _run_cli(
        input="raise ValueError('tiny')\n", env={"BH_SQL_HOME": str(tmp_path)}
    )
    assert "Traceback (most recent call last)" in short.stderr
    assert "full traceback" not in short.stderr