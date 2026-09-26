# SSH mechanic — how to talk to an SSH workspace

> **Mechanic guide.** Strategy notes (when to reach for the SSH driver, auth choice) are folded into this file's "Detection" / "Approach" sections below (decision P: the legacy strategy stub was collapsed into the mechanic).

## Activation

```python
use_workspace("prod-app")          # name from connections.toml (driver=ssh)
print(ssh_info())
# {"connection": "prod-app", "user": "app", "host": "10.0.0.5", "port": 2222, "sftp_available": true}
```

Calling `ssh_*` helpers against a non-SSH workspace raises `RuntimeError("workspace 'X' is a postgres workspace, not an SSH workspace...")`.

## Run a shell command

```python
r = ssh_exec("ps aux | grep postgres | head -3", timeout=10.0)
# r == {"command": ..., "stdout": "...", "stderr": "...", "exit_code": 0, "ok": True, "duration_ms": 12.3}
sys.stdout.write(r["stdout"])
```

The exit code is propagated: the helper returns `r["exit_code"]` so a CLI invocation can propagate failure. Treat non-zero exit codes as failure unless you specifically want to ignore them.

## Upload a local file

```python
ssh_upload("./deploy.sh", "/tmp/deploy.sh")     # SFTP
```

Creates remote parent dirs (`/tmp/...` here) automatically. Overwrites without warning.

## Download a remote file

```python
ssh_download("/var/log/app/app.log", "./app.log")
```

Creates local parent dirs automatically.

## Upload + execute (run-script)

```python
result = ssh_run_script(
    "./scripts/deploy.sh",
    remote_dir="/tmp",        # default
    interpreter="bash",        # default
)
# uploads deploy.sh → /tmp/deploy.sh, then runs `bash /tmp/deploy.sh`
```

Equivalent to:

```python
ssh_upload("./scripts/deploy.sh", "/tmp/deploy.sh")
result = ssh_exec("bash /tmp/deploy.sh")
```

`run-script` is just a convenience for that 2-step pattern.

## CLI equivalents

The heredoc helpers above are also exposed as CLI subcommands (so non-Python callers can use them):

```bash
sql-harness ssh -c prod-app exec 'systemctl status myapp'
sql-harness ssh -c prod-app upload ./deploy.sh /tmp/deploy.sh
sql-harness ssh -c prod-app download /var/log/app.log ./app.log
sql-harness ssh -c prod-app run-script ./scripts/deploy.sh --remote-dir /tmp
sql-harness ssh -c prod-app info
```

The CLI uses the same `ssh_exec/ssh_upload/...` helpers under the hood — what works in Python works in the shell.

## Detection

- Caller has activated a workspace whose `connection.driver == "ssh"` (or its name is in the SSH `connections.toml` block)
- They want to execute a command, transfer a file, or run a script remotely

## Approach (one-liner recipe)

```python
use_workspace("<ssh-connection>")
result = ssh_exec("<command>")           # or ssh_upload / ssh_download / ssh_run_script
```

## Gotchas

- **Default timeout is 30 s.** Long-running commands (e.g. `pg_dump`, `tar czf`) need an explicit `timeout=` arg.
- **`timeout=` applies to the wait for exit**, not to per-line stdout. A command that produces huge stdout (e.g. `cat 10GB log`) will buffer in memory until it exits.
- **SFTP subsystem** may be disabled on the remote (chrooted SFTP, etc.). `RuntimeError("SFTP subsystem not available")` — fall back to `scp` via `ssh_exec`.
- **Shell quoting**: `ssh_exec("ls /tmp | grep foo")` — pass the whole pipeline as one string. paramiko runs it via the remote shell.
- **Working directory on the remote**: `exec_command` does NOT inherit your interactive shell's cwd. Use absolute paths or `cd / && cmd` chains.
- **Environment variables**: `exec_command` inherits a minimal env. If you need `PATH=...`, set it explicitly: `ssh_exec("PATH=/opt/app/bin:$PATH myapp --version")`.
- **Large file transfers**: SFTP buffers in memory. For files > 100 MB, prefer `scp` via `ssh_exec` (uses streaming) or chunk with `sftp.open(..., bufsize=...)` directly.
- **Connection pooling is NOT done by sql-harness**: each SSH workspace = one live TCP connection. Don't open a workspace per row.