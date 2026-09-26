"""Terminal output policy: encoding, long-output spill, error rendering.

Three concerns, all about *how* sql-harness writes to the terminal rather
than what it computes. They live together because they share one rule:
the terminal gets a short, faithful summary; anything long goes to a file
and is referenced by path, so an agent can read it on demand instead of
paying for it in context up front.

- ``set_utf8_streams()`` — force UTF-8 on stdin/stdout/stderr. On Windows
  the default console codec is cp936/GBK, which cannot encode symbols that
  turn up in real data (Chinese column comments, ``⇄`` in the skill docs)
  and raises ``UnicodeEncodeError`` mid-run — aborting the whole heredoc,
  including from inside the error handler that was trying to report it.
- ``spill()`` — long output is written to ``$BH_SQL_HOME/tmp/output/`` and
  replaced by a one-line pointer naming the path, line count and size.
- ``render_exception()`` — a failed run prints a few actionable lines
  (DB error, the SQL, and the line in the *user's own* code) while the
  complete traceback goes to a spill file.
"""

from __future__ import annotations

import os
import sys
import time
import traceback as _traceback
from pathlib import Path

from .paths import ensure_private_dir, tmp_dir

# Below this many characters, printing inline is cheaper than a file plus a
# read. Above it, the agent almost never wants the whole thing in context.
SPILL_THRESHOLD = 4000

# A traceback is judged by readability, not bytes: a 50-line sqlite stack is
# only ~3.5 KB, well under the character bound, yet nobody wants it on screen.
# Past roughly a screenful, it becomes a file.
SPILL_MAX_LINES = 15

# Spill files are scratch, not an archive. Keep the newest N so a long
# session can't grow tmp/output/ without bound.
SPILL_KEEP = 20

# Inverse of analytics' _OFF_VALUES: these opt a feature IN.
_ON_VALUES = {"1", "true", "yes", "on"}


def set_utf8_streams() -> None:
    """Force UTF-8 on the standard streams, replacing unencodable characters.

    Idempotent and best-effort. Streams can be detached or replaced (pytest
    capture, shell redirection), so a failure here is swallowed rather than
    raised — an error while configuring output must not become the error.

    ``errors="replace"`` is deliberate: a mangled character beats losing the
    rest of the output, and beats crashing the run that produced it.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            # AttributeError: stream has no reconfigure (already wrapped).
            # ValueError/OSError: stream detached or closed.
            pass


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _ON_VALUES


def _human(size: int) -> str:
    """Byte count as a short human string."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def _spill_dir() -> Path:
    return ensure_private_dir(tmp_dir() / "output")


def _safe_label(label: str) -> str:
    """Reduce a label to something safe as a filename stem."""
    keep = [c if (c.isalnum() or c in "-_") else "-" for c in label]
    return ("".join(keep).strip("-") or "output")[:40]


