from __future__ import annotations

import asyncio
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import yaml

from nanocursor.config import ConfigError, ProviderConfig, load_config
from nanocursor.diagnostics import diagnose
from nanocursor.setup import run_setup, save_profile
from nanocursor.storage import atomic_write, state_lock
from nanocursor.trust import grant
from nanocursor.workspace import WorkspaceContext


@pytest.fixture
def environment(tmp_path, monkeypatch):
    home = tmp_path / "user state"
    project = tmp_path / "中文 project"
    project.mkdir()
    monkeypatch.setenv("NANOCURSOR_HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.chdir(project)
    return home, project


def profile(**changes):
    return {"name": "demo", "protocol": "openai-compat", "base_url": "https://example.invalid/v1", "model": "test-model", **changes}


def write_config(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))


def test_help_version_no_config_no_writes(environment):
    home, project = environment
    for flag in ("--help", "--version"):
        result = subprocess.run([sys.executable, "-m", "nanocursor", flag], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "nanoCursor" in result.stdout
    assert not home.exists()
    assert not (project / ".nanocursor").exists()


def test_noninteractive_missing_config_is_structured_and_never_prompts(environment):
    result = subprocess.run([sys.executable, "-m", "nanocursor", "-p", "hello", "--output-format", "stream-json"], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)["code"] == "CONFIG_MISSING"
    assert not environment[0].exists()


def test_doctor_invalid_workspace_still_returns_json(environment):
    result = subprocess.run([sys.executable, "-m", "nanocursor", "doctor", "--cwd", "missing", "--json"], capture_output=True, text=True)
    assert result.returncode == 1
    assert not json.loads(result.stdout)["ok"]
    assert not environment[0].exists()


def test_relative_application_home_cannot_follow_workspace_switches(environment, monkeypatch):
    from nanocursor.runtime import app_home
    monkeypatch.setenv("NANOCURSOR_HOME", "relative-state")
    with pytest.raises(ConfigError, match="absolute"):
        app_home()


def test_permission_rules_participate_in_project_review(environment):
    home, project = environment
    save_profile({}, profile(), "synthetic", b"")
    assert load_config().project_trusted
    path = project / ".nanocursor/permissions.yaml"
    write_config(path, [{"rule": "Bash(*)", "effect": "allow"}])
    config = load_config()
    assert not config.project_trusted and str(path) in config.project_files
    grant(project, config.project_fingerprint)
    assert load_config().project_trusted
    write_config(path, [])
    assert not load_config().project_trusted


@pytest.mark.parametrize("args", [["setup"], [], ["elsewhere", "--cwd", "."], ["--network"]])
def test_invalid_or_non_tty_invocations_fail_without_state(environment, args):
    result = subprocess.run([sys.executable, "-m", "nanocursor", *args], input="", capture_output=True, text=True)
    assert result.returncode != 0
    assert not environment[0].exists()


def test_partial_config_explicit_resets_and_sources(environment):
    home, project = environment
    write_config(home / "config.yaml", {"providers": [profile()], "permission_mode": "acceptEdits", "enable_fork": True,
        "sandbox": {"enabled": True, "network_enabled": True}, "worktree": {"stale_cutoff_hours": 50},
        "approval": {"mode": "smart", "provider": "demo"}})
    project_file = project / ".nanocursor/config.yaml"
    write_config(project_file, {"permission_mode": "default", "enable_fork": False,
        "sandbox": {"enabled": False, "network_enabled": False}, "worktree": {"stale_cutoff_hours": 24},
        "approval": {"mode": "manual", "provider": None}})
    config = load_config()
    assert config.permission_mode == "default"
    assert not config.enable_fork and not config.sandbox.enabled and not config.sandbox.network_enabled
    assert config.worktree.stale_cutoff_hours == 24
    assert config.approval.mode == "manual" and config.approval.provider is None
    assert config.sources["sandbox.enabled"] == str(project_file)


def test_v1_hooks_append_v2_hooks_replace_and_mcp_clear(environment):
    home, project = environment
    base = {"providers": [profile()], "hooks": [{"id": "old"}], "mcp_servers": [{"name": "one", "command": "missing"}]}
    write_config(home / "config.yaml", base)
    write_config(project / ".nanocursor/config.yaml", {"hooks": []})
    assert load_config().raw_hooks == [{"id": "old"}]
    write_config(home / "config.yaml", {**base, "schema_version": 2})
    write_config(project / ".nanocursor/config.yaml", {"hooks": [], "mcp_servers": []})
    assert load_config().raw_hooks == [] and load_config().mcp_servers == []
    write_config(project / ".nanocursor/config.yaml", {"mcp_servers": [{"name": "one", "enabled": False}]})
    assert load_config().mcp_servers == []


def test_v2_project_selects_and_tunes_profile_not_endpoint(environment):
    home, project = environment
    write_config(home / "config.yaml", {"schema_version": 2, "providers": [profile()], "default_provider": "demo"})
    path = project / ".nanocursor/config.yaml"
    write_config(path, {"providers": [{"name": "demo", "model": "other-model"}]})
    assert load_config().selected_provider.model == "other-model"
    write_config(path, {"providers": [{"name": "demo", "base_url": "https://other.invalid"}]})
    with pytest.raises(ConfigError, match="endpoints"):
        load_config()


@pytest.mark.parametrize("extra", [{"schema_version": 3}, {"schema_version": True}, {"approval": None}, {"sandbox": []}, {"default_provider": "absent"}])
def test_invalid_config_is_actionable(environment, extra):
    home, _ = environment
    write_config(home / "config.yaml", {"providers": [profile()], **extra})
    with pytest.raises(ConfigError):
        load_config()


def test_explicit_env_source_never_falls_back(environment, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-service-secret")
    provider = ProviderConfig(**profile(api_key_env="DEEPSEEK_API_KEY"))
    assert provider.resolve_api_key() == ""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "correct-service-secret")
    assert provider.resolve_api_key() == "correct-service-secret"


def test_credential_target_and_project_trust(environment, monkeypatch):
    home, project = environment
    save_profile({"schema_version": 2}, profile(), "synthetic-secret", b"")
    config = load_config()
    assert config.selected_provider.resolve_api_key() == "synthetic-secret"
    config.selected_provider.base_url = "https://other.invalid/v1"
    with pytest.raises(ConfigError, match="another"):
        config.selected_provider.resolve_api_key()
    # Legacy project configuration must not silently forward an inherited env key.
    write_config(home / "config.yaml", {"providers": [profile()]})
    write_config(project / ".nanocursor/config.yaml", {"providers": [profile(base_url="https://other.invalid/v1")]})
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    config = load_config()
    with pytest.raises(ConfigError, match="approval"):
        config.selected_provider.resolve_api_key()
    grant(project, config.project_fingerprint)
    assert load_config().selected_provider.resolve_api_key() == "synthetic-secret"
    write_config(project / ".nanocursor/config.yaml", {"providers": [profile(base_url="https://changed.invalid/v1")]})
    assert not load_config().project_trusted


def test_save_permissions_preserves_legacy_unknown_fields_and_backup(environment):
    home, _ = environment
    original = {"providers": [profile(api_key="old-synthetic-secret")], "custom_field": {"kept": True}}
    write_config(home / "config.yaml", original)
    snapshot = (home / "config.yaml").read_bytes()
    save_profile(original, profile(), "new-synthetic-secret", snapshot)
    loaded = yaml.safe_load((home / "config.yaml").read_text())
    assert loaded["custom_field"] == {"kept": True}
    assert "new-synthetic-secret" not in (home / "config.yaml").read_text()
    assert load_config().selected_provider.resolve_api_key() == "new-synthetic-secret"
    assert len(list(home.glob("config.backup-*.yaml"))) == 1
    if os.name == "posix":
        for path in [home / "config.yaml", home / "credentials.json", *home.glob("config.backup-*.yaml")]:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_rejects_stale_snapshot_and_lock_contention(environment):
    home, _ = environment
    save_profile({}, profile(), "secret", b"")
    with pytest.raises(ConfigError, match="changed"):
        save_profile({}, profile(), "another", b"")
    with state_lock(home):
        with pytest.raises(ConfigError, match="Another"):
            with state_lock(home):
                pass


def test_config_failure_leaves_old_credentials_and_config_usable(environment):
    home, _ = environment
    save_profile({}, profile(), "before", b"")
    snapshot = (home / "config.yaml").read_bytes()
    raw = yaml.safe_load(snapshot)
    real_write = atomic_write
    def failing_write(path, data):
        if path.name == "config.yaml":
            raise OSError("disk failure")
        real_write(path, data)
    with patch("nanocursor.setup.atomic_write", failing_write), pytest.raises(OSError):
        save_profile(raw, profile(), "after", snapshot)
    assert (home / "config.yaml").read_bytes() == snapshot
    assert load_config().selected_provider.resolve_api_key() == "before"


def test_secret_store_rejects_symlink(environment, tmp_path):
    home, _ = environment
    home.mkdir()
    other = tmp_path / "keep.json"
    other.write_text("keep")
    (home / "credentials.json").symlink_to(other)
    with pytest.raises(ConfigError, match="symbolic"):
        save_profile({}, profile(), "secret", b"")
    assert other.read_text() == "keep"


def test_setup_offline_and_cancel(environment):
    answers = iter(["1", "", "", "test-model", "1", "n", "y"])
    output = []
    assert run_setup(input_fn=lambda _: next(answers), secret_fn=lambda _: "test-only-key", output=output.append)
    assert load_config().selected_provider.name == "deepseek"
    assert load_config().selected_provider.resolve_api_key() == "test-only-key"
    assert not any("test-only-key" in line for line in output)
    before = (environment[0] / "config.yaml").read_bytes()
    assert not run_setup(input_fn=lambda _: (_ for _ in ()).throw(KeyboardInterrupt()), output=output.append)
    assert (environment[0] / "config.yaml").read_bytes() == before


def test_doctor_does_not_execute_project_settings_or_leak_secrets(environment):
    home, project = environment
    write_config(home / "config.yaml", {"providers": [profile(api_key="plain-secret-test")], "hooks": [{"id": "anything"}]})
    before = sorted(str(p) for p in project.rglob("*"))
    with patch("nanocursor.connection.check_connection", side_effect=AssertionError("no network")):
        report = diagnose(WorkspaceContext.resolve())
    assert "plain-secret-test" not in json.dumps(report)
    assert sorted(str(p) for p in project.rglob("*")) == before
    assert not (home / "logs").exists()


def test_yaml_error_never_echoes_secret(environment):
    home, _ = environment
    home.mkdir()
    (home / "config.yaml").write_text('providers: [\napi_key: "plain-secret-test"\n')
    report = diagnose(WorkspaceContext.resolve())
    assert not report["ok"] and "plain-secret-test" not in json.dumps(report)


@pytest.mark.parametrize("protocol,endpoint,field", [("anthropic", "/v1/messages", "content"), ("openai", "/responses", "output"), ("openai-compat", "/chat/completions", "choices")])
def test_connection_check_uses_expected_protocol_without_tools(environment, protocol, endpoint, field):
    from nanocursor.connection import check_connection
    requests = []
    def respond(request):
        requests.append(request)
        assert request.url.path == endpoint
        assert "tools" not in json.loads(request.content)
        return httpx.Response(200, json={field: []})
    real_client = httpx.AsyncClient
    with patch("nanocursor.connection.httpx.AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw)):
        result = asyncio.run(check_connection(ProviderConfig(**profile(protocol=protocol, base_url="https://example.invalid", api_key="secret"))))
    assert result.ok and len(requests) == 1


@pytest.mark.parametrize("status,code", [(401, "AUTH_FAILED"), (403, "ACCESS_DENIED"), (404, "ENDPOINT_OR_MODEL"), (429, "RATE_LIMITED"), (500, "PROVIDER_ERROR"), (302, "PROVIDER_ERROR")])
def test_connection_errors_do_not_echo_response(environment, status, code):
    from nanocursor.connection import check_connection
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(status, text="credential-was-secret"))
    with patch("nanocursor.connection.httpx.AsyncClient", lambda **kw: real_client(transport=transport, **kw)):
        result = asyncio.run(check_connection(ProviderConfig(**profile(api_key="secret"))))
    assert not result.ok and result.code == code
    assert "credential-was-secret" not in result.message


@pytest.mark.parametrize("protocol", ["anthropic", "openai", "openai-compat"])
@pytest.mark.asyncio
async def test_keyless_sdk_requests_do_not_send_placeholder_credentials(environment, protocol):
    from nanocursor.client import complete_review, create_client
    from nanocursor.conversation import ConversationManager
    requests = []
    def respond(request):
        requests.append(request)
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        if request.method == "GET":
            return httpx.Response(200, json={"max_input_tokens": 12345})
        if json.loads(request.content).get("stream"):
            if protocol == "anthropic":
                events = [
                    {"type": "message_start", "message": {"id": "fixture", "type": "message", "role": "assistant",
                        "model": "test-model", "content": [], "stop_reason": None, "stop_sequence": None,
                        "usage": {"input_tokens": 1, "output_tokens": 0}}},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": 1}},
                    {"type": "message_stop"},
                ]
                content = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)
            if protocol == "openai":
                event = {"type": "response.completed", "sequence_number": 0, "response": {
                    "id": "fixture", "object": "response", "created_at": 0,
                    "model": "test-model", "status": "completed", "output": [], "usage": None,
                }}
            else:
                event = {"id": "fixture", "object": "chat.completion.chunk", "created": 0,
                         "model": "test-model", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n")
        if protocol == "anthropic":
            result = {"content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn",
                      "usage": {"input_tokens": 1, "output_tokens": 1}}
        elif protocol == "openai":
            result = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]}
        else:
            result = {"choices": [{"finish_reason": "stop", "message": {"content": "OK"}}]}
        return httpx.Response(200, json=result)
    provider = ProviderConfig(**profile(protocol=protocol, auth="none"))
    import nanocursor.client as module
    real_factory = module.AsyncAnthropic if protocol == "anthropic" else module.AsyncOpenAI
    def factory(**kwargs):
        return real_factory(http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)), **kwargs)
    with patch.object(module, "AsyncAnthropic" if protocol == "anthropic" else "AsyncOpenAI", factory):
        client = create_client(provider)
        async with client._client:
            conversation = ConversationManager()
            conversation.add_user_message("hello")
            _ = [event async for event in client.stream(conversation)]
            if protocol == "anthropic":
                assert await client.fetch_model_context_window() == 12345
        review = await complete_review(provider, "review", "command", 10)
        assert review.complete and review.text == "OK"
    assert len(requests) == (3 if protocol == "anthropic" else 2)


