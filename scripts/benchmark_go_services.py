#!/usr/bin/env python3
"""Benchmark optional Go microservices against Python fallbacks.

The script starts the Go indexer and executor on temporary local ports, builds a
synthetic workspace unless one is provided, and compares:

- Python ProjectIndex vs Go indexer gRPC
- Python subprocess command runner vs Go executor gRPC
- Python file_ops vs Go filetools gRPC

It does not change the default backend configuration used by the application.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from statistics import mean
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, timeout_seconds: float = 10.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"service did not open port {port}")


def _start_go_service(service_dir: str, cmd_dir: str, port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        ["go", "run", f"./cmd/{cmd_dir}", f"--addr=127.0.0.1:{port}"],
        cwd=ROOT / "go-services" / service_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_port(port)
    except Exception:
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=5)
        raise RuntimeError(f"failed to start {service_dir}:\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
    return proc


def _stop_processes(processes: Iterable[subprocess.Popen]) -> None:
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
    for proc in processes:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _make_workspace(file_count: int = 160) -> Path:
    root = Path(tempfile.mkdtemp(prefix="nanocursor-go-bench-"))
    (root / "src").mkdir()
    (root / "tests").mkdir()
    for i in range(file_count):
        target = root / ("tests" if i % 7 == 0 else "src") / f"module_{i}.py"
        target.write_text(
            "\n".join([
                f"import math",
                f"class Worker{i}:",
                f"    def compute(self, value):",
                f"        return math.sqrt(value + {i})",
                "",
                f"def helper_{i}(value):",
                f"    worker = Worker{i}()",
                f"    return worker.compute(value)",
                "",
            ]),
            encoding="utf-8",
        )
    (root / "README.md").write_text("# benchmark\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='bench'\n", encoding="utf-8")
    return root


def _time_call(fn, iterations: int) -> list[float]:
    times: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    return times


def benchmark_indexer(workspace: Path, iterations: int, indexer_addr: str) -> dict:
    from src.indexer.indexer import ProjectIndex
    from src.indexer.indexer_grpc import ProjectIndexClient

    def run_python():
        idx = ProjectIndex(workspace)
        idx.build(force=True)

    def run_go():
        client = ProjectIndexClient(workspace, server_addr=indexer_addr)
        try:
            client.build(force=True)
        finally:
            client.close()

    py_times = _time_call(run_python, iterations)
    go_times = _time_call(run_go, iterations)
    return {
        "python_avg_ms": round(mean(py_times) * 1000, 2),
        "go_avg_ms": round(mean(go_times) * 1000, 2),
        "speedup": round(mean(py_times) / mean(go_times), 2) if mean(go_times) > 0 else None,
    }


def benchmark_executor(workspace: Path, iterations: int, executor_addr: str) -> dict:
    from src.runtime import command_runner, executor_client

    original_executor_available = command_runner._EXECUTOR_AVAILABLE
    original_executor_addr = executor_client.EXECUTOR_ADDR
    try:
        command_runner._EXECUTOR_AVAILABLE = False
        os.environ["NANOCURSOR_GO_RUNTIME_ENABLED"] = "false"

        def run_python():
            result = command_runner.run_command("python -c 'print(21 * 2)'", cwd=workspace, timeout_seconds=10)
            if int(result.get("exit_code", -1)) != 0:
                raise RuntimeError(result)

        executor_client.close()
        executor_client.EXECUTOR_ADDR = executor_addr

        def run_go():
            result = executor_client.run_command(
                "python -c 'print(21 * 2)'",
                cwd=str(workspace),
                workspace_dir=str(workspace),
                timeout_ms=10_000,
                permission_level="shell_safe",
            )
            if int(result.get("exit_code", -1)) != 0:
                raise RuntimeError(result)

        py_times = _time_call(run_python, iterations)
        go_times = _time_call(run_go, iterations)
        return {
            "python_avg_ms": round(mean(py_times) * 1000, 2),
            "go_avg_ms": round(mean(go_times) * 1000, 2),
            "speedup": round(mean(py_times) / mean(go_times), 2) if mean(go_times) > 0 else None,
        }
    finally:
        command_runner._EXECUTOR_AVAILABLE = original_executor_available
        executor_client.close()
        executor_client.EXECUTOR_ADDR = original_executor_addr


def benchmark_filetools(workspace: Path, iterations: int, filetools_addr: str) -> dict:
    from src.tools import file_ops

    original_enabled = os.environ.get("NANOCURSOR_GO_FILETOOLS_ENABLED")
    original_fallback = os.environ.get("NANOCURSOR_GO_FILETOOLS_FALLBACK")
    original_addr = os.environ.get("NANOCURSOR_GO_FILETOOLS_ADDR")

    def restore_env() -> None:
        for key, value in {
            "NANOCURSOR_GO_FILETOOLS_ENABLED": original_enabled,
            "NANOCURSOR_GO_FILETOOLS_FALLBACK": original_fallback,
            "NANOCURSOR_GO_FILETOOLS_ADDR": original_addr,
        }.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def check_result(result: str) -> None:
        if isinstance(result, str) and result.startswith("Error:"):
            raise RuntimeError(result)

    def run_ops(prefix: str) -> None:
        target = f".bench/{prefix}.txt"
        check_result(file_ops.run_read("src/module_1.py", workspace))
        check_result(file_ops.run_list_directory("src", workspace))
        check_result(file_ops.run_write(target, "alpha\nbeta\ngamma\n", workspace))
        check_result(file_ops.run_edit(target, workspace, start_line=2, end_line=2, new_text="BETA\n"))

    try:
        os.environ["NANOCURSOR_GO_FILETOOLS_ENABLED"] = "false"
        os.environ["NANOCURSOR_GO_FILETOOLS_FALLBACK"] = "true"
        py_counter = 0

        def run_python():
            nonlocal py_counter
            py_counter += 1
            run_ops(f"python_{py_counter}")

        os.environ["NANOCURSOR_GO_FILETOOLS_ENABLED"] = "true"
        os.environ["NANOCURSOR_GO_FILETOOLS_FALLBACK"] = "false"
        os.environ["NANOCURSOR_GO_FILETOOLS_ADDR"] = filetools_addr
        go_counter = 0

        def run_go():
            nonlocal go_counter
            go_counter += 1
            file_ops._GO_FILETOOLS_DISABLED_UNTIL_BY_ADDR.clear()
            run_ops(f"go_{go_counter}")

        os.environ["NANOCURSOR_GO_FILETOOLS_ENABLED"] = "false"
        py_times = _time_call(run_python, iterations)
        os.environ["NANOCURSOR_GO_FILETOOLS_ENABLED"] = "true"
        go_times = _time_call(run_go, iterations)
        return {
            "python_avg_ms": round(mean(py_times) * 1000, 2),
            "go_avg_ms": round(mean(go_times) * 1000, 2),
            "speedup": round(mean(py_times) / mean(go_times), 2) if mean(go_times) > 0 else None,
        }
    finally:
        restore_env()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=None, help="Workspace to benchmark. Defaults to a synthetic temp workspace.")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--files", type=int, default=160, help="Synthetic workspace file count.")
    args = parser.parse_args()

    workspace = args.workspace.resolve() if args.workspace else _make_workspace(args.files)
    indexer_port = _free_port()
    executor_port = _free_port()
    filetools_port = _free_port()
    processes: list[subprocess.Popen] = []

    print(f"workspace: {workspace}")
    print(f"iterations: {args.iterations}")
    try:
        processes.append(_start_go_service("indexer", "nanocursor-indexer", indexer_port))
        processes.append(_start_go_service("executor", "nanocursor-executor", executor_port))
        processes.append(_start_go_service("filetools", "nanocursor-filetools", filetools_port))

        indexer = benchmark_indexer(workspace, args.iterations, f"127.0.0.1:{indexer_port}")
        executor = benchmark_executor(workspace, args.iterations, f"127.0.0.1:{executor_port}")
        filetools = benchmark_filetools(workspace, args.iterations, f"127.0.0.1:{filetools_port}")
        print("\nindexer:")
        print(f"  python avg: {indexer['python_avg_ms']} ms")
        print(f"  go avg:     {indexer['go_avg_ms']} ms")
        print(f"  speedup:    {indexer['speedup']}x")
        print("\nexecutor:")
        print(f"  python avg: {executor['python_avg_ms']} ms")
        print(f"  go avg:     {executor['go_avg_ms']} ms")
        print(f"  speedup:    {executor['speedup']}x")
        print("\nfiletools:")
        print(f"  python avg: {filetools['python_avg_ms']} ms")
        print(f"  go avg:     {filetools['go_avg_ms']} ms")
        print(f"  speedup:    {filetools['speedup']}x")
    finally:
        _stop_processes(processes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
