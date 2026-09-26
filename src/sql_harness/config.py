"""TOML configuration for sql-harness.

Schema (lives at ~/.config/sql-harness/connections.toml by default):

    default_workspace = "local_pg"          # optional, name of a connection
    [pool_defaults]
    size = 5
    recycle = 3600
    pre_ping = true
    echo = false

    [[connections]]
    name = "local_pg"
    driver = "postgres"
    url = "postgresql+psycopg://user:pw@host:5432/db"
    description = "..."
    password = "${env:PG_PASSWORD}"        # optional; overrides URL password
    pool_size = 5
    pool_recycle = 3600
    pre_ping = true
    echo = false
    application_name = "sql-harness"

${env:VAR} in `url` and in `password` is expanded from the process environment.

`password` is a standalone secret for drivers that take one separately from the
URL (currently SSH). Precedence, highest first: password embedded in `url` >
this field > driver-specific env var (SSH: $BH_SSH_PASSWORD).
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# Match ${env:NAME} placeholders inside URL strings.
_ENV_RE = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class PoolConfig:
    """SQLAlchemy QueuePool knobs. Match URL is implied (`QueuePool`)."""

    size: int = 5
    recycle: int = 3600
    pre_ping: bool = True
    echo: bool = False

    def sqlalchemy_kwargs(self) -> dict[str, Any]:
        """Subset of kwargs accepted by sqlalchemy.create_engine()."""
        return {
            "pool_size": self.size,
            "pool_recycle": self.recycle,
            "pool_pre_ping": self.pre_ping,
            "echo": self.echo,
        }


@dataclass
class ConnectionConfig:
    """A single named connection profile."""

    name: str
    driver: str                                # postgres | mysql | redis | ssh
    url: str
    description: str = ""
    password: str = ""                         # standalone secret (SSH password)
    read_only: bool = False                    # refuse writes: see drivers/readonly.py
    pool: PoolConfig = field(default_factory=PoolConfig)
    application_name: str = "sql-harness"

    def masked_url(self) -> str:
        """Return the URL with password redacted."""
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), _mask_password(self.url))

    def masked_password(self) -> str:
        """Return the standalone password redacted (empty if unset)."""
        return "***" if self.password else ""


@dataclass
class ConnectionsConfig:
    """Top-level config; one TOML file."""

    default_workspace: str = ""
    pool_defaults: PoolConfig = field(default_factory=PoolConfig)
    connections: list[ConnectionConfig] = field(default_factory=list)

    def get(self, name: str) -> ConnectionConfig:
        for c in self.connections:
            if c.name == name:
                return c
        raise KeyError(f"no connection named {name!r}")

    def names(self) -> list[str]:
        return [c.name for c in self.connections]

    def apply_pool_defaults(self) -> None:
        """For any connection that didn't override pool fields, use defaults."""
        for c in self.connections:
            if c.pool == PoolConfig():                # pure default
                c.pool = replace(self.pool_defaults)


# --- TOML I/O ---------------------------------------------------------------


def _expand_env(value: str) -> str:
    """Replace ${env:VAR} with the value of $VAR (or empty if unset)."""
    return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)


def _mask_password(url: str) -> str:
    """Replace the userinfo password with *** (best-effort)."""
    # Format: scheme://user:password@host/...
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


def load(path: Path) -> ConnectionsConfig:
    """Load and parse connections.toml. Returns empty config if file missing."""
    if not path.exists():
        return ConnectionsConfig()
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return _from_dict(raw)


