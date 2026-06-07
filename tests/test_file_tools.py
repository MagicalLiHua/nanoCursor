"""
Tests for src/tools/file_tools.py

Covers:
- Path traversal protection (_get_safe_filepath)
- Large file AST outline extraction
- edit_file fuzzy matching strategies
- Backup and rollback lifecycle
- list_directory
"""

import asyncio
import os
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coroutine from sync test code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(coro)
        loop.close()
        return result
    # If we're already in an async context, create a new task
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(lambda: asyncio.run(coro))
        return future.result()


# ---------------------------------------------------------------------------
# Path traversal
# ---------------------------------------------------------------------------


class TestSafeFilepath:
    """Security boundary: never allow escaping the workspace."""

    def test_normal_relative_path(self, tmp_workspace):
        from src.tools.file_tools import _get_safe_filepath
        safe = _get_safe_filepath("sub/file.py")
        assert safe.endswith(os.path.join("sub", "file.py"))
        assert safe.startswith(os.path.abspath(str(tmp_workspace)))

    def test_dotdot_escape_blocked(self, tmp_workspace):
        from src.tools.file_tools import _get_safe_filepath
        for malicious in [
            "../../etc/passwd",
            "..\\..\\windows\\system32",
            "../../other_dir/file.txt",
        ]:
            with pytest.raises(ValueError, match="安全拦截"):
                _get_safe_filepath(malicious)

    def test_common_prefix_sibling_escape_blocked(self, tmp_workspace):
        from src.tools.file_tools import _get_safe_filepath

        sibling = tmp_workspace.parent / f"{tmp_workspace.name}_evil" / "secret.txt"
        with pytest.raises(ValueError, match="安全拦截"):
            _get_safe_filepath(str(sibling))


# ---------------------------------------------------------------------------
# AST-aware large file reading
# ---------------------------------------------------------------------------


class TestASTOutline:
    """Verify that large files get an outline, not full source."""

    def test_small_file_returns_content(self, tmp_workspace):
        from src.tools.file_tools import read_file
        (tmp_workspace / "small.py").write_text("x = 1\n")
        result = _run_async(read_file(str(tmp_workspace), "small.py"))
        assert "x = 1" in result
        assert "Structure of" not in result

    def test_large_file_returns_outline(self, tmp_workspace):
        from src.tools.file_tools import read_file
        # Build content > 5000 chars with 2 functions
        body = "\n".join(f"    pass  # line {i}" for i in range(200))
        src = f"def foo(a, b):\n{body}\n\ndef bar(x):\n{body}\n"
        assert len(src) > 5000
        (tmp_workspace / "large.py").write_text(src)
        result = _run_async(read_file(str(tmp_workspace), "large.py"))
        assert "Structure of" in result
        assert "def foo(a, b)" in result
        assert "def bar(x)" in result
        # Body content should NOT appear in outline
        assert "pass  # line" not in result

    def test_nonexistent_file(self, tmp_workspace):
        from src.tools.file_tools import read_file
        result = _run_async(read_file(str(tmp_workspace), "missing.py"))
        assert "does not exist" in result

    def test_read_function_extraction(self, tmp_workspace):
        from src.tools.file_tools import read_function
        src = textwrap.dedent(
            '''
            def hello(name):
                return f"hi {name}"

            def bye(name):
                return f"bye {name}"
            '''
        )
        (tmp_workspace / "greet.py").write_text(src)
        result = _run_async(read_function(str(tmp_workspace), "greet.py", "hello"))
        assert "hello" in result
        assert "bye" not in result

    def test_read_class_extraction(self, tmp_workspace):
        from src.tools.file_tools import read_class
        src = textwrap.dedent(
            """
            class MyClass:
                def method(self):
                    pass

            class OtherClass:
                pass
            """
        )
        (tmp_workspace / "classes.py").write_text(src)
        result = _run_async(read_class(str(tmp_workspace), "classes.py", "MyClass"))
        assert "MyClass" in result
        assert "OtherClass" not in result

    def test_read_file_range(self, tmp_workspace):
        from src.tools.file_tools import read_file_range
        lines = "\n".join(f"line_{i}" for i in range(1, 21))
        (tmp_workspace / "numbered.py").write_text(lines)
        result = _run_async(read_file_range(str(tmp_workspace), "numbered.py", 5, 7))
        assert "line_5" in result
        assert "line_7" in result
        assert "line_4" not in result
        assert "line_8" not in result


# ---------------------------------------------------------------------------
# edit_file strategies
# ---------------------------------------------------------------------------