def test_workspace_is_explicit_and_nonexistent_paths_not_created(environment):
    _, project = environment
    nested = project / "nested"
    nested.mkdir()
    context = WorkspaceContext.resolve("nested")
    assert context.active_cwd == nested and Path.cwd() == project
    with pytest.raises(ConfigError):
        WorkspaceContext.resolve("missing")
    assert not (project / "missing").exists()


def test_cli_cwd_reaches_tui_without_mutating_callers_cwd(environment, monkeypatch):
    from nanocursor.__main__ import main
    home, project = environment
    elsewhere = project / "nested"
    elsewhere.mkdir()
    save_profile({}, profile(), "synthetic", b"")
    monkeypatch.setattr(sys, "argv", ["nanocursor", "--cwd", str(elsewhere)])
    with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), patch("nanocursor.app.NanoCursorApp") as app:
        main()
    assert app.call_args.kwargs["workspace"].active_cwd == elsewhere
    assert app.call_args.kwargs["default_provider"] == "demo"
    assert Path.cwd() == project
    assert not (project / ".nanocursor").exists()
    assert (elsewhere / ".nanocursor").is_dir()


def make_previous_worktree(project):
    from nanocursor.worktree.models import WorktreeSession
    from nanocursor.worktree.session import save_worktree_session
    directory = project / ".nanocursor/worktrees/previous"
    directory.mkdir(parents=True)
    metadata = project / ".git/worktrees/previous"
    metadata.mkdir(parents=True)
    (metadata / "HEAD").write_text("a" * 40 + "\n")
    (directory / ".git").write_text(f"gitdir: {metadata}\n")
    record = WorktreeSession(str(project), str(directory), "previous", "main", "a" * 40)
    save_worktree_session(project / ".nanocursor", record)
    return record


