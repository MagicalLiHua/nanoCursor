"""Contract tests for Python file_ops and the Go filetools sidecar."""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, timeout_seconds: float = 60.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"Go filetools did not open port {port}")


@pytest.fixture(scope="session")
def go_filetools_addr():
    if shutil.which("go") is None:
        pytest.skip("go is not installed")
    port = _free_port()
    addr = f"127.0.0.1:{port}"
    proc = subprocess.Popen(
        ["go", "run", "./cmd/nanocursor-filetools", "-addr", addr],
        cwd=PROJECT_ROOT / "go-services" / "filetools",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_port(port)
        yield addr
    except Exception:
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=5)
        raise RuntimeError(f"failed to start Go filetools\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def _configure_backend(monkeypatch, backend: str, go_addr: str) -> None:
    if backend == "go":
        monkeypatch.setenv("NANOCURSOR_GO_FILETOOLS_ENABLED", "true")
        monkeypatch.setenv("NANOCURSOR_GO_FILETOOLS_FALLBACK", "false")
        monkeypatch.setenv("NANOCURSOR_GO_FILETOOLS_ADDR", go_addr)
        monkeypatch.setenv("FILETOOLS_GRPC_ADDR", go_addr)
    else:
        monkeypatch.setenv("NANOCURSOR_GO_FILETOOLS_ENABLED", "false")
        monkeypatch.setenv("NANOCURSOR_GO_FILETOOLS_FALLBACK", "true")


@pytest.mark.parametrize("backend", ["python", "go"])
def test_write_read_and_overwrite_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_read, run_write

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    content = "def hello():\n    return '你好'\n"
    assert "Created" in run_write("src/demo.py", content, tmp_path)
    assert run_read("src/demo.py", tmp_path) == content

    updated = "def hello():\n    return 'hello'\n"
    assert "Updated" in run_write("src/demo.py", updated, tmp_path)
    assert run_read("src/demo.py", tmp_path) == updated


@pytest.mark.parametrize("backend", ["python", "go"])
def test_write_creates_parent_directories_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_write

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    result = run_write("a/b/c/config.json", '{"ok": true}', tmp_path)
    assert "Created" in result
    assert (tmp_path / "a/b/c/config.json").read_text(encoding="utf-8") == '{"ok": true}'


@pytest.mark.parametrize("backend", ["python", "go"])
def test_string_edit_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_edit

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    target = tmp_path / "demo.py"
    target.write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    result = run_edit("demo.py", tmp_path, old_text="'hi'", new_text="'hello'")
    assert "Error:" not in result
    assert target.read_text(encoding="utf-8") == "def hello():\n    return 'hello'\n"


@pytest.mark.parametrize("backend", ["python", "go"])
def test_line_edit_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_edit

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    target = tmp_path / "demo.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")
    result = run_edit("demo.txt", tmp_path, start_line=2, end_line=2, new_text="changed")
    assert "Error:" not in result
    assert target.read_text(encoding="utf-8") == "line1\nchanged\nline3\n"


@pytest.mark.parametrize("backend", ["python", "go"])
def test_multiline_delete_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_edit

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    target = tmp_path / "demo.txt"
    target.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
    result = run_edit("demo.txt", tmp_path, start_line=2, end_line=3, new_text="")
    assert "Error:" not in result
    assert target.read_text(encoding="utf-8") == "line1\nline4\n"


@pytest.mark.parametrize("backend", ["python", "go"])
def test_edit_text_not_found_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_edit

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    target = tmp_path / "demo.txt"
    target.write_text("hello\n", encoding="utf-8")
    result = run_edit("demo.txt", tmp_path, old_text="missing", new_text="new")
    assert "not found" in result.lower() or "未能" in result
    assert target.read_text(encoding="utf-8") == "hello\n"


@pytest.mark.parametrize("backend", ["python", "go"])
def test_invalid_line_range_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_edit

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    target = tmp_path / "demo.txt"
    target.write_text("hello\n", encoding="utf-8")
    result = run_edit("demo.txt", tmp_path, start_line=2, end_line=4, new_text="bad")
    assert "error" in result.lower() or "失败" in result
    assert target.read_text(encoding="utf-8") == "hello\n"


