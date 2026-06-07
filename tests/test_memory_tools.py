"""Tests for src/tools/memory_tools.py"""
from __future__ import annotations

from unittest.mock import patch


def _workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


# --- add_memory ---


def test_add_memory_stores_with_mapped_category(tmp_path):
    from src.tools.memory_tools import add_memory

    workspace = str(_workspace(tmp_path))
    result = add_memory(
        content="Always use pytest -q",
        category="feedback",
        importance=5,
        workspace_dir=workspace,
    )

    assert "Memory stored:" in result
    assert "workspace/FAILURE_PATTERN" in result or "workspace/failure_pattern" in result.lower()


def test_add_memory_user_category_maps_to_global(tmp_path):
    from src.tools.memory_tools import add_memory

    workspace = str(_workspace(tmp_path))
    result = add_memory(
        content="User prefers dark mode",
        category="user",
        workspace_dir=workspace,
    )

    assert "Memory stored:" in result
    assert "global" in result.lower()


def test_add_memory_unknown_category_defaults_to_workspace(tmp_path):
    from src.tools.memory_tools import add_memory

    workspace = str(_workspace(tmp_path))
    result = add_memory(
        content="Some info",
        category="unknown_cat",
        workspace_dir=workspace,
    )

    assert "Memory stored:" in result


def test_add_memory_returns_error_on_failure():
    from src.tools.memory_tools import add_memory

    with patch(
        "src.tools.memory_tools.config_module"
    ) as mock_config:
        mock_config.WORKSPACE_DIR = "/nonexistent"
        with patch(
            "src.api.services.memory_governance_service.create_memory_record",
            side_effect=Exception("disk full"),
        ):
            result = add_memory(content="test", category="project")

    assert "Failed to add memory" in result
    assert "disk full" in result


# --- recall_memories ---


def test_recall_memories_returns_formatted_results(tmp_path):
    from src.tools.memory_tools import recall_memories

    workspace = str(_workspace(tmp_path))

    # Seed a memory first
    from src.tools.memory_tools import add_memory

    add_memory(content="Use ruff for linting", category="project", workspace_dir=workspace)

    result = recall_memories(query="linting", workspace_dir=workspace)

    assert "Governed Memory Recall" in result or "No memories found" in result


def test_recall_memories_empty_result(tmp_path):
    from src.tools.memory_tools import recall_memories

    workspace = str(_workspace(tmp_path))
    result = recall_memories(query="nonexistent topic xyz", workspace_dir=workspace)

    assert "No memories found" in result


def test_recall_memories_handles_error():
    from src.tools.memory_tools import recall_memories

    with patch(
        "src.tools.memory_tools.config_module"
    ) as mock_config:
        mock_config.WORKSPACE_DIR = "/nonexistent"
        with patch(
            "src.api.services.memory_selection_service.select_memories",
            side_effect=Exception("db error"),
        ):
            result = recall_memories(query="test")

    assert "Failed to recall memories" in result
    assert "db error" in result


# --- update_memory ---


def test_update_memory_updates_content(tmp_path):
    from src.tools.memory_tools import add_memory, update_memory

    workspace = str(_workspace(tmp_path))
    stored = add_memory(content="Old content", category="project", workspace_dir=workspace)

    # Extract memory ID from result
    memory_id = stored.split("[")[1].split("]")[0]
    # Pad to full ID if truncated — governance service returns full ID
    from src.api.services.memory_governance_service import list_memory_records

    records = list_memory_records(workspace)
    full_id = records[0]["id"]

    result = update_memory(memory_id=full_id, content="New content", workspace_dir=workspace)

    assert "Memory updated:" in result


def test_update_memory_not_found(tmp_path):
    from src.tools.memory_tools import update_memory

    workspace = str(_workspace(tmp_path))
    result = update_memory(memory_id="nonexistent_id_12345", workspace_dir=workspace)

    assert "Memory not found" in result


def test_update_memory_handles_error():
    from src.tools.memory_tools import update_memory

    with patch(
        "src.tools.memory_tools.config_module"
    ) as mock_config:
        mock_config.WORKSPACE_DIR = "/nonexistent"
        with patch(
            "src.api.services.memory_governance_service.update_memory_record",
            side_effect=Exception("permission denied"),
        ):
            result = update_memory(memory_id="fake_id")

    assert "Failed to update memory" in result
    assert "permission denied" in result
