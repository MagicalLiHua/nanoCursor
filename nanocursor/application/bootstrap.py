from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from nanocursor.agent import Agent
from nanocursor.config import MemoryRecallConfig, ProviderConfig, SandboxAppConfig
from nanocursor.permissions import DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from nanocursor.runtime import app_home

if TYPE_CHECKING:
    from nanocursor.client import LLMClient
    from nanocursor.hooks import HookEngine
    from nanocursor.memory.auto_memory import MemoryManager
    from nanocursor.recovery import RecoveryRuntime
    from nanocursor.tools import ToolRegistry


@dataclass(frozen=True)
class AgentSettings:
    provider: ProviderConfig
    work_dir: str
    session_work_dir: str
    rules_dir: Path
    permission_mode: PermissionMode
    sandbox: SandboxAppConfig
    instructions: str


@dataclass(frozen=True)
class AgentDependencies:
    client: LLMClient
    registry: ToolRegistry
    hooks: HookEngine | None = None
    recovery: RecoveryRuntime | None = None
    memory: MemoryManager | None = None
    memory_recall: MemoryRecallConfig | None = None


@dataclass(frozen=True)
class AgentServices:
    agent: Agent
    permissions: PermissionChecker


def create_permissions(settings: AgentSettings, *, sandbox_active: bool = False) -> PermissionChecker:
    return PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(settings.work_dir),
        rule_engine=RuleEngine(
            user_rules_path=app_home() / "permissions.yaml",
            project_rules_path=settings.rules_dir / "permissions.yaml",
            local_rules_path=settings.rules_dir / "permissions.local.yaml",
        ),
        mode=settings.permission_mode,
        sandbox_enabled=sandbox_active and settings.sandbox.auto_allow,
    )


def assemble_agent(settings: AgentSettings, dependencies: AgentDependencies,
                   *, permissions: PermissionChecker) -> AgentServices:
    agent = Agent(
        client=dependencies.client,
        registry=dependencies.registry,
        protocol=settings.provider.protocol,
        work_dir=settings.work_dir,
        permission_checker=permissions,
        context_window=settings.provider.get_context_window(),
        instructions_content=settings.instructions,
        memory_manager=dependencies.memory,
        memory_recall_config=dependencies.memory_recall,
        hook_engine=dependencies.hooks,
        session_work_dir=settings.session_work_dir,
        recovery=dependencies.recovery,
    )
    return AgentServices(agent, permissions)
