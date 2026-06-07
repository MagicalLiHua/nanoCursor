"""Tests for src/tools/file_ops.py"""
from __future__ import annotations

from pathlib import Path

from src.tools.file_ops import (
    _is_missing_verify_tool,
    _should_hide_list_entry,
    auto_verify_file,
    run_edit,
    run_list_directory,
    run_read,
    run_write,
)


# --- _should_hide_list_entry ---


def test_should_hide_pycache():
    assert _should_hide_list_entry(Path("__pycache__")) is True


def test_should_hide_git():
    assert _should_hide_list_entry(Path(".git")) is True


def test_should_hide_pyc_suffix():
    assert _should_hide_list_entry(Path("module.pyc")) is True


def test_should_not_hide_normal_file():
    assert _should_hide_list_entry(Path("main.py")) is False


def test_should_not_hide_normal_dir():
    assert _should_hide_list_entry(Path("src")) is False


# --- _is_missing_verify_tool ---


def test_is_missing_verify_tool_not_found():
    assert _is_missing_verify_tool(127, "command not found") is True


def test_is_missing_verify_tool_no_such_file():
    assert _is_missing_verify_tool(-1, "No such file or directory") is True


def test_is_missing_verify_tool_normal_error():
    assert _is_missing_verify_tool(1, "syntax error") is False


# --- run_read ---


def test_run_read_success(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello world", encoding="utf-8")
    result = run_read("test.txt", str(tmp_path))
    assert result == "hello world"


def test_run_read_with_limit(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\nline3\nline4\nline5", encoding="utf-8")
    result = run_read("test.txt", str(tmp_path), limit=3)
    assert "line1" in result
    assert "line3" in result
    assert "more lines" in result


def test_run_read_file_not_found(tmp_path):
    result = run_read("nonexistent.txt", str(tmp_path))
    assert "error" in result.lower()


# --- run_write ---


def test_run_write_creates_file(tmp_path):
    result = run_write("new.txt", "content", str(tmp_path))
    assert "created" in result.lower()
    assert (tmp_path / "new.txt").read_text() == "content"


def test_run_write_updates_existing(tmp_path):
    f = tmp_path / "existing.txt"
    f.write_text("old", encoding="utf-8")
    result = run_write("existing.txt", "new", str(tmp_path))
    assert "updated" in result.lower()
    assert f.read_text() == "new"


def test_run_write_creates_parent_dirs(tmp_path):
    result = run_write("sub/dir/file.txt", "content", str(tmp_path))
    assert "created" in result.lower()
    assert (tmp_path / "sub" / "dir" / "file.txt").read_text() == "content"


# --- run_edit ---


def test_run_edit_string_based(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("def foo():\n    pass\n", encoding="utf-8")
    result = run_edit("test.py", str(tmp_path), old_text="pass", new_text="return 1")
    assert "edited" in result.lower()
    assert "return 1" in f.read_text()


def test_run_edit_line_based(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    result = run_edit("test.py", str(tmp_path), start_line=2, end_line=2, new_text="replaced")
    assert "edited" in result.lower()
    assert "replaced" in f.read_text()


def test_run_edit_file_not_found(tmp_path):
    result = run_edit("nonexistent.txt", str(tmp_path), old_text="a", new_text="b")
    assert "not found" in result.lower()


def test_run_edit_text_not_found(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello", encoding="utf-8")
    result = run_edit("test.txt", str(tmp_path), old_text="xyz", new_text="abc")
    assert "not found" in result.lower()


def test_run_edit_invalid_line_range(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line1\nline2\n", encoding="utf-8")
    result = run_edit("test.txt", str(tmp_path), start_line=0, end_line=5, new_text="x")
    assert "error" in result.lower()


def test_run_edit_no_params(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("content", encoding="utf-8")
    result = run_edit("test.txt", str(tmp_path))
    assert "error" in result.lower()


# --- run_list_directory ---


def test_run_list_directory(tmp_path):
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    result = run_list_directory(".", str(tmp_path))
    assert "file.txt" in result
    assert "subdir/" in result


def test_run_list_directory_hides_hidden(tmp_path):
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "visible.txt").write_text("x", encoding="utf-8")
    result = run_list_directory(".", str(tmp_path))
    assert "__pycache__" not in result
    assert "visible.txt" in result


def test_run_list_directory_empty(tmp_path):
    subdir = tmp_path / "empty"
    subdir.mkdir()
    result = run_list_directory("empty", str(tmp_path))
    assert "empty" in result.lower()


def test_run_list_directory_not_found(tmp_path):
    result = run_list_directory("nonexistent", str(tmp_path))
    assert "not found" in result.lower()


# --- auto_verify_file ---


def test_auto_verify_valid_python(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert auto_verify_file(f) == ""


def test_auto_verify_invalid_python(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("def foo(\n", encoding="utf-8")
    result = auto_verify_file(f)
    assert "syntax error" in result.lower()


def test_auto_verify_valid_json(tmp_path):
    f = tmp_path / "test.json"
    f.write_text('{"key": "value"}', encoding="utf-8")
    assert auto_verify_file(f) == ""


def test_auto_verify_invalid_json(tmp_path):
    f = tmp_path / "test.json"
    f.write_text("{invalid}", encoding="utf-8")
    result = auto_verify_file(f)
    assert "json" in result.lower()


def test_auto_verify_unknown_suffix(tmp_path):
    f = tmp_path / "test.xyz"
    f.write_text("content", encoding="utf-8")
    assert auto_verify_file(f) == ""
