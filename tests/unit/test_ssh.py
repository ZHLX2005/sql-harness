"""Unit tests for drivers/ssh.py — URL parsing + auth resolution (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sql_harness.drivers import ssh as ssh_mod
from sql_harness.drivers.ssh import SshDriver, _parse_url, _SshHandle


@pytest.mark.parametrize(
    "url, expected",
    [
        ("ssh://user:pw@host.example.com:2222?key=/tmp/id_ed25519",
         ("host.example.com", 2222, "user", "pw", "/tmp/id_ed25519")),
        ("ssh+password://alice:secret@db1", ("db1", 22, "alice", "secret", None)),
        ("ssh://root@host", ("host", 22, "root", None, None)),
        ("ssh://app@10.0.0.5:2222", ("10.0.0.5", 2222, "app", None, None)),
        ("ssh+key://user@host.example.com?key=/opt/keys/prod.pem",
         ("host.example.com", 22, "user", None, "/opt/keys/prod.pem")),
    ],
)
def test_parse_url_supported_forms(url, expected):
    assert _parse_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "postgres://localhost",  # not ssh
        "mysql://localhost",     # not ssh
        "not-a-url",
    ],
)
def test_parse_url_rejects_non_ssh(url):
    with pytest.raises(ValueError):
        _parse_url(url)


def test_parse_url_missing_host():
    with pytest.raises(ValueError, match="missing host"):
        _parse_url("ssh://user@")


def test_parse_url_default_port():
    """No explicit port → 22."""
    host, port, _, _, _ = _parse_url("ssh://user@host.example.com")
    assert port == 22
    assert host == "host.example.com"


def test_parse_url_ssh_plus_password_requires_password():
    """ssh+password:// with no password should reject."""
    with pytest.raises(ValueError, match="requires a password"):
        _parse_url("ssh+password://user@host")


def test_parse_url_fallback_password_fills_missing_url_password():
    host, port, user, pw, key = _parse_url("ssh://root@h:22", fallback_password="fb")
    assert (host, port, user, pw, key) == ("h", 22, "root", "fb", None)


def test_parse_url_url_password_beats_fallback():
    _, _, _, pw, _ = _parse_url("ssh://root:urlpw@h:22", fallback_password="fb")
    assert pw == "urlpw"


# --- password resolution through make_engine -------------------------------


class _FakeShell:
    def settimeout(self, _t):
        pass


class _FakeClient:
    """Minimal stand-in for paramiko.SSHClient (make_engine's post-open steps)."""

    def invoke_shell(self, **_kw):
        return _FakeShell()

    def open_sftp(self):
        raise RuntimeError("no sftp subsystem")


@pytest.fixture
def captured_connect(monkeypatch):
    """Stub _open_client and record the kwargs it was handed."""
    seen: dict = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return _FakeClient()

    monkeypatch.setattr(ssh_mod, "_open_client", fake)
    monkeypatch.delenv("BH_SSH_PASSWORD", raising=False)
    return seen


def _open(url: str, password: str | None = None) -> None:
    SshDriver().make_engine(url, None, password)


def test_password_from_connection_field(captured_connect):
    """The `password` field authenticates a credential-free URL."""
    _open("ssh://root@113.44.193.72:22", "Iot66688")
    assert captured_connect["password"] == "Iot66688"
    assert captured_connect["host"] == "113.44.193.72"
    assert captured_connect["username"] == "root"


def test_url_password_beats_connection_field(captured_connect):
    _open("ssh://root:urlpw@h:22", "fieldpw")
    assert captured_connect["password"] == "urlpw"


def test_connection_field_beats_env(captured_connect, monkeypatch):
    monkeypatch.setenv("BH_SSH_PASSWORD", "envpw")
    _open("ssh://root@h:22", "fieldpw")
    assert captured_connect["password"] == "fieldpw"


def test_env_password_used_when_nothing_else_set(captured_connect, monkeypatch):
    """$BH_SSH_PASSWORD now applies on the key branch too."""
    monkeypatch.setenv("BH_SSH_PASSWORD", "envpw")
    _open("ssh://root@h:22")
    assert captured_connect["password"] == "envpw"


def test_no_password_anywhere(captured_connect):
    _open("ssh://root@h:22")
    assert captured_connect["password"] is None


def test_ssh_plus_password_scheme_accepts_field(captured_connect):
    """`ssh+password://` no longer demands the password be embedded in the URL."""
    _open("ssh+password://root@h:22", "fieldpw")
    assert captured_connect["password"] == "fieldpw"


def test_ssh_plus_password_scheme_still_requires_one(captured_connect):
    with pytest.raises(ValueError, match="requires a password"):
        _open("ssh+password://root@h:22")


# --- SFTP transfer ---------------------------------------------------------


class _FakeSftp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get(self, remote: str, local: str) -> None:
        self.calls.append((remote, local))
        Path(local).write_text("payload", encoding="utf-8")

    def close(self) -> None:
        pass


def test_download_creates_local_parent_dir(tmp_path: Path) -> None:
    """Regression: download() called an unimported `Path` → NameError."""
    sftp = _FakeSftp()
    handle = _SshHandle(client=None, shell=None, sftp=sftp, url="ssh://h")
    target = tmp_path / "nested" / "dir" / "f.txt"
    handle.download("/remote/f.txt", str(target))
    assert target.read_text(encoding="utf-8") == "payload"
    assert sftp.calls == [("/remote/f.txt", str(target))]


# --- auth policy (which credential wins) -----------------------------------


class _RecordingClient:
    def __init__(self) -> None:
        self.connect_kwargs: dict | None = None

    def load_system_host_keys(self) -> None:
        pass

    def set_missing_host_key_policy(self, _policy) -> None:
        pass

    def connect(self, **kwargs) -> None:
        self.connect_kwargs = kwargs


@pytest.fixture
def recording_client(monkeypatch):
    """Replace paramiko.SSHClient with a recorder; stub key discovery."""
    made: list[_RecordingClient] = []

    def factory(*_a, **_kw):
        c = _RecordingClient()
        made.append(c)
        return c

    monkeypatch.setattr(ssh_mod.paramiko, "SSHClient", factory)
    monkeypatch.delenv("BH_SSH_KEY", raising=False)
    monkeypatch.delenv("BH_SSH_PASSWORD", raising=False)
    # Pretend a usable local key exists, so the "ignore it" case is meaningful.
    monkeypatch.setattr(ssh_mod, "_resolve_key_path", lambda explicit: explicit or "/k/auto")
    return made


def test_configured_password_beats_locally_discovered_key(recording_client):
    """A password connection must not let a ~/.ssh key hijack the handshake."""
    ssh_mod._open_client(host="h", port=22, username="root", password="pw", key_path=None)
    kw = recording_client[0].connect_kwargs
    assert kw["password"] == "pw"
    assert "key_filename" not in kw
    assert kw["look_for_keys"] is False
    assert kw["allow_agent"] is False


def test_explicit_key_beats_password(recording_client):
    ssh_mod._open_client(host="h", port=22, username="root", password="pw", key_path="/k/one")
    kw = recording_client[0].connect_kwargs
    assert kw["key_filename"] == "/k/one"
    assert kw["password"] == "pw"  # kept as fallback


def test_no_password_falls_back_to_discovered_key(recording_client):
    ssh_mod._open_client(host="h", port=22, username="root", password=None, key_path=None)
    kw = recording_client[0].connect_kwargs
    assert kw["key_filename"] == "/k/auto"
