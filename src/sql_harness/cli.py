"""sql-harness CLI — argparse subparsers + cmd_* dispatch.

Subcommands:
    list                     Show all configured connections.
    add [--name N --driver D --url U ...]   Add a connection (interactive if missing flags).
    edit <name>              Open connections.toml in $EDITOR.
    remove <name>            Remove a connection.
    show <name>              Print details (password masked).
    test <name>              Open engine, SELECT 1, close; report latency.
    workspace list           List open workspaces in this process (usually empty for CLI).
    workspace use <name>     Print the masked connection details; signals intent.
    workspace close <name>   Close + dispose an engine (no-op if not open).
    workspace show <name>    Same as `show`.
    skill list               List available agent-workspace skills.
    skill show <name>        Print the skill markdown.
    save <name>              Persist stdin (a heredoc) as agent-workspace/scripts/<name>.py.
    run <name>               Execute a saved script (scripts/<name>.py) in the helpers namespace.
    scripts                  List saved scripts.
    web [--port N --host H]  Serve the doc library (browse + edit) on loopback.
    init                     Write a starter connections.toml if none exists.
    version                  Print sql-harness version + driver versions.
    doctor                   Sanity-check config + try SELECT 1 on each connection.

Mirrors `lab/subprocess/cli.py` style.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Callable

from sqlalchemy import text

from . import __version__
from .analytics import CLI_INVOKED, COMMAND_EXECUTED, DOC_READ, emit, load_stats
from .config import (
    ConnectionConfig,
    PoolConfig,
    load as load_config,
    save as save_config,
)
from .manager import SqlHarness
from .output import render_exception, set_utf8_streams, spill
from .paths import (
    config_file,
    ensure_private_dir,
    home_dir,
    scripts_dir,
    workspace_dir,
    zone_dir,
    zone_scripts_dir,
    zone_skills_dir,
)


# --- Output helpers --------------------------------------------------------

def _emit(data) -> None:
    """Print structured data as JSON; plain text otherwise."""
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    else:
        print(data)


# --- Subcommand implementations --------------------------------------------

def cmd_list(_args, harness: SqlHarness) -> int:
    cfg = harness.config
    if not cfg.connections:
        _emit({"connections": [], "hint": "run `sql-harness init` to scaffold one"})
        return 0
    rows = []
    for c in cfg.connections:
        rows.append({
            "name": c.name,
            "driver": c.driver,
            "url": c.masked_url(),
            "description": c.description,
            "password_set": bool(c.password),
            "read_only": c.read_only,
            "pool_size": c.pool.size,
        })
    _emit({"default_workspace": cfg.default_workspace, "connections": rows})
    return 0


def cmd_add(args, harness: SqlHarness) -> int:
    cfg = harness.config
    # Pull from flags or prompt.
    name = args.name or input("connection name: ").strip()
    if not name:
        print("error: name required", file=sys.stderr)
        return 2
    if any(c.name == name for c in cfg.connections):
        print(f"error: connection {name!r} already exists", file=sys.stderr)
        return 2

    driver = args.driver or input("driver (postgres|mysql|redis|sqlite|ssh): ").strip()
    if driver not in ("postgres", "mysql", "redis", "sqlite", "ssh", "ssh+password", "ssh+key"):
        print(f"error: unknown driver {driver!r}", file=sys.stderr)
        return 2

    url = args.url or input("URL: ").strip()
    if not url:
        print("error: url required", file=sys.stderr)
        return 2

    description = args.description or ""
    password = getattr(args, "password", None) or ""
    read_only = bool(getattr(args, "read_only", False))
    pool = PoolConfig(size=args.pool_size or cfg.pool_defaults.size)

    cfg.connections.append(
        ConnectionConfig(
            name=name,
            driver=driver,
            url=url,
            description=description,
            password=password,
            read_only=read_only,
            pool=pool,
        )
    )
    save_config(cfg, config_file())
    _emit({
        "added": name,
        "driver": driver,
        "url": _mask(url),
        "password_set": bool(password),
        "read_only": read_only,
    })
    return 0


def cmd_edit(args, _harness: SqlHarness) -> int:
    path = config_file()
    if not path.exists():
        print(f"error: {path} does not exist; run `sql-harness init` first", file=sys.stderr)
        return 2
    editor = os.environ.get("EDITOR") or ("notepad" if sys.platform == "win32" else "vi")
    if _harness_contains(_harness_name_filter(args.name), load_config(path)):
        pass
    return subprocess.call([editor, str(path)])


def _harness_contains(name: str, cfg) -> bool:
    return any(c.name == name for c in cfg.connections)


def _harness_name_filter(_name: str | None) -> str:
    return _name or ""


def cmd_remove(args, harness: SqlHarness) -> int:
    cfg = harness.config
    before = len(cfg.connections)
    cfg.connections = [c for c in cfg.connections if c.name != args.name]
    if len(cfg.connections) == before:
        print(f"error: no connection named {args.name!r}", file=sys.stderr)
        return 2
    if cfg.default_workspace == args.name:
        cfg.default_workspace = ""
    save_config(cfg, config_file())
    _emit({"removed": args.name})
    return 0


def cmd_show(args, harness: SqlHarness) -> int:
    try:
        c = harness.config.get(args.name)
    except KeyError:
        print(f"error: no connection named {args.name!r}", file=sys.stderr)
        return 2
    _emit({
        "name": c.name,
        "driver": c.driver,
        "url": c.masked_url(),
        "description": c.description,
        "password_set": bool(c.password),
        "password": c.masked_password(),
        "read_only": c.read_only,
        "pool": {
            "size": c.pool.size,
            "recycle": c.pool.recycle,
            "pre_ping": c.pool.pre_ping,
            "echo": c.pool.echo,
        },
        "application_name": c.application_name,
    })
    return 0


def _probe_workspace(ws) -> dict:
    """Liveness-probe an open workspace.

    SQL workspaces answer `SELECT 1`; SSH workspaces expose no `.engine.connect()`,
    so they're probed with a remote `echo`. Returns a result dict whose `ok` flag
    is the verdict (raises only on transport/library errors).
    """
    t0 = time.monotonic()
    if ws.driver.name == "ssh":
        result = ws.engine.exec("echo sql-harness-ok")
        return {
            "ok": result.ok,
            "probe": result.command,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "exit_code": result.exit_code,
            "sftp_available": ws.engine.sftp is not None,
            "latency_ms": round((time.monotonic() - t0) * 1000, 2),
        }
    if ws.driver.name == "redis":
        # PING is the canonical liveness probe; DBSIZE gives one round of
        # useful info on the same connection so the caller can sanity-check
        # the keyspace without an extra round-trip.
        pong = ws.engine.ping()
        dbsize = int(ws.engine.dbsize())
        return {
            "ok": bool(pong),
            "probe": "PING",
            "pong": pong,
            "dbsize": dbsize,
            "latency_ms": round((time.monotonic() - t0) * 1000, 2),
        }
    with ws.engine.connect() as conn:
        row = conn.execute(text("SELECT 1")).scalar()
    return {
        "ok": True,
        "select_1": row,
        "latency_ms": round((time.monotonic() - t0) * 1000, 2),
    }


def cmd_test(args, harness: SqlHarness) -> int:
    name = args.name
    try:
        ws = harness.workspace(name)
    except KeyError:
        print(f"error: no connection named {name!r}", file=sys.stderr)
        return 2
    except Exception as e:
        _emit({"name": name, "ok": False, "error": str(e)})
        return 1

    try:
        result = _probe_workspace(ws)
    except Exception as e:
        _emit({"name": name, "ok": False, "error": str(e)})
        return 1
    _emit({"name": name, "driver": ws.driver.name, **result})
    return 0 if result["ok"] else 1


def cmd_workspace(args, harness: SqlHarness) -> int:
    sub = args.workspace_cmd
    if sub == "list":
        _emit({"open": harness.list_workspaces()})
        return 0
    if sub == "use":
        try:
            ws = harness.workspace(args.name)
        except KeyError:
            print(f"error: no connection named {args.name!r}", file=sys.stderr)
            return 2
        _emit({"name": ws.config.connection.name, "driver": ws.driver.name, "ok": True})
        return 0
    if sub == "close":
        if not harness.has_workspace(args.name):
            print(f"info: workspace {args.name!r} was not open", file=sys.stderr)
            return 0
        harness.close_workspace(args.name)
        _emit({"closed": args.name})
        return 0
    if sub == "show":
        return cmd_show(argparse.Namespace(name=args.name), harness)
    print(f"unknown workspace subcommand: {sub}", file=sys.stderr)
    return 2


def _resolve_connection(args) -> str | None:
    """Resolve the active DSN zone for a zone-scoped CLI command.

    Order: explicit `--connection` flag > $BH_SQL_ACTIVE_CONNECTION env >
    None (caller errors with guidance).
    """
    conn = getattr(args, "connection", None)
    if conn:
        return conn
    return os.environ.get("BH_SQL_ACTIVE_CONNECTION")


def _require_connection_or_fail(args) -> tuple[str | None, int]:
    """Return (connection, exit_code). If exit_code is set, connection is None."""
    conn = _resolve_connection(args)
    if not conn:
        print(
            "error: no active connection. Pass --connection NAME, set "
            "BH_SQL_ACTIVE_CONNECTION, or call use_workspace(name) first.",
            file=sys.stderr,
        )
        return None, 2
    return conn, 0


def cmd_skill(args, _harness: SqlHarness) -> int:
    sub = args.skill_cmd
    # Bare `sql-harness skill` (no subcommand) emits the packaged SKILL.md
    # to stdout — mirrors `browser-harness skill`. Used for agent registration:
    #   sql-harness skill > ~/.codex/skills/sql-harness/SKILL.md
    if sub is None:
        body = _packaged_skill_body()
        if body is None:
            print(
                "error: packaged SKILL.md not found (expected at "
                "sql_harness/_skills/SKILL.md, or the repo-root SKILL.md "
                "in a source checkout)",
                file=sys.stderr,
            )
            return 2
        # stdout is already UTF-8 (set_utf8_streams() runs at the top of main()),
        # which is what lets non-ASCII skill bodies emit cleanly on Windows —
        # the default GBK/CP936 codepage can't encode symbols like ⇄.
        out = sys.stdout
        out.write(body)
        if not body.endswith("\n"):
            out.write("\n")
        emit(DOC_READ, {"doc": "SKILL.md", "via": "skill"})
        return 0
    # `skill install` materializes the bundled docs to the user's skills dir.
    # It does NOT need a connection (unlike zone-scoped list/show).
    if sub == "install":
        return cmd_skill_install(args, _harness)
    # `skill list` / `skill show` are zone-scoped (per-DSN, like domain-skills).
    conn, code = _require_connection_or_fail(args)
    if code:
        return code
    if sub == "list":
        seen: set[str] = set()
        names: list[str] = []
        # Two-layer lookup: active zone wins, zones/meta supplies cross-DSN fallback.
        for d in (zone_skills_dir(conn), zone_skills_dir("meta")):
            if d.is_dir():
                for p in sorted(d.glob("*.md")):
                    if p.stem not in seen:
                        seen.add(p.stem)
                        names.append(p.stem)
        _emit({"connection": conn, "skills": names})
        return 0
    if sub == "show":
        zone_path = zone_skills_dir(conn) / f"{args.name}.md"
        meta_path = zone_skills_dir("meta") / f"{args.name}.md"
        if zone_path.is_file():
            print(zone_path.read_text(encoding="utf-8"))
            emit(DOC_READ, {"doc": f"{args.name}.md", "via": "skill show"})
        elif meta_path.is_file():
            print(meta_path.read_text(encoding="utf-8"))
            emit(DOC_READ, {"doc": f"{args.name}.md", "via": "skill show"})
        else:
            print(
                f"error: no skill named {args.name!r} in zone {conn!r} or zones/meta",
                file=sys.stderr,
            )
            return 2
        return 0
    print(f"unknown skill subcommand: {sub}", file=sys.stderr)
    return 2


def _package_root_dir() -> Path:
    """The sql-harness container dir (lab/sql_harness/), 3 levels above cli.py.

    cli.py lives at <root>/src/sql_harness/cli.py, so parents[2] = <root>.
    Holds repo-level content (agent-workspace/, skills/, interaction-skills/)
    that ships with the source checkout but is not inside the importable package.
    """
    return Path(__file__).resolve().parents[2]


def _shipped_skills_dir() -> Path:
    """Shipped preset (cross-DSN) skills source: zones/meta/skills/.

    `sql-harness init` copies every .md under this dir into the runtime
    workspace's zones/meta/skills/ so `apply_skill()` can find them as
    fallback from any active zone.
    """
    return _package_root_dir() / "agent-workspace" / "zones" / "meta" / "skills"


def _packaged_skill_body() -> str | None:
    """Return the in-package SKILL.md body, or None if not found.

    Mirrors browser-harness's run.py:_print_skill, which reads the packaged
    SKILL.md via importlib.resources so it works from both a source checkout
    and a built wheel. Falls back to the repo-level SKILL.md (root).
    """
    # 1. in-package: sql_harness/_skills/SKILL.md (force-included in the wheel)
    try:
        ref = importlib_resources.files("sql_harness").joinpath("_skills", "SKILL.md")
        if ref.is_file():
            return ref.read_text(encoding="utf-8")
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    # 2. repo-level fallback (source checkout): lab/sql_harness/SKILL.md
    repo = _package_root_dir() / "SKILL.md"
    if repo.is_file():
        return repo.read_text(encoding="utf-8")
    return None


def _bundled_skills_root():
    """Return the `_skills/` dir bundled in the wheel (importlib.resources ref).

    Layout inside the wheel:
        sql_harness/_skills/SKILL.md
        sql_harness/_skills/interaction-skills/*.md
        sql_harness/_skills/agent-workspace-skills/*.md
    Returns None if the bundle isn't present (e.g. running from a source
    checkout without a build).
    """
    try:
        root = importlib_resources.files("sql_harness").joinpath("_skills")
        if root.is_dir():
            return root
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    return None


def _resolve_skill_install_target(args) -> Path:
    """Resolve the install target per the directory-address spec.

    Priority:
      1. --target <path>           (explicit)
      2. --codex                   → $CODEX_HOME/skills/sql-harness/ (default ~/.codex/...)
      3. default                   → $CLAUDE_CONFIG_DIR/skills/sql-harness/ (default ~/.claude/...)
    """
    if getattr(args, "target", None):
        return Path(args.target).expanduser().resolve()
    if getattr(args, "codex", False):
        base = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
        return Path(base) / "skills" / "sql-harness"
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "skills" / "sql-harness"


def _copytree_resource(src_root, dst: Path, written: list[str]) -> None:
    """Copy a resource tree (importlib.resources ref → real filesystem dir)."""
    for entry in src_root.iterdir():
        if entry.is_dir():
            sub_dst = dst / entry.name
            sub_dst.mkdir(parents=True, exist_ok=True)
            _copytree_resource(entry, sub_dst, written)
        else:
            dst.mkdir(parents=True, exist_ok=True)
            target_file = dst / entry.name
            target_file.write_bytes(entry.read_bytes())
            written.append(str(target_file.relative_from_user()) if hasattr(target_file, "relative_from_user") else str(target_file))


def cmd_skill_install(args, _harness: SqlHarness) -> int:
    """Materialize the bundled skill docs to the user's skills directory.

    Directory-address spec (see pypi-workflow skill):
      <target>/
      ├── SKILL.md                   (entry; the packaged skill body)
      ├── interaction-skills/        (mechanic docs; agent reads on-demand)
      └── agent-workspace-skills/    (strategy docs)

    Target resolution: --target > --codex > default (~/.claude/skills/sql-harness/).
    Idempotent: overwrites existing files, does NOT delete user-added files.
    """
    src_root = _bundled_skills_root()
    if src_root is None:
        print(
            "error: bundled _skills/ not found. This command needs the wheel "
            "install (not a raw source checkout). Reinstall via "
            "`uv tool install sql-harness`.",
            file=sys.stderr,
        )
        return 2
    target = _resolve_skill_install_target(args)
    target.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    # src_root is the _skills/ dir; copy its children into target.
    for entry in src_root.iterdir():
        if entry.is_dir():
            sub_dst = target / entry.name
            sub_dst.mkdir(parents=True, exist_ok=True)
            _copytree_resource(entry, sub_dst, written)
        else:
            (target / entry.name).write_bytes(entry.read_bytes())
            written.append(entry.name)

    _emit({
        "installed_to": str(target),
        "files": len(written),
        "entry": str(target / "SKILL.md"),
        "hint": (
            "Restart your agent (Claude Code / Codex) so it re-scans the "
            "skills dir. The 'sql-harness' skill should now trigger on SQL work."
        ),
    })
    return 0


def _copy_shipped_skills(target_dir: Path) -> list[str]:
    """Copy every shipped preset skill (.md) into target_dir if not present.

    Returns the list of skill names (stems) now present in target_dir.
    """
    src = _shipped_skills_dir()
    if not src.is_dir():
        return []
    present: list[str] = []
    for f in sorted(src.glob("*.md")):
        dst = target_dir / f.name
        if not dst.exists():
            shutil.copy2(str(f), str(dst))
        present.append(f.stem)
    return present


def cmd_init(_args, _harness: SqlHarness) -> int:
    """Scaffold a starter connections.toml if none exists.

    Also copies shipped preset skills into the runtime workspace so they're
    reachable via `apply_skill()` from any zone — mirrors browser-harness's
    skill-registration pattern.
    """
    path = config_file()
    ensure_private_dir(home_dir())
    if not path.exists():
        from .config import example_config

        save_config(example_config(), path)
        _emit({"path": str(path), "existed": False, "wrote": "example_config"})
    else:
        _emit({"path": str(path), "existed": True})

    # Shipped cross-DSN strategy skills land in zones/meta/skills/ (decision P:
    # collapsed the legacy global layer into a regular zone so apply_skill()
    # has a single shape: zone or meta).
    target_meta = zone_skills_dir("meta")
    ensure_private_dir(target_meta)
    copied = _copy_shipped_skills(target_meta)
    if copied:
        print(
            f"installed {len(copied)} preset skills: {', '.join(copied)}",
            file=sys.stderr,
        )
    return 0


def cmd_stats(_args, _harness: SqlHarness) -> int:
    """Print analytics: CLI subcommand call counts + doc-read tracking."""
    _emit(load_stats())
    return 0


def cmd_version(_args, _harness: SqlHarness) -> int:
    import importlib.metadata

    info = {"sql-harness": __version__}
    for pkg in ("psycopg", "pymysql", "sqlalchemy"):
        try:
            info[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            info[pkg] = "(not installed)"
    _emit(info)
    return 0


def cmd_doctor(_args, harness: SqlHarness) -> int:
    """Sanity-check: list connections + probe each one."""
    rows = []
    for c in harness.config.connections:
        try:
            ws = harness.workspace(c.name)
            rows.append({"name": c.name, "driver": c.driver, **_probe_workspace(ws)})
        except Exception as e:
            rows.append({"name": c.name, "driver": c.driver, "ok": False, "error": str(e)})
    _emit({"results": rows})
    return 0 if all(r["ok"] for r in rows) else 1


def _validate_script_name(name: str) -> None:
    """Reject path-traversal / weird names for saved scripts."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"invalid script name: {name!r}")
    if not name.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"invalid script name: {name!r} (letters/digits/_/- only)")


def cmd_save(args, _harness: SqlHarness) -> int:
    """Persist stdin (a heredoc body) as zones/<connection>/scripts/<name>.py.

    This is the essence of the harness: executed code is saved as reusable
    Python, scoped to the DSN it was written for (mirrors browser-harness's
    per-domain domain-skills). Next session, `sql-harness run <name>` (with
    the same --connection) re-executes it.
    """
    try:
        _validate_script_name(args.name)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    conn, code = _require_connection_or_fail(args)
    if code:
        return code
    # stdin is already UTF-8 (set_utf8_streams() runs at the top of main()):
    # on Windows the default GBK/CP936 locale mangles non-ASCII bytes into
    # surrogates that write_text rejects. Mirrors run.py's heredoc mode.
    body = sys.stdin.read()
    if not body.strip():
        print("error: stdin is empty; nothing to save", file=sys.stderr)
        return 2
    target_dir = zone_scripts_dir(conn)
    ensure_private_dir(target_dir)
    target = target_dir / f"{args.name}.py"
    existed = target.exists()
    # Prepend a provenance header so the file is self-describing.
    header = (
        '"""Saved by `sql-harness save ' + args.name + '` (connection: ' + conn + ').\n'
        "Re-run with: sql-harness run " + args.name + " --connection " + conn + "\n"
        "Helpers (use_workspace, query, execute, ...) are pre-imported.\n"
        '"""\n'
    )
    target.write_text(header + body, encoding="utf-8")
    _emit({
        "saved": args.name,
        "connection": conn,
        "path": str(target),
        "existed": existed,
        "bytes": len(body),
    })
    return 0


def cmd_run(args, harness: SqlHarness) -> int:
    """Execute a saved script (zones/<connection>/scripts/<name>.py).

    The script runs with all helpers pre-imported, exactly like heredoc mode.
    """
    try:
        _validate_script_name(args.name)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    conn, code = _require_connection_or_fail(args)
    if code:
        return code
    path = zone_scripts_dir(conn) / f"{args.name}.py"
    if not path.is_file():
        print(
            f"error: no saved script named {args.name!r} in zone {conn!r} "
            f"({zone_scripts_dir(conn)})",
            file=sys.stderr,
        )
        return 2
    # Build the same exec namespace run.py uses for heredocs.
    from . import helpers
    from .helpers import set_active, use_workspace

    set_active(harness)
    # Activate the connection's zone so zone helpers + skills resolve.
    use_workspace(conn)
    namespace: dict = {}
    for n, v in vars(helpers).items():
        if not n.startswith("_"):
            namespace[n] = v
    # Parity with heredoc mode (run.py globals): expose `sys` so saved
    # scripts can use sys.stdout.write etc. without importing it.
    namespace["sys"] = sys
    code_text = path.read_text(encoding="utf-8")
    try:
        exec(compile(code_text, str(path), "exec"), namespace)
        return 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0
    # Real failures propagate to main(), which stores the full traceback in the
    # analytics log and renders the short form to stderr. Catching here would
    # mean a saved-script crash left no traceback anywhere — the frames already
    # carry the script's real path, so the report names the failing line.



def cmd_scripts(args, _harness: SqlHarness) -> int:
    """List saved scripts in a connection's zone."""
    conn, code = _require_connection_or_fail(args)
    if code:
        return code
    d = zone_scripts_dir(conn)
    names = sorted(p.stem for p in d.glob("*.py")) if d.is_dir() else []
    _emit({"connection": conn, "scripts": names, "dir": str(d)})
    return 0


def _path_record(name: str, path: Path) -> dict:
    """Build a {name, path, exists} record for the paths listing."""
    return {"name": name, "path": str(path), "exists": path.exists()}


def cmd_paths(args, harness: SqlHarness) -> int:
    """Print every folder/file the CLI involves, so you can open them.

    Global paths always shown; pass `-c CONN` to also show that DSN's zone
    (scripts / skills / per-connection helpers). Mirrors the "where does my
    stuff live" ergonomics browser-harness gives via its state-dir layout.
    """
    from . import paths as P
    from importlib import resources as importlib_resources

    pkg_src = Path(str(importlib_resources.files("sql_harness"))).resolve()
    records: list[dict] = [
        _path_record("home", P.home_dir()),
        _path_record("config_dir", P.config_dir()),
        _path_record("config_file", P.config_file()),
        _path_record("agent_workspace", P.workspace_dir()),
        _path_record("meta_skills", P.zone_skills_dir("meta")),
        _path_record("runtime_dir", P.runtime_dir()),
        _path_record("tmp_dir", P.tmp_dir()),
        _path_record("package_root", _package_root_dir()),
        _path_record("package_source", pkg_src),
    ]
    out: dict = {
        "global": records,
        "env": {
            "BH_SQL_HOME": os.environ.get("BH_SQL_HOME", ""),
            "BH_SQL_CONFIG_FILE": os.environ.get("BH_SQL_CONFIG_FILE", ""),
            "BH_SQL_AGENT_WORKSPACE": os.environ.get("BH_SQL_AGENT_WORKSPACE", ""),
        },
        "connections": harness.config.names(),
        "default_workspace": harness.config.default_workspace,
    }
    conn = _resolve_connection(args)
    if conn:
        out["zone"] = {
            "connection": conn,
            "dirs": [
                _path_record("zone_dir", P.zone_dir(conn)),
                _path_record("zone_scripts", zone_scripts_dir(conn)),
                _path_record("zone_skills", zone_skills_dir(conn)),
                _path_record("zone_helpers", P.zone_helpers_file(conn)),
            ],
        }
    _emit(out)
    return 0


_OPEN_TARGETS = ("home", "config", "workspace", "scripts", "skills", "runtime", "tmp", "package", "source", "zone")


def _resolve_open_path(target: str, conn: str | None) -> Path:
    """Map an open-target name to a concrete directory."""
    from . import paths as P

    table = {
        "home": P.home_dir(),
        "config": P.config_dir(),
        "workspace": P.workspace_dir(),
        "skills": P.zone_skills_dir("meta"),
        "runtime": P.runtime_dir(),
        "tmp": P.tmp_dir(),
        "package": _package_root_dir(),
        "source": Path(str(importlib_resources.files("sql_harness"))).resolve(),
    }
    if target in table:
        return table[target]
    if target == "scripts":
        if not conn:
            raise ValueError("scripts is zone-scoped; pass --connection NAME")
        return zone_scripts_dir(conn)
    if target == "zone":
        if not conn:
            raise ValueError("zone is connection-scoped; pass --connection NAME")
        return P.zone_dir(conn)
    raise ValueError(f"unknown target {target!r}; one of: {', '.join(_OPEN_TARGETS)}")


def _reveal(path: Path) -> None:
    """Open `path` in the OS file manager (create it first if missing)."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def cmd_open(args, _harness: SqlHarness) -> int:
    """Open a folder in your OS file manager.

    `sql-harness open workspace`          → agent-workspace
    `sql-harness open scripts -c test_pg` → that zone's saved scripts
    `sql-harness open config`             → connections.toml folder
    """
    from importlib import resources as importlib_resources  # noqa: F401

    target = args.target or "workspace"
    conn = _resolve_connection(args)
    try:
        path = _resolve_open_path(target, conn)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    _reveal(path)
    _emit({"opened": target, "path": str(path), "connection": conn})
    return 0


def cmd_web(args, _harness: SqlHarness) -> int:
    """Serve the doc library (shipped docs + runtime zones + saved scripts).

    `sql-harness web`            → http://127.0.0.1:8765/
    `sql-harness web --port 0`   → ephemeral port, printed on startup
    `sql-harness web --read-only` → browse only

    Foreground: it blocks until Ctrl-C. Not a daemon.
    """
    import webbrowser

    from . import webapp

    read_only = args.read_only
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"warning: binding to {args.host} exposes the doc editor beyond this "
            f"machine; forcing read-only (the Host/Origin guards assume loopback)",
            file=sys.stderr,
        )
        read_only = True

    try:
        server = webapp.make_server(args.host, args.port, read_only=read_only)
    except OSError as e:
        print(f"error: cannot bind {args.host}:{args.port} ({e}); pass --port", file=sys.stderr)
        return 2

    port = server.server_port
    url = f"http://{args.host}:{port}/"
    _emit(
        {
            "url": url,
            "host": args.host,
            "port": port,
            "read_only": read_only,
            "pid": os.getpid(),
            "roots": [
                {
                    "id": r.id,
                    "label": r.label,
                    "kind": r.kind,
                    "editable": r.editable and not read_only,
                }
                for r in webapp.build_roots()
            ],
        }
    )
    # stdout is block-buffered once piped, so without this an agent sees
    # nothing until Ctrl-C — long after it needed the port number.
    sys.stdout.flush()
    print(f"serving {url}  —  Ctrl-C to stop", file=sys.stderr)

    if not args.no_open:
        # The socket is already listening (bound in make_server), so the
        # browser can connect before serve_forever starts draining the backlog.
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("", file=sys.stderr)
    finally:
        # Calling shutdown() from this thread deadlocks; serve_forever has
        # already returned, so closing the socket is all that is left.
        server.server_close()
    _emit({"stopped": True, "url": url})
    return 0


# --- Helpers ---------------------------------------------------------------

def _mask(url: str) -> str:
    if "@" not in url:
        return url
    head, tail = url.split("@", 1)
    if "://" not in head:
        return url
    scheme, creds = head.split("://", 1)
    if ":" in creds:
        user, _ = creds.split(":", 1)
        return f"{scheme}://{user}:***@{tail}"
    return url


def _activate_ssh_zone(harness: SqlHarness, conn_name: str | None) -> None:
    """Activate the SSH workspace (sets helpers._active_connection + opens workspace)."""
    from . import helpers as _h
    from .helpers import set_active, use_workspace

    set_active(harness)
    name = conn_name or harness.config.default_workspace
    if not name:
        raise RuntimeError(
            "no SSH connection specified. Pass --connection NAME or set "
            "default_workspace in connections.toml."
        )
    if name not in harness.config.names():
        raise RuntimeError(f"no connection named {name!r}")
    use_workspace(name)


def cmd_ssh(args, harness: SqlHarness) -> int:
    """Dispatch `sql-harness ssh {exec,upload,download,run-script,info}`."""
    from . import helpers as _h

    sub = getattr(args, "ssh_cmd", None)
    if sub is None:
        print("error: `ssh` needs a subcommand: exec / upload / download / run-script / info",
              file=sys.stderr)
        return 2

    conn, code = _require_connection_or_fail(args)
    if code:
        return code
    try:
        _activate_ssh_zone(harness, conn)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if sub == "exec":
        result = _h.ssh_exec(args.command, timeout=args.timeout)
        # Remote output length is unbounded (cat of a big file, a chatty log);
        # spill it so a long command doesn't flood the caller's context.
        sys.stdout.write(spill(result["stdout"], "ssh-stdout"))
        if result["stderr"]:
            sys.stderr.write(spill(result["stderr"], "ssh-stderr"))
        return 0 if result["ok"] else result["exit_code"]

    if sub == "upload":
        _h.ssh_upload(args.local, args.remote)
        _emit({"uploaded": args.local, "to": args.remote})
        return 0

    if sub == "download":
        _h.ssh_download(args.remote, args.local)
        _emit({"downloaded": args.remote, "to": args.local})
        return 0

    if sub == "run-script":
        result = _h.ssh_run_script(args.local, args.remote_dir, args.interpreter)
        sys.stdout.write(spill(result["stdout"], "ssh-stdout"))
        if result["stderr"]:
            sys.stderr.write(spill(result["stderr"], "ssh-stderr"))
        return 0 if result["ok"] else result["exit_code"]

    if sub == "info":
        _emit(_h.ssh_info())
        return 0

    print(f"error: unknown ssh subcommand: {sub}", file=sys.stderr)
    return 2


# --- Parser & dispatch -----------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sql-harness",
        description="Single-process SQL CLI for LLM agents (postgres, mysql, redis-soon).",
    )
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="show all configured connections")

    p_add = sub.add_parser("add", help="add a connection")
    p_add.add_argument("--name")
    p_add.add_argument(
        "--driver",
        choices=["postgres", "mysql", "redis", "sqlite", "ssh", "ssh+password", "ssh+key"],
    )
    p_add.add_argument("--url")
    p_add.add_argument("--description")
    p_add.add_argument(
        "--password",
        help="standalone secret (SSH password); keeps the URL credential-free",
    )
    p_add.add_argument(
        "--read-only",
        action="store_true",
        help="refuse writes on this connection (statement guard + session read-only)",
    )
    p_add.add_argument("--pool-size", type=int)

    p_edit = sub.add_parser("edit", help="edit connections.toml in $EDITOR")
    p_edit.add_argument("name", nargs="?")

    p_remove = sub.add_parser("remove", help="remove a connection")
    p_remove.add_argument("name")

    p_show = sub.add_parser("show", help="show connection details (password masked)")
    p_show.add_argument("name")

    p_test = sub.add_parser(
        "test", help="connect + probe (SELECT 1 for SQL, remote echo for SSH)"
    )
    p_test.add_argument("name")

    p_ws = sub.add_parser("workspace", help="manage workspaces")
    ws_sub = p_ws.add_subparsers(dest="workspace_cmd")
    ws_sub.add_parser("list", help="list open workspaces")
    p_ws_use = ws_sub.add_parser("use", help="open a workspace")
    p_ws_use.add_argument("name")
    p_ws_close = ws_sub.add_parser("close", help="close a workspace")
    p_ws_close.add_argument("name")
    ws_sub.add_parser("show", help="alias for `show`")

    p_skill = sub.add_parser(
        "skill",
        help="emit packaged SKILL.md (bare) or manage per-DSN zone skills",
    )
    p_skill.add_argument("-c", "--connection", help="DSN zone (for list/show)")
    skill_sub = p_skill.add_subparsers(dest="skill_cmd")
    skill_sub.add_parser("list", help="list skills in the zone")
    p_skill_show = skill_sub.add_parser("show", help="show a skill in the zone")
    p_skill_show.add_argument("name")

    p_skill_install = skill_sub.add_parser(
        "install",
        help="materialize the bundled skill docs to ~/.claude/skills/sql-harness/",
    )
    p_skill_install.add_argument("--target", help="explicit install path (overrides --codex)")
    p_skill_install.add_argument("--codex", action="store_true", help="install to ~/.codex/skills/sql-harness/")

    p_save = sub.add_parser("save", help="persist stdin as zones/<conn>/scripts/<name>.py")
    p_save.add_argument("name")
    p_save.add_argument("-c", "--connection", help="DSN zone to save into")

    p_run = sub.add_parser("run", help="execute a saved script in the helpers namespace")
    p_run.add_argument("name")
    p_run.add_argument("-c", "--connection", help="DSN zone to run from")

    p_scripts = sub.add_parser("scripts", help="list saved scripts in a zone")
    p_scripts.add_argument("-c", "--connection", help="DSN zone to list")

    p_paths = sub.add_parser("paths", help="show every folder/file the CLI involves")
    p_paths.add_argument("-c", "--connection", help="also show this DSN's zone dirs")

    p_open = sub.add_parser("open", help="open a folder in your file manager")
    p_open.add_argument(
        "target", nargs="?", default="workspace", choices=_OPEN_TARGETS,
        help="which folder (default: workspace)",
    )
    p_open.add_argument("-c", "--connection", help="DSN zone (for scripts/zone targets)")

    p_web = sub.add_parser("web", help="browse + edit the doc library in a local web UI")
    p_web.add_argument("--port", type=int, default=8765, help="port (default 8765, 0 = ephemeral)")
    p_web.add_argument("--host", default="127.0.0.1", help="bind address (default loopback)")
    p_web.add_argument("--no-open", action="store_true", help="do not open a browser")
    p_web.add_argument(
        "--read-only", action="store_true", help="serve the docs but refuse every write"
    )

    sub.add_parser("init", help="write a starter connections.toml")
    sub.add_parser("version", help="print versions")
    sub.add_parser("doctor", help="test every configured connection")
    sub.add_parser(
        "stats", help="show analytics: CLI call counts + doc-read tracking"
    )

    # --- ssh subcommand group -----------------------------------------------
    p_ssh = sub.add_parser("ssh", help="run shell commands / file ops on an SSH workspace")
    p_ssh.add_argument("-c", "--connection", help="which SSH connection to use")
    ssh_sub = p_ssh.add_subparsers(dest="ssh_cmd")

    p_ssh_exec = ssh_sub.add_parser("exec", help="run a shell command and print stdout/stderr")
    p_ssh_exec.add_argument("command", help="shell command (quoted)")
    p_ssh_exec.add_argument("--timeout", type=float, default=30.0, help="seconds")

    p_ssh_up = ssh_sub.add_parser("upload", help="upload a local file to the remote")
    p_ssh_up.add_argument("local", help="local file path")
    p_ssh_up.add_argument("remote", help="remote file path")

    p_ssh_dl = ssh_sub.add_parser("download", help="download a remote file to local")
    p_ssh_dl.add_argument("remote", help="remote file path")
    p_ssh_dl.add_argument("local", help="local file path")

    p_ssh_rs = ssh_sub.add_parser(
        "run-script",
        help="upload a local script to a remote dir and execute it",
    )
    p_ssh_rs.add_argument("local", help="local script path")
    p_ssh_rs.add_argument("--remote-dir", default="/tmp", help="where to upload (default /tmp)")
    p_ssh_rs.add_argument("--interpreter", default="bash", help="interpreter to invoke (default bash)")

    p_ssh_info = ssh_sub.add_parser("info", help="show active SSH connection info")
    return p


def main(argv: list[str]) -> int:
    # Before anything can print: force UTF-8 on the standard streams so data
    # that legitimately contains non-GBK characters (Chinese descriptions in
    # connections.toml, CJK paths, remote host output) reports instead of
    # crashing the process that was trying to report it.
    set_utf8_streams()

    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0

    cfg = load_config(config_file())
    harness = SqlHarness(cfg)

    commands: dict[str, Callable[[argparse.Namespace, SqlHarness], int]] = {
        "list": cmd_list,
        "add": cmd_add,
        "edit": cmd_edit,
        "remove": cmd_remove,
        "show": cmd_show,
        "test": cmd_test,
        "workspace": cmd_workspace,
        "skill": cmd_skill,
        "save": cmd_save,
        "run": cmd_run,
        "scripts": cmd_scripts,
        "paths": cmd_paths,
        "open": cmd_open,
        "web": cmd_web,
        "ssh": cmd_ssh,
        "init": cmd_init,
        "version": cmd_version,
        "doctor": cmd_doctor,
        "stats": cmd_stats,
    }
    handler = commands[args.cmd]
    emit(CLI_INVOKED, {"subcommand": args.cmd})  # 调用即事件（含 stats 自身）
    try:
        rc = handler(args, harness)
        emit(
            COMMAND_EXECUTED,
            {"argv": argv, "code": None, "ok": rc == 0, "exit_code": rc, "error": None},
        )
        return rc
    except Exception as exc:
        # The analytics log keeps the full traceback (machine-readable record);
        # the terminal gets the short form. Same split as heredoc mode.
        emit(
            COMMAND_EXECUTED,
            {"argv": argv, "code": None, "ok": False, "exit_code": 1,
             "error": "".join(traceback.format_exception(exc))},
        )
        sys.stderr.write(render_exception(exc, label=f"cli-{args.cmd}"))
        return 1
    finally:
        harness.close_all()