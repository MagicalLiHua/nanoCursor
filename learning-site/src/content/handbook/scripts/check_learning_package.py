#!/usr/bin/env python3
"""Lightweight checks for the nanoCursor learning handbook."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "index.html",
    "README.md",
    "LEARNING_PACKAGE_PLAN.md",
    "chapters/01-project-overview.md",
    "chapters/02-request-lifecycle.md",
    "chapters/03-agent-loop.md",
    "chapters/04-agent-orchestration.md",
    "chapters/05-context-management.md",
    "chapters/06-memory-system.md",
    "chapters/07-tool-governance.md",
    "chapters/08-event-store-and-sse.md",
    "chapters/09-runtime-and-async-boundary.md",
    "chapters/10-go-sidecar.md",
    "chapters/11-mcp-and-skills.md",
    "chapters/12-frontend-observability.md",
    "chapters/13-testing-and-quality.md",
    "chapters/14-deployment-and-startup.md",
    "chapters/15-project-retrospective.md",
    "maps/backend-code-map.md",
    "maps/api-map.md",
    "maps/event-map.md",
    "exercises/01-read-the-request-lifecycle.md",
    "interview/01-project-pitch.md",
]

REQUIRED_DIRS = [
    "chapters",
    "maps",
    "exercises",
    "interview",
    "assets",
    "scripts",
]


def main() -> int:
    errors: list[str] = []

    for rel in REQUIRED_DIRS:
        path = ROOT / rel
        if not path.is_dir():
            errors.append(f"missing directory: {rel}")

    for rel in REQUIRED_FILES:
        path = ROOT / rel
        if not path.is_file():
            errors.append(f"missing file: {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            errors.append(f"empty file: {rel}")
        if rel.endswith(".md") and not text.lstrip().startswith("#"):
            errors.append(f"markdown missing title: {rel}")

    index_text = (ROOT / "index.html").read_text(encoding="utf-8")
    for rel in [
        "README.md",
        "LEARNING_PACKAGE_PLAN.md",
        "chapters/01-project-overview.md",
        "chapters/02-request-lifecycle.md",
        "maps/backend-code-map.md",
        "interview/01-project-pitch.md",
    ]:
        if rel not in index_text:
            errors.append(f"index.html does not link: {rel}")

    if errors:
        print("learning handbook check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"learning handbook check passed: {len(REQUIRED_FILES)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

