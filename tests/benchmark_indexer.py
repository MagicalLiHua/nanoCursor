"""Benchmark: Python indexer vs Go gRPC indexer."""

import time
from pathlib import Path


def benchmark_python_indexer(workspace: Path, iterations: int = 3):
    from src.indexer.indexer import ProjectIndex

    times = []
    for _ in range(iterations):
        idx = ProjectIndex(workspace)
        start = time.perf_counter()
        idx.build(force=True)
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    avg = sum(times) / len(times)
    return avg


def benchmark_grpc_indexer(workspace: Path, iterations: int = 3):
    from src.indexer.indexer_grpc import ProjectIndexClient

    times = []
    for _ in range(iterations):
        client = ProjectIndexClient(workspace, server_addr="localhost:50051")
        start = time.perf_counter()
        client.build(force=True)
        elapsed = time.perf_counter() - start
        times.append(elapsed)
        client.close()

    avg = sum(times) / len(times)
    return avg


if __name__ == "__main__":
    import sys

    workspace = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")

    print(f"Benchmarking on workspace: {workspace}")
    print()

    py_time = benchmark_python_indexer(workspace)
    print(f"Python indexer: {py_time:.3f}s avg")

    try:
        go_time = benchmark_grpc_indexer(workspace)
        print(f"Go gRPC indexer: {go_time:.3f}s avg")
        print(f"Speedup: {py_time / go_time:.1f}x")
    except Exception as e:
        print(f"Go gRPC indexer: FAILED ({e})")
        print("Make sure go-indexer is running on localhost:50051")
