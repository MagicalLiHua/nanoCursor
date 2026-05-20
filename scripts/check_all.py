#!/usr/bin/env python3
"""nanoCursor check-all — run backend tests, backend audit, and frontend check."""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], cwd: str | None = None, label: str = "") -> int:
    print(f"  [{label or cmd[0]}] {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd or str(PROJECT_ROOT))
    return result.returncode


def main() -> int:
    print("nanoCursor Check All\n")
    failed = 0

    # Backend compile
    py_patterns = ["api_server.py", "src/api/services/*.py", "src/runtime/*.py", "src/agent/*.py"]
    for pattern in py_patterns:
        matches = list(PROJECT_ROOT.glob(pattern))
        if matches:
            rc = run(["python", "-m", "py_compile"] + [str(p) for p in matches], label="compile")
            if rc != 0:
                failed += 1

    # Backend tests
    rc = run(["pytest", "-q"], label="pytest")
    if rc != 0:
        failed += 1

    # Backend audit
    rc = run(["python", "scripts/backend_audit.py"], label="audit")
    if rc != 0:
        failed += 1

    # API smoke
    rc = run(["python", "scripts/api_smoke.py"], label="api-smoke")
    if rc != 0:
        failed += 1

    # Frontend check
    rc = run(["npm", "run", "check"], cwd=str(PROJECT_ROOT / "frontend"), label="frontend")
    if rc != 0:
        failed += 1

    print()
    if failed == 0:
        print("  All checks passed.")
        return 0
    else:
        print(f"  {failed} check(s) failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
