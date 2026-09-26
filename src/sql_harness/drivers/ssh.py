"""SSH driver — generic remote-shell channel (paramiko-based).

A "connection" with driver=ssh opens a paramiko SSHClient (interactive shell
channel + optional SFTP). It is treated as a Workspace like any DB engine:
lazy open, cached for the process lifetime, disposed on close.

URL format:
    ssh://user[:password]@host[:port][?key=/path/to/private_key]
    ssh+password://user[:password]@host:port
    ssh+key://user@host:port?key=/path/to/private_key
    ssh://user@host:port (uses the connection's `password` field, $BH_SSH_PASSWORD, or a default key)

Password precedence (highest first):
  1. password embedded in the URL          (ssh://user:pw@host)
  2. the connection's `password` field     (connections.toml: password = "...")
  3. $BH_SSH_PASSWORD

Key resolution (when `?key=...` is absent):
  1. $BH_SSH_KEY env var
  2. ~/.ssh/id_ed25519, id_rsa, id_ecdsa (skipped when a password is set)
  3. none — password-only auth

Which auth wins is decided by what the connection states explicitly:
  - an explicit key (?key= / $BH_SSH_KEY) → key auth, password as fallback
  - otherwise, a password                → password-only auth
  - otherwise                            → auto-discovered ~/.ssh key

So each connection carries its own credential and switching workspaces needs no
env fiddling; a configured password is never shadowed by an unrelated key.

Use the `Workspace.client` attribute (and `.sftp`) for shell + file ops.
"""

from __future__ import annotations

import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paramiko


SSH_DEFAULT_PORT = 22
KEY_FALLBACK = ("~/.ssh/id_ed25519", "~/.ssh/id_rsa", "~/.ssh/id_ecdsa")
KEY_ENV = "BH_SSH_KEY"
PASSWORD_ENV = "BH_SSH_PASSWORD"


@dataclass
class CommandResult:
    """Result of an SSH command execution."""

    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "ok": self.ok,
        }


class SshDriver:
    """Drives a paramiko SSHClient + interactive shell channel as a Workspace.

    Not registered for SQL — sql-harness callers don't use sql() on this
    workspace. Use .client / .sftp directly.
    """

    name = "ssh"

    def make_engine(
        self, url: str, pool, password: str | None = None, read_only: bool = False
    ):
        """Return a live `SshClient` (opened). Caller owns it.

        `password` is the connection's standalone secret
        (ConnectionConfig.password), so each connection carries its own
        credentials and switching workspaces needs no env fiddling.
        Precedence: URL-embedded > `password` > $BH_SSH_PASSWORD.

        PoolConfig is ignored for SSH (no pooling). We accept it for API parity
        with DB drivers. `read_only` is accepted for the same reason and
        ignored: a remote shell is not a statement stream, so there is nothing
        meaningful to filter — use an unprivileged remote account instead.
        """
        fallback = password or os.environ.get(PASSWORD_ENV)
        host, port, username, resolved_password, key_path = _parse_url(
            url, fallback_password=fallback
        )
        client = _open_client(
            host=host,
            port=port,
            username=username,
            password=resolved_password,
            key_path=key_path,
        )
        # Attach a long-lived shell channel (paramiko's transport keeps it
        # alive). For one-shot commands, callers may use exec_command().
        shell = client.invoke_shell(term="xterm", width=200, height=50)
        shell.settimeout(5.0)
        try:
            sftp = client.open_sftp()
        except Exception:
            sftp = None
        return _SshHandle(client=client, shell=shell, sftp=sftp, url=url)

    def list_tables(self, handle, schema=None):
        """Not applicable for SSH — raise to fail loudly."""
        raise NotImplementedError("SSH workspaces have no tables; use ssh_exec()")

    def describe(self, handle, table, schema=None):
        raise NotImplementedError("SSH workspaces have no tables")


class _SshHandle:
    """Live SSH client + shell + SFTP, held by Workspace.engine.

    Public surface (the bits agents actually call):
      - .client      → paramiko.SSHClient
      - .sftp        → paramiko.SFTPClient (or None if subsystem unavailable)
      - .exec(cmd, timeout=30)  → CommandResult (stdout/stderr/exit_code)
      - .upload(src, dst)        → None  (local→remote)
      - .download(src, dst)      → None  (remote→local)
      - .close()                 → tear down
    """

    def __init__(self, client, shell, sftp, url: str):
        self.client = client
        self._shell = shell
        self.sftp = sftp
        self._url = url
        self._shell_buf = ""

    # --- one-shot command (preferred for non-interactive) ----------------

    def exec(self, command: str, timeout: float = 30.0) -> CommandResult:
        """Run `command` and wait for completion.

        Uses exec_command (returns a single result tuple), NOT the interactive
        shell — safer for parsing. Use the shell via .client.invoke_shell()
        if you need a long-running interactive session.
        """
        t0 = time.monotonic()
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        # Close stdin so the remote side sees EOF and exits cleanly.
        stdin.close()
        # readlines() waits for EOF → exit.
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        dt = (time.monotonic() - t0) * 1000
        return CommandResult(command, out, err, code, round(dt, 2))

    # --- file transfer (SFTP) ------------------------------------------------

    def upload(self, local_path: str, remote_path: str) -> None:
        """Copy local file → remote. Creates remote parent dirs."""
        if self.sftp is None:
            raise RuntimeError("SFTP subsystem not available on this host")
        _ensure_remote_dir(self.sftp, os.path.dirname(remote_path) or ".")
        self.sftp.put(local_path, remote_path)

    def download(self, remote_path: str, local_path: str) -> None:
        """Copy remote file → local. Creates local parent dirs."""
        if self.sftp is None:
            raise RuntimeError("SFTP subsystem not available on this host")
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self.sftp.get(remote_path, local_path)

    # --- context manager helpers --------------------------------------------

    def close(self) -> None:
        try:
            if self.sftp is not None:
                self.sftp.close()
        finally:
            try:
                self._shell.close()
            finally:
                self.client.close()


