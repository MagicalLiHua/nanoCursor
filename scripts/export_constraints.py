"""Regenerate or verify runtime constraints from the committed uv.lock."""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    uv = shutil.which("uv")
    if not uv:
        parser.error("uv is required")
    result = subprocess.run([uv, "export", "--no-cache", "--frozen", "--no-dev", "--no-emit-project",
                             "--no-hashes", "--no-header", "--no-annotate"], cwd=root,
                            check=True, capture_output=True, text=True)
    path = root / "packaging/runtime-constraints.txt"
    if args.check:
        if not path.exists() or path.read_text() != result.stdout:
            raise SystemExit("Runtime constraints differ from uv.lock; run python scripts/export_constraints.py")
        print("Runtime constraints match uv.lock")
    else:
        path.write_text(result.stdout)
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
