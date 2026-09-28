"""A transport owner's exit must remain visible to durable MCP cleanup."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from nanocursor.config import MCPServerConfig
from nanocursor.mcp.client import MCPClient
from nanocursor.mcp.manager import MCPManager
from nanocursor.recovery import RecoveryRuntime, RecoveryStore


class Session:
    def __init__(self, *_):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def initialize(self):
        return SimpleNamespace(instructions="")

    async def list_tools(self):
        return SimpleNamespace(tools=[])


@pytest.fixture
def host(tmp_path, monkeypatch):
    work = tmp_path / "project"
    work.mkdir()
    store = RecoveryStore(tmp_path / "state")
    runtime = RecoveryRuntime.acquire(work, store=store)
    manager = MCPManager()
    manager.recovery = runtime
    manager.work_dir = str(work)
    monkeypatch.setattr("nanocursor.mcp.client.ClientSession", Session)
    yield manager, runtime
    runtime.close()
    store.close()


def install_transport(monkeypatch, *, fail_exit=False):
    exits = []

    class Transport:
        async def __aenter__(self):
            return None, None

        async def __aexit__(self, *_):
            exits.append(True)
            if fail_exit:
                raise RuntimeError("transport cleanup unconfirmed")

    async def connect(client):
        return await client._stack.enter_async_context(Transport())

    monkeypatch.setattr(MCPClient, "_connect_stdio", connect)
    return exits


@pytest.mark.asyncio
async def test_failed_transport_exit_is_unknown_not_completed(host, monkeypatch):
    manager, runtime = host
    exits = install_transport(monkeypatch, fail_exit=True)
    await manager.start(MCPServerConfig(name="local", command="unused"))
    client = manager._clients["local"]

    with pytest.raises(RuntimeError, match="cleanup unconfirmed"):
        await manager.stop("local")

    assert exits == [True]
    assert client._owner is None and not client.is_alive
    pending = runtime.pending()
    assert len(pending) == 1
    assert pending[0]["kind"] == "mcp_server"
    assert pending[0]["state"] == "outcome_unknown"
    assert "cleanup unconfirmed" in pending[0]["observation"]
    # Cleanup retry can release dead in-memory resources. It must not rewrite
    # the historical unknown outcome as a successful connection shutdown.
    await manager.shutdown()
    assert not manager._clients
    assert runtime.pending()[0]["state"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_successful_transport_exit_completes_lifetime(host, monkeypatch):
    manager, runtime = host
    exits = install_transport(monkeypatch)
    await manager.start(MCPServerConfig(name="local", command="unused"))
    await manager.stop("local")
    assert exits == [True]
    assert runtime.pending() == []
    assert runtime.store.rows("SELECT state FROM operations")[0]["state"] == "completed"


@pytest.mark.asyncio
async def test_owner_cancellation_with_successful_cleanup_is_not_exit_failure(monkeypatch):
    monkeypatch.setattr("nanocursor.mcp.client.ClientSession", Session)
    exits = install_transport(monkeypatch)
    client = MCPClient(MCPServerConfig(name="local", command="unused"))
    await client.connect()
    owner = client._owner
    owner.cancel()
    await asyncio.gather(owner, return_exceptions=True)
    await client.close()
    assert exits == [True]
    assert client._close_error is None
    assert client._owner is None and not client.is_alive
