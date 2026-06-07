"""Tests for src/tools/project_tools.py"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def _mock_index():
    idx = MagicMock()
    idx.workspace.name = "test-project"
    idx.summary.return_value = {
        "entry_points": ["src/main.py"],
        "total_files": 50,
        "source_count": 30,
        "test_count": 15,
        "config_count": 5,
        "total_loc": 5000,
        "recently_modified": [("src/main.py", "2026-06-06")],
        "modules": {
            "src/main.py": {"role": "source", "symbols": [{"type": "def", "name": "main"}]},
        },
        "dependency_graph": {"src/main.py": ["src/utils.py"]},
    }
    return idx


# --- search_codebase ---


def test_search_codebase_symbol(tmp_path):
    from src.tools.project_tools import search_codebase

    idx = _mock_index()
    idx.search_symbol.return_value = [
        {"symbol_type": "def", "symbol_name": "main", "file": "src/main.py", "lineno": 10}
    ]

    with patch("src.tools.project_tools.get_project_index", return_value=idx):
        result = search_codebase("main", search_type="symbol")

    assert "找到 1 个符号" in result
    assert "main" in result


def test_search_codebase_symbol_not_found(tmp_path):
    from src.tools.project_tools import search_codebase

    idx = _mock_index()
    idx.search_symbol.return_value = []

    with patch("src.tools.project_tools.get_project_index", return_value=idx):
        result = search_codebase("nonexistent", search_type="symbol")

    assert "未找到符号" in result


def test_search_codebase_dependency(tmp_path):
    from src.tools.project_tools import search_codebase

    idx = _mock_index()
    idx.search_dependents.return_value = ["src/app.py", "src/cli.py"]

    with patch("src.tools.project_tools.get_project_index", return_value=idx):
        result = search_codebase("src/utils.py", search_type="dependency")

    assert "2" in result
    assert "src/app.py" in result


def test_search_codebase_import(tmp_path):
    from src.tools.project_tools import search_codebase

    idx = _mock_index()
    idx.search_dependents.return_value = ["src/main.py"]

    with patch("src.tools.project_tools.get_project_index", return_value=idx):
        result = search_codebase("src/utils.py", search_type="import")

    assert "1" in result
    assert "src/main.py" in result


def test_search_codebase_unknown_type(tmp_path):
    from src.tools.project_tools import search_codebase

    idx = _mock_index()

    with patch("src.tools.project_tools.get_project_index", return_value=idx):
        result = search_codebase("test", search_type="unknown")

    assert "未知搜索类型" in result


def test_search_codebase_handles_error():
    from src.tools.project_tools import search_codebase

    with patch(
        "src.tools.project_tools.get_project_index",
        side_effect=Exception("indexer broken"),
    ):
        result = search_codebase("test", search_type="symbol")

    assert "搜索代码库失败" in result
    assert "indexer broken" in result


# --- project_context ---


def test_project_context_returns_summary():
    from src.tools.project_tools import project_context

    idx = _mock_index()

    with patch("src.tools.project_tools.get_project_index", return_value=idx):
        result = project_context()

    assert "项目概况" in result
    assert "test-project" in result
    assert "50 个" in result
    assert "5,000 行" in result


def test_project_context_handles_error():
    from src.tools.project_tools import project_context

    with patch(
        "src.tools.project_tools.get_project_index",
        side_effect=Exception("indexer broken"),
    ):
        result = project_context()

    assert "获取项目上下文失败" in result