@pytest.mark.parametrize("restore", [False, True])
def test_cli_worktree_restore_is_explicit(environment, monkeypatch, restore):
    from nanocursor.__main__ import main
    home, project = environment
    record = make_previous_worktree(project)
    save_profile({}, profile(), "synthetic", b"")
    saved = (project / ".nanocursor/worktree_session.json").read_bytes()
    monkeypatch.setattr(sys, "argv", ["nanocursor"])
    with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), patch("builtins.input", return_value="y" if restore else "n"), patch("nanocursor.app.NanoCursorApp") as app:
        main()
    context = app.call_args.kwargs["workspace"]
    assert context.active_cwd == (Path(record.worktree_path) if restore else project)
    assert (project / ".nanocursor/worktree_session.json").read_bytes() == saved


def test_worktree_records_cannot_escape_workspace(environment):
    from nanocursor.worktree.session import save_worktree_session
    _, project = environment
    record = make_previous_worktree(project)
    context = WorkspaceContext.resolve()
    assert context.previous_worktree() == record
    record.worktree_path = str(project.parent)
    save_worktree_session(context.state_dir, record)
    assert context.previous_worktree() is None


@pytest.mark.asyncio
async def test_tui_worktree_banner_and_tool_directory_agree(environment):
    from nanocursor.app import NanoCursorApp
    home, project = environment
    provider = ProviderConfig(**profile(api_key="synthetic"))
    app = NanoCursorApp([provider])
    with patch("nanocursor.app.create_client"), patch("nanocursor.app.resolve_context_window", return_value=None):
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.agent.work_dir == str(project)
            nested = project / "other"
            nested.mkdir()
            app.agent.set_work_dir(str(nested), isolated=True)
            await pilot.pause()
            assert app.workspace.active_cwd == nested
            assert str(nested) in str(app.query_one("#title-bar").render())
            assert app.agent.permission_checker.sandbox.project_root == nested


