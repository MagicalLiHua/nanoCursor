"""Tests for parallel tool execution in agent loops."""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.engine import PARALLEL_TOOLS, _execute_single_tool


def test_parallel_tools_constant():
    """read_file and list_directory should be parallelizable."""
    assert "read_file" in PARALLEL_TOOLS
    assert "list_directory" in PARALLEL_TOOLS
    assert "write_file" not in PARALLEL_TOOLS
    assert "edit_file" not in PARALLEL_TOOLS
    assert "bash" not in PARALLEL_TOOLS


def test_execute_single_tool_read_file(tmp_path):
    """_execute_single_tool should work for read_file."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello", encoding="utf-8")

    with patch("src.agent.engine.get_workdir", return_value=str(tmp_path)):
        result = asyncio.run(
            _execute_single_tool(
                tool_name="read_file",
                tool_input={"path": "test.txt"},
                tool_id="test-id",
                on_tool_check=None,
                on_tool_call=None,
                on_cancel_check=None,
                session_id=None,
            )
        )

    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "test-id"
    assert "hello" in result["content"]


def test_execute_single_tool_blocked_by_policy():
    """_execute_single_tool should respect policy decisions."""
    async def mock_check(name, input):
        decision = MagicMock()
        decision.allowed = False
        decision.reason = "blocked by policy"
        return decision

    result = asyncio.run(
        _execute_single_tool(
            tool_name="bash",
            tool_input={"command": "rm -rf /"},
            tool_id="test-id",
            on_tool_check=mock_check,
            on_tool_call=None,
            on_cancel_check=None,
            session_id=None,
        )
    )

    assert "blocked" in result["content"].lower()


def test_parallel_tools_run_concurrently(tmp_path):
    """Multiple read_file calls should run concurrently, not sequentially."""
    for i in range(3):
        (tmp_path / f"file{i}.txt").write_text(f"content {i}", encoding="utf-8")

    async def run():
        with patch("src.agent.engine.get_workdir", return_value=str(tmp_path)):
            start = time.monotonic()
            tasks = [
                _execute_single_tool(
                    tool_name="read_file",
                    tool_input={"path": f"file{i}.txt"},
                    tool_id=f"id-{i}",
                    on_tool_check=None,
                    on_tool_call=None,
                    on_cancel_check=None,
                    session_id=None,
                )
                for i in range(3)
            ]
            results = await asyncio.gather(*tasks)
            elapsed = time.monotonic() - start
        return results, elapsed

    results, elapsed = asyncio.run(run())

    assert len(results) == 3
    for i, result in enumerate(results):
        assert f"content {i}" in result["content"]
    assert elapsed < 2.0