def _prune(directory: Path, keep: int = SPILL_KEEP) -> None:
    """Drop the oldest spill files, keeping the newest ``keep``."""
    try:
        entries = sorted(
            (p for p in directory.glob("*.log") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for stale in entries[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass


def write_spill(text: str, label: str) -> str:
    """Write ``text`` to a spill file unconditionally; return a pointer line.

    The no-threshold primitive underneath ``spill()``, for callers that have
    already decided the text is too long to print.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stem = _safe_label(label)
    try:
        directory = _spill_dir()
        # Two spills in the same second would collide; disambiguate.
        path = directory / f"{stem}-{stamp}.log"
        n = 2
        while path.exists():
            path = directory / f"{stem}-{stamp}-{n}.log"
            n += 1
        path.write_text(text, encoding="utf-8")
        _prune(directory)
        size = path.stat().st_size
    except OSError:
        # Out of disk, read-only home, exotic permissions: losing the tail of
        # the output is bad, but losing the *error* would be worse. Callers
        # decide what to do; returning an empty pointer is the safe default.
        return ""
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    return f"({lines} lines, {_human(size)}) -> {path}"


def spill(text: str, label: str, *, threshold: int = SPILL_THRESHOLD) -> str:
    """Return ``text`` unchanged if short, else spill it and return a pointer.

    Keeps short output byte-identical to what callers printed before, so this
    is a no-op for the common case.
    """
    if len(text) <= threshold:
        return text
    pointer = write_spill(text, label)
    if not pointer:
        return text
    return f"full output {pointer}\n"


def _frame_kind(filename: str) -> str:
    """Classify a traceback frame as ``"harness"``, ``"user"`` or ``"library"``.

    The distinction matters: the point of the concise report is to name the
    line in the *user's* code that failed, and a DB error's traceback is
    mostly library frames (pymysql, sqlalchemy) that would otherwise win the
    "innermost frame" slot and bury it — the same failure mode as the full
    traceback, just shorter.
    """
    if filename == "<string>":
        return "user"  # the heredoc body is exec'd under this name
    if filename.startswith("<"):
        return "library"  # <frozen importlib...>, <stdin>, ...

    try:
        path = Path(filename).resolve()
    except (OSError, ValueError):
        return "library"

    # Harness first: when sql-harness is pip-installed, its own package sits
    # under sys.prefix and would otherwise be misread as library code.
    try:
        if path.is_relative_to(Path(__file__).resolve().parent):
            return "harness"
    except (OSError, ValueError):
        pass

    # stdlib + site-packages, covering both a venv and its base interpreter.
    for root in {sys.prefix, sys.base_prefix}:
        try:
            if path.is_relative_to(Path(root)):
                return "library"
        except (OSError, ValueError):
            pass
    return "user"


def _chain(exc: BaseException):
    """Yield ``exc`` then its ``__cause__``/``__context__`` chain, deepest last."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _dbapi_error(exc: BaseException) -> BaseException | None:
    """Deepest exception carrying a driver error in ``.orig``, if any.

    Usually the raised exception *is* SQLAlchemy's wrapper, but user code that
    does ``raise RuntimeError(...) from e`` buries it a level down — and the
    driver's own message is the actionable one either way.
    """
    found = None
    for link in _chain(exc):
        if getattr(link, "orig", None) is not None:
            found = link
    return found


def _oneline(text: str, limit: int = 300) -> str:
    """Collapse a multi-line driver message to one bounded line.

    psycopg in particular appends a ``LINE 1: ... ^`` caret block; useful in
    a terminal, but it is what makes an error stop being scannable.
    """
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _summarize(exc: BaseException) -> tuple[str, str, str | None]:
    """``(headline, message, sql)`` — unwrapping SQLAlchemy's DBAPI wrapper.

    SQLAlchemy hangs the driver's exception off ``.orig`` and the offending
    statement off ``.statement``. Both beat the SQLAlchemy-level text, which
    is the same message with more noise around it.
    """
    wrapper = _dbapi_error(exc)
    if wrapper is None:
        return type(exc).__name__, _oneline(exc), None

    sql = getattr(wrapper, "statement", None)
    orig = wrapper.orig
    name = type(orig).__name__
    args = getattr(orig, "args", ())

    if args and isinstance(args[0], int):
        # DBAPI convention: (errno, message). The errno is the searchable bit.
        code = args[0]
        message = str(args[1]) if len(args) > 1 else str(orig)
    else:
        # sqlite and psycopg put the message first; PG carries SQLSTATE as an
        # attribute (psycopg3: .sqlstate, psycopg2: .pgcode).
        code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        message = str(args[0]) if args else str(orig)

    headline = f"{name} ({code})" if code else name
    return headline, _oneline(message), sql


def _locate(exc: BaseException) -> tuple[str | None, str | None]:
    """``(user frame, innermost harness frame)`` for the concise report.

    The user frame is the payoff: it is the line in the heredoc body or saved
    script that actually failed, and it is exactly what a 112-line traceback
    buries. The harness frame is the fallback for bugs inside sql-harness
    itself, so those stay locatable without opening the spill file.
    """
    frames = _traceback.extract_tb(exc.__traceback__)
    user = harness = None
    for frame in frames:
        kind = _frame_kind(frame.filename)
        if kind == "harness":
            harness = f"{Path(frame.filename).name}:{frame.lineno} in {frame.name}"
        elif kind == "user":
            user = f"{frame.filename}:{frame.lineno}"
    return user, harness


def render_exception(exc: BaseException, *, label: str = "traceback") -> str:
    """Render a failed run: whole if short, summarized with a spill if long.

    A ``NameError`` in the user's heredoc is seven readable lines — printing
    it verbatim hides nothing, and summarizing it would mean writing a 300-byte
    file for no reason. A SQLAlchemy ``DBAPIError`` is the opposite: ~112 lines
    of driver internals with the actionable part (the DB's own message, the
    SQL, the user's line) buried in it. So the length decides.

    ``BH_SQL_TRACEBACK=1`` forces the raw traceback. An environment variable
    rather than a CLI flag because in heredoc mode every argument is routed to
    the CLI subparser, so a flag could never reach this path.
    """
    tb_text = "".join(
        _traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    if _truthy(os.environ.get("BH_SQL_TRACEBACK")):
        return tb_text
    if len(tb_text) <= SPILL_THRESHOLD and tb_text.count("\n") <= SPILL_MAX_LINES:
        return tb_text

    headline, message, sql = _summarize(exc)
    lines = [f"sql-harness: {headline}", f"  {message}"]
    if sql:
        lines.append(f"  sql: {_oneline(sql)}")

    user, harness = _locate(exc)
    if user:
        lines.append(f"  at:  {user}")
    if harness:
        lines.append(f"  via: {harness}")

    pointer = write_spill(tb_text, label)
    if pointer:
        lines.append(f"  full traceback {pointer}")
    else:
        # Spilling failed, so the traceback is the only copy left. Print it
        # rather than silently dropping the one thing we promised to keep.
        lines.append("  full traceback:")
        lines.extend(f"    {line}" for line in tb_text.splitlines())
    return "\n".join(lines) + "\n"
