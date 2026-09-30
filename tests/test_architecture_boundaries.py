from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def runtime_imports(tree):
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.If) and ast.unparse(node.test) in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}:
            continue
        if isinstance(node, ast.ImportFrom):
            yield node.module or ""
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        yield from runtime_imports(node)


@pytest.mark.parametrize("directory", [
    "nanocursor/application", "nanocursor/commands/ports.py", "nanocursor/commands/registry.py",
    "nanocursor/commands/compat.py", "nanocursor/commands/handlers", "nanocursor/agents/factory.py",
    "nanocursor/events.py",
])
def test_application_contracts_do_not_import_frontends(directory):
    path = ROOT / directory
    files = list(path.rglob("*.py")) if path.is_dir() else [path]
    for file in files:
        for module in runtime_imports(ast.parse(file.read_text())):
            assert module not in {"nanocursor.app", "nanocursor.remote", "nanocursor.__main__"}, (file, module)
            assert module.split(".")[0] not in {"textual", "websockets"}, (file, module)


def test_events_can_be_imported_without_loading_engine_or_frontends():
    result = subprocess.run([sys.executable, "-c", "import sys; import nanocursor.events; "
                             "assert not {'nanocursor.agent', 'nanocursor.app', 'nanocursor.remote'} & sys.modules.keys()"],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_frontends_and_handlers_do_not_touch_private_agent_state():
    paths = [ROOT / "nanocursor/app.py", ROOT / "nanocursor/remote.py"]
    paths.extend((ROOT / "nanocursor/commands/handlers").glob("*.py"))
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Attribute) or not node.attr.startswith("_"):
                continue
            target = node.value
            is_agent = (isinstance(target, ast.Name) and target.id == "agent"
                        or isinstance(target, ast.Attribute) and target.attr == "agent")
            assert not is_agent, (path.name, node.lineno, node.attr)


def test_handlers_do_not_read_frontend_private_state_or_callback_dictionaries():
    for path in (ROOT / "nanocursor/commands/handlers").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "ctx":
                assert node.attr != "config", (path.name, node.lineno)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr == "ui":
                assert not node.attr.startswith("_"), (path.name, node.lineno, node.attr)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"getattr", "hasattr"}:
                if node.args and isinstance(node.args[0], ast.Attribute) and node.args[0].attr == "ui":
                    pytest.fail(f"{path.name}:{node.lineno} probes concrete UI capabilities")


def test_frontend_running_entrypoints_use_the_shared_coordinator():
    for filename in ["app.py", "remote.py", "__main__.py"]:
        tree = ast.parse((ROOT / "nanocursor" / filename).read_text())
        assert any(isinstance(node, ast.Name) and node.id == "ForegroundRun" for node in ast.walk(tree)), filename
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run":
                target = node.func.value
                assert not (isinstance(target, ast.Name) and target.id == "agent"
                            or isinstance(target, ast.Attribute) and target.attr == "agent"), filename


def test_child_entrypoints_do_not_construct_agents_directly():
    for filename in ["tools/agent_tool.py", "skills/executor.py"]:
        tree = ast.parse((ROOT / "nanocursor" / filename).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"Agent", "AgentClass"}, (filename, node.lineno)