@pytest.mark.parametrize("backend", ["python", "go"])
def test_list_directory_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_list_directory

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    (tmp_path / "src").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    (tmp_path / "module.pyc").write_text("", encoding="utf-8")

    listing = run_list_directory(".", tmp_path)
    assert "src/" in listing
    assert "README.md" in listing
    assert "__pycache__" not in listing
    assert ".git" not in listing
    assert "node_modules" not in listing
    assert "module.pyc" not in listing


@pytest.mark.parametrize("backend", ["python", "go"])
def test_python_syntax_verification_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_write

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    result = run_write("broken.py", "def broken(\n", tmp_path)
    assert "Python syntax error" in result


@pytest.mark.parametrize("backend", ["python", "go"])
def test_path_escape_write_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_write

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    result = run_write("../outside.txt", "bad", tmp_path)
    assert "error" in result.lower() or "安全拦截" in result
    assert not (tmp_path.parent / "outside.txt").exists()


@pytest.mark.parametrize("backend", ["python", "go"])
def test_path_escape_read_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_read

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    result = run_read("../outside.txt", tmp_path)
    assert "error" in result.lower() or "安全拦截" in result


@pytest.mark.parametrize("backend", ["python", "go"])
def test_read_missing_file_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_read

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    result = run_read("missing.txt", tmp_path)
    assert "error" in result.lower() or "does not exist" in result.lower()


@pytest.mark.parametrize("backend", ["python", "go"])
def test_edit_missing_file_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_edit

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    result = run_edit("missing.txt", tmp_path, old_text="a", new_text="b")
    assert "not found" in result.lower() or "不存在" in result


@pytest.mark.parametrize("backend", ["python", "go"])
def test_empty_directory_listing_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_list_directory

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    (tmp_path / "empty").mkdir()
    assert run_list_directory("empty", tmp_path) == "(empty directory)"


@pytest.mark.parametrize("backend", ["python", "go"])
def test_list_non_directory_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_list_directory

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    (tmp_path / "file.txt").write_text("hello", encoding="utf-8")
    result = run_list_directory("file.txt", tmp_path)
    assert "error" in result.lower() or "不是一个存在的目录" in result


@pytest.mark.parametrize("backend", ["python", "go"])
def test_multiline_replace_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_edit

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    target = tmp_path / "demo.txt"
    target.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    result = run_edit("demo.txt", tmp_path, start_line=2, end_line=3, new_text="TWO\nTHREE")
    assert "Error:" not in result
    assert target.read_text(encoding="utf-8") == "one\nTWO\nTHREE\nfour\n"


@pytest.mark.parametrize("backend", ["python", "go"])
def test_absolute_path_inside_workspace_contract(tmp_path, monkeypatch, go_filetools_addr, backend):
    from src.tools.file_ops import run_read, run_write

    _configure_backend(monkeypatch, backend, go_filetools_addr)
    target = tmp_path / "absolute.txt"
    result = run_write(str(target), "hello absolute", tmp_path)
    assert "Created" in result
    assert run_read(str(target), tmp_path) == "hello absolute"


def test_go_backup_and_rollback_contract(tmp_path, go_filetools_addr):
    from src.tools.filetools_client import FileToolsClient

    client = FileToolsClient(str(tmp_path), server_addr=go_filetools_addr)
    try:
        asyncio.run(client.write_file("demo.txt", "original\n"))
        asyncio.run(client.edit_file("demo.txt", search_block="original", replace_block="changed", create_backup=True))
        assert (tmp_path / "demo.txt").read_text(encoding="utf-8") == "changed\n"

        backups = asyncio.run(client.list_backups("demo.txt"))
        assert "demo.txt.bak" in backups

        message = asyncio.run(client.rollback_file("demo.txt", -1))
        assert "成功回滚" in message
        assert (tmp_path / "demo.txt").read_text(encoding="utf-8") == "original\n"
    finally:
        client.close()
