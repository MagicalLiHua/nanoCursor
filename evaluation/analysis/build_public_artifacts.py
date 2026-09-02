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
import statistics
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

TASK_LABELS = {
    "issue-eval-astropy-12907": "astro-12907",
    "issue-eval-astropy-13453": "astro-13453",
    "issue-eval-django-11133": "django-11133",
    "issue-eval-django-11141": "django-11141",
    "issue-eval-matplotlib-23412": "mpl-23412",
    "issue-eval-pytest-8399": "pytest-8399",
    "issue-eval-requests-1142": "requests-1142",
    "issue-eval-sklearn-13142": "skl-13142",
    "issue-eval-sklearn-13328": "skl-13328",
    "issue-eval-sphinx-10449": "sphinx-10449",
    "issue-eval-sympy-11618": "sympy-11618",
    "issue-eval-xarray-3677": "xarray-3677",
}

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
    output: dict[str, Any] = {"schema_version": "nanocursor-public-results-v2", "harnesses": {}}
    for harness in ("nanoCursor", "Pi"):
        selected = [row for row in rows if row["harness"] == harness]
        distributions = {}
        for field in ("turns", "total_tokens", "tool_calls", "wall_seconds"):
            values = [float(row[field]) for row in selected]
            distributions[field] = {
                "mean": round(statistics.mean(values), 1),
                "median": round(statistics.median(values), 1),
                "standard_deviation": round(statistics.stdev(values), 1),
                "minimum": round(min(values), 1),
                "maximum": round(max(values), 1),
            }
        output["harnesses"][harness] = {
            "runs": len(selected),
            "content_passed": sum(row["content_passed"] for row in selected),
            "protocol_completed": sum(row["protocol_completed"] for row in selected),
            "status_counts": dict(sorted(Counter(row["status"] for row in selected).items())),
            "turns": sum(row["turns"] for row in selected),
            "total_tokens": sum(row["total_tokens"] for row in selected),
            "tool_calls": sum(row["tool_calls"] for row in selected),
            "wall_seconds": round(sum(row["wall_seconds"] for row in selected), 1),
            "distribution": distributions,
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
    output["per_task"] = {}
    for task in TASK_ORDER:
        output["per_task"][task] = {}
        for harness in ("nanoCursor", "Pi"):
            selected = [row for row in rows if row["task_id"] == task and row["harness"] == harness]
            output["per_task"][task][harness] = {
                "content_passed": sum(row["content_passed"] for row in selected),
                "protocol_completed": sum(row["protocol_completed"] for row in selected),
                "mean_turns": round(statistics.mean(row["turns"] for row in selected), 1),
                "mean_total_tokens": round(statistics.mean(row["total_tokens"] for row in selected), 1),
                "mean_tool_calls": round(statistics.mean(row["tool_calls"] for row in selected), 1),
                "mean_wall_seconds": round(statistics.mean(row["wall_seconds"] for row in selected), 1),
            }
    return output


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg_document(width: int, height: int, body: str) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#ffffff"/>
<style>
text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; fill: #0f172a; }}
.title {{ font-size: 24px; font-weight: 700; }} .subtitle {{ font-size: 13px; fill: #737373; }}
.label {{ font-size: 13px; }} .small {{ font-size: 11px; fill: #737373; }} .value {{ font-size: 12px; font-weight: 650; }}
.axis {{ stroke: #cbd5e1; stroke-width: 1; }} .grid {{ stroke: #e2e8f0; stroke-width: 1; }}
</style>
{body}</svg>
'''


COLORS = {"nanoCursor": "#2563eb", "Pi": "#f97316"}


def short_task(task: str) -> str:
    return TASK_LABELS.get(task, task.removeprefix("issue-eval-"))


def line_path(points: list[tuple[float, float]]) -> str:
    return " ".join(("M" if index == 0 else "L") + f" {x:.1f} {y:.1f}" for index, (x, y) in enumerate(points))


def legend(parts: list[str], x: int, y: int) -> None:
    parts.extend([
        f'<line x1="{x}" y1="{y}" x2="{x + 22}" y2="{y}" stroke="{COLORS["nanoCursor"]}" stroke-width="3"/><circle cx="{x + 11}" cy="{y}" r="3" fill="{COLORS["nanoCursor"]}"/><text x="{x + 30}" y="{y + 5}" class="label">nanoCursor</text>',
        f'<line x1="{x + 125}" y1="{y}" x2="{x + 147}" y2="{y}" stroke="{COLORS["Pi"]}" stroke-width="3"/><circle cx="{x + 136}" cy="{y}" r="3" fill="{COLORS["Pi"]}"/><text x="{x + 155}" y="{y + 5}" class="label">Pi reference</text>',
    ])


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
        for offset, harness, color in ((0, "nanoCursor", COLORS["nanoCursor"]), (27, "Pi", COLORS["Pi"])):
            value = counts[(task, harness)]
            bar_h = value * chart_h / 3
            parts.append(f'<rect x="{x + offset}" y="{top + chart_h - bar_h}" width="22" height="{bar_h}" rx="3" fill="{color}"/>')
            parts.append(f'<text x="{x + offset + 11}" y="{top + chart_h - bar_h - 7}" text-anchor="middle" class="value">{value}</text>')
        parts.append(f'<text x="{x + 24}" y="{top + chart_h + 22}" transform="rotate(45 {x + 24} {top + chart_h + 22})" class="label">{esc(short)}</text>')
    parts.extend([
        f'<rect x="850" y="30" width="13" height="13" rx="2" fill="{COLORS["nanoCursor"]}"/><text x="870" y="42" class="label">nanoCursor</text>',
        f'<rect x="960" y="30" width="13" height="13" rx="2" fill="{COLORS["Pi"]}"/><text x="980" y="42" class="label">Pi reference</text>',
    ])
    path.write_text(svg_document(width, height, "\n".join(parts)), encoding="utf-8")


def outcome_chart(summary: dict[str, Any], path: Path) -> None:
    width, height = 820, 420
    parts = ['<text x="50" y="42" class="title">Evaluation outcomes across 36 runs</text>']
    metrics = (("Content accepted", "content_passed"), ("Protocol completed", "protocol_completed"))
    for group, (label, field) in enumerate(metrics):
        base_x = 240 + group * 290
        parts.append(f'<text x="{base_x + 70}" y="92" text-anchor="middle" class="label">{label}</text>')
        for index, (harness, color) in enumerate((("nanoCursor", COLORS["nanoCursor"]), ("Pi", COLORS["Pi"]))):
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
        parts.append(f'<rect x="170" y="{y}" width="600" height="28" rx="5" fill="#eff6ff"/>')
        parts.append(f'<rect x="170" y="{y}" width="{600 * min(ratio, 110) / 110:.1f}" height="28" rx="5" fill="{COLORS["nanoCursor"]}"/>')
        reference_x = 170 + 600 * 100 / 110
        parts.append(f'<line x1="{reference_x:.1f}" y1="{y - 4}" x2="{reference_x:.1f}" y2="{y + 32}" stroke="{COLORS["Pi"]}" stroke-width="3"/>')
        parts.append(f'<text x="790" y="{y + 20}" class="value">{ratio:.1f}</text>')
    path.write_text(svg_document(width, height, "\n".join(parts)), encoding="utf-8")


def task_metric_profiles(rows: list[dict[str, Any]], path: Path) -> None:
    """Four line panels: mean execution cost for every task and harness."""
    width, height = 1280, 900
    metrics = (
        ("Mean turns", "turns", "turns"),
        ("Mean total tokens", "total_tokens", "tokens"),
        ("Mean tool calls", "tool_calls", "calls"),
        ("Mean wall time", "wall_seconds", "seconds"),
    )
    means: dict[tuple[str, str, str], float] = {}
    for task in TASK_ORDER:
        for harness in ("nanoCursor", "Pi"):
            selected = [row for row in rows if row["task_id"] == task and row["harness"] == harness]
            for _, field, _ in metrics:
                means[(task, harness, field)] = statistics.mean(float(row[field]) for row in selected)

    parts = ['<text x="55" y="40" class="title">Per-task execution profiles</text>',
             '<text x="55" y="64" class="subtitle">Each point is the mean of three runs; identical task order is used in all panels.</text>']
    legend(parts, 920, 48)
    for index, (title, field, unit) in enumerate(metrics):
        col, row_index = index % 2, index // 2
        x0, y0 = 70 + col * 620, 120 + row_index * 370
        plot_left, plot_top, plot_w, plot_h = x0 + 54, y0 + 40, 485, 205
        values = [means[(task, harness, field)] for task in TASK_ORDER for harness in ("nanoCursor", "Pi")]
        max_v = max(values) * 1.08
        parts.append(f'<text x="{x0}" y="{y0 + 12}" class="value">{title}</text>')
        for tick in range(5):
            value = max_v * tick / 4
            y = plot_top + plot_h - plot_h * tick / 4
            parts.append(f'<line x1="{plot_left}" y1="{y:.1f}" x2="{plot_left + plot_w}" y2="{y:.1f}" class="grid"/>')
            label = f"{value / 1000:.0f}k" if field == "total_tokens" else f"{value:.0f}"
            parts.append(f'<text x="{plot_left - 9}" y="{y + 4:.1f}" text-anchor="end" class="small">{label}</text>')
        for harness in ("nanoCursor", "Pi"):
            points = []
            for task_index, task in enumerate(TASK_ORDER):
                x = plot_left + task_index * plot_w / (len(TASK_ORDER) - 1)
                y = plot_top + plot_h - means[(task, harness, field)] / max_v * plot_h
                points.append((x, y))
            parts.append(f'<path d="{line_path(points)}" fill="none" stroke="{COLORS[harness]}" stroke-width="2.5" stroke-linejoin="round"/>')
            for x, y in points:
                parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{COLORS[harness]}"/>')
        for task_index, task in enumerate(TASK_ORDER):
            x = plot_left + task_index * plot_w / (len(TASK_ORDER) - 1)
            label = short_task(task).rsplit("-", 1)[-1]
            parts.append(f'<text x="{x:.1f}" y="{plot_top + plot_h + 17}" text-anchor="middle" class="small">{esc(label)}</text>')
        parts.append(f'<text x="{plot_left}" y="{plot_top + plot_h + 38}" class="small">unit: {unit}</text>')
    path.write_text(svg_document(width, height, "\n".join(parts)), encoding="utf-8")


def trial_token_lines(rows: list[dict[str, Any]], path: Path) -> None:
    """Show the actual 36-run token trajectory instead of hiding variance in totals."""
    width, height = 1320, 570
    left, top, plot_w, plot_h = 75, 95, 1175, 340
    indexed = {(row["harness"], row["task_id"], row["trial"]): row for row in rows}
    order = [(task, trial) for task in TASK_ORDER for trial in (1, 2, 3)]
    max_v = max(float(row["total_tokens"]) for row in rows) * 1.05
    parts = ['<text x="55" y="40" class="title">Token use across all 36 runs</text>',
             '<text x="55" y="64" class="subtitle">Three adjacent points belong to the same task. Peaks reveal trial-level path divergence.</text>']
    legend(parts, 950, 48)
    for tick in range(5):
        value = max_v * tick / 4
        y = top + plot_h - plot_h * tick / 4
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" class="small">{value / 1000:.0f}k</text>')
    for task_index in range(1, len(TASK_ORDER)):
        x = left + (task_index * 3 - 0.5) * plot_w / (len(order) - 1)
        parts.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h}" stroke="#cbd5e1" stroke-dasharray="3 4"/>')
    for harness in ("nanoCursor", "Pi"):
        points = []
        for index, (task, trial) in enumerate(order):
            x = left + index * plot_w / (len(order) - 1)
            value = float(indexed[(harness, task, trial)]["total_tokens"])
            y = top + plot_h - value / max_v * plot_h
            points.append((x, y))
        parts.append(f'<path d="{line_path(points)}" fill="none" stroke="{COLORS[harness]}" stroke-width="2.2" stroke-linejoin="round"/>')
        for x, y in points:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{COLORS[harness]}"/>')
    for task_index, task in enumerate(TASK_ORDER):
        x = left + (task_index * 3 + 1) * plot_w / (len(order) - 1)
        parts.append(f'<text x="{x:.1f}" y="{top + plot_h + 22}" text-anchor="middle" transform="rotate(35 {x:.1f} {top + plot_h + 22})" class="small">{esc(short_task(task))}</text>')
    path.write_text(svg_document(width, height, "\n".join(parts)), encoding="utf-8")


def variability_chart(rows: list[dict[str, Any]], path: Path) -> None:
    """Min/median/max tokens across the three trials for each task."""
    width, height = 1280, 690
    left, top, plot_w, plot_h = 155, 92, 1030, 500
    max_v = max(float(row["total_tokens"]) for row in rows) * 1.05
    parts = ['<text x="55" y="40" class="title">Within-task token variability</text>',
             '<text x="55" y="64" class="subtitle">Vertical ranges show min–max; the dot is the median of three runs.</text>']
    legend(parts, 910, 48)
    for tick in range(5):
        value = max_v * tick / 4
        x = left + plot_w * tick / 4
        parts.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h}" class="grid"/>')
        parts.append(f'<text x="{x:.1f}" y="{top + plot_h + 24}" text-anchor="middle" class="small">{value / 1000:.0f}k</text>')
    for task_index, task in enumerate(TASK_ORDER):
        y_center = top + (task_index + 0.5) * plot_h / len(TASK_ORDER)
        parts.append(f'<text x="{left - 12}" y="{y_center + 4:.1f}" text-anchor="end" class="small">{esc(short_task(task))}</text>')
        for offset, harness in ((-7, "nanoCursor"), (7, "Pi")):
            values = sorted(float(row["total_tokens"]) for row in rows if row["task_id"] == task and row["harness"] == harness)
            xs = [left + value / max_v * plot_w for value in values]
            y = y_center + offset
            parts.append(f'<line x1="{xs[0]:.1f}" y1="{y:.1f}" x2="{xs[-1]:.1f}" y2="{y:.1f}" stroke="{COLORS[harness]}" stroke-width="3" opacity="0.75"/>')
            parts.append(f'<circle cx="{xs[1]:.1f}" cy="{y:.1f}" r="4" fill="{COLORS[harness]}"/>')
    path.write_text(svg_document(width, height, "\n".join(parts)), encoding="utf-8")


def agreement_chart(rows: list[dict[str, Any]], path: Path) -> None:
    indexed = {(row["harness"], row["task_id"], row["trial"]): row for row in rows}
    cells = Counter()
    for task in TASK_ORDER:
        for trial in (1, 2, 3):
            nano = indexed[("nanoCursor", task, trial)]["content_passed"]
            pi = indexed[("Pi", task, trial)]["content_passed"]
            cells[(nano, pi)] += 1
    width, height = 720, 540
    x0, y0, size = 220, 120, 135
    parts = ['<text x="45" y="42" class="title">Nominal functional agreement</text>',
             '<text x="45" y="66" class="subtitle">Same task and trial index; descriptive pairing, not shared randomness.</text>',
             f'<text x="{x0 + size}" y="100" text-anchor="middle" class="value">Pi result</text>',
             f'<text x="82" y="{y0 + size}" text-anchor="middle" transform="rotate(-90 82 {y0 + size})" class="value">nanoCursor result</text>']
    labels = [(False, "Fail"), (True, "Pass")]
    for col, (pi_value, label) in enumerate(labels):
        parts.append(f'<text x="{x0 + col * size + size/2}" y="{y0 - 12}" text-anchor="middle" class="label">{label}</text>')
    for row_index, (nano_value, label) in enumerate(reversed(labels)):
        parts.append(f'<text x="{x0 - 18}" y="{y0 + row_index * size + size/2 + 5}" text-anchor="end" class="label">{label}</text>')
        for col, (pi_value, _) in enumerate(labels):
            value = cells[(nano_value, pi_value)]
            if nano_value and pi_value:
                shade, text_color = "#16a34a", "#ffffff"
            elif not nano_value and not pi_value:
                shade, text_color = "#475569", "#ffffff"
            elif nano_value:
                shade, text_color = "#7c3aed", "#ffffff"
            else:
                shade, text_color = "#f59e0b", "#0f172a"
            x, y = x0 + col * size, y0 + row_index * size
            parts.append(f'<rect x="{x}" y="{y}" width="{size - 4}" height="{size - 4}" rx="8" fill="{shade}"/>')
            parts.append(f'<text x="{x + (size-4)/2}" y="{y + size/2 + 8}" text-anchor="middle" style="font-size:28px;font-weight:700;fill:{text_color}">{value}</text>')
    parts.append('<text x="220" y="435" class="label">Agreement: 35/36 (97.2%)</text>')
    parts.append('<text x="220" y="461" class="small">32 both pass · 3 both fail · 1 Pi-only pass · 0 nanoCursor-only pass</text>')
    path.write_text(svg_document(width, height, "\n".join(parts)), encoding="utf-8")


def success_cost_chart(rows: list[dict[str, Any]], path: Path) -> None:
    """Scatter plot keeps outliers and success state visible."""
    width, height = 940, 600
    left, top, plot_w, plot_h = 85, 95, 785, 410
    max_tokens = max(float(row["total_tokens"]) for row in rows) * 1.05
    max_turns = max(float(row["turns"]) for row in rows) * 1.05
    parts = ['<text x="45" y="40" class="title">Run cost and outcome</text>',
             '<text x="45" y="64" class="subtitle">Filled points passed the code grader; hollow points failed. The top boundary is the 96-turn budget.</text>']
    legend(parts, 620, 48)
    for tick in range(5):
        tx = max_tokens * tick / 4
        x = left + plot_w * tick / 4
        y = top + plot_h - plot_h * tick / 4
        parts.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h}" class="grid"/>')
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" class="grid"/>')
        parts.append(f'<text x="{x:.1f}" y="{top + plot_h + 23}" text-anchor="middle" class="small">{tx/1000:.0f}k</text>')
        parts.append(f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" class="small">{max_turns*tick/4:.0f}</text>')
    for row in rows:
        x = left + float(row["total_tokens"]) / max_tokens * plot_w
        y = top + plot_h - float(row["turns"]) / max_turns * plot_h
        color = COLORS[row["harness"]]
        fill = color if row["content_passed"] else "#ffffff"
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{fill}" stroke="{color}" stroke-width="2" opacity="0.88"/>')
    parts.append(f'<text x="{left + plot_w/2}" y="565" text-anchor="middle" class="label">total tokens per run</text>')
    parts.append(f'<text x="28" y="{top + plot_h/2}" text-anchor="middle" transform="rotate(-90 28 {top + plot_h/2})" class="label">turns per run</text>')
    path.write_text(svg_document(width, height, "\n".join(parts)), encoding="utf-8")


def efficiency_chart(rows: list[dict[str, Any]], path: Path) -> None:
    width, height = 930, 500
    computed = {}
    for harness in ("nanoCursor", "Pi"):
        selected = [row for row in rows if row["harness"] == harness]
        computed[harness] = {
            "Tokens / turn": sum(row["total_tokens"] for row in selected) / sum(row["turns"] for row in selected),
            "Tool calls / turn": sum(row["tool_calls"] for row in selected) / sum(row["turns"] for row in selected),
            "Seconds / turn": sum(row["wall_seconds"] for row in selected) / sum(row["turns"] for row in selected),
        }
    parts = ['<text x="45" y="42" class="title">Execution intensity</text>',
             '<text x="45" y="66" class="subtitle">Normalized ratios show what happened inside each turn, not just total run cost.</text>']
    metrics = list(computed["nanoCursor"])
    for index, metric in enumerate(metrics):
        y = 120 + index * 105
        max_v = max(computed[h][metric] for h in computed) * 1.12
        parts.append(f'<text x="45" y="{y + 18}" class="label">{metric}</text>')
        for h_index, harness in enumerate(("nanoCursor", "Pi")):
            bar_y = y + h_index * 34
            value = computed[harness][metric]
            width_bar = value / max_v * 570
            parts.append(f'<rect x="220" y="{bar_y}" width="{width_bar:.1f}" height="24" rx="4" fill="{COLORS[harness]}"/>')
            parts.append(f'<text x="{230 + width_bar:.1f}" y="{bar_y + 17}" class="value">{value:,.2f}</text>')
            parts.append(f'<text x="205" y="{bar_y + 17}" text-anchor="end" class="small">{harness}</text>')
    path.write_text(svg_document(width, height, "\n".join(parts)), encoding="utf-8")


def evidence_charts(evidence: dict[str, Any], figure_dir: Path) -> None:
    """Charts for safe aggregate fields not present in the public per-run CSV."""
    aggregate = evidence["aggregate"]
    width, height = 820, 430
    parts = ['<text x="45" y="42" class="title">Input and output token composition</text>',
             '<text x="45" y="66" class="subtitle">Aggregate totals across 36 runs for each harness.</text>']
    max_total = max(aggregate["nanoCursor"]["totalTokens"], aggregate["pi"]["totalTokens"])
    for index, (label, key) in enumerate((("nanoCursor", "nanoCursor"), ("Pi reference", "pi"))):
        y = 125 + index * 110
        item = aggregate[key]
        x, full_w = 180, 530
        input_w = item["inputTokens"] / max_total * full_w
        output_w = item["outputTokens"] / max_total * full_w
        parts.append(f'<text x="155" y="{y + 24}" text-anchor="end" class="label">{label}</text>')
        parts.append(f'<rect x="{x}" y="{y}" width="{input_w:.1f}" height="38" rx="5" fill="#4f46e5"/>')
        parts.append(f'<rect x="{x + input_w:.1f}" y="{y}" width="{output_w:.1f}" height="38" rx="5" fill="#06b6d4"/>')
        parts.append(f'<text x="{x + input_w/2:.1f}" y="{y + 24}" text-anchor="middle" style="font-size:11px;fill:#fff">input {item["inputTokens"]/1000:.0f}k</text>')
        parts.append(f'<text x="{x + input_w + output_w/2:.1f}" y="{y + 24}" text-anchor="middle" style="font-size:11px;fill:#fff">output {item["outputTokens"]/1000:.0f}k</text>')
    parts.append('<rect x="180" y="350" width="13" height="13" fill="#4f46e5"/><text x="201" y="361" class="small">input tokens</text>')
    parts.append('<rect x="310" y="350" width="13" height="13" fill="#06b6d4"/><text x="331" y="361" class="small">output tokens</text>')
    (figure_dir / "token-composition.svg").write_text(svg_document(width, height, "\n".join(parts)), encoding="utf-8")

    contract = evidence["toolContract"]
    categories = contract["comparableCategories"]
    category_labels = {
        "inline-python": "inline Python",
        "protected-test-edit": "protected test edit",
        "command-allowlist": "command allowlist",
        "project-command-allowlist": "project command allowlist",
    }
    width, height = 940, 500
    parts = ['<text x="45" y="42" class="title">Comparable tool-policy rejections</text>',
             '<text x="45" y="66" class="subtitle">Only categories encoded consistently by both adapters are compared.</text>']
    max_v = max(contract["nanoCursor"]["categories"].get(c, 0) for c in categories)
    max_v = max(max_v, max(contract["pi"]["categories"].get(c, 0) for c in categories)) * 1.1
    for index, category in enumerate(categories):
        y = 115 + index * 82
        parts.append(f'<text x="190" y="{y + 22}" text-anchor="end" class="label">{category_labels.get(category, category)}</text>')
        for h_index, (harness, key) in enumerate((("nanoCursor", "nanoCursor"), ("Pi", "pi"))):
            value = contract[key]["categories"].get(category, 0)
            bar_y = y + h_index * 29
            bar_w = value / max_v * 590 if max_v else 0
            parts.append(f'<rect x="210" y="{bar_y}" width="{bar_w:.1f}" height="21" rx="3" fill="{COLORS[harness]}"/>')
            parts.append(f'<text x="{218 + bar_w:.1f}" y="{bar_y + 15}" class="value">{value}</text>')
    parts.append(f'<text x="210" y="464" class="small">Comparable rejection rate: nanoCursor {contract["nanoCursor"]["comparableRejectionRatePercent"]:.2f}% · Pi {contract["pi"]["comparableRejectionRatePercent"]:.2f}%</text>')
    (figure_dir / "tool-policy-rejections.svg").write_text(svg_document(width, height, "\n".join(parts)), encoding="utf-8")


def pipeline_diagram(path: Path) -> None:
    width, height = 1180, 360
    boxes = (
        (45, "Frozen task", "issue + base commit", "tests + hashes", "#eff6ff", "#60a5fa"),
        (270, "Harness adapter", "nanoCursor / Pi", "same prompt & budget", "#eef2ff", "#818cf8"),
        (495, "Docker sandbox", "2 CPU · 4 GiB", "network disabled", "#ecfeff", "#22d3ee"),
        (720, "Deterministic grader", "target + regression", "protected-file checks", "#f0fdf4", "#4ade80"),
        (945, "Run artifacts", "status + metrics", "trace + attribution", "#fff7ed", "#fb923c"),
    )
    parts = ['<text x="45" y="42" class="title">Controlled evaluation pipeline</text>',
             '<text x="45" y="66" class="subtitle">The harness changes; task assets, model, budget, sandbox and grader remain fixed.</text>']
    for index, (x, title, line1, line2, fill, stroke) in enumerate(boxes):
        parts.append(f'<rect x="{x}" y="120" width="185" height="125" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
        parts.append(f'<rect x="{x}" y="120" width="185" height="7" rx="4" fill="{stroke}"/>')
        parts.append(f'<text x="{x + 92.5}" y="153" text-anchor="middle" class="value">{esc(title)}</text>')
        parts.append(f'<text x="{x + 92.5}" y="183" text-anchor="middle" class="small">{esc(line1)}</text>')
        parts.append(f'<text x="{x + 92.5}" y="207" text-anchor="middle" class="small">{esc(line2)}</text>')
        if index < len(boxes) - 1:
            parts.append(f'<line x1="{x + 185}" y1="182" x2="{x + 218}" y2="182" stroke="#2563eb" stroke-width="2"/>')
            parts.append(f'<path d="M {x + 212} 176 L {x + 220} 182 L {x + 212} 188" fill="none" stroke="#2563eb" stroke-width="2"/>')
    parts.append('<text x="45" y="304" class="label">2 harnesses × 12 tasks × 3 trials = 72 recorded runs</text>')
    parts.append('<text x="45" y="330" class="small">Two result layers are retained: code acceptance and Agent protocol completion.</text>')
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
    task_metric_profiles(rows, figure_dir / "task-metric-profiles.svg")
    trial_token_lines(rows, figure_dir / "trial-token-lines.svg")
    variability_chart(rows, figure_dir / "token-variability.svg")
    agreement_chart(rows, figure_dir / "functional-agreement.svg")
    success_cost_chart(rows, figure_dir / "success-cost-scatter.svg")
    efficiency_chart(rows, figure_dir / "execution-intensity.svg")
    pipeline_diagram(figure_dir / "evaluation-pipeline.svg")
    evidence_path = args.output_dir / "data" / "attribution-evidence.json"
    if evidence_path.exists():
        evidence_charts(json.loads(evidence_path.read_text(encoding="utf-8")), figure_dir)
    chart_count = 12 if evidence_path.exists() else 10
    print(f"wrote {len(rows)} public run records and {chart_count} SVG charts to {args.output_dir}")


if __name__ == "__main__":
    main()
