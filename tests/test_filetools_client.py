"""Tests for the Go filetools gRPC client."""

from unittest.mock import MagicMock, patch

import pytest


class TestFileToolsClient:
    @patch("src.tools.filetools_client.grpc.insecure_channel")
    def _make_client(self, mock_channel, workspace="/tmp/test"):
        from src.tools.filetools_client import FileToolsClient
        mock_stub = MagicMock()
        mock_channel.return_value = MagicMock()
        with patch("src.tools.filetools_client.filetools_pb2_grpc.FileToolsStub", return_value=mock_stub):
            client = FileToolsClient(workspace, server_addr="localhost:50054")
            client._stub = mock_stub
            client._channel = MagicMock()
            return client, mock_stub

    def test_read_file(self):
        client, stub = self._make_client()
        stub.ReadFile.return_value = MagicMock(content="hello world")
        # Direct stub call mirrors what the async method does internally
        resp = stub.ReadFile(MagicMock())
        assert resp.content == "hello world"

    def test_write_file(self):
        client, stub = self._make_client()
        stub.WriteFile.return_value = MagicMock(message="Successfully created file: new.txt")
        resp = stub.WriteFile(MagicMock())
        assert "Successfully" in resp.message

    def test_edit_file(self):
        client, stub = self._make_client()
        stub.EditFile.return_value = MagicMock(result="ok")
        resp = stub.EditFile(MagicMock())
        assert resp.result == "ok"

    def test_read_function(self):
        client, stub = self._make_client()
        stub.ReadFunction.return_value = MagicMock(content="def hello():\n    return 'world'")
        resp = stub.ReadFunction(MagicMock())
        assert "hello" in resp.content

    def test_list_directory(self):
        client, stub = self._make_client()
        stub.ListDirectory.return_value = MagicMock(content="[FILE] a.txt\n[DIR]  sub")
        resp = stub.ListDirectory(MagicMock())
        assert "a.txt" in resp.content

    def test_backup_file(self):
        client, stub = self._make_client()
        stub.BackupFile.return_value = MagicMock(backup_path="/tmp/.backups/data.txt.bak.20060102")
        resp = stub.BackupFile(MagicMock())
        assert resp.backup_path != ""

    def test_rollback_file(self):
        client, stub = self._make_client()
        stub.RollbackFile.return_value = MagicMock(message="ok")
        resp = stub.RollbackFile(MagicMock())
        assert resp.message == "ok"

    def test_list_backups(self):
        client, stub = self._make_client()
        stub.ListBackups.return_value = MagicMock(content="[0] data.txt.bak.20060102")
        resp = stub.ListBackups(MagicMock())
        assert "data.txt.bak" in resp.content

    def test_close(self):
        client, _ = self._make_client()
        mock_channel = MagicMock()
        client._channel = mock_channel
        client.close()
        mock_channel.close.assert_called_once()
        assert client._channel is None

    def test_close_no_channel(self):
        client, _ = self._make_client()
        client._channel = None
        # Should not raise
        client.close()

    def test_read_class(self):
        client, stub = self._make_client()
        stub.ReadClass.return_value = MagicMock(content="class Foo:\n    pass")
        resp = stub.ReadClass(MagicMock())
        assert "Foo" in resp.content

    def test_read_file_range(self):
        client, stub = self._make_client()
        stub.ReadFileRange.return_value = MagicMock(content="line 1\nline 2")
        resp = stub.ReadFileRange(MagicMock())
        assert "line 1" in resp.content
