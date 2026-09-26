from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from contextlib import aclosing

from nanocursor.config import ConfigError, load_config
from nanocursor.hooks import HookConfigError, HookEngine, load_hooks
from nanocursor.permissions import PermissionMode


def main() -> None:
    from nanocursor.environment import ENVIRONMENT_PROVIDERS, EnvironmentSelectionRequired
    from nanocursor.runtime import configure_logging, get_version, redact
    from nanocursor.validator import MissingConfigError, CredentialError
    from nanocursor.workspace import WorkspaceContext

    parser = argparse.ArgumentParser(prog="nanocursor", description="nanoCursor AI coding assistant")
    parser.add_argument("target", nargs="?", help="Workspace path, or setup / doctor")
    parser.add_argument("--cwd", metavar="PATH", help="Workspace directory (defaults to current directory)")
    parser.add_argument("--version", action="version", version=f"nanoCursor {get_version()}")
    parser.add_argument("--mode", choices=[m.value for m in PermissionMode], default=None)
    connection = parser.add_mutually_exclusive_group()
    connection.add_argument("--provider", help="Configured provider name for this invocation")
    connection.add_argument("--env", choices=list(ENVIRONMENT_PROVIDERS),
                            help="Use API_KEY, BASE_URL and MODEL from this environment group for this invocation")
    parser.add_argument("-p", metavar="PROMPT", default=None, help="Run one prompt non-interactively")
    parser.add_argument("--output-format", choices=["text", "stream-json"], default="text")
    parser.add_argument("--remote", action="store_true", help="Serve browser UI on 0.0.0.0:18888")
    parser.add_argument("--json", action="store_true", help="Machine-readable doctor report")
    parser.add_argument("--network", action="store_true", help="Doctor: send a short model test (may incur API charges)")
    parser.add_argument("--trust-project", action="store_true", help="Approve this invocation's project settings, including hooks, MCP and permissions")
    args = parser.parse_args()
    command = args.target if args.target in {"setup", "doctor"} else "run"
    if args.target and command == "run" and args.cwd:
        parser.error("Specify a positional path or --cwd, not both")
    if (args.json or args.network) and command != "doctor":
        parser.error("--json and --network are only available with doctor")
    if command != "run" and (args.p is not None or args.remote or args.mode or args.provider or args.trust_project):
        parser.error("Agent options cannot be used with setup or doctor")
    if command == "setup" and args.env:
        parser.error("--env is available for agent execution and doctor; setup already detects environment defaults")
    if args.p is not None and args.remote:
        parser.error("-p and --remote cannot be used together")
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    launch = Path.cwd()
    selected_environment = args.env

    def load_selected_config():
        nonlocal selected_environment
        try:
            return load_config(work_dir=workspace.workspace_dir, env_provider=selected_environment)
        except EnvironmentSelectionRequired as exc:
            if not interactive or args.p is not None or args.remote:
                raise
            print("Several complete environment connections are available:")
            for number, name in enumerate(exc.choices, 1):
                print(f"  {number}. {name}")
            while True:
                choice = input("Choose a name or number (empty cancels): ").strip().lower()
                if not choice:
                    raise KeyboardInterrupt
                if choice in {str(n) for n in range(1, len(exc.choices) + 1)}:
                    choice = exc.choices[int(choice) - 1]
                if choice in exc.choices:
                    selected_environment = choice
                    return load_config(work_dir=workspace.workspace_dir, env_provider=choice)
                print("Choose one of the listed connections.")

    try:
        if command == "setup":
            if not interactive:
                raise ConfigError("Setup requires an interactive terminal; no credentials were read or saved")
            from nanocursor.setup import run_setup
            if not run_setup():
                sys.exit(130)
            return
        workspace = WorkspaceContext.resolve(args.cwd or (args.target if command == "run" else None))
        if command == "doctor":
            from nanocursor.diagnostics import run_doctor
            sys.exit(run_doctor(workspace, json_output=args.json, network=args.network, env_provider=args.env))
        if args.p is None and not args.remote and not interactive:
            raise ConfigError("Interactive mode requires a terminal. Use -p PROMPT for non-interactive execution")
        try:
            config = load_selected_config()
        except MissingConfigError as exc:
            if not interactive or args.p is not None or args.remote:
                raise
            print(str(exc))
            from nanocursor.setup import run_setup
            if not run_setup():
                sys.exit(130)
            config = load_selected_config()
        if not config.project_trusted:
            if args.trust_project:
                config.project_trusted = True
                for provider in config.providers:
                    provider._needs_trust = False
            elif interactive and args.p is None and not args.remote:
                print("This workspace has project settings that can configure providers, permissions, hooks and MCP:")
                for path in config.project_files:
                    print(f"  {path}")
                for provider in config.providers:
                    print(f"  Connection: {provider.name} | {provider.protocol} | {provider.base_url}")
                if input("Trust these settings? (y/N): ").strip().lower() != "y":
                    raise ConfigError("Project settings were not approved; no hooks, MCP or model requests started")
                from nanocursor.trust import grant
                grant(workspace.workspace_dir, config.project_fingerprint)
                config = load_selected_config()
                if not config.project_trusted:
                    raise ConfigError("Project settings changed while being reviewed; restart and review again")
            else:
                raise ConfigError("Untrusted project settings. Review interactively first, or explicitly use --trust-project")
        if args.provider:
            if args.provider not in {p.name for p in config.providers}:
                raise ConfigError("--provider does not name a configured provider")
            config.default_provider = args.provider
        provider = config.selected_provider
        if not provider.resolve_api_key():
            if interactive and args.p is None and not args.remote:
                print(f"No credential available for {provider.name}. Opening setup.")
                from nanocursor.setup import run_setup
                if not run_setup():
                    sys.exit(130)
                # Re-enter the same trust/selection checks; setup may alter profiles.
                return main()
            raise CredentialError("No credential available. Run nanocursor setup or set the configured environment variable")
        if interactive and args.p is None and not args.remote:
            previous = workspace.previous_worktree()
            if previous:
                print(f"Previous Worktree: {previous.worktree_path}")
                if input("Restore it? Default keeps the current workspace. (y/N): ").strip().lower() == "y":
                    workspace.restore(previous)
        os.chdir(workspace.active_cwd)
        # Nothing above writes project state or starts configured processes.
        try:
            (workspace.active_cwd / ".nanocursor").mkdir(exist_ok=True)
            if not os.access(workspace.active_cwd / ".nanocursor", os.W_OK):
                raise OSError("workspace state directory is not writable")
            configure_logging()
        except OSError as exc:
            raise ConfigError("Cannot write application/workspace state; check directory permissions") from exc
        mode = PermissionMode(args.mode or config.permission_mode)
        hooks = load_hooks(config.raw_hooks)
        hook_engine = HookEngine(hooks) if hooks else None
        if args.p is not None:
            status = asyncio.run(_run_prompt(config, mode, hook_engine, args.p, args.output_format, workspace=workspace))
            if status:
                sys.exit(status)
            return
        if args.remote:
            from nanocursor.remote import RemoteServer
            ordered = [provider] + [p for p in config.providers if p is not provider]
            server = RemoteServer(providers=ordered, mcp_servers=config.mcp_servers,
                                  hook_engine=hook_engine, workspace=workspace, permission_mode=mode, sandbox_config=config.sandbox)
            asyncio.run(server.run())
            return
        from nanocursor.app import NanoCursorApp
        from nanocursor.driver import NoAltScreenDriver
        app = NanoCursorApp(
            providers=config.providers, permission_mode=mode, mcp_servers=config.mcp_servers,
            hook_engine=hook_engine, enable_fork=config.enable_fork,
            enable_verification_agent=config.enable_verification_agent,
            worktree_config=config.worktree, teammate_mode=config.teammate_mode,
            enable_coordinator_mode=config.enable_coordinator_mode,
            driver_class=NoAltScreenDriver, sandbox_config=config.sandbox,
            approval_config=config.approval, workspace=workspace,
            default_provider=provider.name,
        )
        app.run()
    except (ConfigError, HookConfigError, OSError) as exc:
        code = getattr(exc, "code", "STARTUP_ERROR")
        message = redact(str(exc))
        if args.p is not None and args.output_format == "stream-json":
            print(json.dumps({"type": "error", "code": code, "message": message}), flush=True)
        elif command == "doctor" and args.json:
            print(json.dumps({"version": get_version(), "ok": False, "checks": [
                {"name": "workspace", "status": "error", "message": message}
            ]}), flush=True)
        print(f"{code}: {message}", file=sys.stderr)
        sys.exit(1)
    except (EOFError, KeyboardInterrupt):
        print("Cancelled.", file=sys.stderr)
        sys.exit(130)
    finally:
        os.chdir(launch)


