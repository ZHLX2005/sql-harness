"""Auto-load agent-editable helpers from $BH_SQL_AGENT_WORKSPACE/agent_helpers.py.

Mirrors browser-harness's helpers.py:493-508 pattern. Public attributes
(no leading underscore) are merged into the supplied globals dict.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType

from .paths import workspace_dir


def _resolve_agent_file() -> Path | None:
    override = os.environ.get("BH_SQL_AGENT_WORKSPACE")
    if override:
        return Path(override).expanduser() / "agent_helpers.py"
    return workspace_dir() / "agent_helpers.py"


def load_agent_helpers(target_globals: dict) -> int:
    """Load agent_helpers.py and merge public names into `target_globals`.

    The agent_helpers.py module is exec'd in a namespace pre-seeded with the
    core helpers (query, execute, use_workspace, ...) so that functions defined
    there can call them directly. Returns the number of names merged
    (0 if no file present).
    """
    path = _resolve_agent_file()
    if path is None or not path.is_file():
        return 0
    spec = importlib.util.spec_from_file_location("sql_harness_agent_helpers", path)
    if spec is None or spec.loader is None:
        return 0
    module = importlib.util.module_from_spec(spec)

    # Pre-seed the module namespace with core helpers so that functions
    # defined in agent_helpers.py can call query()/execute()/etc. by name.
    from . import helpers as _helpers

    for _n, _v in vars(_helpers).items():
        if not _n.startswith("_"):
            setattr(module, _n, _v)

    spec.loader.exec_module(module)
    merged = 0
    for name, value in vars(module).items():
        if name.startswith("_"):
            continue
        target_globals[name] = value
        merged += 1
    return merged