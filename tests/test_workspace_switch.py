from pathlib import Path


def test_default_workspace_is_isolated_from_project_source():
    from src.infra import config as config_module

    project_root = Path(config_module.PROJECT_ROOT).resolve()
    default_workspace = Path(config_module.DEFAULT_WORKSPACE_DIR).resolve()
    workspace_root = Path(config_module.WORKSPACE_ROOT).resolve()
    legacy_workspace = project_root / "workspace"

    assert default_workspace != legacy_workspace
    assert workspace_root != project_root
    assert default_workspace.is_absolute()
    assert workspace_root.is_absolute()


def test_workspace_list_exposes_default_workspace_metadata():
    from fastapi.testclient import TestClient

    from src.api import legacy_runtime as api_server

    client = TestClient(api_server.app)
    response = client.get("/api/workspaces")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_workspace"]
    assert payload["workspace_root"]
    assert payload["project_root"]
    assert "is_default_workspace" in payload
    assert Path(payload["default_workspace"]).is_absolute()
    assert Path(payload["workspace_root"]).is_absolute()


def test_set_active_workspace_resets_workspace_scoped_caches(tmp_path):
    from src.api.legacy_runtime import _set_active_workspace
    from src.agent.engine import get_todo_manager, get_workdir
    from src.indexer.indexer import get_project_index
    from src.infra import config as config_module
    from src.tools.git_tools import get_git_workspace

    original_workspace = config_module.WORKSPACE_DIR
    first = tmp_path / "workspace-a"
    second = tmp_path / "workspace-b"
    first.mkdir()
    second.mkdir()

    try:
        _set_active_workspace(str(first))
        first_index = get_project_index()
        get_todo_manager().add("first workspace item")

        _set_active_workspace(str(second))
        second_index = get_project_index()
        second_todos = get_todo_manager().list_all()

        assert first_index.workspace == first.resolve()
        assert second_index.workspace == second.resolve()
        assert get_git_workspace() == second.resolve()
        assert get_workdir() == second.resolve()
        assert second_todos == []
        assert (first / ".todos.json").exists()
        assert not (second / ".todos.json").exists()
    finally:
        _set_active_workspace(original_workspace)