async def _run_prompt(config, permission_mode, hook_engine, prompt: str, output_format: str = "text", *, workspace=None) -> int:
    from nanocursor.agent import (
        Agent,
        CompactNotification,
        ErrorEvent,
        LoopComplete,
        PermissionRequest,
        RetryEvent,
        StreamText,
        ThinkingText,
        ToolResultEvent,
        ToolUseEvent,
        TurnComplete,
        UsageEvent,
    )
    from nanocursor.client import create_client, resolve_context_window
    from nanocursor.conversation import ConversationManager
    from nanocursor.memory.instructions import load_instructions
    from nanocursor.permissions import (
        DangerousCommandDetector,
        PathSandbox,
        PermissionChecker,
        RuleEngine,
    )
    from nanocursor.tools import create_default_registry
    from nanocursor.agents.loader import AgentLoader
    from nanocursor.agents.task_manager import TaskManager
    from nanocursor.agents.trace import TraceManager
    from nanocursor.tools.agent_tool import AgentTool
    from nanocursor.tools.impl.tool_search import ToolSearchTool
    from nanocursor.teams.manager import TeamManager
    from nanocursor.tools.team_create import TeamCreateTool
    from nanocursor.tools.team_delete import TeamDeleteTool
    from nanocursor.worktree import WorktreeManager
    from nanocursor.config import WorktreeConfig

    is_json = output_format == "stream-json"

    def emit_json(obj: dict) -> None:
        """输出一行 NDJSON 到 stdout"""
        print(json.dumps(obj, ensure_ascii=False), flush=True)

    provider = config.selected_provider
    client = create_client(provider)
    # 第 2 层：尽力从 provider 自动拉取模型的 context window（缓存在 provider 上）。
    # 不会抛异常或阻塞启动；失败则退化到映射表。
    await resolve_context_window(provider)
    work_dir = str(workspace.active_cwd) if workspace else os.getcwd()
    from nanocursor.runtime import app_home

    checker = PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(work_dir),
        rule_engine=RuleEngine(
            user_rules_path=app_home() / "permissions.yaml",
            project_rules_path=Path(work_dir) / ".nanocursor" / "permissions.yaml",
            local_rules_path=Path(work_dir) / ".nanocursor" / "permissions.local.yaml",
        ),
        mode=permission_mode,
    )

    instructions = load_instructions(work_dir)
    registry = create_default_registry()
    from nanocursor.sandbox import configure_bash_sandbox
    active = configure_bash_sandbox(registry, work_dir, config.sandbox)
    checker.sandbox_enabled = active and config.sandbox.auto_allow
    registry.register(ToolSearchTool(registry, protocol=provider.protocol))

    agent = Agent(
        client=client,
        registry=registry,
        protocol=provider.protocol,
        work_dir=work_dir,
        permission_checker=checker,
        context_window=provider.get_context_window(),
        instructions_content=instructions,
        hook_engine=hook_engine,
    )

    wt_cfg = config.worktree or WorktreeConfig()
    wt_manager = WorktreeManager(
        repo_root=work_dir,
        symlink_directories=wt_cfg.symlink_directories,
    )
    trace_manager = TraceManager()
    task_manager = TaskManager()
    agent_loader = AgentLoader(work_dir, enable_verification=config.enable_verification_agent)
    agent_loader.load_all()
    team_manager = TeamManager(worktree_manager=wt_manager, trace_manager=trace_manager)

    agent_tool = AgentTool(
        agent_loader=agent_loader,
        task_manager=task_manager,
        trace_manager=trace_manager,
        parent_agent=agent,
        enable_fork=config.enable_fork,
        provider_config=provider,
        worktree_manager=wt_manager,
        team_manager=team_manager,
    )
    registry.register(agent_tool)
    registry.register(TeamCreateTool(
        team_manager=team_manager,
        parent_agent=agent,
        teammate_mode="in-process",
        is_interactive=False,
        enable_coordinator_mode=config.enable_coordinator_mode,
    ))
    registry.register(TeamDeleteTool(team_manager=team_manager, parent_agent=agent))

    def drain_notifications() -> list[str]:
        notes: list[str] = []
        for t in task_manager.poll_completed():
            notes.append(
                f"<task-notification>\n<task_id>{t.id}</task_id>\n"
                f"<status>{t.status}</status>\n<result>{t.result}</result>\n"
                f"</task-notification>"
            )
        notes.extend(team_manager.drain_lead_mailbox())
        return notes

    def drain_mailbox_only() -> list[str]:
        return team_manager.drain_lead_mailbox()

    agent.notification_fn = drain_mailbox_only

    # 使用事件驱动的 agent.run()，支持 text 和 stream-json 两种输出格式
    conv = ConversationManager()
    conv.add_user_message(prompt)

    from nanocursor.runtime import redact

    start = time.monotonic()
    text_buf = ""
    total_input = total_output = num_turns = 0
    tool_calls: list[dict] = []
    calls_by_id: dict[str, dict] = {}
    exit_code = 0
    stop_reason = "end_turn"

    def report_error(message: str, code: str, *, fatal: bool = True) -> None:
        nonlocal exit_code, stop_reason
        message = redact(message)
        if fatal:
            exit_code = 1
            stop_reason = code
        if is_json:
            emit_json({"type": "error", "message": message, "code": code, "fatal": fatal})
        else:
            print(f"{'Error' if fatal else 'Warning'}: {message}", file=sys.stderr, flush=True)

    async def consume_turn() -> None:
        nonlocal text_buf, total_input, total_output, num_turns
        base_turns = num_turns
        completed = False
        async with aclosing(agent.run(conv)) as events:
            async for event in events:
                if isinstance(event, StreamText):
                    text_buf += event.text
                    if is_json:
                        emit_json({"type": "assistant", "text": event.text})
                elif isinstance(event, ThinkingText):
                    if is_json:
                        emit_json({"type": "thinking", "text": event.text})
                elif isinstance(event, ToolUseEvent):
                    call = {"tool_id": event.tool_id, "name": event.tool_name, "is_error": None}
                    tool_calls.append(call)
                    calls_by_id[event.tool_id] = call
                    if is_json:
                        emit_json({"type": "tool_use", "tool_name": event.tool_name,
                                   "tool_id": event.tool_id, "args": event.arguments})
                elif isinstance(event, ToolResultEvent):
                    if event.tool_id in calls_by_id:
                        calls_by_id[event.tool_id]["is_error"] = event.is_error
                    if is_json:
                        emit_json({"type": "tool_result", "tool_name": event.tool_name,
                                   "tool_id": event.tool_id, "output": event.output,
                                   "is_error": event.is_error, "elapsed": round(event.elapsed, 3)})
                elif isinstance(event, UsageEvent):
                    total_input, total_output = event.input_tokens, event.output_tokens
                    if is_json:
                        emit_json({"type": "usage", "input_tokens": total_input, "output_tokens": total_output})
                elif isinstance(event, TurnComplete):
                    num_turns = base_turns + event.turn
                    if is_json:
                        emit_json({"type": "turn_complete", "turn": num_turns})
                elif isinstance(event, LoopComplete):
                    num_turns = base_turns + event.total_turns
                    completed = True
                    break
                elif isinstance(event, ErrorEvent):
                    report_error(event.message, event.code, fatal=event.fatal)
                    if event.fatal:
                        break
                elif isinstance(event, CompactNotification):
                    if is_json:
                        emit_json({"type": "compact", "message": event.message})
                elif isinstance(event, RetryEvent):
                    if is_json:
                        emit_json({"type": "retry", "reason": event.reason})
                elif isinstance(event, PermissionRequest):
                    # Keep the request pending until aclosing cancels the whole
                    # batch, so a dependent write cannot start between denial
                    # and shutdown.
                    report_error(
                        f"{event.tool_name} requires approval. No approval was granted. "
                        "Use interactive nanocursor to review it, or explicitly authorize "
                        "the required operation with a permission mode/rule before rerunning.",
                        "permission_required",
                    )
                    break
        if not completed and not exit_code:
            report_error("Agent ended without a completion event", "incomplete")

    try:
        await consume_turn()
        if team_manager._teams and not exit_code:
            for _ in range(90):
                notes = drain_notifications()
                if notes:
                    for note in notes:
                        conv.add_system_reminder(note)
                    await consume_turn()
                    if exit_code:
                        break
                running = any(not t.done() for t in task_manager._async_tasks.values())
                if not running:
                    break
                await asyncio.sleep(2)
            else:
                report_error("Timed out waiting for background team tasks", "team_timeout")
            if not exit_code and any(t.status in {"failed", "cancelled"} for t in task_manager.list_tasks()):
                report_error("A background team task failed or was cancelled", "team_failed")
    except asyncio.CancelledError:
        report_error("Operation cancelled", "cancelled")
        exit_code = 130
    except Exception as exc:
        report_error(str(exc), "runtime_error")
    finally:
        pending = list(task_manager._async_tasks.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # Exactly one terminal result, after all requested work and cleanup.
    if is_json:
        emit_json({
            "type": "result", "result": text_buf,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "num_turns": num_turns, "tool_calls": tool_calls,
            "usage": {"input_tokens": total_input, "output_tokens": total_output},
            "stop_reason": stop_reason, "is_error": bool(exit_code), "exit_code": exit_code,
        })
    else:
        print(text_buf, end="", flush=True)
    return exit_code


if __name__ == "__main__":
    main()
