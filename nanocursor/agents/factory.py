from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from nanocursor.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine

if TYPE_CHECKING:
    from nanocursor.agent import Agent
    from nanocursor.client import LLMClient
    from nanocursor.context import ContentReplacementState
    from nanocursor.hooks import HookEngine
    from nanocursor.tools import ToolRegistry


@dataclass(frozen=True)
class SpawnContext:
    client: LLMClient
    registry: ToolRegistry
    protocol: str
    work_dir: str
    permissions: PermissionChecker | None
    context_window: int
    instructions: str
    max_iterations: int
    hooks: HookEngine | None
    sandbox_root: str | None
    parent_id: str
    trace_id: str
    session_work_dir: str | None = None
    guard: Callable[[], str | None] | None = None
    replacements: ContentReplacementState | None = None

    @classmethod
    def inherit(cls, parent: Agent, *, client: LLMClient, registry: ToolRegistry,
                work_dir: str, permissions: PermissionChecker, instructions: str,
                max_iterations: int, sandbox_root: str | None, fork: bool = False) -> SpawnContext:
        return cls(
            client, registry, parent.protocol, work_dir, permissions,
            parent.context_window, instructions, max_iterations, parent.hook_engine,
            sandbox_root, parent.agent_id, parent.trace_id or parent.agent_id,
            replacements=parent.replacement_state if fork else None,
        )


class AgentFactory:
    @staticmethod
    def permissions(parent: Agent, work_dir: str, mode: PermissionMode) -> PermissionChecker:
        return PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(work_dir),
            rule_engine=parent.permission_checker.rule_engine if parent.permission_checker else RuleEngine(),
            mode=mode,
        )

    @staticmethod
    def create(context: SpawnContext) -> Agent:
        from nanocursor.agent import Agent

        child = Agent(
            client=context.client,
            registry=context.registry,
            protocol=context.protocol,
            work_dir=context.work_dir,
            permission_checker=context.permissions,
            context_window=context.context_window,
            instructions_content=context.instructions,
            max_iterations=context.max_iterations,
            hook_engine=context.hooks,
            sandbox_root=context.sandbox_root,
            spawn_allowed=False,
            session_work_dir=context.session_work_dir,
            execution_guard=context.guard,
        )
        child.parent_id = context.parent_id
        child.trace_id = context.trace_id
        if context.replacements is not None:
            from nanocursor.context import clone_replacement_state
            child.replacement_state = clone_replacement_state(context.replacements)
        return child
