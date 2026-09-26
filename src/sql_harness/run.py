"""sql-harness CLI / heredoc entry point.

Mirrors browser-harness's run.py:

- Without args + non-TTY stdin: read heredoc body, exec in helpers' globals.
- Otherwise: dispatch to cli.main().

The console script `sql-harness` resolves to this module's `main()`.
"""

from __future__ import annotations

import sys
import traceback

from . import cli, helpers
from .agent_loader import load_agent_helpers
from .analytics import CLI_INVOKED, COMMAND_EXECUTED, emit
from .config import load as load_config
from .helpers import set_active
from .manager import SqlHarness
from .output import render_exception, set_utf8_streams
from .paths import config_file, ensure_private_dir, home_dir


# Merge every helper into this module's globals so `exec(code, globals())`
# in heredoc mode sees them as bare names. Mirrors browser-harness's
# `from .helpers import *` pattern.
for _name, _value in vars(helpers).items():
    if _name.startswith("_"):
        continue
    globals()[_name] = _value

# Mirror the same names for `from .run import *` users.
__all__ = [n for n in globals().keys() if not n.startswith("_")]


def _build_harness() -> SqlHarness:
    """Build a SqlHarness from the user's config file."""
    cfg_path = config_file()
    if not cfg_path.exists():
        # First run: create a fresh state dir + empty config file.
        ensure_private_dir(home_dir())
        cfg = load_config(cfg_path)
    else:
        cfg = load_config(cfg_path)
    return SqlHarness(cfg)


def main() -> int:
    """Entry point: branch on TTY vs heredoc."""
    # Force UTF-8 on the standard streams before anything can print. Windows
    # defaults to GBK/CP936, which mangles non-ASCII bytes on the way in and
    # raises UnicodeEncodeError on the way out — for data that legitimately
    # comes back from a database or a remote host. Mirrors browser-harness's
    # Windows stream handling.
    set_utf8_streams()

    harness = _build_harness()
    set_active(harness)

    # Merge agent_helpers.py into our globals so user-defined helpers
    # are also visible in heredoc mode.
    load_agent_helpers(globals())

    # Heredoc mode: no args + piped stdin.
    if len(sys.argv) == 1 and not sys.stdin.isatty():
        code = sys.stdin.read()
        rc = 0
        # Always the full traceback, regardless of what we show the terminal:
        # the analytics log is the machine-readable record of what failed.
        error: str | None = None
        try:
            exec(code, globals())
        except SystemExit as e:
            rc = int(e.code) if e.code is not None else 0
        except Exception as exc:
            error = "".join(traceback.format_exception(exc))
            sys.stderr.write(render_exception(exc, label="heredoc"))
            rc = 1
        finally:
            emit(CLI_INVOKED, {"subcommand": "heredoc"})
            emit(
                COMMAND_EXECUTED,
                {"argv": [], "code": code, "ok": rc == 0, "exit_code": rc, "error": error},
            )
        return rc

    # Otherwise: dispatch to the CLI.
    try:
        return cli.main(sys.argv[1:])
    finally:
        harness.close_all()


if __name__ == "__main__":
    sys.exit(main())