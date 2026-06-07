from src.runtime.executor_routing import choose_executor_backend


def test_executor_routing_uses_python_when_go_disabled(monkeypatch):
    monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "false")

    decision = choose_executor_backend("pytest -q", timeout_seconds=120)

    assert decision.backend == "python_subprocess"
    assert "disabled" in decision.reason


def test_executor_routing_low_latency_command_uses_python(monkeypatch):
    monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "true")
    monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "auto")

    decision = choose_executor_backend("ls -la", timeout_seconds=120)

    assert decision.backend == "python_subprocess"
    assert "low-latency" in decision.reason


def test_executor_routing_test_command_uses_go(monkeypatch):
    monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "true")
    monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "auto")

    decision = choose_executor_backend("pytest -q", timeout_seconds=120)

    assert decision.backend == "go_executor"
    assert decision.expected_long_running is True


def test_executor_routing_risky_command_uses_go(monkeypatch):
    monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "true")

    decision = choose_executor_backend("git clean -fd", timeout_seconds=120, permission_level="shell_risky")

    assert decision.backend == "go_executor"
    assert decision.risky is True


def test_executor_routing_never_mode_forces_python(monkeypatch):
    monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "true")
    monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "never")

    decision = choose_executor_backend("pytest -q", timeout_seconds=120)

    assert decision.backend == "python_subprocess"


def test_executor_routing_always_mode_forces_go(monkeypatch):
    monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "true")
    monkeypatch.setenv("NANOCURSOR_EXECUTOR_ROUTING_MODE", "always")

    decision = choose_executor_backend("ls", timeout_seconds=120)

    assert decision.backend == "go_executor"


def test_executor_routing_custom_env_uses_python(monkeypatch):
    monkeypatch.setenv("NANOCURSOR_GO_EXECUTOR_ENABLED", "true")

    decision = choose_executor_backend("pytest -q", timeout_seconds=120, env={"A": "B"})

    assert decision.backend == "python_subprocess"
    assert "environment" in decision.reason
