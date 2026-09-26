"""Integration tests for the SSH driver — skipped unless $BH_SSH_URL is set.

The URL must point to a reachable SSH host. The test only runs a no-op
`uname -a` so any host (even a minimal container) works.
"""

from __future__ import annotations

import os

import pytest

from sql_harness.config import ConnectionConfig, ConnectionsConfig, PoolConfig
from sql_harness.helpers import set_active, ssh_exec, ssh_info
from sql_harness.manager import SqlHarness

SSH_URL = os.environ.get("BH_SSH_URL")

pytestmark = pytest.mark.skipif(not SSH_URL, reason="BH_SSH_URL not set")


@pytest.fixture()
def ssh_harness():
    cfg = ConnectionsConfig(
        default_workspace="test_ssh",
        pool_defaults=PoolConfig(size=1),
        connections=[
            ConnectionConfig(name="test_ssh", driver="ssh", url=SSH_URL),
        ],
    )
    h = SqlHarness(cfg)
    set_active(h)
    yield h
    h.close_all()


def test_ssh_exec_uname(ssh_harness) -> None:
    """Run `uname -a` and assert it returned kernel info."""
    r = ssh_exec("uname -a", timeout=10.0)
    assert r["ok"]
    assert r["exit_code"] == 0
    assert "Linux" in r["stdout"] or "Darwin" in r["stdout"] or "BSD" in r["stdout"]


def test_ssh_info_reports_peer(ssh_harness) -> None:
    info = ssh_info()
    assert info["user"]
    assert info["host"]
    assert info["port"] > 0


def test_ssh_exec_propagates_nonzero(ssh_harness) -> None:
    """Non-zero exit should be reflected in result dict, not raised."""
    r = ssh_exec("false")
    assert not r["ok"]
    assert r["exit_code"] != 0