"""Analytics — CLI call counters + doc-read tracking for sql-harness.

Design: **observer pattern behind a facade**.

- Business code only *emits events*: ``emit(CLI_INVOKED, {"subcommand": ...})``
  or ``emit(DOC_READ, {"doc": ..., "via": ...})``. It never touches storage.
- ``on(event, handler)`` registers stable callbacks (listeners). The built-in
  store listeners persist to JSON; external code can attach its own listeners
  (log forwarding, alerting, ...) without touching sql-harness internals.
- A failing listener never breaks the caller or other listeners.
- Analysis mode is ON by default: ``BH_SQL_ANALYTICS`` unset (or any value
  other than ``0|off|false|no``) enables tracking. Disabled ⇒ ``emit()``
  short-circuits and nothing is recorded or dispatched.
- Data is persisted as JSON at ``$BH_SQL_HOME/analytics.json`` (same state
  root as ``connections.toml`` / ``agent-workspace/``), so it follows
  ``BH_SQL_HOME`` overrides and is per-user.

File shape::

    {
      "version": 1,
      "since": "2026-..T..",
      "last_run": "2026-..T..",
      "cli": {"total_runs": N, "by_subcommand": {"list": 2, ...}},
      "doc_reads": [{"doc": "x.md", "via": "apply_skill", "ts": "..."}, ...]
    }

``doc_reads`` is capped at DOC_READS_CAP entries (oldest dropped).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from . import paths

# --- stable event contract -------------------------------------------------
#
# Event names and their payload shapes are the public interface. Business
# code emits these; listeners receive the payload dict. Do not rename without
# updating every emitter/listener.

CLI_INVOKED = "cli.invoked"  # payload: {"subcommand": str}
DOC_READ = "doc.read"        # payload: {"doc": str, "via": str}
COMMAND_EXECUTED = "command.executed"
# payload: {"argv": list[str], "code": str|None, "ok": bool,
#           "exit_code": int, "error": str|None}
#   argv  = CLI args including the subcommand; [] in heredoc mode
#   code  = the heredoc body; None in CLI mode
#   error = the FULL traceback when the run died on an uncaught exception,
#           else None. stderr shows a compressed form (see output.py), so this
#           log is the only complete channel — it keeps everything.

_OFF_VALUES = {"0", "off", "false", "no"}
DOC_READS_CAP = 500
RECENT_LIMIT = 10


def analytics_file() -> Path:
    """Path of the analytics JSON (default: $BH_SQL_HOME/analytics.json)."""
    return paths.home_dir() / "analytics.json"


def log_file() -> Path:
    """Path of the append-only NDJSON execution log (default: $BH_SQL_HOME/sql-harness.log)."""
    return paths.home_dir() / "sql-harness.log"


def analytics_enabled() -> bool:
    """Analysis mode. Default ON; disable with BH_SQL_ANALYTICS=0|off|false|no."""
    val = os.environ.get("BH_SQL_ANALYTICS")
    return val is None or val.strip().lower() not in _OFF_VALUES


# --- store (persistence) ---------------------------------------------------


class AnalyticsStore:
    """JSON persistence for analytics. Load/reset on corrupt data.

    ``path`` may be a fixed ``Path`` or a zero-arg callable returning one
    (the module-level singleton passes ``analytics_file`` so the location
    always follows the current ``$BH_SQL_HOME`` — important under tests).
    """

    def __init__(self, path: Path | Callable[[], Path]):
        self._path = path

    def _resolve_path(self) -> Path:
        return self._path() if callable(self._path) else self._path

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _fresh(self) -> dict:
        return {
            "version": 1,
            "since": self._now(),
            "last_run": None,
            "cli": {"total_runs": 0, "by_subcommand": {}},
            "doc_reads": [],
        }

    def load(self) -> dict:
        path = self._resolve_path()
        if not path.is_file():
            return self._fresh()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # Corrupt or half-written file: reset rather than crash the CLI.
            return self._fresh()
        if not isinstance(data, dict):
            return self._fresh()
        data.setdefault("cli", {"total_runs": 0, "by_subcommand": {}})
        data.setdefault("doc_reads", [])
        data.setdefault("last_run", None)
        data.setdefault("since", self._now())
        return data

    def save(self, data: dict) -> None:
        path = self._resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def record_cli(self, subcommand: str) -> None:
        data = self.load()
        data["cli"]["total_runs"] += 1
        sub = data["cli"]["by_subcommand"]
        sub[subcommand] = sub.get(subcommand, 0) + 1
        data["last_run"] = self._now()
        self.save(data)

    def record_doc_read(self, doc: str, via: str) -> None:
        data = self.load()
        data["doc_reads"].append({"doc": doc, "via": via, "ts": self._now()})
        data["doc_reads"] = data["doc_reads"][-DOC_READS_CAP:]
        self.save(data)

    def stats(self) -> dict:
        data = self.load()
        by_doc: dict[str, int] = {}
        by_via: dict[str, int] = {}
        for r in data.get("doc_reads", []):
            by_doc[r.get("doc", "?")] = by_doc.get(r.get("doc", "?"), 0) + 1
            by_via[r.get("via", "?")] = by_via.get(r.get("via", "?"), 0) + 1
        recent = data.get("doc_reads", [])[-RECENT_LIMIT:][::-1]  # newest first
        return {
            "analytics_file": str(self._resolve_path()),
            "since": data.get("since"),
            "last_run": data.get("last_run"),
            "total_runs": data.get("cli", {}).get("total_runs", 0),
            "by_subcommand": data.get("cli", {}).get("by_subcommand", {}),
            "doc_reads": {
                "total": len(data.get("doc_reads", [])),
                "by_doc": by_doc,
                "by_via": by_via,
                "recent": recent,
            },
        }


# --- event log (append-only NDJSON) ----------------------------------------


class EventLog:
    """Append-only execution log: one JSON object per line.

    Every executed command (CLI argv or heredoc body) is appended with its
    outcome, so the full history — including failures — survives restarts.
    ``path`` may be a fixed ``Path`` or a zero-arg callable (the singleton
    passes ``log_file`` for dynamic $BH_SQL_HOME resolution).
    """

    def __init__(self, path: Path | Callable[[], Path]):
        self._path = path

    def _resolve_path(self) -> Path:
        return self._path() if callable(self._path) else self._path

    def append(self, record: dict) -> None:
        path = self._resolve_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": AnalyticsStore._now(), **record}, ensure_ascii=False) + "\n")


# --- event bus (observer core) ---------------------------------------------

class EventBus:
    """Tiny observer: register callbacks per event, dispatch on emit.

    Listeners are called with the payload dict. A listener raising never
    propagates — one bad listener must not break the CLI or its peers.
    """

    def __init__(self):
        self._handlers: dict[str, list[Callable[[dict], None]]] = {}

    def on(self, event: str, handler: Callable[[dict], None]) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def off(self, event: str, handler: Callable[[dict], None]) -> None:
        handlers = self._handlers.get(event)
        if not handlers:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            pass

    def emit(self, event: str, payload: dict) -> None:
        for handler in list(self._handlers.get(event, ())):
            try:
                handler(payload)
            except Exception:
                # Stable by contract: listener failures never surface.
                pass


# --- facade -----------------------------------------------------------------


class Analytics:
    """Facade over the event bus + store.

    - ``emit()`` dispatches an event to all listeners (built-in store first).
      Short-circuits when analysis mode is disabled.
    - ``record_cli()`` / ``record_doc_read()`` are convenience wrappers that
      emit the standard events.
    - ``stats()`` returns the aggregated summary for `sql-harness stats`.
    """

    def __init__(self, store: AnalyticsStore, bus: EventBus, event_log: EventLog | None = None):
        self._store = store
        self._bus = bus
        self._event_log = event_log
        # Built-in listeners: persist standard events to the JSON store.
        bus.on(CLI_INVOKED, lambda p: store.record_cli(p["subcommand"]))
        bus.on(DOC_READ, lambda p: store.record_doc_read(p["doc"], p["via"]))
        if event_log is not None:
            bus.on(COMMAND_EXECUTED, event_log.append)

    def enabled(self) -> bool:
        return analytics_enabled()

    def on(self, event: str, handler: Callable[[dict], None]) -> None:
        self._bus.on(event, handler)

    def off(self, event: str, handler: Callable[[dict], None]) -> None:
        self._bus.off(event, handler)

    def emit(self, event: str, payload: dict) -> None:
        if not self.enabled():
            return
        self._bus.emit(event, payload)

    def record_cli(self, subcommand: str) -> None:
        self.emit(CLI_INVOKED, {"subcommand": subcommand})

    def record_doc_read(self, doc: str, via: str) -> None:
        self.emit(DOC_READ, {"doc": doc, "via": via})

    def stats(self) -> dict:
        if not self.enabled():
            return {"enabled": False, "analytics_file": str(self._store._resolve_path())}
        return {"enabled": True, **self._store.stats()}


# --- module-level singleton + stable public API -----------------------------
#
# One bus + one store per process. The store resolves its path *dynamically*
# (passes the analytics_file function, not a fixed Path) so the location
# always follows the current $BH_SQL_HOME — required under tests that
# monkeypatch env vars, and keeps the singleton correct if BH_SQL_HOME is
# set after import.

_analytics = Analytics(
    AnalyticsStore(analytics_file), EventBus(), EventLog(log_file)
)

emit = _analytics.emit
on = _analytics.on
off = _analytics.off
record_cli = _analytics.record_cli
record_doc_read = _analytics.record_doc_read
load_stats = _analytics.stats
