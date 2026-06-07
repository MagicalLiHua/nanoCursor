#!/usr/bin/env python3
"""nanoCursor dev — start backend + frontend together."""

import os
import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Start nanoCursor development services.")
    parser.add_argument("--with-go-runtime", action="store_true", help="Start the optional Go executor and MCP services.")
    parser.add_argument("--with-go-indexer", action="store_true", help="Start the optional Go project indexer gRPC service.")
    parser.add_argument("--with-go-filetools", action="store_true", help="Start the optional Go filetools gRPC service.")
    parser.add_argument("--indexer-port", type=int, default=50051, help="Go indexer gRPC port when enabled.")
    parser.add_argument("--executor-port", type=int, default=50055, help="Go executor gRPC port when enabled.")
    parser.add_argument("--mcp-port", type=int, default=50056, help="Go MCP gRPC port when enabled.")
    parser.add_argument("--filetools-port", type=int, default=50054, help="Go filetools gRPC port when enabled.")
    args = parser.parse_args()

    print("nanoCursor Dev Server")
    print("=" * 40)

    env = os.environ.copy()
    procs: list[subprocess.Popen] = []

    def cleanup(sig, frame):
        print("\nShutting down...")
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    if args.with_go_indexer:
        indexer_addr = f"127.0.0.1:{args.indexer_port}"
        print(f"[go-indexer] Starting on {indexer_addr} ...")
        env["NANOCURSOR_GO_INDEXER_ENABLED"] = "true"
        env["NANOCURSOR_GO_INDEXER_FALLBACK"] = env.get("NANOCURSOR_GO_INDEXER_FALLBACK", "true")
        env["NANOCURSOR_GO_INDEXER_ADDR"] = indexer_addr
        env["INDEXER_GRPC_ADDR"] = indexer_addr
        go_indexer = subprocess.Popen(
            ["go", "run", "./cmd/nanocursor-indexer", f"--addr={indexer_addr}"],
            cwd=str(PROJECT_ROOT / "go-services" / "indexer"),
            env=env,
        )
        procs.append(go_indexer)
        time.sleep(0.8)

    if args.with_go_runtime:
        executor_addr = f"127.0.0.1:{args.executor_port}"
        mcp_addr = f"127.0.0.1:{args.mcp_port}"
        print(f"[go-executor] Starting on {executor_addr} ...")
        print(f"[go-mcp] Starting on {mcp_addr} ...")
        go_env = env.copy()
        go_env["NANOCURSOR_GO_RUNTIME_ADDR"] = executor_addr
        env["NANOCURSOR_GO_RUNTIME_ENABLED"] = "true"
        env["NANOCURSOR_GO_RUNTIME_URL"] = f"http://{executor_addr}"
        env["NANOCURSOR_EXECUTOR_ADDR"] = executor_addr
        env["NANOCURSOR_MCP_ADDR"] = mcp_addr
        go_executor = subprocess.Popen(
            ["go", "run", "./cmd/nanocursor-executor"],
            cwd=str(PROJECT_ROOT / "go-services" / "executor"),
            env=go_env,
        )
        procs.append(go_executor)
        go_mcp_env = env.copy()
        go_mcp_env["NANOCURSOR_GO_RUNTIME_ADDR"] = mcp_addr
        go_mcp = subprocess.Popen(
            ["go", "run", "./cmd/nanocursor-mcp"],
            cwd=str(PROJECT_ROOT / "go-services" / "mcp"),
            env=go_mcp_env,
        )
        procs.append(go_mcp)
        time.sleep(0.8)

    if args.with_go_filetools:
        filetools_addr = f"127.0.0.1:{args.filetools_port}"
        print(f"[go-filetools] Starting on {filetools_addr} ...")
        env["NANOCURSOR_GO_FILETOOLS_ENABLED"] = "true"
        env["NANOCURSOR_GO_FILETOOLS_FALLBACK"] = env.get("NANOCURSOR_GO_FILETOOLS_FALLBACK", "true")
        env["NANOCURSOR_GO_FILETOOLS_ADDR"] = filetools_addr
        env["FILETOOLS_GRPC_ADDR"] = filetools_addr
        go_filetools = subprocess.Popen(
            ["go", "run", "./cmd/nanocursor-filetools", "-addr", filetools_addr],
            cwd=str(PROJECT_ROOT / "go-services" / "filetools"),
            env=env,
        )
        procs.append(go_filetools)
        time.sleep(0.8)

    # Backend
    print("[backend] Starting uvicorn on http://127.0.0.1:8100 ...")
    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api.server:app", "--host", "127.0.0.1", "--port", "8100"],
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    procs.append(backend)
    time.sleep(1.5)

    # Frontend
    frontend_dir = PROJECT_ROOT / "frontend"
    if not (frontend_dir / "node_modules").exists():
        print("[frontend] Installing dependencies (first run)...")
        subprocess.run(["npm", "install"], cwd=str(frontend_dir), check=False)

    print(f"[frontend] Starting dev server on http://127.0.0.1:5173 ...")
    frontend = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"],
        cwd=str(frontend_dir),
        env=env,
    )
    procs.append(frontend)

    print("\n  Backend:  http://127.0.0.1:8100")
    print("  Frontend: http://127.0.0.1:5173")
    if args.with_go_runtime:
        print(f"  Go Executor: {executor_addr}")
        print(f"  Go MCP: {mcp_addr}")
    if args.with_go_indexer:
        print(f"  Go Indexer: {indexer_addr}")
    if args.with_go_filetools:
        print(f"  Go Filetools: {filetools_addr}")
    print("\nPress Ctrl+C to stop.\n")

    # Wait for any child to exit
    try:
        while True:
            for p in procs:
                if p.poll() is not None:
                    print(f"Process exited with code {p.returncode}")
                    cleanup(None, None)
            time.sleep(1)
    except KeyboardInterrupt:
        cleanup(None, None)


if __name__ == "__main__":
    main()
