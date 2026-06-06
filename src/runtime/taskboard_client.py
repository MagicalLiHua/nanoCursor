"""gRPC client for the Go taskboard service."""

from __future__ import annotations

import json
import os
from typing import Optional

import grpc

from src.indexer.proto import taskboard_pb2, taskboard_pb2_grpc


class TaskBoardClient:
    """gRPC client compatible with original RunTaskBoard interface."""

    def __init__(self, run_id: str, server_addr: Optional[str] = None):
        self._run_id = run_id
        if server_addr is None:
            server_addr = os.environ.get("TASKBOARD_GRPC_ADDR", "localhost:50053")
        self._addr = server_addr
        self._channel: Optional[grpc.Channel] = None
        self._stub: Optional[taskboard_pb2_grpc.TaskBoardStub] = None

    def _ensure_channel(self) -> None:
        if self._channel is None:
            self._channel = grpc.insecure_channel(self._addr)
            self._stub = taskboard_pb2_grpc.TaskBoardStub(self._channel)

    def task(self, task_id: str) -> Optional[dict]:
        """Get a single task by ID, returns None if not found."""
        self._ensure_channel()
        assert self._stub is not None
        resp = self._stub.GetTask(taskboard_pb2.GetTaskRequest(
            run_id=self._run_id, task_id=task_id,
        ))
        if not resp.found:
            return None
        return _task_to_dict(resp.task)

    def ready_nodes(self) -> list[dict]:
        """Get tasks whose dependencies are all satisfied."""
        self._ensure_channel()
        assert self._stub is not None
        resp = self._stub.GetReadyNodes(taskboard_pb2.GetReadyNodesRequest(run_id=self._run_id))
        return [_task_to_dict(t) for t in resp.tasks]

    def apply_task_status(self, task_id: str, status: str) -> None:
        """Transition a task to a new status."""
        self._ensure_channel()
        assert self._stub is not None
        self._stub.ApplyTaskStatus(taskboard_pb2.ApplyTaskStatusRequest(
            run_id=self._run_id, task_id=task_id, status=status,
        ))

    def add_or_update_task(self, task: dict, reason: str = "") -> None:
        """Add a new task or update an existing one."""
        self._ensure_channel()
        assert self._stub is not None
        self._stub.AddTask(taskboard_pb2.AddTaskRequest(
            run_id=self._run_id, task=_dict_to_task(task), reason=reason,
        ))

    def remove_task(self, task_id: str, reason: str = "") -> None:
        """Remove a task from the board."""
        self._ensure_channel()
        assert self._stub is not None
        self._stub.RemoveTask(taskboard_pb2.RemoveTaskRequest(
            run_id=self._run_id, task_id=task_id, reason=reason,
        ))

    def connect_tasks(self, upstream: str, downstream: str, reason: str = "") -> None:
        """Add a dependency edge from upstream to downstream."""
        self._ensure_channel()
        assert self._stub is not None
        self._stub.ConnectTasks(taskboard_pb2.ConnectTasksRequest(
            run_id=self._run_id, upstream_task=upstream, downstream_task=downstream, reason=reason,
        ))

    def save(self, run_dir: str) -> str:
        """Persist the board to disk, returns the saved file path."""
        self._ensure_channel()
        assert self._stub is not None
        resp = self._stub.SaveBoard(taskboard_pb2.SaveBoardRequest(
            run_id=self._run_id, run_dir=run_dir,
        ))
        return resp.path

    def load(self, run_dir: str) -> bool:
        """Load the board from disk, returns True if a saved board was found."""
        self._ensure_channel()
        assert self._stub is not None
        resp = self._stub.LoadBoard(taskboard_pb2.LoadBoardRequest(
            run_id=self._run_id, run_dir=run_dir,
        ))
        return resp.found

    def to_task_board(self) -> dict:
        """Get the full board state as a dict."""
        self._ensure_channel()
        assert self._stub is not None
        resp = self._stub.GetBoardState(taskboard_pb2.GetBoardStateRequest(run_id=self._run_id))
        return _board_to_dict(resp.board)

    def close(self) -> None:
        """Close the gRPC channel."""
        if self._channel:
            self._channel.close()
            self._channel = None
            self._stub = None


def build_task_board(
    run_id: str,
    execution_plan: Optional[dict] = None,
    conversation_id: Optional[str] = None,
    server_addr: str = "localhost:50053",
) -> TaskBoardClient:
    """Build a task board via gRPC and return a client."""
    client = TaskBoardClient(run_id, server_addr)
    client._ensure_channel()
    assert client._stub is not None
    plan_json = json.dumps(execution_plan or {}, ensure_ascii=False)
    client._stub.BuildBoard(taskboard_pb2.BuildBoardRequest(
        run_id=run_id,
        execution_plan_json=plan_json,
        conversation_id=conversation_id or "",
    ))
    return client


def _task_to_dict(t) -> dict:
    """Convert a TaskMessage protobuf to a plain dict."""
    return {
        "id": t.id,
        "type": t.type,
        "title": t.title,
        "goal": t.goal,
        "owner_agent_id": t.owner_agent_id,
        "agent_role": t.agent_role,
        "status": t.status,
        "dependencies": list(t.dependencies),
        "can_parallel": t.can_parallel,
        "writes_files": t.writes_files,
        "resource_locks": list(t.resource_locks),
    }


def _dict_to_task(d: dict) -> taskboard_pb2.TaskMessage:
    """Convert a plain dict to a TaskMessage protobuf."""
    return taskboard_pb2.TaskMessage(
        id=d.get("id", ""),
        type=d.get("type", ""),
        title=d.get("title", ""),
        goal=d.get("goal", ""),
        owner_agent_id=d.get("owner_agent_id", ""),
        agent_role=d.get("agent_role", "lead"),
        status=d.get("status", "pending"),
        dependencies=d.get("dependencies", []),
        can_parallel=d.get("can_parallel", False),
        writes_files=d.get("writes_files", False),
        resource_locks=d.get("resource_locks", []),
    )


def _board_to_dict(b) -> dict:
    """Convert a BoardStateMessage protobuf to a plain dict."""
    return {
        "run_id": b.run_id,
        "strategy": b.strategy,
        "status": b.status,
        "revision": b.revision,
        "nodes": [_task_to_dict(t) for t in b.nodes],
    }
