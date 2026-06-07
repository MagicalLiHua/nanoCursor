"""Tests for src/agent/prompt_builder.py"""
from __future__ import annotations

from unittest.mock import patch

from src.agent.prompt_builder import (
    DYNAMIC_BOUNDARY,
    SystemPromptBuilder,
    _build_core,
    _build_dynamic_context,
    _build_env_info,
    _build_identity,
    _build_principles,
    _build_tool_listing,
    _build_verification,
    _build_workflow,
)


# --- Private builders ---


def test_build_identity():
    result = _build_identity()
    assert "Lead Agent" in result


def test_build_principles():
    result = _build_principles()
    assert "核心原则" in result
    assert "中文" in result


def test_build_env_info():
    with patch("src.agent.prompt_builder.platform") as mock_platform:
        mock_platform.system.return_value = "Darwin"
        with patch("src.agent.engine.get_workdir", return_value="/tmp/test"):
            result = _build_env_info()
    assert "/tmp/test" in result
    assert "Darwin" in result


def test_build_workflow_analysis_only():
    result = _build_workflow("analysis_only")
    assert "只读分析" in result


def test_build_workflow_docs_only():
    result = _build_workflow("docs_only")
    assert "文档任务" in result


def test_build_workflow_small_patch():
    result = _build_workflow("small_patch")
    assert "小改动" in result


def test_build_workflow_feature_delivery():
    result = _build_workflow("feature_delivery")
    assert "多 Agent" in result


def test_build_verification_analysis_only():
    assert _build_verification("analysis_only") == ""


def test_build_verification_docs_only():
    assert _build_verification("docs_only") == ""


def test_build_verification_feature_delivery():
    result = _build_verification("feature_delivery")
    assert "run_tests" in result


def test_build_tool_listing_empty():
    assert _build_tool_listing([]) == ""


def test_build_tool_listing_with_tools():
    tools = [
        {"name": "bash", "description": "Run shell commands"},
        {"name": "read_file", "description": "Read files"},
    ]
    result = _build_tool_listing(tools)
    assert "bash" in result
    assert "read_file" in result


def test_build_dynamic_context():
    with patch("src.agent.engine.get_workdir", return_value="/tmp/test"):
        result = _build_dynamic_context()
    assert "日期" in result
    assert "/tmp/test" in result


# --- SystemPromptBuilder ---


def test_builder_build_returns_string():
    builder = SystemPromptBuilder(tools=[], strategy="analysis_only")
    with patch("src.agent.engine.get_workdir", return_value="/tmp"):
        result = builder.build()
    assert isinstance(result, str)
    assert "Lead Agent" in result


def test_builder_build_includes_tools():
    tools = [{"name": "bash", "description": "shell"}]
    builder = SystemPromptBuilder(tools=tools, strategy="analysis_only")
    with patch("src.agent.engine.get_workdir", return_value="/tmp"):
        result = builder.build()
    assert "bash" in result


def test_builder_build_static_caches():
    builder = SystemPromptBuilder(tools=[], strategy="analysis_only")
    with patch("src.agent.engine.get_workdir", return_value="/tmp"):
        r1 = builder.build_static()
        r2 = builder.build_static()
    assert r1 == r2
    assert builder._static_cache is not None


def test_builder_clear_cache():
    builder = SystemPromptBuilder(tools=[], strategy="analysis_only")
    with patch("src.agent.engine.get_workdir", return_value="/tmp"):
        builder.build_static()
    builder.clear_cache()
    assert builder._static_cache is None


def test_builder_build_dynamic():
    builder = SystemPromptBuilder()
    with patch("src.agent.engine.get_workdir", return_value="/tmp"):
        result = builder.build_dynamic()
    assert "日期" in result


def test_builder_default_strategy():
    builder = SystemPromptBuilder()
    assert builder.strategy == "feature_delivery"
    assert builder.tools == []
    assert builder.team == []
