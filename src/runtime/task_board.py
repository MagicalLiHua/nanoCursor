"""Mutable task-board models.

The Agent loop is the primary decision-maker. This module only keeps a
structured, mutable task board for observability, local retry, resource locks,
and evidence. It is intentionally not a LangGraph-style workflow engine.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


TASK_STATUSES = {"pending", "ready", "running", "blocked", "passed", "failed", "skipped", "cancelled"}
NodeStatus = Literal["pending", "ready", "running", "blocked", "passed", "failed", "skipped", "cancelled"]
NodeType = Literal[
    "intake",
    "plan",
    "context_build",
    "analysis",
    "implementation",
    "test",
    "review",
    "security",
    "merge",
    "report",
    "recovery",
    "direct_reply",
]


class AcceptanceCriterion(BaseModel):
    id: str
    description: str
    required: bool = True


class RetryPolicy(BaseModel):
    max_retries: int = 1
    retry_count: int = 0
    fallback_node: str | None = None


class RunTask(BaseModel):
    id: str
    type: NodeType
    title: str
    goal: str = ""
    owner_agent_id: str | None = None
    agent_role: str = "lead"
    status: NodeStatus = "pending"
    dependencies: list[str] = Field(default_factory=list)
    can_parallel: bool = False
    writes_files: bool = False
    resource_locks: list[str] = Field(default_factory=list)
    tool_policy: dict[str, Any] = Field(default_factory=dict)
    context_policy: dict[str, Any] = Field(default_factory=dict)
    acceptance: list[AcceptanceCriterion] = Field(default_factory=list)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)


class RunEdge(BaseModel):
    from_node: str
    to_node: str
    type: Literal["depends_on", "blocks", "reviews", "tests", "produces_context_for", "fallback_to"] = "depends_on"
    condition: str | None = None


class ResourceLock(BaseModel):
    id: str
    owner_node_id: str | None = None
    status: Literal["free", "locked"] = "free"


class QualityGate(BaseModel):
    id: str
    node_id: str
    title: str
    status: Literal["pending", "passed", "failed", "warning"] = "pending"


class RunTaskBoard(BaseModel):
    """Mutable Agent Loop task board.

    This object is not an execution graph. It is a mutable record of what the
    Agent Loop currently believes should be done, what is blocked, and what
    evidence has been produced.
    """
    run_id: str
    conversation_id: str | None = None
    strategy: str = "feature_delivery"
    status: Literal["created", "running", "paused", "completed", "failed", "cancelled"] = "created"
    nodes: list[RunTask] = Field(default_factory=list)
    edges: list[RunEdge] = Field(default_factory=list)
    resources: list[ResourceLock] = Field(default_factory=list)
    gates: list[QualityGate] = Field(default_factory=list)
    revision: int = 1
    change_log: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    def task(self, task_id: str) -> RunTask | None:
        return next((task for task in self.nodes if task.id == task_id), None)

    def node(self, node_id: str) -> RunTask | None:
        return self.task(node_id)

    def ready_nodes(self) -> list[RunTask]:
        passed = {node.id for node in self.nodes if node.status in {"passed", "skipped"}}
        ready: list[RunTask] = []
        for node in self.nodes:
            if node.status not in {"pending", "ready", "blocked"}:
                continue
            if all(dep in passed for dep in node.dependencies):
                node.status = "ready"
                ready.append(node)
        return ready

    def apply_task_status(self, task_id: str, status: NodeStatus) -> None:
        task = self.task(task_id)
        if not task:
            raise ValueError(f"Run task not found: {task_id}")
        if status not in TASK_STATUSES:
            raise ValueError(f"Invalid task status: {status}")
        task.status = status
        self.updated_at = time.time()
        if status == "failed":
            for child in self.nodes:
                if task_id in child.dependencies and child.status in {"pending", "ready"}:
                    child.status = "blocked"
        self.record_change("task_status", {"node_id": task_id, "task_id": task_id, "status": status})

    def apply_node_status(self, node_id: str, status: NodeStatus) -> None:
        self.apply_task_status(node_id, status)

    def record_change(self, change_type: str, payload: dict[str, Any] | None = None) -> None:
        self.revision += 1
        self.updated_at = time.time()
        self.change_log.append(
            {
                "revision": self.revision,
                "type": change_type,
                "timestamp": self.updated_at,
                "payload": payload or {},
            }
        )
        self.change_log = self.change_log[-100:]

    def add_or_update_task(self, task: RunTask, reason: str = "agent_loop_update") -> None:
        existing = self.task(task.id)
        if existing:
            existing.type = task.type
            existing.title = task.title
            existing.goal = task.goal
            existing.owner_agent_id = task.owner_agent_id
            existing.agent_role = task.agent_role
            existing.dependencies = list(task.dependencies)
            existing.can_parallel = task.can_parallel
            existing.writes_files = task.writes_files
            existing.resource_locks = list(task.resource_locks)
            existing.tool_policy = dict(task.tool_policy)
            existing.context_policy = dict(task.context_policy)
            existing.acceptance = list(task.acceptance)
            self.record_change("task_updated", {"task_id": task.id, "node_id": task.id, "reason": reason})
        else:
            self.nodes.append(task)
            self.record_change("task_added", {"task_id": task.id, "node_id": task.id, "reason": reason})
        self._sync_edges_and_resources()

    def add_or_update_node(self, node: RunTask, reason: str = "agent_loop_update") -> None:
        self.add_or_update_task(node, reason=reason)

    def remove_task(self, task_id: str, reason: str = "agent_loop_update") -> None:
        if not self.task(task_id):
            raise ValueError(f"Run task not found: {task_id}")
        self.nodes = [node for node in self.nodes if node.id != task_id]
        for node in self.nodes:
            node.dependencies = [dep for dep in node.dependencies if dep != task_id]
        self.edges = [
            edge for edge in self.edges
            if edge.from_node != task_id and edge.to_node != task_id
        ]
        self.gates = [gate for gate in self.gates if gate.node_id != task_id]
        self.record_change("task_removed", {"task_id": task_id, "node_id": task_id, "reason": reason})
        self._sync_edges_and_resources()

    def remove_node(self, node_id: str, reason: str = "agent_loop_update") -> None:
        self.remove_task(node_id, reason=reason)

    def connect_tasks(self, upstream_task: str, downstream_task: str, reason: str = "agent_loop_update") -> None:
        if not self.task(upstream_task):
            raise ValueError(f"Run task not found: {upstream_task}")
        target = self.task(downstream_task)
        if not target:
            raise ValueError(f"Run task not found: {downstream_task}")
        if upstream_task not in target.dependencies:
            target.dependencies.append(upstream_task)
        self._sync_edges_and_resources()
        self.record_change(
            "tasks_connected",
            {
                "upstream_task": upstream_task,
                "downstream_task": downstream_task,
                "from_node": upstream_task,
                "to_node": downstream_task,
                "reason": reason,
            },
        )

    def connect(self, from_node: str, to_node: str, reason: str = "agent_loop_update") -> None:
        self.connect_tasks(from_node, to_node, reason=reason)

    def disconnect_tasks(self, upstream_task: str, downstream_task: str, reason: str = "agent_loop_update") -> None:
        target = self.task(downstream_task)
        if not target:
            raise ValueError(f"Run task not found: {downstream_task}")
        target.dependencies = [dep for dep in target.dependencies if dep != upstream_task]
        self._sync_edges_and_resources()
        self.record_change(
            "tasks_disconnected",
            {
                "upstream_task": upstream_task,
                "downstream_task": downstream_task,
                "from_node": upstream_task,
                "to_node": downstream_task,
                "reason": reason,
            },
        )

    def disconnect(self, from_node: str, to_node: str, reason: str = "agent_loop_update") -> None:
        self.disconnect_tasks(from_node, to_node, reason=reason)

    def _sync_edges_and_resources(self) -> None:
        self.edges = [
            RunEdge(from_node=dep, to_node=node.id)
            for node in self.nodes
            for dep in node.dependencies
        ]
        existing_locks = {lock.id: lock for lock in self.resources}
        lock_ids = _unique([
            lock
            for node in self.nodes
            for lock in node.resource_locks
            if lock
        ])
        self.resources = [
            existing_locks.get(lock_id) or ResourceLock(id=lock_id)
            for lock_id in lock_ids
        ]

    def to_task_board(self) -> dict[str, Any]:
        """Return a loop-friendly task-board representation."""
        return {
            "run_id": self.run_id,
            "conversation_id": self.conversation_id,
            "strategy": self.strategy,
            "status": self.status,
            "revision": self.revision,
            "tasks": [
                {
                    "id": node.id,
                    "kind": node.type,
                    "title": node.title,
                    "goal": node.goal,
                    "status": node.status,
                    "agent_role": node.agent_role,
                    "blocked_by": node.dependencies,
                    "can_parallel": node.can_parallel,
                    "writes_files": node.writes_files,
                    "resource_locks": node.resource_locks,
                    "context_policy": node.context_policy,
                    "evidence_count": len(node.evidence),
                    "output_count": len(node.outputs),
                    "evidence_preview": node.evidence[-6:],
                    "output_preview": node.outputs[-4:],
                    "tool_evidence": [
                        item for item in node.evidence[-8:]
                        if item.get("kind") in {"tool_call", "file_change", "test", "quality", "diff"}
                    ],
                }
                for node in self.nodes
            ],
            "locks": [lock.model_dump() for lock in self.resources],
            "recent_changes": self.change_log[-20:],
            "metadata": self.metadata,
        }


def build_task_board(
    run_id: str,
    execution_plan: dict[str, Any] | None = None,
    conversation_id: str | None = None,
) -> RunTaskBoard:
    """Build the initial mutable task board from the current execution plan."""
    plan = execution_plan or {}
    strategy = str(plan.get("strategy") or "feature_delivery")
    now = time.time()

    if strategy == "lead_direct_reply":
        return RunTaskBoard(
            run_id=run_id,
            conversation_id=conversation_id,
            strategy=strategy,
            status="created",
            nodes=[],
            edges=[],
            created_at=now,
            updated_at=now,
            metadata={
                "runtime_model": "agent_loop_with_mutable_task_board",
                "graph_compat": True,
                "task_board_suppressed": True,
                "suppressed_reason": "lead_direct_reply",
            },
        )

    stages = plan.get("stages") if isinstance(plan.get("stages"), list) else []
    nodes: list[RunTask] = [
        RunTask(
            id="node-001-intake",
            type="intake",
            title="接收需求",
            goal="确认任务目标、工作区边界和用户约束。",
            agent_role="lead",
            status="ready",
            acceptance=[AcceptanceCriterion(id="scope_confirmed", description="任务范围已确认。")],
        ),
        RunTask(
            id="node-002-context",
            type="context_build",
            title="构建上下文",
            goal="选择相关文件、摘要、最近变更和运行约束。",
            agent_role="lead",
            dependencies=["node-001-intake"],
            acceptance=[AcceptanceCriterion(id="context_ready", description="上下文包已生成。")],
        ),
    ]

    analysis_nodes: list[RunTask] = []
    write_nodes: list[RunTask] = []
    verify_nodes: list[RunTask] = []
    review_nodes: list[RunTask] = []

    for index, stage in enumerate(stages, start=1):
        if not isinstance(stage, dict):
            continue
        stage_id = str(stage.get("id") or f"stage-{index}")
        role = str(stage.get("owner_role") or stage.get("owner") or "agent").lower()
        title = str(stage.get("title") or stage_id)
        desc = str(stage.get("description") or "")
        node_type = _node_type_for_stage(stage_id, role, title)
        node_id = f"node-{index + 2:03d}-{_safe_slug(stage_id)}"
        node = RunTask(
            id=node_id,
            type=node_type,
            title=title,
            goal=desc,
            agent_role=role,
            can_parallel=node_type == "analysis",
            writes_files=node_type == "implementation",
            resource_locks=["global:workspace_write"] if node_type == "implementation" else [],
            tool_policy=plan.get("tool_policy", {}) if isinstance(plan.get("tool_policy"), dict) else {},
            context_policy=_context_policy_for_node(node_type, stage),
            acceptance=[
                AcceptanceCriterion(id="stage_done", description=desc or f"{title} 已完成。")
            ],
        )
        if node_type in {"analysis", "plan"}:
            node.dependencies = ["node-002-context"]
            analysis_nodes.append(node)
        elif node_type == "implementation":
            write_nodes.append(node)
        elif node_type == "test":
            verify_nodes.append(node)
        elif node_type in {"review", "security"}:
            review_nodes.append(node)
        else:
            analysis_nodes.append(node)
        nodes.append(node)

    if not write_nodes and strategy not in {"analysis_only", "docs_only"}:
        write_nodes.append(
            RunTask(
                id=f"node-{len(nodes) + 1:03d}-implementation",
                type="implementation",
                title="代码实现",
                goal="按计划完成必要文件修改。",
                agent_role="coder",
                writes_files=True,
                resource_locks=["global:workspace_write"],
                context_policy={"mode": "snippet", "focus": "implementation"},
                acceptance=[AcceptanceCriterion(id="changes_made", description="必要文件已修改。")],
            )
        )
        nodes.append(write_nodes[-1])

    _wire_dependencies(nodes, analysis_nodes, write_nodes, verify_nodes, review_nodes, strategy)

    report_deps = [
        node.id for node in nodes
        if node.type in {"test", "review", "security", "implementation", "analysis"}
    ] or ["node-002-context"]
    report_node = RunTask(
        id=f"node-{len(nodes) + 1:03d}-report",
        type="report",
        title="整理交付结果",
        goal="汇总完成内容、验证证据、风险和下一步建议。",
        agent_role="lead",
        dependencies=_unique(report_deps),
        acceptance=[AcceptanceCriterion(id="report_ready", description="交付报告已生成。")],
    )
    nodes.append(report_node)

    edges = [
        RunEdge(from_node=dep, to_node=node.id)
        for node in nodes
        for dep in node.dependencies
    ]
    resources = _resources_from_nodes(nodes)
    gates = [
        QualityGate(id=f"gate-{node.id}", node_id=node.id, title=node.title)
        for node in nodes
        if node.type in {"test", "review", "security"}
    ]
    return RunTaskBoard(
        run_id=run_id,
        conversation_id=conversation_id,
        strategy=strategy,
        status="created",
        nodes=nodes,
        edges=edges,
        resources=resources,
        gates=gates,
        created_at=now,
        updated_at=now,
        metadata={"runtime_model": "agent_loop_with_mutable_task_board", "graph_compat": True},
    )


def save_task_board(board: RunTaskBoard, run_dir: Path) -> Path:
    path = run_dir / "run_state.json"
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(board.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

    # Keep the legacy artifact in sync while older routes/tools still read it.
    legacy_path = run_dir / "run_graph.json"
    legacy_tmp = legacy_path.with_name(f".{legacy_path.name}.{uuid.uuid4().hex}.tmp")
    legacy_tmp.write_text(json.dumps(board.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    legacy_tmp.replace(legacy_path)
    return path


def load_task_board(run_dir: Path) -> RunTaskBoard | None:
    path = run_dir / "run_state.json"
    if not path.exists():
        path = run_dir / "run_graph.json"
    if not path.exists():
        return None
    try:
        return RunTaskBoard(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


TaskDependency = RunEdge

RunNode = RunTask
RunGraph = RunTaskBoard

build_run_graph = build_task_board
load_run_graph = load_task_board
save_run_graph = save_task_board


def _node_type_for_stage(stage_id: str, role: str, title: str) -> NodeType:
    text = f"{stage_id} {role} {title}".lower()
    if "analysis" in str(stage_id).lower() or "分析" in str(stage_id).lower():
        return "analysis"
    if "verify" in text or "test" in text or "测试" in text or "验证" in text:
        return "test"
    if "review" in text or "reviewer" in text or "复核" in text or "审查" in text:
        return "review"
    if "security" in text or "安全" in text:
        return "security"
    if "analysis" in text or "分析" in text:
        return "analysis"
    if "implement" in text or "coder" in text or "代码" in text or "实现" in text:
        return "implementation"
    if "plan" in text or "planner" in text or "规划" in text:
        return "plan"
    return "analysis"


def _context_policy_for_node(node_type: NodeType, stage: dict[str, Any]) -> dict[str, Any]:
    base = {"stage_id": stage.get("id"), "capabilities": stage.get("capabilities", [])}
    if node_type == "analysis":
        return {**base, "mode": "outline", "scope": "read_only"}
    if node_type == "implementation":
        return {**base, "mode": "snippet", "scope": "write_target"}
    if node_type in {"test", "review", "security"}:
        return {**base, "mode": "evidence", "scope": "changed_files"}
    return {**base, "mode": "summary", "scope": "stage"}


def _wire_dependencies(
    nodes: list[RunTask],
    analysis_nodes: list[RunTask],
    write_nodes: list[RunTask],
    verify_nodes: list[RunTask],
    review_nodes: list[RunTask],
    strategy: str,
) -> None:
    context_node = "node-002-context"
    for node in analysis_nodes:
        if not node.dependencies:
            node.dependencies = [context_node]
    analysis_ids = [node.id for node in analysis_nodes] or [context_node]
    previous_write: str | None = None
    for node in write_nodes:
        if not node.dependencies:
            node.dependencies = [previous_write] if previous_write else analysis_ids
        previous_write = node.id
    write_tail = previous_write or (analysis_ids[-1] if analysis_ids else context_node)
    for node in verify_nodes:
        if not node.dependencies:
            node.dependencies = [write_tail]
    verify_tail_ids = [node.id for node in verify_nodes] or ([write_tail] if write_tail else [])
    for node in review_nodes:
        if not node.dependencies:
            node.dependencies = verify_tail_ids or [write_tail]

    if strategy in {"analysis_only", "docs_only"}:
        for node in nodes:
            if node.type == "analysis" and not node.dependencies:
                node.dependencies = [context_node]


def _resources_from_nodes(nodes: list[RunTask]) -> list[ResourceLock]:
    lock_ids = _unique([
        lock
        for node in nodes
        for lock in node.resource_locks
        if lock
    ])
    return [ResourceLock(id=lock_id) for lock_id in lock_ids]


def _safe_slug(value: str) -> str:
    chars = []
    for char in value.lower().replace("_", "-").replace(" ", "-"):
        if char.isalnum() or char == "-":
            chars.append(char)
    slug = "".join(chars).strip("-")
    return slug[:40] or "node"


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result