# --- URL parsing + connection bootstrap ----------------------------------


def _parse_url(
    url: str, fallback_password: str | None = None
) -> tuple[str, int, str, str | None, str | None]:
    """Extract (host, port, username, password, key_path) from ssh:// URL.

    `fallback_password` supplies the password when the URL carries none —
    callers pass the connection's `password` field (then $BH_SSH_PASSWORD),
    so a URL can stay credential-free while still being password-authenticated.

    Accepts:
        ssh://user:pw@host:port?key=/path
        ssh://user@host:port (auth from fallback_password / default key)
        ssh+password://user[:pw]@host:port
        ssh+key://user@host:port?key=...
    """
    from urllib.parse import urlparse, parse_qs

    p = urlparse(url)
    scheme = p.scheme
    if not scheme.startswith("ssh"):
        raise ValueError(f"not an ssh URL: {url!r}")
    host = p.hostname
    if not host:
        raise ValueError(f"missing host in URL: {url!r}")
    port = p.port or SSH_DEFAULT_PORT
    username = p.username or "root"
    password = p.password or fallback_password
    query = parse_qs(p.query)
    key_path = query.get("key", [None])[0]
    # Scheme hints
    if scheme == "ssh+password" and not password:
        raise ValueError(f"ssh+password:// requires a password: {url!r}")
    return host, port, username, password, key_path


def _resolve_key_path(explicit: str | None) -> str | None:
    """Resolve a key path: explicit > $BH_SSH_KEY > ~/.ssh/{ed25519,id_rsa,id_ecdsa}."""
    import os.path as _op

    def expand(p: str) -> str:
        return _op.expanduser(_op.expandvars(p))

    if explicit:
        p = expand(explicit)
        if not _op.exists(p):
            raise FileNotFoundError(f"SSH key not found: {p}")
        return p
    env = os.environ.get(KEY_ENV)
    if env:
        p = expand(env)
        if _op.exists(p):
            return p
    for cand in KEY_FALLBACK:
        p = expand(cand)
        if _op.exists(p):
            return p
    return None


def _open_client(
    *, host: str, port: int, username: str, password: str | None, key_path: str | None
) -> paramiko.SSHClient:
    """Open a paramiko SSHClient, with policy that trusts the user's known_hosts.

    `password` must already be resolved (URL > connection `password` > env) —
    this function does no further fallback.

    Auth policy, in order of intent:
      - explicit key (?key= / $BH_SSH_KEY) → key auth, password as fallback
      - else, a password is set            → password-only, ~/.ssh keys ignored
      - else                               → auto-discover ~/.ssh/{ed25519,rsa,ecdsa}

    That middle rule matters: with look_for_keys enabled paramiko walks every key
    in ~/.ssh, and one unparseable key there (a legacy DSA key, say) raises out of
    the transport thread and aborts the handshake before the password is ever
    tried. A connection that names a password gets password auth, full stop.
    """
    client = paramiko.SSHClient()
    # Auto-add host keys (like `ssh` does on first connect, when
    # StrictHostKeyChecking=accept-new is set).
    client.load_system_host_keys()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    explicit_key = key_path or os.environ.get(KEY_ENV)
    if explicit_key:
        key_file: str | None = _resolve_key_path(explicit_key)
    elif password:
        key_file = None
    else:
        key_file = _resolve_key_path(None)

    try:
        if key_file is not None:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                key_filename=key_file,
                timeout=15.0,
                allow_agent=True,
                look_for_keys=True,
            )
        else:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=15.0,
                allow_agent=False,
                look_for_keys=False,
            )
    except paramiko.AuthenticationException as e:
        raise RuntimeError(
            f"SSH auth failed for {username}@{host}:{port}: {e}\n"
            f"(set `password` on the connection in connections.toml, embed it in "
            f"the URL, or set $BH_SSH_PASSWORD for password auth; "
            f"provide ?key= or set $BH_SSH_KEY for key auth)"
        ) from e
    return client


def _ensure_remote_dir(sftp, remote_dir: str) -> None:
    """mkdir -p on the remote side, no error if exists."""
    remote_dir = remote_dir.rstrip("/")
    if not remote_dir or remote_dir == "." or remote_dir == "/":
        return
    try:
        sftp.stat(remote_dir)
    except IOError:
        # Recurse up.
        parent = "/".join(remote_dir.split("/")[:-1])
        _ensure_remote_dir(sftp, parent)
        sftp.mkdir(remote_dir)


def run_remote_script(handle, local_script_path: str, remote_dir: str, interpreter: str = "bash") -> CommandResult:
    """Upload a local script to remote_dir and execute it.

    Convenience for `sql-harness ssh run-script`. Returns the CommandResult.
    """
    import os.path as _op
    fname = _op.basename(local_script_path)
    remote_path = remote_dir.rstrip("/") + "/" + fname
    handle.upload(local_script_path, remote_path)
    cmd = f"{shlex.quote(interpreter)} {shlex.quote(remote_path)}"
    return handle.exec(cmd)