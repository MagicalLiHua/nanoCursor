"""Resolve declared Skill settings against existing connections and authority."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from nanocursor.config import ProviderConfig
from nanocursor.mcp.tool_wrapper import MCPToolWrapper
from nanocursor.skills.parser import SkillDef, _validate_meta
from nanocursor.tools import ToolRegistry, create_default_registry
from nanocursor.tools.impl.tool_search import ToolSearchTool

if TYPE_CHECKING:
    from nanocursor.agent import Agent
    from nanocursor.permissions import PermissionChecker

BUILTINS = frozenset({"ReadFile", "WriteFile", "EditFile", "Bash", "Grep", "Glob"})


def validate_definition(skill: SkillDef) -> None:
    meta = {"name": skill.name, "description": skill.description, "mode": skill.mode, "context": skill.context}
    for key in ("model", "provider", "tools"):
        value = getattr(skill, key)
        if value is not None:
            meta[key] = list(value) if key == "tools" else value
    _validate_meta(meta, str(skill.source_path or skill.name))


def resolve_provider(skill: SkillDef, providers: list[ProviderConfig], current: ProviderConfig | None) -> ProviderConfig | None:
    validate_definition(skill)
    if skill.mode == "inline":
        return current
    if skill.provider:
        matches = [p for p in providers if p.name == skill.provider]
        if len(matches) != 1:
            raise ValueError(f"Unknown or ambiguous provider '{skill.provider}'; choose an existing connection name")
        selected = matches[0]
        if skill.model not in (None, "inherit", selected.model):
            raise ValueError(f"Model '{skill.model}' does not match provider '{selected.name}' ({selected.model})")
    elif skill.model not in (None, "inherit"):
        matches = [p for p in providers if p.model == skill.model]
        if current and current.model == skill.model:
            selected = current
        elif len(matches) == 1:
            selected = matches[0]
        else:
            raise ValueError(f"Model '{skill.model}' must match one configured connection; specify provider to disambiguate")
    else:
        selected = current
    if selected and selected._needs_trust:
        raise ValueError(f"Provider '{selected.name}' requires endpoint approval in the main session")
    return copy.deepcopy(selected)


def allowed_tools(skill: SkillDef, parent: Agent) -> tuple[str, ...]:
    registry = parent.registry
    safe = {tool.name for tool in registry.list_tools()
            if (tool.name in BUILTINS or tool.name == "ToolSearch" or isinstance(tool, MCPToolWrapper))
            and registry.is_enabled(tool.name)
            and (not isinstance(tool, MCPToolWrapper) or tool._client.is_alive)}
    names = tuple(tool.name for tool in registry.list_tools() if tool.name in safe) if skill.tools is None else skill.tools
    for name in names:
        if name not in safe:
            raise ValueError(f"Skill tool '{name}' is unknown, disabled, disconnected, or unsupported in fork; allowed: {', '.join(sorted(safe))}")
    return names


def authority(parent: Agent) -> tuple:
    checker = parent.permission_checker
    policy = None
    if checker:
        policy = (id(checker), checker.mode, checker.plan_file_path, checker.sandbox_enabled,
                  frozenset(checker._session_allowed), repr(checker.sandbox.__dict__),
                  tuple(tuple(tier) for tier in checker.rule_engine._load_tiers()))
    bash = parent.registry.get("Bash")
    return (parent.work_dir, parent.sandbox_root, parent.permission_mode, policy,
            repr(getattr(bash, "sandbox_config", None)), id(getattr(bash, "sandbox", None)))


@dataclass(frozen=True)
class ForkScope:
    registry: ToolRegistry
    permission_checker: PermissionChecker | None
    guard: Callable[[], str | None]
    work_dir: str
    sandbox_root: str | None
    session_work_dir: str


def build_scope(skill: SkillDef, parent: Agent, protocol: str) -> ForkScope:
    names = allowed_tools(skill, parent)
    original = parent.registry
    objects = {name: original.get(name) for name in names}
    connections = {name: (tool._client, tool._client._session) for name, tool in objects.items()
                   if isinstance(tool, MCPToolWrapper)}
    snapshot = authority(parent)

    def guard() -> str | None:
        try:
            if parent.registry is not original or authority(parent) != snapshot:
                return "Skill authority changed; start it again from the main session"
        except Exception:
            return "Skill permission policy cannot be verified; start it again from the main session"
        return None

    def available(name: str) -> bool:
        return (parent.registry is original and name in objects
                and original.is_enabled(name) and original.get(name) is objects[name]
                and (name not in connections or (objects[name]._client is connections[name][0]
                     and objects[name]._client.is_alive and objects[name]._client._session is connections[name][1])))

    registry = ToolRegistry(availability=available)
    # Fresh read-before-write cache; keep edits attached to the captured parent
    # session's undo history and invalidate its file read cache on writes.
    write = original.get("WriteFile")
    builtins = create_default_registry(file_cache=getattr(write, "_cache", None),
                                       file_history=getattr(write, "file_history", None))
    for name in names:
        if name == "ToolSearch":
            tool = ToolSearchTool(registry, protocol)
        elif name in BUILTINS:
            tool = builtins.get(name)
            if name == "Bash":
                for attr in ("work_dir", "sandbox", "sandbox_config"):
                    value = getattr(objects[name], attr, None)
                    setattr(tool, attr, copy.deepcopy(value) if attr == "sandbox_config" else value)
        else:
            tool = copy.copy(objects[name])  # Share MCP transport, not scope state.
        registry.register(tool)
        if original.is_discovered(name) or (isinstance(tool, MCPToolWrapper) and "ToolSearch" not in names):
            registry.mark_discovered(name)
    return ForkScope(registry, copy.deepcopy(parent.permission_checker), guard,
                     parent.work_dir, parent.sandbox_root, parent.session_work_dir)