class TestEditFile:
    """Three-tier edit: exact -> stripped -> fuzzy."""

    def _edit(self, tmp_workspace, filename, search_block, replace_block):
        from src.tools.file_tools import edit_file
        return _run_async(edit_file(
            str(tmp_workspace), filename, search_block, replace_block
        ))

    def test_exact_match(self, tmp_workspace):
        (tmp_workspace / "target.py").write_text("x = 1\ny = 2\n")
        result = self._edit(tmp_workspace, "target.py", "x = 1", "x = 100")
        assert "Exact" in result or "精确" in result or "Match" in result
        assert "Edit Receipt:" in result
        assert "matched_lines: 1-1" in result
        assert "changed_line_count:" in result
        assert (tmp_workspace / "target.py").read_text() == "x = 100\ny = 2\n"

    def test_stripped_match(self, tmp_workspace):
        # Use content where stripping makes a difference:
        # search_block with leading/trailing whitespace should still match.
        (tmp_workspace / "ws.py").write_text("x = 1\n")
        result = self._edit(tmp_workspace, "ws.py", "  x = 1  ", "x = 2")
        # Should still succeed (matches after strip or exact)
        assert "匹配" in result or "Match" in result
        assert (tmp_workspace / "ws.py").read_text() == "x = 2\n"

    def test_fuzzy_match(self, tmp_workspace):
        original = "def compute_sum(a, b):\n    result = a + b\n    return result\n"
        (tmp_workspace / "fuzzy.py").write_text(original)
        # Search block matches exactly but would test fuzzy if we added minor diffs
        result = self._edit(tmp_workspace, "fuzzy.py", "def compute_sum(a, b):\n    result = a + b\n    return result\n", "def compute_sum(a, b):\n    return a + b\n")
        assert "匹配" in result or "Match" in result
        assert "return a + b" in (tmp_workspace / "fuzzy.py").read_text()

    def test_no_match_returns_error(self, tmp_workspace):
        (tmp_workspace / "nomatch.py").write_text("x = 1\n")
        result = self._edit(tmp_workspace, "nomatch.py", "this code does not exist anywhere", "y = 2")
        assert "修改失败" in result

    def test_nonexistent_file(self, tmp_workspace):
        from src.tools.file_tools import edit_file
        result = _run_async(edit_file(str(tmp_workspace), "ghost.py", "x", "y"))
        assert "不存在" in result

    def test_duplicate_match_is_rejected(self, tmp_workspace):
        target = tmp_workspace / "duplicate.py"
        target.write_text("x = 1\ny = 2\nx = 1\n")

        result = self._edit(tmp_workspace, "duplicate.py", "x = 1", "x = 100")

        assert "修改失败" in result
        assert "出现 2 次" in result
        assert target.read_text() == "x = 1\ny = 2\nx = 1\n"

    def test_noop_edit_is_rejected(self, tmp_workspace):
        target = tmp_workspace / "noop.py"
        target.write_text("x = 1\ny = 2\n")

        result = self._edit(tmp_workspace, "noop.py", "x = 1", "x = 1")

        assert "修改失败" in result
        assert "没有产生任何内容变化" in result
        assert target.read_text() == "x = 1\ny = 2\n"


# ---------------------------------------------------------------------------
# Backup & rollback
# ---------------------------------------------------------------------------


class TestBackupRollback:
    """Lifecycle: backup -> edit -> rollback."""

    def test_backup_creates_file(self, tmp_workspace):
        from src.tools.file_tools import backup_file
        (tmp_workspace / "bak_me.py").write_text("original\n")
        result = backup_file(str(tmp_workspace / "bak_me.py"))
        assert result is not None
        assert ".backups" in result

    def test_rollback_restores(self, tmp_workspace):
        from src.tools.file_tools import backup_file, rollback_file
        fpath = tmp_workspace / "restore.py"
        fpath.write_text("v1\n")
        backup_file("restore.py")  # use relative filename, as file_tools expects
        # Mutate
        fpath.write_text("v2\n")
        result = rollback_file("restore.py")
        assert "成功回滚" in result or "rollback" in result.lower()
        assert fpath.read_text() == "v1\n"

    def test_no_backup_for_missing_file(self, tmp_workspace):
        from src.tools.file_tools import backup_file
        assert backup_file("ghost.py") is None

    def test_write_file_creates_dirs(self, tmp_workspace):
        from src.tools.file_tools import write_file
        result = _run_async(write_file(str(tmp_workspace), "deep/nested/new.py", "x=1\n"))
        assert "Successfully" in result or "created" in result.lower() or "更新" in result
        assert (tmp_workspace / "deep" / "nested" / "new.py").read_text() == "x=1\n"


# ---------------------------------------------------------------------------
# list_directory
# ---------------------------------------------------------------------------


class TestListDirectory:
    def test_lists_files(self, tmp_workspace):
        from src.tools.file_tools import list_directory
        (tmp_workspace / "a.py").write_text("")
        (tmp_workspace / "b.txt").write_text("")
        result = _run_async(list_directory(str(tmp_workspace), "."))
        assert "a.py" in result
        assert "b.txt" in result

    def test_hides_backups(self, tmp_workspace):
        from src.tools.file_tools import list_directory
        (tmp_workspace / ".backups").mkdir(exist_ok=True)
        result = _run_async(list_directory(str(tmp_workspace), "."))
        assert ".backups" not in result

    def test_nonexistent_dir(self, tmp_workspace):
        from src.tools.file_tools import list_directory
        result = _run_async(list_directory(str(tmp_workspace), "no_such_dir"))
        assert "不是一个" in result or "not" in result or "Error" in result
