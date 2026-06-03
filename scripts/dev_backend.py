#!/usr/bin/env python3
"""Start nanoCursor backend server on http://127.0.0.1:8100"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    print("nanoCursor Backend")
    print("=" * 40)
    print(f"Starting uvicorn on http://127.0.0.1:8100 ...")
    print("Press Ctrl+C to stop.\n")

    try:
        subprocess.run(
            [sys.executable, "-m", "uvicorn", "src.api.server:app",
             "--host", "127.0.0.1", "--port", "8100"],
            cwd=str(PROJECT_ROOT),
        )
    except KeyboardInterrupt:
        print("\nBackend stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
