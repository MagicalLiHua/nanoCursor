"""Tests for optional Go microservice integration facades."""

from __future__ import annotations


class _FakeFileToolsClient:
    def __init__(self, workspace: str, server_addr: str | None = None):
        self.workspace = workspace
        self.server_addr = server_addr
        self.closed = False

    async def read_file(self, filename: str) -> str:
        return f"go read {filename}"

    async def write_file(
        self,
        filename: str,
        content: str,
        *,
        overwrite: bool = False,
        backup_existing: bool = False,
    ) -> str:
        from pathlib import Path

        if Path(self.workspace, filename).exists() and not overwrite:
            return "exists"
        Path(self.workspace, filename).write_text(content, encoding="utf-8")
        return "go wrote"

    async def edit_file(
        self,
        filename: str,
        search_block: str = "",
        replace_block: str = "",
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        new_text: str = "",
        match_mode: str = "fuzzy",
        create_backup: bool = True,
    ) -> str:
        from pathlib import Path

        path = Path(self.workspace, filename)
        content = path.read_text(encoding="utf-8")
        if start_line is not None and end_line is not None:
            lines = content.splitlines(keepends=True)
            replacement = new_text if new_text.endswith("\n") else f"{new_text}\n"
            path.write_text("".join(lines[: start_line - 1]) + replacement + "".join(lines[end_line:]), encoding="utf-8")
            return "go edited lines"
        path.write_text(content.replace(search_block, replace_block, 1), encoding="utf-8")
        return "go edited text"

    async def list_directory(self, path: str = ".") -> str:
        return "go-list\nfile.py"

    def close(self) -> None:
        self.closed = True


class _FailingFileToolsClient:
    def __init__(self, workspace: str, server_addr: str | None = None):
        pass

    async def read_file(self, filename: str) -> str:
        raise RuntimeError("go down")

    def close(self) -> None:
        pass


class _CountingFailingFileToolsClient:
    calls = 0

    def __init__(self, workspace: str, server_addr: str | None = None):
        self.workspace = workspace

    async def read_file(self, filename: str) -> str:
        type(self).calls += 1
        raise RuntimeError("go down")

    def close(self) -> None:
        pass


def test_file_ops_uses_go_filetools_when_enabled(tmp_path, monkeypatch):
    from src.tools import file_ops

    file_ops._GO_FILETOOLS_DISABLED_UNTIL_BY_ADDR.clear()
    monkeypatch.setattr(file_ops, "go_filetools_enabled", lambda: True)
    monkeypatch.setattr(file_ops, "go_filetools_fallback_enabled", lambda: True)
    monkeypatch.setattr(file_ops, "go_filetools_addr", lambda: "localhost:50054")
    monkeypatch.setattr("src.tools.filetools_client.FileToolsClient", _FakeFileToolsClient)

    assert file_ops.run_read("demo.py", tmp_path) == "go read demo.py"
    event = file_ops.pop_filetools_backend_event()
    assert event is not None
    assert event["backend"] == "go"
    assert event["fallback"] is False
    assert file_ops.run_list_directory(".", tmp_path) == "go-list\nfile.py"
    result = file_ops.run_write("demo.py", "print('hi')\n", tmp_path)
    assert "go wrote" in result
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "print('hi')\n"
    result = file_ops.run_edit("demo.py", tmp_path, old_text="hi", new_text="hello")
    assert "go edited text" in result
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "print('hello')\n"
    assert file_ops.pop_filetools_backend_event()["backend"] == "go"


def test_file_ops_falls_back_when_go_filetools_fails(tmp_path, monkeypatch):
    from src.tools import file_ops

    file_ops._GO_FILETOOLS_DISABLED_UNTIL_BY_ADDR.clear()
    (tmp_path / "demo.py").write_text("print('python fallback')\n", encoding="utf-8")
    monkeypatch.setattr(file_ops, "go_filetools_enabled", lambda: True)
    monkeypatch.setattr(file_ops, "go_filetools_fallback_enabled", lambda: True)
    monkeypatch.setattr(file_ops, "go_filetools_addr", lambda: "localhost:50054")
    monkeypatch.setattr("src.tools.filetools_client.FileToolsClient", _FailingFileToolsClient)

    assert "python fallback" in file_ops.run_read("demo.py", tmp_path)
    event = file_ops.pop_filetools_backend_event()
    assert event is not None
    assert event["backend"] == "python"
    assert event["from_backend"] == "go"
    assert event["fallback"] is True
    assert "go down" in event["reason"]


def test_go_filetools_failure_cooldown_skips_repeated_connection_attempts(tmp_path, monkeypatch):
    from src.tools import file_ops

    file_ops._GO_FILETOOLS_DISABLED_UNTIL_BY_ADDR.clear()
    _CountingFailingFileToolsClient.calls = 0
    (tmp_path / "demo.py").write_text("print('python fallback')\n", encoding="utf-8")
    monkeypatch.setattr(file_ops, "go_filetools_enabled", lambda: True)
    monkeypatch.setattr(file_ops, "go_filetools_fallback_enabled", lambda: True)
    monkeypatch.setattr(file_ops, "go_filetools_addr", lambda: "localhost:50054")
    monkeypatch.setattr(file_ops, "_go_filetools_failure_cooldown_seconds", lambda: 30.0)
    monkeypatch.setattr("src.tools.filetools_client.FileToolsClient", _CountingFailingFileToolsClient)

    assert "python fallback" in file_ops.run_read("demo.py", tmp_path)
    assert "python fallback" in file_ops.run_read("demo.py", tmp_path)

    assert _CountingFailingFileToolsClient.calls == 1
    event = file_ops.pop_filetools_backend_event()
    assert event["fallback"] is True
    assert "cooldown" in event["reason"]
    file_ops._GO_FILETOOLS_DISABLED_UNTIL_BY_ADDR.clear()
