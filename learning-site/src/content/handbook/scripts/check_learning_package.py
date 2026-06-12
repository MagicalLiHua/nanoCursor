#!/usr/bin/env python3
"""Lightweight checks for the nanoCursor learning handbook."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "index.html",
    "README.md",
    "LEARNING_PACKAGE_PLAN.md",
    "chapters/00-learning-roadmap.md",
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
    "chapters/16-architecture-decisions.md",
    "maps/backend-code-map.md",
    "maps/api-map.md",
    "maps/concept-glossary.md",
    "maps/debugging-playbook.md",
    "maps/event-map.md",
    "maps/module-evidence-matrix.md",
    "maps/source-navigation-index.md",
    "exercises/01-read-the-request-lifecycle.md",
    "exercises/02-trace-one-real-run.md",
    "exercises/03-memory-tool-governance-lab.md",
    "exercises/04-run-benchmark-and-ablation.md",
    "exercises/05-mastery-audit.md",
    "exercises/06-real-run-walkthroughs.md",
    "interview/01-project-pitch.md",
    "interview/03-agent-loop-deep-dive.md",
    "interview/04-context-and-memory.md",
    "interview/05-tools-recovery-and-observability.md",
    "interview/06-go-mcp-and-project-boundary.md",
    "interview/07-interview-question-bank.md",
    "interview/08-testing-benchmark-retrospective.md",
    "interview/09-four-day-final-drill.md",
]

REQUIRED_DIRS = [
    "chapters",
    "maps",
    "exercises",
    "interview",
    "assets",
    "scripts",
]


def _slugify(text: str) -> str:
    value = text.strip().lower()
    value = re.sub(r"[`*_~()[\]{}:：，。,.!?/\\|]+", "", value)
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "section"


def _duplicate_rendered_heading_ids(text: str) -> list[str]:
    counts: dict[str, int] = {}
    rendered_ids: set[str] = set()
    duplicates: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,4})\s+(.+)$", line)
        if not match:
            continue
        slug = _slugify(match.group(2).rstrip("#").strip())
        seen = counts.get(slug, 0)
        counts[slug] = seen + 1
        rendered_id = slug if seen == 0 else f"{slug}-{seen + 1}"
        if rendered_id in rendered_ids and rendered_id not in duplicates:
            duplicates.append(rendered_id)
        rendered_ids.add(rendered_id)
    return duplicates


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
        if rel.startswith(("chapters/", "maps/", "exercises/", "interview/")) and "```mermaid" not in text:
            errors.append(f"learning doc missing mermaid diagram: {rel}")
        duplicates = _duplicate_rendered_heading_ids(text)
        if duplicates:
            errors.append(f"markdown duplicate heading ids in {rel}: {', '.join(duplicates[:5])}")

    for path in [
        *sorted((ROOT / "chapters").glob("*.md")),
        *sorted((ROOT / "maps").glob("*.md")),
        *sorted((ROOT / "exercises").glob("*.md")),
        *sorted((ROOT / "interview").glob("*.md")),
    ]:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        if not text.lstrip().startswith("#"):
            errors.append(f"markdown missing title: {rel}")
        if "```mermaid" not in text:
            errors.append(f"learning doc missing mermaid diagram: {rel}")
        duplicates = _duplicate_rendered_heading_ids(text)
        if duplicates:
            errors.append(f"markdown duplicate heading ids in {rel}: {', '.join(duplicates[:5])}")

    index_text = (ROOT / "index.html").read_text(encoding="utf-8")
    for rel in [
        "README.md",
        "LEARNING_PACKAGE_PLAN.md",
        "chapters/00-learning-roadmap.md",
        "chapters/01-project-overview.md",
        "chapters/02-request-lifecycle.md",
        "chapters/16-architecture-decisions.md",
        "maps/concept-glossary.md",
        "maps/debugging-playbook.md",
        "maps/module-evidence-matrix.md",
        "maps/backend-code-map.md",
        "maps/source-navigation-index.md",
        "exercises/05-mastery-audit.md",
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
