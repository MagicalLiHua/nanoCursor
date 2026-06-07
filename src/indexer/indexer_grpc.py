"""
gRPC client for the Go indexer service.
Drop-in replacement for the Python ProjectIndex class.
"""

import os
from pathlib import Path
from typing import Optional

import grpc

from src.indexer.proto import indexer_pb2, indexer_pb2_grpc
from src.infra.logger import logger


class ProjectIndexClient:
    """gRPC client with the same interface as the Python ProjectIndex."""

    def __init__(self, workspace: Path, server_addr: Optional[str] = None):
        self.workspace = Path(workspace).resolve()
        if server_addr is None:
            server_addr = os.environ.get("INDEXER_GRPC_ADDR", "localhost:50051")
        self._addr = server_addr
        self._channel: Optional[grpc.Channel] = None
        self._stub: Optional[indexer_pb2_grpc.IndexerStub] = None

    def _ensure_channel(self):
        if self._channel is None:
            self._channel = grpc.insecure_channel(self._addr)
            self._stub = indexer_pb2_grpc.IndexerStub(self._channel)

    def build(self, force: bool = False) -> bool:
        self._ensure_channel()
        try:
            resp = self._stub.BuildIndex(indexer_pb2.BuildIndexRequest(
                workspace=str(self.workspace),
                force=force,
            ))
            return resp.built
        except grpc.RpcError as e:
            logger.warning(f"[IndexerGRPC] Build failed: {e}")
            raise

    def update(self) -> int:
        self._ensure_channel()
        try:
            resp = self._stub.UpdateIndex(indexer_pb2.UpdateIndexRequest(
                workspace=str(self.workspace),
            ))
            return resp.updated_count
        except grpc.RpcError as e:
            logger.warning(f"[IndexerGRPC] Update failed: {e}")
            raise

    def search_symbol(self, query: str) -> list[dict]:
        self._ensure_channel()
        try:
            resp = self._stub.SearchSymbol(indexer_pb2.SearchSymbolRequest(query=query))
            return [
                {
                    "file": r.file,
                    "symbol_name": r.symbol_name,
                    "symbol_type": r.symbol_type,
                    "lineno": r.lineno,
                }
                for r in resp.results
            ]
        except grpc.RpcError as e:
            logger.warning(f"[IndexerGRPC] SearchSymbol failed: {e}")
            raise

    def search_dependents(self, module: str) -> list[str]:
        self._ensure_channel()
        try:
            resp = self._stub.SearchDependents(
                indexer_pb2.SearchDependentsRequest(module=module)
            )
            return list(resp.files)
        except grpc.RpcError as e:
            logger.warning(f"[IndexerGRPC] SearchDependents failed: {e}")
            raise

    def summary(self) -> dict:
        self._ensure_channel()
        try:
            resp = self._stub.GetSummary(indexer_pb2.GetSummaryRequest(
                workspace=str(self.workspace),
            ))
            return {
                "entry_points": list(resp.entry_points),
                "source_count": resp.source_count,
                "test_count": resp.test_count,
                "config_count": resp.config_count,
                "total_files": resp.total_files,
                "total_loc": resp.total_loc,
                "modules": {
                    k: {"role": v.role, "symbols": [
                        {"name": s.name, "type": s.type, "lineno": s.lineno}
                        for s in v.symbols
                    ]} for k, v in resp.modules.items()
                },
                "dependency_graph": {
                    k: list(v.values) for k, v in resp.dependency_graph.items()
                },
                "recently_modified": [
                    {"path": r.path, "mtime": r.mtime} for r in resp.recently_modified
                ],
            }
        except grpc.RpcError as e:
            logger.warning(f"[IndexerGRPC] GetSummary failed: {e}")
            raise

    def summary_text(self) -> str:
        self._ensure_channel()
        try:
            resp = self._stub.GetSummary(indexer_pb2.GetSummaryRequest(
                workspace=str(self.workspace),
            ))
            return resp.summary_text
        except grpc.RpcError as e:
            logger.warning(f"[IndexerGRPC] GetSummary failed: {e}")
            raise

    def route_summary(self) -> list[dict]:
        self._ensure_channel()
        try:
            resp = self._stub.GetRouteSummary(indexer_pb2.GetRouteSummaryRequest(
                workspace=str(self.workspace),
            ))
            return [
                {
                    "method": r.method,
                    "path": r.path,
                    "handler": r.handler,
                    "file": r.file,
                    "lineno": r.lineno,
                }
                for r in resp.routes
            ]
        except grpc.RpcError as e:
            logger.warning(f"[IndexerGRPC] GetRouteSummary failed: {e}")
            raise

    def callers(self, function_name: str) -> list[str]:
        self._ensure_channel()
        try:
            resp = self._stub.SearchCallers(
                indexer_pb2.SearchCallersRequest(function_name=function_name)
            )
            return list(resp.callers)
        except grpc.RpcError as e:
            logger.warning(f"[IndexerGRPC] SearchCallers failed: {e}")
            raise

    def health_sync(self, timeout_seconds: float = 1.0) -> dict:
        self._ensure_channel()
        resp = self._stub.Health(indexer_pb2.HealthRequest(), timeout=timeout_seconds)
        return {
            "ok": bool(resp.ok),
            "service": resp.service,
            "version": resp.version,
            "indexed_files": int(resp.indexed_files),
        }

    def close(self):
        if self._channel:
            self._channel.close()
            self._channel = None
            self._stub = None


# Global singleton (compatible with original get_project_index)

_client: Optional[ProjectIndexClient] = None


def get_project_index(workspace: Path = None) -> ProjectIndexClient:
    """Get or create the global indexer client."""
    global _client
    if _client is None:
        if workspace is None:
            from src.infra.config import WORKSPACE_DIR
            workspace = Path(WORKSPACE_DIR)
        _client = ProjectIndexClient(workspace)
    return _client


def reset_index():
    """Reset the global indexer client (called on workspace switch)."""
    global _client
    if _client:
        _client.close()
    _client = None
