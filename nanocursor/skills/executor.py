from __future__ import annotations

import asyncio
import copy
import logging
import uuid
from contextlib import aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nanocursor.client import create_client
from nanocursor.config import ProviderConfig
from nanocursor.conversation import ConversationManager, Message
from nanocursor.hooks.engine import HookEngine
from nanocursor.memory.budget import clip, estimate
from nanocursor.prompts import build_system_prompt
from nanocursor.skills.parser import SkillDef, substitute_arguments
from nanocursor.skills.runtime import ForkScope, allowed_tools, build_scope, resolve_provider, validate_definition

if TYPE_CHECKING:
    from nanocursor.agent import Agent
    from nanocursor.client import LLMClient

log = logging.getLogger(__name__)
FORK_RECENT_COUNT = 5


@dataclass(frozen=True)
class SkillRunResult:
    status: str
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    usage_unknown: bool = False
    stop_reason: str = ""

    def display(self) -> str:
        usage = f"input {self.input_tokens} / output {self.output_tokens}" + (" (partial/unknown)" if self.usage_unknown else "")
        return f"{self.status} · {self.provider}/{self.model} · {usage}\n{self.text}"


@dataclass(frozen=True)
class SkillInvocation:
    skill: SkillDef
    prompt: str
    context: tuple[Message, ...]
    provider: ProviderConfig | None
    scope: ForkScope
    hooks: tuple
    max_iterations: int
    instructions: str
    context_window: int
    parent_id: str
    trace_id: str


