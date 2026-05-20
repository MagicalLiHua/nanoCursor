"""Path guard tests — escape prevention, slug sanitization, safe resolution."""

import os
import tempfile
from pathlib import Path

import pytest

from src.infra.path_guard import (
    resolve_workspace_path,
    assert_within_workspace,
    safe_relative_to_workspace,
    safe_slug,
)


# ---------------------------------------------------------------------------
# resolve_workspace_path
# ---------------------------------------------------------------------------

class TestResolveWorkspacePath:
    def test_relative_path_inside_workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "file.txt").write_text("hello")
        result = resolve_workspace_path(ws, "file.txt")
        assert result == (ws / "file.txt").resolve()

    def test_absolute_path_inside_workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        abs_path = str(ws / "sub" / "file.py")
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        Path(abs_path).write_text("data")
        result = resolve_workspace_path(ws, abs_path)
        assert result == Path(abs_path).resolve()

    def test_dot_dot_escape_rejected(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        with pytest.raises(ValueError, match="越界"):
            resolve_workspace_path(ws, "../escape.txt")

    def test_absolute_path_outside_rejected(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        with pytest.raises(ValueError, match="越界"):
            resolve_workspace_path(ws, str(outside / "file.txt"))

    def test_symlink_escape_rejected(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        # Create symlink inside workspace pointing outside
        link = ws / "link"
        os.symlink(str(outside / "secret.txt"), str(link))
        with pytest.raises(ValueError, match="越界"):
            resolve_workspace_path(ws, "link")

    def test_nested_path_inside_workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "a" / "b" / "c").mkdir(parents=True)
        result = resolve_workspace_path(ws, "a/b/c")
        assert result == (ws / "a" / "b" / "c").resolve()

    def test_must_exist_raises_when_missing(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        with pytest.raises(FileNotFoundError):
            resolve_workspace_path(ws, "nonexistent.txt", must_exist=True)

    def test_empty_path_raises(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        with pytest.raises(ValueError):
            resolve_workspace_path(ws, "")

    def test_workspace_not_dir_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Workspace"):
            resolve_workspace_path("/nonexistent/path/12345", "file.txt")

    def test_dot_path_stays_in_workspace(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        result = resolve_workspace_path(ws, ".")
        assert result == ws.resolve()

    def test_double_dot_with_safe_prefix_still_rejected(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        with pytest.raises(ValueError, match="越界"):
            resolve_workspace_path(ws, "subdir/../../escape.txt")

    def test_nanocursor_dot_dir_allowed(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        nanodir = ws / ".nanocursor"
        nanodir.mkdir()
        result = resolve_workspace_path(ws, ".nanocursor")
        assert result == nanodir.resolve()

    def test_windows_style_backslash_escape_handled(self, tmp_path):
        ws = tmp_path / "workspace"
        ws.mkdir()
        # On Unix, backslash is a regular filename character, so "..\\escape.txt"
        # is a valid filename (not a traversal).  On Windows, this would be
        # rejected.  Either outcome is acceptable.
        try:
            result = resolve_workspace_path(ws, "..\\escape.txt")
            # If it resolved, it must still be inside the workspace
            assert result.is_relative_to(ws.resolve())
        except ValueError:
            pass  # rejected is also fine


# ---------------------------------------------------------------------------
# assert_within_workspace
# ---------------------------------------------------------------------------

class TestAssertWithinWorkspace:
    def test_valid_path_passes(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        p = assert_within_workspace(ws, ws / "file.txt")
        assert p is not None

    def test_invalid_path_raises(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        with pytest.raises(ValueError, match="越界"):
            assert_within_workspace(ws, tmp_path / "other" / "file.txt")


# ---------------------------------------------------------------------------
# safe_relative_to_workspace
# ---------------------------------------------------------------------------

class TestSafeRelative:
    def test_relative_path(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        f = ws / "sub" / "file.txt"
        f.parent.mkdir()
        f.write_text("x")
        rel = safe_relative_to_workspace(ws, f)
        assert rel == "sub/file.txt"

    def test_outside_path_returns_original(self, tmp_path):
        ws = tmp_path / "ws"
        ws.mkdir()
        outside = tmp_path / "other.txt"
        outside.write_text("x")
        result = safe_relative_to_workspace(ws, outside)
        assert result == str(outside.resolve())


# ---------------------------------------------------------------------------
# safe_slug
# ---------------------------------------------------------------------------

class TestSafeSlug:
    def test_simple_name(self):
        assert safe_slug("hello") == "hello"

    def test_slashes_replaced(self):
        slug = safe_slug("a/b/c")
        assert "/" not in slug
        assert "a-b-c" in slug

    def test_backslashes_replaced(self):
        slug = safe_slug("a\\b\\c")
        assert "\\" not in slug

    def test_dot_dot_normalized(self):
        slug = safe_slug("../escape")
        assert ".." not in slug

    def test_special_chars_stripped(self):
        slug = safe_slug("my file!@#$%^&*()name")
        assert "!" not in slug
        assert " " not in slug

    def test_max_length_truncation(self):
        long_name = "a" * 200
        slug = safe_slug(long_name, max_length=80)
        assert len(slug) == 80

    def test_empty_input_raises(self):
        with pytest.raises(ValueError):
            safe_slug("")

    def test_all_special_chars_raises(self):
        with pytest.raises(ValueError):
            safe_slug("!!!@@@###")

    def test_consecutive_dashes_collapsed(self):
        slug = safe_slug("a///b\\\\c")
        assert "---" not in slug
