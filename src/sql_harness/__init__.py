"""sql-harness — single-process SQL CLI for LLM agents.

Mirrors browser-harness's structure: a small protected core plus an
agent-editable workspace. Connections live in plaintext in one TOML file
(default: ~/.config/sql-harness/connections.toml).
"""

from __future__ import annotations

try:
    from importlib.metadata import version as _pkg_version
except Exception:
    _pkg_version = None  # type: ignore

from .config import ConnectionConfig, ConnectionsConfig, PoolConfig, load, save
from .drivers import Driver, get_driver
from .manager import SqlHarness, Workspace

__version__ = _pkg_version("sql-harness") if _pkg_version else "0.0.0+local"

__all__ = [
    "ConnectionConfig",
    "ConnectionsConfig",
    "Driver",
    "PoolConfig",
    "SqlHarness",
    "Workspace",
    "__version__",
    "get_driver",
    "load",
    "save",
]