class SkillExecutor:
    def __init__(self, agent: Agent, client: LLMClient, protocol: str,
                 *, providers: list[ProviderConfig] | None = None,
                 current_provider: ProviderConfig | None = None, trace_manager=None) -> None:
        self.agent, self.client, self.protocol = agent, client, protocol
        self.providers = providers or []
        self.current_provider = current_provider
        self.trace_manager = trace_manager
        self.last_result: SkillRunResult | None = None

    def validate(self, skill: SkillDef) -> ProviderConfig | None:
        selected = resolve_provider(skill, self.providers, self.current_provider)
        if skill.mode == "fork":
            if not self.agent.spawn_allowed:
                raise ValueError("Nested Skill forks are unsupported; invoke this Skill from the main session")
            allowed_tools(skill, self.agent)
            if selected and not selected.resolve_api_key():
                raise ValueError(f"No credential for Skill provider '{selected.name}'; configure it in the main session")
        return selected

    def describe(self, skill: SkillDef) -> str:
        selected = self.validate(skill)
        if skill.mode == "inline":
            return "Effective: main model, main permissions, no context copy"
        return (f"Effective provider/model: {selected.name}/{selected.model}" if selected else "Effective model: inherited client") + (
            f"\nWindow: {selected.get_context_window() if selected else self.agent.context_window}"
            f"\nTools: {', '.join(allowed_tools(skill, self.agent)) or '(none)'}"
            "\nPermission: inherited; operations requiring approval return to the main session")

    def execute_inline(self, skill: SkillDef, args: str) -> None:
        validate_definition(skill)
        if skill.mode != "inline":
            raise ValueError("Fork Skill must use the independent executor")
        prompt = substitute_arguments(skill.prompt_body, args)
        self.agent.activate_skill(skill.name, prompt)
        if getattr(self.agent, "recovery_state", None) is not None:
            self.agent.recovery_state.record_skill_invocation(skill.name, prompt)

    def prepare_fork(self, skill: SkillDef, args: str, *, context_messages: list[Message] | None = None) -> SkillInvocation:
        selected = self.validate(skill)
        if skill.mode != "fork":
            raise ValueError("Expected mode: fork")
        if context_messages is None and skill.context != "none":
            raise ValueError("Skill fork requires an explicit current conversation snapshot")
        return SkillInvocation(
            copy.deepcopy(skill), substitute_arguments(skill.prompt_body, args),
            tuple(copy.deepcopy(context_messages or [])), selected,
            build_scope(skill, self.agent, selected.protocol if selected else self.protocol),
            tuple(copy.deepcopy(self.agent.hook_engine.hooks)) if self.agent.hook_engine else (),
            self.agent.max_iterations, self.agent.instructions_content,
            selected.get_context_window() if selected else self.agent.context_window,
            self.agent.agent_id, self.agent.trace_id or uuid.uuid4().hex[:12],
        )

    async def execute_fork(self, skill: SkillDef, args: str, *, context_messages: list[Message] | None = None,
                           invocation: SkillInvocation | None = None) -> SkillRunResult:
        from nanocursor.agent import Agent, ErrorEvent, LoopComplete, StreamText
        invocation = invocation or self.prepare_fork(skill, args, context_messages=context_messages)
        selected, scope = invocation.provider, invocation.scope
        model = selected.model if selected else getattr(self.client, "model", "inherited")
        provider = selected.name if selected else "inherited"
        client = None
        child = None
        trace = None
        hooks = HookEngine(list(invocation.hooks)) if invocation.hooks else None
        status, reason, text = "error", "incomplete", ""
        stream_started = False
        try:
            if error := scope.guard():
                raise ValueError(error)
            # The selected connection owns its protocol, thinking, output limit,
            # window metadata and SDK lifetime. No parent object is reconfigured.
            client = create_client(selected) if selected else self.client
            if selected and not selected.context_window and not selected._fetched_context_window:
                fetch = getattr(client, "fetch_model_context_window", None)
                if fetch:
                    try:
                        window = await asyncio.wait_for(fetch(), 2)
                        if window:
                            selected.set_fetched_context_window(window)
                    except Exception:
                        pass
            window = selected.get_context_window() if selected else invocation.context_window
            child = Agent(client=client, registry=scope.registry,
                          protocol=selected.protocol if selected else self.protocol,
                          work_dir=scope.work_dir, max_iterations=invocation.max_iterations,
                          permission_checker=scope.permission_checker, hook_engine=hooks,
                          spawn_allowed=False, sandbox_root=scope.sandbox_root,
                          session_work_dir=scope.session_work_dir, context_window=window,
                          instructions_content=invocation.instructions, execution_guard=scope.guard)
            child.parent_id = invocation.parent_id
            child.trace_id = invocation.trace_id
            if self.trace_manager:
                trace = self.trace_manager.create(f"skill:{invocation.skill.name}", parent_id=invocation.parent_id, trace_id=invocation.trace_id)
                child.agent_id = trace.agent_id
                trace.work_dir = scope.work_dir
            if child.plan_mode:
                child.system_prompt_override = build_system_prompt() + "\nInherited Plan mode: follow the parent read-only and allocated plan-file restrictions."
            conversation = ConversationManager()
            output = getattr(client, "max_output_tokens", 0)
            # Keep inherited context small enough for the target connection.
            # Explicit SOP/arguments are never silently truncated.
            input_budget = min(int(window * .8), window - output)
            required = estimate(invocation.prompt) + estimate(invocation.instructions) + estimate(build_system_prompt())
            if required >= input_budget:
                raise ValueError("Skill prompt and output reserve exceed the target model window; shorten the prompt or adjust the connection limits")
            remaining = min(8192, max(0, input_budget - required - 2048))
            projected = []
            for message in reversed(invocation.context):
                content = clip(message.content, remaining)
                if content:
                    projected.append(Message(role=message.role, content=content))
                    remaining -= estimate(content)
            conversation.history.extend(reversed(projected))
            conversation.add_user_message(invocation.prompt)
            stream_started = True
            async with aclosing(child.run(conversation, interactive=False)) as events:
                async for event in events:
                    if isinstance(event, StreamText):
                        text = clip(text + event.text, 8192)
                    elif isinstance(event, ErrorEvent) and event.fatal:
                        status, reason = "error", event.code
                        text = clip(text + f"\n{event.message}", 8192)
                    elif isinstance(event, LoopComplete):
                        status, reason = "success", "complete"
        except asyncio.CancelledError:
            status = "cancelled"
            self.last_result = SkillRunResult("cancelled", text, provider, model,
                child.total_input_tokens if child else 0, child.total_output_tokens if child else 0,
                True, "cancelled")
            raise
        except Exception as exc:
            status, reason, text = "error", "execution_failed", str(exc)
        finally:
            if trace and child:
                self.trace_manager.update(trace.agent_id, input_tokens=child.total_input_tokens, output_tokens=child.total_output_tokens)
                self.trace_manager.complete(trace.agent_id, status=status)
            if hooks:
                await hooks.shutdown()
            if selected and client:
                try:
                    await asyncio.wait_for(client.aclose(), 2)
                except Exception:
                    log.warning("Skill client cleanup failed", exc_info=True)
        result = SkillRunResult(status, text, provider, model,
            child.total_input_tokens if child else 0, child.total_output_tokens if child else 0,
            bool(child and child.usage_missing_requests) or (stream_started and reason == "execution_failed"), reason)
        self.last_result = result
        return result

    def snapshot_context(self, mode: str, conversation: ConversationManager) -> list[Message]:
        """Copy text only, never unmatched tool protocol or host metadata."""
        if mode == "none":
            return []
        messages = [m for m in conversation.history if m.content and not m.tool_results and not m.memory_context]
        if mode == "recent":
            return [Message(role=m.role, content=m.content) for m in messages[-FORK_RECENT_COUNT:]]
        if mode == "full" and messages:
            lines = [f"{'User' if m.role == 'user' else 'Assistant'}: {m.content[:200]}{'...' if len(m.content) > 200 else ''}" for m in messages]
            return [Message(role="user", content="## Previous conversation summary\n\n" + "\n\n".join(lines))]
        return []
