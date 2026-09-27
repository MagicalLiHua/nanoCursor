from __future__ import annotations

import pytest
from rich.text import Text
from textual.app import App

from nanocursor.app import MAX_TRUNCATED_LINES, ToolCallBlock, _format_detail


@pytest.fixture(autouse=True)
def app_context():
    # Rich Text conversion uses the current app's terminal theme in Textual.
    with App()._context():
        yield


@pytest.mark.parametrize("name,arguments,literal", [
    ("Bash", {"command": "echo '[/missing] [bold]literal[/bold]'"}, "[/missing] [bold]literal[/bold]"),
    ("ReadFile", {"file_path": "/tmp/[/missing]/[bold]literal.txt"}, "[bold]literal.txt"),
    ("WriteFile", {"file_path": "/tmp/[/missing]/[bold]literal.txt"}, "[bold]literal.txt"),
    ("Grep", {"pattern": "[/missing] [bold]literal[/bold]"}, "[/missing] [bold]literal[/bold]"),
    ("mcp__test__[/missing]", {}, "mcp__test__[/missing]"),
])
def test_tool_titles_and_expanded_error_output_are_literal(name, arguments, literal):
    block = ToolCallBlock(name, arguments)
    assert literal in block.render().plain
    output = "error: [/missing] [bold]literal[/bold] \\[brackets]"
    block.set_result(output, True, 0.1)
    assert not block._collapsed
    assert literal in block.render().plain
    assert output in block.render().plain
    if "file_path" in arguments:
        assert arguments["file_path"] in block.render().plain

    block.on_click()
    assert block._collapsed
    assert literal in block.render().plain
    block.on_click()
    assert output in block.render().plain


def test_bash_error_preview_is_bounded_but_retains_output_for_inspection():
    lines = [f"error {index}" for index in range(MAX_TRUNCATED_LINES + 7)]
    output = "\n".join(lines)
    block = ToolCallBlock("Bash", {"command": "false"})
    block.set_result(output, True, 0.1)
    rendered = block.render().plain
    assert lines[MAX_TRUNCATED_LINES - 1] in rendered
    assert lines[-1] not in rendered
    assert "7 more lines" in rendered
    assert block._full_output == output


def test_edit_diff_keeps_colors_and_literal_code():
    output = "- old [/missing]\n+ new [bold]literal[/bold]"
    text = Text.from_markup(_format_detail("EditFile", {"file_path": "x.py"}, output))
    assert "- old [/missing]" in text.plain
    assert "+ new [bold]literal[/bold]" in text.plain
    assert any(span.style == "red" and "old" in text.plain[span.start:span.end] for span in text.spans)
    assert any(span.style == "green" and "new" in text.plain[span.start:span.end] for span in text.spans)
