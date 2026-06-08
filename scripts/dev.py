#!/usr/bin/env python3
"""Start nanoCursor backend, frontend, and optional Go sidecars."""

from __future__ import annotations

import argparse
import os
import signal
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class GoService:
    key: str
    label: str
    port_arg: str
    default_port: int
    service_dir: Path
    command: list[str]
    env_enabled: str
    env_fallback: str
    env_addr: str
    env_aliases: tuple[str, ...] = ()


GO_SERVICES = (
    GoService(
        key="indexer",
        label="Go Indexer",
        port_arg="indexer_port",
        default_port=50051,
        service_dir=PROJECT_ROOT / "go-services" / "indexer",
        command=["go", "run", "./cmd/nanocursor-indexer", "--addr={addr}"],
        env_enabled="NANOCURSOR_GO_INDEXER_ENABLED",
        env_fallback="NANOCURSOR_GO_INDEXER_FALLBACK",
        env_addr="NANOCURSOR_GO_INDEXER_ADDR",
        env_aliases=("INDEXER_GRPC_ADDR",),
    ),
    GoService(
        key="filetools",
        label="Go Filetools",
        port_arg="filetools_port",
        default_port=50054,
        service_dir=PROJECT_ROOT / "go-services" / "filetools",
        command=["go", "run", "./cmd/nanocursor-filetools", "-addr", "{addr}"],
        env_enabled="NANOCURSOR_GO_FILETOOLS_ENABLED",
        env_fallback="NANOCURSOR_GO_FILETOOLS_FALLBACK",
        env_addr="NANOCURSOR_GO_FILETOOLS_ADDR",
        env_aliases=("FILETOOLS_GRPC_ADDR",),
    ),
    GoService(
        key="executor",
        label="Go Executor",
        port_arg="executor_port",
        default_port=50055,
        service_dir=PROJECT_ROOT / "go-services" / "executor",
        command=["go", "run", "./cmd/nanocursor-executor", "--addr={addr}"],
        env_enabled="NANOCURSOR_GO_EXECUTOR_ENABLED",
        env_fallback="NANOCURSOR_GO_EXECUTOR_FALLBACK",
        env_addr="NANOCURSOR_GO_EXECUTOR_ADDR",
        env_aliases=("NANOCURSOR_EXECUTOR_ADDR",),
    ),
    GoService(
        key="mcp",
        label="Go MCP Gateway",
        port_arg="mcp_port",
        default_port=50056,
        service_dir=PROJECT_ROOT / "go-services" / "mcp",
        command=["go", "run", "./cmd/nanocursor-mcp", "--addr={addr}"],
        env_enabled="NANOCURSOR_GO_MCP_GATEWAY_ENABLED",
        env_fallback="NANOCURSOR_GO_MCP_GATEWAY_FALLBACK",
        env_addr="NANOCURSOR_GO_MCP_GATEWAY_ADDR",
        env_aliases=("NANOCURSOR_MCP_ADDR",),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start nanoCursor development services.")
    parser.add_argument("--with-go", "--with-go-all", action="store_true", help="Start all integrated Go sidecars.")
    parser.add_argument("--no-go", action="store_true", help="Disable all Go sidecars without prompting.")
    parser.add_argument("--with-go-runtime", action="store_true", help="Start the optional Go executor and MCP services.")
    parser.add_argument("--with-go-indexer", action="store_true", help="Start the optional Go project indexer gRPC service.")
    parser.add_argument("--with-go-filetools", action="store_true", help="Start the optional Go filetools gRPC service.")
    parser.add_argument("--indexer-port", type=int, default=50051, help="Go indexer gRPC port when enabled.")
    parser.add_argument("--executor-port", type=int, default=50055, help="Go executor gRPC port when enabled.")
    parser.add_argument("--mcp-port", type=int, default=50056, help="Go MCP gRPC port when enabled.")
    parser.add_argument("--filetools-port", type=int, default=50054, help="Go filetools gRPC port when enabled.")
    parser.add_argument("--dry-run", action="store_true", help="Print the startup plan without starting services.")
    return parser.parse_args()


def ask_enable_go() -> bool:
    if not sys.stdin.isatty():
        print("[go] Non-interactive shell detected; Go sidecars disabled. Use --with-go to enable.")
        return False
    answer = input("Start integrated Go sidecars? Indexer/Filetools/Executor/MCP [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def decide_go_services(args: argparse.Namespace) -> set[str]:
    if args.no_go:
        return set()
    explicit = args.with_go or args.with_go_indexer or args.with_go_filetools or args.with_go_runtime
    if not explicit and not ask_enable_go():
        return set()
    if args.with_go:
        return {service.key for service in GO_SERVICES}
    selected: set[str] = set()
    if args.with_go_indexer:
        selected.add("indexer")
    if args.with_go_filetools:
        selected.add("filetools")
    if args.with_go_runtime:
        selected.update({"executor", "mcp"})
    return selected or {service.key for service in GO_SERVICES}


def check_go_support(selected: set[str]) -> tuple[bool, list[str]]:
    if not selected:
        return True, []
    problems: list[str] = []
    if not shutil.which("go"):
        problems.append("Go toolchain was not found in PATH.")
    for service in GO_SERVICES:
        if service.key in selected and not service.service_dir.exists():
            problems.append(f"{service.label} directory is missing: {service.service_dir}")
    return not problems, problems


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def service_addr(args: argparse.Namespace, service: GoService) -> str:
    return f"127.0.0.1:{getattr(args, service.port_arg)}"


def configure_go_env(env: dict[str, str], args: argparse.Namespace, selected: set[str]) -> dict[str, str]:
    addresses: dict[str, str] = {}
    for service in GO_SERVICES:
        enabled = service.key in selected
        addr = service_addr(args, service)
        env[service.env_enabled] = "true" if enabled else "false"
        env[service.env_fallback] = env.get(service.env_fallback, "true")
        env[service.env_addr] = addr
        for alias in service.env_aliases:
            env[alias] = addr
        addresses[service.key] = addr
    return addresses


def command_for(service: GoService, addr: str) -> list[str]:
    return [part.format(addr=addr) for part in service.command]


def start_go_services(
    args: argparse.Namespace,
    env: dict[str, str],
    selected: set[str],
    addresses: dict[str, str],
    procs: list[subprocess.Popen],
) -> None:
    for service in GO_SERVICES:
        if service.key not in selected:
            continue
        addr = addresses[service.key]
        port = int(addr.rsplit(":", 1)[1])
        if not is_port_available(port):
            print(f"[{service.key}] Port {addr} is already in use; assuming an existing sidecar is available.")
            continue
        print(f"[{service.key}] Starting {service.label} on {addr} ...")
        proc = subprocess.Popen(
            command_for(service, addr),
            cwd=str(service.service_dir),
            env=env,
        )
        procs.append(proc)
        time.sleep(0.6)
        if proc.poll() is not None:
            print(f"[{service.key}] Failed to start {service.label}; disabling this sidecar.")
            env[service.env_enabled] = "false"
            procs.remove(proc)


def start_backend(env: dict[str, str], procs: list[subprocess.Popen]) -> None:
    print("[backend] Starting uvicorn on http://127.0.0.1:8100 ...")
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api.server:app", "--host", "127.0.0.1", "--port", "8100"],
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    procs.append(backend)
    time.sleep(1.5)


def start_frontend(env: dict[str, str], procs: list[subprocess.Popen]) -> None:
    frontend_dir = PROJECT_ROOT / "frontend"
    if not (frontend_dir / "node_modules").exists():
        print("[frontend] Installing dependencies (first run)...")
        subprocess.run(["npm", "install"], cwd=str(frontend_dir), check=False)

    print("[frontend] Starting dev server on http://127.0.0.1:5173 ...")
    frontend = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"],
        cwd=str(frontend_dir),
        env=env,
    )
    procs.append(frontend)


def print_summary(selected: set[str], addresses: dict[str, str]) -> None:
    print("\n  Backend:  http://127.0.0.1:8100")
    print("  Frontend: http://127.0.0.1:5173")
    if selected:
        for service in GO_SERVICES:
            if service.key in selected:
                print(f"  {service.label}: {addresses[service.key]}")
    else:
        print("  Go sidecars: disabled")
    print("\nPress Ctrl+C to stop.\n")


def main() -> int:
    args = parse_args()

    print("nanoCursor Dev Server")
    print("=" * 40)

    selected_go = decide_go_services(args)
    supported, problems = check_go_support(selected_go)
    if not supported:
        print("[go] Go sidecars requested but cannot be started:")
        for problem in problems:
            print(f"  - {problem}")
        print("[go] Continuing with Python-only runtime and fallback paths.")
        selected_go = set()

    env = os.environ.copy()
    addresses = configure_go_env(env, args, selected_go)

    if args.dry_run:
        print_summary(selected_go, addresses)
        return 0

    procs: list[subprocess.Popen] = []

    def cleanup(sig=None, frame=None) -> None:
        print("\nShutting down...")
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    start_go_services(args, env, selected_go, addresses, procs)
    start_backend(env, procs)
    start_frontend(env, procs)
    print_summary(selected_go, addresses)

    try:
        while True:
            for proc in procs:
                if proc.poll() is not None:
                    print(f"Process exited with code {proc.returncode}")
                    cleanup()
            time.sleep(1)
    except KeyboardInterrupt:
        cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
