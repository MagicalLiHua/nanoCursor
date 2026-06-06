"""gRPC client for the Go filetools service."""

import os
from typing import Optional

import grpc

from src.indexer.proto import filetools_pb2, filetools_pb2_grpc


class FileToolsClient:
    """gRPC client compatible with original file_tools interface."""

    def __init__(self, workspace: str, server_addr: Optional[str] = None):
        self._workspace = workspace
        if server_addr is None:
            server_addr = os.environ.get("FILETOOLS_GRPC_ADDR", "localhost:50054")
        self._addr = server_addr
        self._channel: Optional[grpc.Channel] = None
        self._stub: Optional[filetools_pb2_grpc.FileToolsStub] = None

    def _ensure_channel(self):
        if self._channel is None:
            self._channel = grpc.insecure_channel(self._addr)
            self._stub = filetools_pb2_grpc.FileToolsStub(self._channel)

    async def read_file(self, filename: str) -> str:
        self._ensure_channel()
        resp = self._stub.ReadFile(filetools_pb2.ReadFileRequest(
            workspace=self._workspace, filename=filename,
        ))
        return resp.content

    async def read_function(self, filename: str, function_name: str) -> str:
        self._ensure_channel()
        resp = self._stub.ReadFunction(filetools_pb2.ReadFunctionRequest(
            workspace=self._workspace, filename=filename, function_name=function_name,
        ))
        return resp.content

    async def read_class(self, filename: str, class_name: str) -> str:
        self._ensure_channel()
        resp = self._stub.ReadClass(filetools_pb2.ReadClassRequest(
            workspace=self._workspace, filename=filename, class_name=class_name,
        ))
        return resp.content

    async def read_file_range(self, filename: str, start_line: int, end_line: int) -> str:
        self._ensure_channel()
        resp = self._stub.ReadFileRange(filetools_pb2.ReadFileRangeRequest(
            workspace=self._workspace, filename=filename,
            start_line=start_line, end_line=end_line,
        ))
        return resp.content

    async def list_directory(self, path: str = ".") -> str:
        self._ensure_channel()
        resp = self._stub.ListDirectory(filetools_pb2.ListDirectoryRequest(
            workspace=self._workspace, path=path,
        ))
        return resp.content

    async def write_file(self, filename: str, content: str) -> str:
        self._ensure_channel()
        resp = self._stub.WriteFile(filetools_pb2.WriteFileRequest(
            workspace=self._workspace, filename=filename, content=content,
        ))
        return resp.message

    async def edit_file(self, filename: str, search_block: str, replace_block: str) -> str:
        self._ensure_channel()
        resp = self._stub.EditFile(filetools_pb2.EditFileRequest(
            workspace=self._workspace, filename=filename,
            search_block=search_block, replace_block=replace_block,
        ))
        return resp.result

    async def backup_file(self, filename: str) -> Optional[str]:
        self._ensure_channel()
        resp = self._stub.BackupFile(filetools_pb2.BackupFileRequest(
            workspace=self._workspace, filename=filename,
        ))
        return resp.backup_path or None

    async def rollback_file(self, filename: str, backup_index: int = -1) -> str:
        self._ensure_channel()
        resp = self._stub.RollbackFile(filetools_pb2.RollbackFileRequest(
            workspace=self._workspace, filename=filename, backup_index=backup_index,
        ))
        return resp.message

    async def list_backups(self, filename: Optional[str] = None) -> str:
        self._ensure_channel()
        resp = self._stub.ListBackups(filetools_pb2.ListBackupsRequest(
            workspace=self._workspace, filename=filename or "",
        ))
        return resp.content

    def close(self):
        if self._channel:
            self._channel.close()
            self._channel = None
            self._stub = None
