"""Path resolution for sql-harness state directory."""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"


def home_dir() -> Path:
    """Return the sql-harness state root.

    Resolved in order:
      1. $BH_SQL_HOME (matches browser-harness env-var style)
      2. $XDG_CONFIG_HOME/sql-harness
      3. ~/.config/sql-harness (POSIX / fallback)
    """
    override = os.environ.get("BH_SQL_HOME")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "sql-harness"
    return Path.home() / ".config" / "sql-harness"


def config_dir() -> Path:
    """Directory holding connections.toml and other static config."""
    override = os.environ.get("BH_SQL_CONFIG_DIR")
    return Path(override).expanduser() if override else home_dir()


def config_file() -> Path:
    """Path to the plaintext connections.toml."""
    override = os.environ.get("BH_SQL_CONFIG_FILE")
    return Path(override).expanduser() if override else config_dir() / "connections.toml"


def workspace_dir() -> Path:
    """Agent-editable workspace (mirrors browser-harness agent-workspace/).

    Layout:
      - agent_helpers.py        shared base helpers (auto-imported every run)
      - zones/<connection>/     per-DSN isolated zone (mirrors domain-skills/<host>/)
          - helpers.py          per-connection helpers (merged over the shared base)
          - scripts/<name>.py   saved heredocs for this connection
          - skills/<name>.md    per-connection skill knowledge
      - zones/meta/skills/      cross-DSN strategy skills (apply_skill fallback)
      - scripts/                (legacy global scripts, pre-zone)
    """
    override = os.environ.get("BH_SQL_AGENT_WORKSPACE")
    return Path(override).expanduser() if override else home_dir() / "agent-workspace"


def scripts_dir() -> Path:
    """Legacy global scripts dir (pre-zone). Prefer zone_scripts_dir()."""
    return workspace_dir() / "scripts"


def zone_dir(connection: str) -> Path:
    """Per-connection isolated zone root: agent-workspace/zones/<connection>/.

    Mirrors browser-harness's domain-skills/<host>/ — one isolated area per
    DSN. `connection` is validated to be a simple identifier (no path parts).
    """
    _validate_zone_name(connection)
    return workspace_dir() / "zones" / connection


def zone_scripts_dir(connection: str) -> Path:
    return zone_dir(connection) / "scripts"


def zone_skills_dir(connection: str) -> Path:
    return zone_dir(connection) / "skills"


def zone_helpers_file(connection: str) -> Path:
    """Per-connection helpers.py, merged over the shared base on use_workspace."""
    return zone_dir(connection) / "helpers.py"


def _validate_zone_name(name: str) -> None:
    """A zone name is a connection name; reject path traversal / odd chars."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"invalid zone/connection name: {name!r}")


def runtime_dir() -> Path:
    """Runtime state: logs, lock files."""
    override = os.environ.get("BH_SQL_RUNTIME_DIR")
    return Path(override).expanduser() if override else home_dir() / "runtime"


def tmp_dir() -> Path:
    """Per-session scratch files (query logs, dumps)."""
    override = os.environ.get("BH_SQL_TMP_DIR")
    return Path(override).expanduser() if override else home_dir() / "tmp"


def ensure_private_dir(p: Path) -> Path:
    """Create directory (parents=True) and tighten perms on POSIX."""
    p.mkdir(parents=True, exist_ok=True)
    if not IS_WINDOWS:
        # Best-effort: only tighten on creation
        try:
            p.chmod(0o700)
        except (PermissionError, OSError):
            pass
    return p