def save(cfg: ConnectionsConfig, path: Path) -> None:
    """Serialize to TOML. Pure stdlib (manual writer — no tomllib writer in 3.11)."""
    lines: list[str] = []
    if cfg.default_workspace:
        lines.append(f'default_workspace = "{_toml_str(cfg.default_workspace)}"')
        lines.append("")

    pd = cfg.pool_defaults
    lines.append("[pool_defaults]")
    lines.append(f"size = {pd.size}")
    lines.append(f"recycle = {pd.recycle}")
    lines.append(f"pre_ping = {'true' if pd.pre_ping else 'false'}")
    lines.append(f"echo = {'true' if pd.echo else 'false'}")
    lines.append("")

    for c in cfg.connections:
        lines.append("[[connections]]")
        lines.append(f'name = "{_toml_str(c.name)}"')
        lines.append(f'driver = "{_toml_str(c.driver)}"')
        lines.append(f'url = "{_toml_str(c.url)}"')
        if c.description:
            lines.append(f'description = "{_toml_str(c.description)}"')
        if c.password:
            lines.append(f'password = "{_toml_str(c.password)}"')
        if c.read_only:
            lines.append("read_only = true")
        if c.pool.size != cfg.pool_defaults.size:
            lines.append(f"pool_size = {c.pool.size}")
        if c.pool.recycle != cfg.pool_defaults.recycle:
            lines.append(f"pool_recycle = {c.pool.recycle}")
        if c.pool.pre_ping != cfg.pool_defaults.pre_ping:
            lines.append(f"pre_ping = {'true' if c.pool.pre_ping else 'false'}")
        if c.pool.echo != cfg.pool_defaults.echo:
            lines.append(f"echo = {'true' if c.pool.echo else 'false'}")
        if c.application_name != "sql-harness":
            lines.append(f'application_name = "{_toml_str(c.application_name)}"')
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _toml_str(s: str) -> str:
    """Escape a string for embedding in a TOML basic string."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _from_dict(raw: dict[str, Any]) -> ConnectionsConfig:
    """Parse the loaded TOML dict into dataclasses + env-expand URLs."""
    pd_raw = raw.get("pool_defaults") or {}
    pool_defaults = PoolConfig(
        size=int(pd_raw.get("size", 5)),
        recycle=int(pd_raw.get("recycle", 3600)),
        pre_ping=bool(pd_raw.get("pre_ping", True)),
        echo=bool(pd_raw.get("echo", False)),
    )

    conns: list[ConnectionConfig] = []
    for entry in raw.get("connections", []) or []:
        p_raw = entry.get("pool") or {}
        pool = PoolConfig(
            size=int(p_raw.get("size", pool_defaults.size)),
            recycle=int(p_raw.get("recycle", pool_defaults.recycle)),
            pre_ping=bool(p_raw.get("pre_ping", pool_defaults.pre_ping)),
            echo=bool(p_raw.get("echo", pool_defaults.echo)),
        )
        # Top-level pool_* keys (per the schema example) override the nested block.
        if "pool_size" in entry:
            pool.size = int(entry["pool_size"])
        if "pool_recycle" in entry:
            pool.recycle = int(entry["pool_recycle"])
        if "pre_ping" in entry:
            pool.pre_ping = bool(entry["pre_ping"])
        if "echo" in entry:
            pool.echo = bool(entry["echo"])

        conns.append(
            ConnectionConfig(
                name=str(entry["name"]),
                driver=str(entry["driver"]),
                url=_expand_env(str(entry["url"])),
                description=str(entry.get("description", "")),
                password=_expand_env(str(entry.get("password", ""))),
                read_only=bool(entry.get("read_only", False)),
                pool=pool,
                application_name=str(entry.get("application_name", "sql-harness")),
            )
        )

    return ConnectionsConfig(
        default_workspace=str(raw.get("default_workspace", "")),
        pool_defaults=pool_defaults,
        connections=conns,
    )


def example_config() -> ConnectionsConfig:
    """A safe-to-write starter config (no real secrets)."""
    return ConnectionsConfig(
        default_workspace="local_pg",
        pool_defaults=PoolConfig(),
        connections=[
            ConnectionConfig(
                name="local_pg",
                driver="postgres",
                url="postgresql+psycopg://postgres:postgres@localhost:5432/postgres",
                description="Local PostgreSQL for dev",
                pool=PoolConfig(size=5),
            ),
            ConnectionConfig(
                name="local_mysql",
                driver="mysql",
                url="mysql+pymysql://root:root@localhost:3306/mysql?charset=utf8mb4",
                description="Local MySQL for dev",
                pool=PoolConfig(size=5),
            ),
        ],
    )