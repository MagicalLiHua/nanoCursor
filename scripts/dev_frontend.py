#!/usr/bin/env python3
"""Start nanoCursor frontend dev server on http://127.0.0.1:5173"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"


def main() -> int:
    print("nanoCursor Frontend")
    print("=" * 40)

    if not (FRONTEND_DIR / "node_modules").exists():
        print("Installing frontend dependencies (first run)...")
        result = subprocess.run(["npm", "install"], cwd=str(FRONTEND_DIR))
        if result.returncode != 0:
            print("npm install failed. Please check Node.js and npm installation.")
            return 1

    print(f"Starting frontend dev server on http://127.0.0.1:5173 ...")
    print("Press Ctrl+C to stop.\n")

    try:
        subprocess.run(
            ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"],
            cwd=str(FRONTEND_DIR),
        )
    except KeyboardInterrupt:
        print("\nFrontend stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
