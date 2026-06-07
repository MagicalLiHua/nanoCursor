"""Tests for the Go indexer gRPC client."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestProjectIndexClient:
    """Unit tests for ProjectIndexClient with mocked gRPC stub."""

    @patch("src.indexer.indexer_grpc.grpc.insecure_channel")
    def _make_client(self, mock_channel, workspace="/tmp/test"):
        from src.indexer.indexer_grpc import ProjectIndexClient

        mock_stub = MagicMock()
        mock_channel.return_value = MagicMock()

        with patch("src.indexer.indexer_grpc.indexer_pb2_grpc.IndexerStub", return_value=mock_stub):
            client = ProjectIndexClient(Path(workspace), server_addr="localhost:50051")
            # Pre-set channel so _ensure_channel is a no-op
            client._channel = MagicMock()
            client._stub = mock_stub
            return client, mock_stub

    def test_build(self):
        client, stub = self._make_client()
        stub.BuildIndex.return_value = MagicMock(built=True, file_count=10)
        result = client.build(force=True)
        assert result is True
        stub.BuildIndex.assert_called_once()

    def test_update(self):
        client, stub = self._make_client()
        stub.UpdateIndex.return_value = MagicMock(updated_count=3, removed_count=1)
        result = client.update()
        assert result == 3

    def test_search_symbol(self):
        client, stub = self._make_client()
        stub.SearchSymbol.return_value = MagicMock(results=[
            MagicMock(file="src/main.py", symbol_name="MyClass", symbol_type="class", lineno=10),
        ])
        results = client.search_symbol("MyClass")
        assert len(results) == 1
        assert results[0]["symbol_name"] == "MyClass"
        assert results[0]["file"] == "src/main.py"

    def test_search_dependents(self):
        client, stub = self._make_client()
        stub.SearchDependents.return_value = MagicMock(files=["src/a.py", "src/b.py"])
        result = client.search_dependents("fastapi")
        assert result == ["src/a.py", "src/b.py"]

    def test_summary(self):
        client, stub = self._make_client()
        stub.GetSummary.return_value = MagicMock(
            entry_points=["src/main.py"],
            source_count=5,
            test_count=3,
            config_count=2,
            total_files=10,
            total_loc=1000,
            modules={},
            dependency_graph={},
            recently_modified=[],
            summary_text="项目: test",
        )
        result = client.summary()
        assert result["total_files"] == 10
        assert result["source_count"] == 5
        assert "entry_points" in result

    def test_summary_text(self):
        client, stub = self._make_client()
        stub.GetSummary.return_value = MagicMock(summary_text="项目: test\n文件: 10")
        result = client.summary_text()
        assert "项目: test" in result

    def test_route_summary(self):
        client, stub = self._make_client()
        stub.GetRouteSummary.return_value = MagicMock(routes=[
            MagicMock(method="GET", path="/api/health", handler="health", file="src/main.py", lineno=10),
        ])
        result = client.route_summary()
        assert len(result) == 1
        assert result[0]["method"] == "GET"
        assert result[0]["path"] == "/api/health"

    def test_callers(self):
        client, stub = self._make_client()
        stub.SearchCallers.return_value = MagicMock(callers=["src/main.py:handle"])
        result = client.callers("process")
        assert result == ["src/main.py:handle"]

    def test_close(self):
        client, _ = self._make_client()
        mock_channel = client._channel
        client.close()
        mock_channel.close.assert_called_once()
        assert client._channel is None


class TestGetProjectIndex:
    """Tests for the global get_project_index function."""

    def test_singleton(self):
        from src.indexer.indexer_grpc import get_project_index, reset_index

        reset_index()

        with patch("src.indexer.indexer_grpc.ProjectIndexClient") as MockClient:
            MockClient.return_value = MagicMock()
            idx1 = get_project_index(Path("/tmp/test"))
            idx2 = get_project_index()
            assert idx1 is idx2

        reset_index()

    def test_reset(self):
        from src.indexer.indexer_grpc import get_project_index, reset_index

        reset_index()

        with patch("src.indexer.indexer_grpc.ProjectIndexClient") as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            get_project_index(Path("/tmp/test"))
            reset_index()
            mock_instance.close.assert_called_once()


class TestHybridProjectIndex:
    """Tests for the opt-in Go indexer facade exposed by src.indexer.indexer."""

    def test_disabled_get_project_index_uses_python(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NANOCURSOR_GO_INDEXER_ENABLED", "false")
        from src.indexer.indexer import ProjectIndex, get_project_index, reset_index

        reset_index()
        idx = get_project_index(tmp_path)
        assert isinstance(idx, ProjectIndex)
        reset_index()

    def test_go_indexer_enabled_falls_back_to_python(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NANOCURSOR_GO_INDEXER_ENABLED", "true")
        monkeypatch.setenv("NANOCURSOR_GO_INDEXER_FALLBACK", "true")

        (tmp_path / "app.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")

        from src.indexer.indexer import HybridProjectIndex, get_project_index, reset_index

        reset_index()
        idx = get_project_index(tmp_path)
        assert isinstance(idx, HybridProjectIndex)
        assert idx.build(force=True) is True
        summary = idx.summary()
        assert summary["total_files"] >= 1
        assert any(result["symbol_name"] == "hello" for result in idx.search_symbol("hello"))
        reset_index()

    def test_go_indexer_without_fallback_raises_when_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NANOCURSOR_GO_INDEXER_ENABLED", "true")
        monkeypatch.setenv("NANOCURSOR_GO_INDEXER_FALLBACK", "false")
        monkeypatch.setenv("NANOCURSOR_GO_INDEXER_ADDR", "localhost:9")

        from src.indexer.indexer import get_project_index, reset_index

        reset_index()
        idx = get_project_index(tmp_path)
        with pytest.raises(Exception):
            idx.build(force=True)
        reset_index()
