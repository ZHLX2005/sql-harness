"""SqlHarness — single-process manager holding open workspaces.

A workspace = a named connection + its SQLAlchemy Engine. Engines are opened
lazily and kept alive until `close_workspace()` or process exit.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine

from .analytics import DOC_READ, emit
from .config import ConnectionsConfig
from .drivers import Driver, get_driver
from .paths import workspace_dir


class Workspace:
    """One connection = one workspace."""

    def __init__(self, config: "WorkspaceConfig", engine: Engine, driver):
        self.config = config
        self.engine = engine
        self.driver = driver

    def __repr__(self) -> str:
        return f"Workspace(name={self.config.connection.name!r}, driver={self.driver.name})"


class WorkspaceConfig:
    """Lightweight wrapper used at runtime; created from ConnectionConfig."""

    __slots__ = ("connection",)

    def __init__(self, connection):
        self.connection = connection


class SqlHarness:
    """Owns the open workspace registry."""

    def __init__(self, config: ConnectionsConfig):
        self.config = config
        self._workspaces: dict[str, Workspace] = {}

    # --- workspace lifecycle -----------------------------------------------

    def workspace(self, name: str) -> Workspace:
        """Open (or return cached) workspace for `name`."""
        if name in self._workspaces:
            return self._workspaces[name]
        conn = self.config.get(name)
        driver = get_driver(conn.driver)
        # `password` is the connection's standalone secret; SQL drivers ignore
        # it (their URL carries credentials), SSH uses it when the URL doesn't.
        engine = driver.make_engine(
            conn.url, conn.pool, conn.password, read_only=conn.read_only
        )
        ws = Workspace(WorkspaceConfig(conn), engine, driver)
        self._workspaces[name] = ws
        return ws

    def close_workspace(self, name: str) -> None:
        ws = self._workspaces.pop(name, None)
        if ws is not None:
            # SQLAlchemy engines use .dispose(); SSH handles use .close().
            closer = getattr(ws.engine, "close", None)
            if callable(closer):
                closer()
            else:
                ws.engine.dispose()

    def list_workspaces(self) -> list[str]:
        return sorted(self._workspaces.keys())

    def has_workspace(self, name: str) -> bool:
        return name in self._workspaces

    def get_workspace(self, name: str) -> Workspace | None:
        return self._workspaces.get(name)

    # --- default workspace (from connections.toml) -----------------------

    @property
    def default_workspace_name(self) -> str:
        return self.config.default_workspace

    def default_workspace(self) -> Workspace | None:
        name = self.config.default_workspace
        if not name:
            return None
        if name not in self.config.names():
            return None
        return self.workspace(name)

    # --- skills ------------------------------------------------------------

    def apply_skill(self, name: str) -> str:
        """Read an agent-workspace skill markdown file."""
        path = self._skill_path(name)
        if not path.is_file():
            raise FileNotFoundError(
                f"no skill named {name!r} in {self._skills_dir()}"
            )
        body = path.read_text(encoding="utf-8")
        emit(DOC_READ, {"doc": f"{name}.md", "via": "apply_skill"})
        return body

    def list_skills(self) -> list[str]:
        d = self._skills_dir()
        if not d.is_dir():
            return []
        return sorted(p.stem for p in d.glob("*.md") if p.is_file())

    def _skill_path(self, name: str) -> Path:
        # Defensive: prevent path traversal.
        if "/" in name or "\\" in name or name.startswith("."):
            raise ValueError(f"invalid skill name: {name!r}")
        return self._skills_dir() / f"{name}.md"

    def _skills_dir(self) -> Path:
        return workspace_dir() / "skills"

    # --- teardown ----------------------------------------------------------

    def close_all(self) -> None:
        for name in list(self._workspaces.keys()):
            self.close_workspace(name)