def test_remote_and_noninteractive_use_selected_workspace(environment, monkeypatch):
    from nanocursor.__main__ import main
    from unittest.mock import AsyncMock
    home, project = environment
    other = project / "target"
    other.mkdir()
    save_profile({}, profile(), "synthetic", b"")
    monkeypatch.setattr(sys, "argv", ["nanocursor", "--cwd", str(other), "-p", "hello"])
    with patch("nanocursor.__main__._run_prompt", new_callable=AsyncMock) as run:
        run.return_value = 0
        main()
    assert run.call_args.kwargs["workspace"].active_cwd == other
    monkeypatch.setattr(sys, "argv", ["nanocursor", "--cwd", str(other), "--remote"])
    with patch("nanocursor.remote.RemoteServer") as server:
        server.return_value.run = AsyncMock()
        main()
    assert server.call_args.kwargs["workspace"].active_cwd == other


def test_untrusted_project_never_initializes_hooks(environment, monkeypatch):
    from nanocursor.__main__ import main
    home, project = environment
    save_profile({}, profile(), "synthetic", b"")
    write_config(project / ".nanocursor/config.yaml", {"hooks": []})
    monkeypatch.setattr(sys, "argv", ["nanocursor", "-p", "hi"])
    with patch("nanocursor.__main__.load_hooks", side_effect=AssertionError("must not load hooks")), pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


def test_enabled_unavailable_sandbox_never_auto_allows(environment):
    from nanocursor.sandbox import configure_bash_sandbox
    from nanocursor.config import SandboxAppConfig
    from nanocursor.tools import create_default_registry
    with patch("nanocursor.sandbox.create_sandbox", return_value=None), pytest.raises(ConfigError, match="unavailable"):
        configure_bash_sandbox(create_default_registry(), str(environment[1]), SandboxAppConfig(enabled=True, auto_allow=True))
