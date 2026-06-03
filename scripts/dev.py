#!/usr/bin/env python3
"""nanoCursor dev — start backend + frontend together."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
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
        ["npm", "run", "dev"],
        cwd=str(frontend_dir),
        env=env,
    )
    procs.append(frontend)

    print("\n  Backend:  http://127.0.0.1:8100")
    print("  Frontend: http://127.0.0.1:5173")
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
