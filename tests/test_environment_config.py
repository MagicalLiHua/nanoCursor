"""Environment-only startup and its interaction with saved project settings."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from nanocursor.config import ConfigError, MissingConfigError, load_config
from nanocursor.diagnostics import diagnose
from nanocursor.environment import EnvironmentSelectionRequired, connection_variables
from nanocursor.setup import run_setup
from nanocursor.workspace import WorkspaceContext


@pytest.fixture
def environment(tmp_path, monkeypatch):
    home = tmp_path / "user"
    project = tmp_path / "中文 project"
    project.mkdir()
    monkeypatch.setenv("NANOCURSOR_HOME", str(home))
    for name in ("deepseek", "anthropic", "openai"):
        for variable in connection_variables(name):
            monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("OPENAI_PROTOCOL", raising=False)
    monkeypatch.chdir(project)
    return home, project


def export_connection(monkeypatch, name="deepseek"):
    for variable, value in zip(connection_variables(name),
                               (f"synthetic-{name}-key", f"https://{name}.example.invalid/v1", f"{name}-model")):
        monkeypatch.setenv(variable, value)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))


@pytest.mark.parametrize("name,protocol", [("deepseek", "openai-compat"), ("anthropic", "anthropic"), ("openai", "openai")])
def test_complete_environment_starts_without_config_or_state_writes(environment, monkeypatch, name, protocol):
    export_connection(monkeypatch, name)
    config = load_config()
    provider = config.selected_provider
    assert provider.name == f"env:{name}" and provider.protocol == protocol
    assert provider.base_url == f"https://{name}.example.invalid/v1"
    assert provider.model == f"{name}-model"
    assert provider.resolve_api_key() == f"synthetic-{name}-key"
    assert not provider.api_key and not provider.credential_ref
    with patch("nanocursor.connection.check_connection", side_effect=AssertionError("no network")):
        report = diagnose(WorkspaceContext.resolve())
    assert report["ok"] and f"{name.upper()}_MODEL" in json.dumps(report)
    assert f"synthetic-{name}-key" not in json.dumps(report) + repr(config._raw) + repr(provider)
    assert not environment[0].exists()
    assert not (environment[1] / ".nanocursor").exists()


def test_incomplete_alias_does_not_compete_with_complete_connection(environment, monkeypatch):
    export_connection(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-deepseek-key")
    assert load_config().selected_provider.name == "env:deepseek"


def test_environment_groups_are_never_combined(environment, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    with pytest.raises(MissingConfigError, match="DEEPSEEK_API_KEY") as error:
        load_config()
    assert "synthetic-key" not in str(error.value)
    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        load_config(env_provider="deepseek")


def test_existing_connections_win_until_environment_is_explicit(environment, monkeypatch):
    home, _ = environment
    saved = {"providers": [{"name": "deepseek", "protocol": "openai-compat", "base_url": "https://saved.example.invalid",
                            "model": "saved-model", "api_key": "saved-test-key"}],
             "permission_mode": "plan", "approval": {"mode": "manual"}}
    save(home / "config.yaml", saved)
    before = (home / "config.yaml").read_bytes()
    export_connection(monkeypatch)
    export_connection(monkeypatch, "openai")
    assert load_config().selected_provider.model == "saved-model"
    selected = load_config(env_provider="deepseek")
    assert selected.selected_provider.model == "deepseek-model"
    assert selected.selected_provider.resolve_api_key() == "synthetic-deepseek-key"
    assert selected.permission_mode == "plan" and selected.approval.mode == "manual"
    assert [p.name for p in selected.providers] == ["deepseek", "env:deepseek"]
    assert (home / "config.yaml").read_bytes() == before
    # An unused broken environment group must not break an explicit file profile.
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "invalid-url")
    assert load_config().selected_provider.model == "saved-model"


@pytest.mark.parametrize("url", ["https://user:secret@example.invalid", "https://example.invalid?key=secret", "bad-url"])
def test_environment_endpoint_errors_name_variable_without_echoing_value(environment, monkeypatch, url):
    export_connection(monkeypatch)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", url)
    with pytest.raises(ConfigError, match="DEEPSEEK_BASE_URL") as error:
        load_config()
    assert url not in str(error.value) and "synthetic-deepseek-key" not in str(error.value)


def test_openai_can_explicitly_use_chat_completions(environment, monkeypatch):
    export_connection(monkeypatch, "openai")
    monkeypatch.setenv("OPENAI_PROTOCOL", "openai-compat")
    assert load_config().selected_provider.protocol == "openai-compat"
    monkeypatch.setenv("OPENAI_PROTOCOL", "unexpected-sensitive-value")
    with pytest.raises(ConfigError, match="OPENAI_PROTOCOL") as error:
        load_config()
    assert "unexpected-sensitive-value" not in str(error.value)


def test_malformed_config_is_not_bypassed_by_environment(environment, monkeypatch):
    home, _ = environment
    home.mkdir()
    (home / "config.yaml").write_text("providers: [")
    export_connection(monkeypatch)
    with pytest.raises(ConfigError, match="Invalid YAML"):
        load_config(env_provider="deepseek")


def test_multiple_connections_require_selection_and_noninteractive_error_is_json(environment, monkeypatch):
    export_connection(monkeypatch)
    export_connection(monkeypatch, "anthropic")
    with pytest.raises(EnvironmentSelectionRequired):
        load_config()
    result = subprocess.run([sys.executable, "-m", "nanocursor", "-p", "hi", "--output-format", "stream-json"],
                            capture_output=True, text=True)
    assert result.returncode == 1
    error = json.loads(result.stdout)
    assert error["code"] == "ENV_CONFIG_AMBIGUOUS" and "--env" in error["message"]
    assert "synthetic" not in result.stdout + result.stderr
    assert not environment[0].exists()


def test_interactive_selection_uses_environment_without_setup_or_saving(environment, monkeypatch):
    from nanocursor.__main__ import main
    export_connection(monkeypatch)
    export_connection(monkeypatch, "anthropic")
    monkeypatch.setattr(sys, "argv", ["nanocursor"])
    with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), \
         patch("builtins.input", return_value="2"), patch("nanocursor.setup.run_setup", side_effect=AssertionError("no setup")), \
         patch("nanocursor.app.NanoCursorApp") as app:
        main()
    assert app.call_args.kwargs["default_provider"] == "env:anthropic"
    assert not (environment[0] / "config.yaml").exists()
    assert not (environment[0] / "credentials.json").exists()


@pytest.mark.parametrize("remote", [False, True])
def test_environment_reaches_noninteractive_and_remote_entrypoints(environment, monkeypatch, remote):
    from nanocursor.__main__ import main
    export_connection(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["nanocursor", "--env", "deepseek", *(["--remote"] if remote else ["-p", "hi"])])
    with patch("nanocursor.__main__._run_prompt", new_callable=AsyncMock) as prompt, \
         patch("nanocursor.remote.RemoteServer") as server, \
         patch("nanocursor.setup.run_setup", side_effect=AssertionError("no setup")):
        prompt.return_value = 0
        server.return_value.run = AsyncMock()
        if remote:
            with pytest.raises(SystemExit) as failure:
                main()
            assert failure.value.code == 1
            server.assert_not_called()
            prompt.assert_not_called()
            return
        main()
    provider = prompt.call_args.args[0].selected_provider
    assert provider.name == "env:deepseek" and provider.model == "deepseek-model"
    assert not (environment[0] / "config.yaml").exists()


def test_environment_does_not_skip_project_permissions_review(environment, monkeypatch):
    from nanocursor.__main__ import main
    export_connection(monkeypatch)
    save(environment[1] / ".nanocursor/config.yaml", {"permission_mode": "bypassPermissions"})
    config = load_config()
    assert not config.project_trusted
    monkeypatch.setattr(sys, "argv", ["nanocursor", "--env", "deepseek", "-p", "hi"])
    with patch("nanocursor.__main__._run_prompt", side_effect=AssertionError("must not run")), pytest.raises(SystemExit) as exit:
        main()
    assert exit.value.code == 1


def test_doctor_can_select_an_environment_connection_without_writing(environment, monkeypatch):
    export_connection(monkeypatch)
    export_connection(monkeypatch, "openai")
    result = subprocess.run([sys.executable, "-m", "nanocursor", "doctor", "--env", "deepseek", "--json"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] and "env:deepseek" in result.stdout
    assert "synthetic-deepseek-key" not in result.stdout
    assert not environment[0].exists()


def test_setup_reuses_available_environment_values_and_asks_for_missing_model(environment, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://custom.example.invalid/v1")
    answers = iter(["1", "", "", "new-model", "", "", "n", "y"])
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        return next(answers)
    with patch("getpass.getpass", side_effect=AssertionError("must not ask for the existing key")):
        assert run_setup(input_fn=answer, output=lambda _: None)
    provider = load_config().selected_provider
    assert provider.model == "new-model" and provider.base_url == "https://custom.example.invalid/v1"
    assert provider.api_key_env == "DEEPSEEK_API_KEY"
    assert not (environment[0] / "credentials.json").exists()
    assert "synthetic-key" not in (environment[0] / "config.yaml").read_text() + "".join(prompts)
