#!/usr/bin/env python3
"""Generate a conservative coverage + reference audit report.

The script consumes ``coverage.py`` JSON output and writes a markdown report with
zero/low coverage files plus rough repository reference counts. It is a
candidate list, not an auto-delete tool.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COVERAGE_JSON = ROOT / "audit-results" / "coverage.json"
DEFAULT_OUTPUT = ROOT / "audit-results" / "dead-code-candidates.md"
DEFAULT_COVERAGE_THRESHOLD = 20.0


@dataclass(frozen=True)
class CoverageFile:
    path: str
    percent: float
    missing_lines: int
    covered_lines: int
    reference_count: int
    category: str


def load_coverage_files(coverage_json: Path, *, threshold: float) -> list[CoverageFile]:
    """Load coverage JSON and classify files by coverage percentage."""

    payload = json.loads(coverage_json.read_text(encoding="utf-8"))
    files = payload.get("files") if isinstance(payload, dict) else {}
    if not isinstance(files, dict):
        raise ValueError("coverage json missing files object")

    results: list[CoverageFile] = []
    for raw_path, item in sorted(files.items()):
        if not isinstance(item, dict):
            continue
        summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
        percent = float(summary.get("percent_covered") or 0.0)
        missing_lines = int(summary.get("missing_lines") or 0)
        covered_lines = int(summary.get("covered_lines") or 0)
        if percent > threshold:
            continue
        path = _normalize_path(raw_path)
        category = "zero_coverage" if percent <= 0 else "low_coverage"
        results.append(CoverageFile(
            path=path,
            percent=round(percent, 2),
            missing_lines=missing_lines,
            covered_lines=covered_lines,
            reference_count=count_references(path),
            category=category,
        ))
    return results


def count_references(path: str) -> int:
    """Count rough textual references to a source file.

    We use basename/stem references because Python imports do not usually include
    the full path. This intentionally over-counts a little; the output is only a
    review queue.
    """

    source_path = Path(path)
    terms = {
        source_path.name,
        source_path.stem,
        ".".join(source_path.with_suffix("").parts),
    }
    total = 0
    for term in sorted(item for item in terms if item):
        try:
            proc = subprocess.run(
                [
                    "rg",
                    "--fixed-strings",
                    "--glob",
                    "!audit-results/**",
                    "--glob",
                    "!frontend/node_modules/**",
                    "--glob",
                    "!learning-site/node_modules/**",
                    "--glob",
                    "!**/__pycache__/**",
                    term,
                    str(ROOT),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except FileNotFoundError:
            return 0
        total += len([line for line in proc.stdout.splitlines() if line.strip()])
    return total


def build_report(files: list[CoverageFile], *, threshold: float, coverage_json: Path) -> str:
    zero = [item for item in files if item.category == "zero_coverage"]
    low = [item for item in files if item.category == "low_coverage"]
    lines = [
        "# Dead Code Candidate Report",
        "",
        "This report is generated from coverage JSON plus rough text-reference counts.",
        "Treat every row as a review candidate, not as proof that the file can be deleted.",
        "",
        f"- Coverage JSON: `{_display_path(coverage_json)}`",
        f"- Low coverage threshold: `{threshold:.1f}%`",
        f"- Zero coverage files: `{len(zero)}`",
        f"- Low coverage files: `{len(low)}`",
        "",
        "## Recommended Workflow",
        "",
        "1. Delete generated/cache artifacts first.",
        "2. For zero-coverage files with no meaningful references, inspect imports with `rg` before deleting.",
        "3. For legacy adapters, keep them only when a current API, startup script, or test still depends on them.",
        "4. After each deletion batch, run targeted tests before broad cleanup.",
        "",
    ]
    lines.extend(_table("Zero Coverage", zero))
    lines.extend(_table("Low Coverage", low))
    return "\n".join(lines) + "\n"


def write_report(files: list[CoverageFile], output: Path, *, threshold: float, coverage_json: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_report(files, threshold=threshold, coverage_json=coverage_json), encoding="utf-8")


def _table(title: str, rows: list[CoverageFile]) -> list[str]:
    lines = [
        f"## {title}",
        "",
    ]
    if not rows:
        return lines + ["No files in this bucket.", ""]
    lines.extend([
        "| File | Coverage | Missing | Covered | Rough refs | Suggested action |",
        "|---|---:|---:|---:|---:|---|",
    ])
    for item in rows:
        action = _suggest_action(item)
        lines.append(
            f"| `{item.path}` | {item.percent:.2f}% | {item.missing_lines} | "
            f"{item.covered_lines} | {item.reference_count} | {action} |"
        )
    lines.append("")
    return lines


def _suggest_action(item: CoverageFile) -> str:
    if "__pycache__" in item.path or item.path.endswith(".pyc"):
        return "delete generated artifact"
    if item.reference_count <= 1 and item.percent <= 0:
        return "inspect for deletion"
    if "legacy" in item.path or "runtime" in item.path:
        return "document legacy boundary or add tests"
    return "add tests or classify keep/delete"


def _normalize_path(path: str) -> str:
    raw = Path(path)
    try:
        return raw.resolve().relative_to(ROOT).as_posix()
    except Exception:
        return raw.as_posix()


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except Exception:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage-json", type=Path, default=DEFAULT_COVERAGE_JSON)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--threshold", type=float, default=DEFAULT_COVERAGE_THRESHOLD)
    args = parser.parse_args(argv)

    if not args.coverage_json.exists():
        print(
            "coverage json not found. Run: "
            f"pytest --cov=src --cov-report=json:{_display_path(args.coverage_json)}",
            file=sys.stderr,
        )
        return 2
    files = load_coverage_files(args.coverage_json, threshold=args.threshold)
    write_report(files, args.output, threshold=args.threshold, coverage_json=args.coverage_json)
    print(f"wrote {_display_path(args.output)} with {len(files)} candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
