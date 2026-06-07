#!/usr/bin/env python3
"""Migrate one workspace's legacy `.memory` directory to governed memory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.api.services.legacy_memory_migration_service import migrate_legacy_memory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace_dir")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--archive", action="store_true")
    args = parser.parse_args()
    result = migrate_legacy_memory(
        args.workspace_dir,
        dry_run=args.dry_run,
        archive=args.archive,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
