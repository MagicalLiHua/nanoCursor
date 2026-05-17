def test_set_active_workspace_resets_workspace_scoped_caches(tmp_path):
    from api_server import _set_active_workspace
    from src.agent.engine import get_todo_manager, get_workdir
    from src.indexer.indexer import get_project_index
    from src.tools.git_tools import get_git_workspace

    first = tmp_path / "workspace-a"
    second = tmp_path / "workspace-b"
    first.mkdir()
    second.mkdir()

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
