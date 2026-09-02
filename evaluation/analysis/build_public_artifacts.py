#!/usr/bin/env python3
"""Export privacy-safe run records and render reproducible SVG charts.

The raw result files contain model messages and traces, so they are deliberately
not committed. This script extracts only run-level metrics required to audit the
reported aggregate numbers.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


TASK_ORDER = [
    "issue-eval-astropy-12907",
    "issue-eval-astropy-13453",
    "issue-eval-django-11133",
    "issue-eval-django-11141",
    "issue-eval-matplotlib-23412",
    "issue-eval-pytest-8399",
    "issue-eval-requests-1142",
    "issue-eval-sklearn-13142",
    "issue-eval-sklearn-13328",
    "issue-eval-sphinx-10449",
    "issue-eval-sympy-11618",
    "issue-eval-xarray-3677",
]

CSV_FIELDS = [
    "harness",
    "run_id",
    "task_id",
    "repository",
    "trial",
    "status",
    "protocol_completed",
    "content_passed",
    "termination",
    "turns",
    "total_tokens",
    "tool_calls",
    "wall_seconds",
]


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def records_in(path: Path) -> Iterable[dict[str, Any]]:
    for candidate in sorted(path.rglob("*.json")):
        if {"regrades", "calibration", "preflight-current", "preflight-smoke"}.intersection(candidate.parts):
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        values = payload if isinstance(payload, list) else [payload]
        for value in values:
            if (
                isinstance(value, dict)
                and value.get("taskId") in TASK_ORDER
                and "trialIndex" in value
                and "manifest" in value
                and "grade" in value
            ):
                yield value


def public_row(
    harness: str,
    record: dict[str, Any],
    audit_task: dict[str, Any],
) -> dict[str, Any]:
    trial = int(record["trialIndex"])
    metrics = audit_task["pi" if harness == "Pi" else harness]
    index = trial - 1
    started = parse_time(str(record["startedAt"]))
    finished = parse_time(str(record["finishedAt"]))
    grade = record.get("grade") or {}
    return {
        "harness": harness,
        "run_id": record["runId"],
        "task_id": record["taskId"],
        "repository": record["manifest"]["repository"],
        "trial": trial,
        "status": record["outcomeStatus"],
        "protocol_completed": record["outcomeStatus"] == "COMPLETED",
        "content_passed": bool(grade.get("passed", record.get("passed", False))),
        "termination": record["terminationReason"],
        "turns": metrics["turns"][index],
        "total_tokens": metrics["totalTokens"][index],
        "tool_calls": metrics["toolCalls"][index],
        "wall_seconds": round((finished - started).total_seconds(), 1),
    }


def extract_rows(
    nanocursor_raw: Path,
    pi_raw: Path,
    audit: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for harness, root in (("nanoCursor", nanocursor_raw), ("Pi", pi_raw)):
        seen: set[tuple[str, int]] = set()
        for record in records_in(root):
            key = (str(record["taskId"]), int(record["trialIndex"]))
            if key in seen:
                continue
            seen.add(key)
            rows.append(public_row(harness, record, audit["byTask"][key[0]]))
        expected = {(task, trial) for task in TASK_ORDER for trial in (1, 2, 3)}
        missing = sorted(expected - seen)
        if missing:
            raise RuntimeError(f"{harness}: missing {len(missing)} task/trial records: {missing}")
    return sorted(rows, key=lambda row: (row["task_id"], row["harness"], row["trial"]))


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                {
                    **row,
                    "trial": int(row["trial"]),
                    "turns": int(row["turns"]),
                    "total_tokens": int(row["total_tokens"]),
                    "tool_calls": int(row["tool_calls"]),
                    "wall_seconds": float(row["wall_seconds"]),
                    "protocol_completed": row["protocol_completed"].lower() == "true",
                    "content_passed": row["content_passed"].lower() == "true",
                }
            )
    return rows


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {"schema_version": "nanocursor-public-results-v1", "harnesses": {}}
    for harness in ("nanoCursor", "Pi"):
        selected = [row for row in rows if row["harness"] == harness]
        output["harnesses"][harness] = {
            "runs": len(selected),
            "content_passed": sum(row["content_passed"] for row in selected),
            "protocol_completed": sum(row["protocol_completed"] for row in selected),
            "status_counts": dict(sorted(Counter(row["status"] for row in selected).items())),
            "turns": sum(row["turns"] for row in selected),
            "total_tokens": sum(row["total_tokens"] for row in selected),
            "tool_calls": sum(row["tool_calls"] for row in selected),
            "wall_seconds": round(sum(row["wall_seconds"] for row in selected), 1),
        }
    agreement = 0
    indexed = {(row["harness"], row["task_id"], row["trial"]): row for row in rows}
    for task in TASK_ORDER:
        for trial in (1, 2, 3):
            if indexed[("nanoCursor", task, trial)]["content_passed"] == indexed[("Pi", task, trial)][
                "content_passed"
            ]:
                agreement += 1
    output["nominal_functional_agreement"] = {"count": agreement, "total": 36, "percent": round(agreement / 36 * 100, 1)}
    return output


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg_document(width: int, height: int, body: str) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#ffffff"/>
<style>
text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; fill: #171717; }}
.title {{ font-size: 24px; font-weight: 700; }} .label {{ font-size: 13px; }} .value {{ font-size: 12px; font-weight: 650; }}
.axis {{ stroke: #d4d4d4; stroke-width: 1; }} .grid {{ stroke: #ececec; stroke-width: 1; }}
</style>
{body}</svg>
'''


def task_chart(rows: list[dict[str, Any]], path: Path) -> None:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        counts[(row["task_id"], row["harness"])] += int(row["content_passed"])
    width, height = 1180, 620
    left, top, chart_h = 70, 80, 400
    group_w = 84
    parts = ['<text x="70" y="42" class="title">Functional passes by task (3 trials each)</text>']
    for tick in range(4):
        y = top + chart_h - tick * chart_h / 3
        parts.append(f'<line x1="{left}" y1="{y}" x2="1110" y2="{y}" class="grid"/>')
        parts.append(f'<text x="48" y="{y + 5}" class="label">{tick}</text>')
    for index, task in enumerate(TASK_ORDER):
        x = left + index * group_w + 16
        short = task.removeprefix("issue-eval-")
        for offset, harness, color in ((0, "nanoCursor", "#171717"), (27, "Pi", "#a3a3a3")):
            value = counts[(task, harness)]
            bar_h = value * chart_h / 3
            parts.append(f'<rect x="{x + offset}" y="{top + chart_h - bar_h}" width="22" height="{bar_h}" rx="3" fill="{color}"/>')
            parts.append(f'<text x="{x + offset + 11}" y="{top + chart_h - bar_h - 7}" text-anchor="middle" class="value">{value}</text>')
        parts.append(f'<text x="{x + 24}" y="{top + chart_h + 22}" transform="rotate(45 {x + 24} {top + chart_h + 22})" class="label">{esc(short)}</text>')
    parts.extend([
        '<rect x="850" y="30" width="13" height="13" rx="2" fill="#171717"/><text x="870" y="42" class="label">nanoCursor</text>',
        '<rect x="960" y="30" width="13" height="13" rx="2" fill="#a3a3a3"/><text x="980" y="42" class="label">Pi reference</text>',
    ])
    path.write_text(svg_document(width, height, "\n".join(parts)), encoding="utf-8")


def outcome_chart(summary: dict[str, Any], path: Path) -> None:
    width, height = 820, 420
    parts = ['<text x="50" y="42" class="title">Evaluation outcomes across 36 runs</text>']
    metrics = (("Content accepted", "content_passed"), ("Protocol completed", "protocol_completed"))
    for group, (label, field) in enumerate(metrics):
        base_x = 240 + group * 290
        parts.append(f'<text x="{base_x + 70}" y="92" text-anchor="middle" class="label">{label}</text>')
        for index, (harness, color) in enumerate((("nanoCursor", "#171717"), ("Pi", "#a3a3a3"))):
            value = summary["harnesses"][harness][field]
            bar_h = value / 36 * 230
            x = base_x + index * 82
            parts.append(f'<rect x="{x}" y="{330 - bar_h}" width="54" height="{bar_h}" rx="5" fill="{color}"/>')
            parts.append(f'<text x="{x + 27}" y="{315 - bar_h}" text-anchor="middle" class="value">{value}/36</text>')
            parts.append(f'<text x="{x + 27}" y="354" text-anchor="middle" class="label">{harness}</text>')
    parts.append('<line x1="170" y1="330" x2="750" y2="330" class="axis"/>')
    path.write_text(svg_document(width, height, "\n".join(parts)), encoding="utf-8")


def cost_chart(summary: dict[str, Any], path: Path) -> None:
    width, height = 900, 420
    fields = (("Turns", "turns"), ("Tokens", "total_tokens"), ("Tool calls", "tool_calls"), ("Wall time", "wall_seconds"))
    parts = ['<text x="50" y="42" class="title">Execution cost (Pi reference = 100)</text>']
    for index, (label, field) in enumerate(fields):
        nano = summary["harnesses"]["nanoCursor"][field]
        pi = summary["harnesses"]["Pi"][field]
        ratio = nano / pi * 100
        y = 95 + index * 72
        parts.append(f'<text x="50" y="{y + 20}" class="label">{label}</text>')
        parts.append(f'<rect x="170" y="{y}" width="600" height="28" rx="5" fill="#eeeeee"/>')
        parts.append(f'<rect x="170" y="{y}" width="{600 * min(ratio, 110) / 110:.1f}" height="28" rx="5" fill="#171717"/>')
        parts.append(f'<text x="790" y="{y + 20}" class="value">{ratio:.1f}</text>')
    path.write_text(svg_document(width, height, "\n".join(parts)), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--nanocursor-raw", type=Path)
    parser.add_argument("--pi-raw", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "results")
    args = parser.parse_args()

    csv_path = args.output_dir / "data" / "runs.csv"
    if args.nanocursor_raw and args.pi_raw and args.audit:
        audit = json.loads(args.audit.read_text(encoding="utf-8"))
        rows = extract_rows(args.nanocursor_raw, args.pi_raw, audit)
        write_rows(csv_path, rows)
    else:
        rows = read_rows(csv_path)

    summary = aggregate(rows)
    summary_path = args.output_dir / "data" / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    figure_dir = args.output_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    task_chart(rows, figure_dir / "pass-rate-by-task.svg")
    outcome_chart(summary, figure_dir / "overall-outcomes.svg")
    cost_chart(summary, figure_dir / "cost-comparison.svg")
    print(f"wrote {len(rows)} public run records and 3 SVG charts to {args.output_dir}")


if __name__ == "__main__":
    main()
