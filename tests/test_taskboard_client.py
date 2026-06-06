"""Tests for the Go taskboard gRPC client."""

from unittest.mock import MagicMock, patch

import pytest


class TestTaskBoardClient:
    @patch("src.runtime.taskboard_client.grpc.insecure_channel")
    def _make_client(self, mock_channel, run_id="test-run"):
        from src.runtime.taskboard_client import TaskBoardClient
        mock_stub = MagicMock()
        mock_channel.return_value = MagicMock()
        with patch("src.runtime.taskboard_client.taskboard_pb2_grpc.TaskBoardStub", return_value=mock_stub):
            client = TaskBoardClient(run_id, server_addr="localhost:50053")
            client._stub = mock_stub
            client._channel = MagicMock()
            return client, mock_stub

    def test_task_found(self):
        client, stub = self._make_client()
        stub.GetTask.return_value = MagicMock(found=True, task=MagicMock(
            id="t1", type="analysis", title="Analyze", goal="", status="pending",
            owner_agent_id="", agent_role="lead", dependencies=[],
            can_parallel=False, writes_files=False, resource_locks=[],
        ))
        result = client.task("t1")
        assert result is not None
        assert result["id"] == "t1"
        assert result["title"] == "Analyze"

    def test_task_not_found(self):
        client, stub = self._make_client()
        stub.GetTask.return_value = MagicMock(found=False)
        result = client.task("missing")
        assert result is None

    def test_ready_nodes(self):
        client, stub = self._make_client()
        stub.GetReadyNodes.return_value = MagicMock(tasks=[
            MagicMock(id="t2", type="test", title="B", goal="", status="ready",
                      owner_agent_id="", agent_role="tester", dependencies=["t1"],
                      can_parallel=False, writes_files=False, resource_locks=[]),
        ])
        result = client.ready_nodes()
        assert len(result) == 1
        assert result[0]["id"] == "t2"
        assert result[0]["status"] == "ready"

    def test_apply_task_status(self):
        client, stub = self._make_client()
        stub.ApplyTaskStatus.return_value = MagicMock()
        client.apply_task_status("t1", "passed")
        stub.ApplyTaskStatus.assert_called_once()

    def test_add_or_update_task(self):
        client, stub = self._make_client()
        stub.AddTask.return_value = MagicMock()
        client.add_or_update_task({"id": "t1", "type": "analysis", "title": "A"})
        stub.AddTask.assert_called_once()

    def test_remove_task(self):
        client, stub = self._make_client()
        stub.RemoveTask.return_value = MagicMock()
        client.remove_task("t1", "test")
        stub.RemoveTask.assert_called_once()

    def test_save(self):
        client, stub = self._make_client()
        stub.SaveBoard.return_value = MagicMock(path="/tmp/run_state.json")
        path = client.save("/tmp")
        assert path == "/tmp/run_state.json"

    def test_close(self):
        client, _ = self._make_client()
        mock_channel = MagicMock()
        client._channel = mock_channel
        client.close()
        mock_channel.close.assert_called_once()
        assert client._channel is None
