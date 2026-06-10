"""Component ablation benchmark planning and scoring.

The first version is deliberately deterministic: it builds a baseline +
single-component-disable matrix and scores already-produced eval results. Real
runner integration can sit on top of this without changing the report format.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.api.services.ablation_config_service import (
    AblationConfig,
    make_ablation_config,
    normalize_component_name,
)
from src.api.services.eval_service import _evals_root, _workspace


COMPONENTS: list[dict[str, Any]] = [
    {
        "id": "agent_loop",
        "title": "Agent Loop",
        "description": "判断任务复杂度、选择直接回答或进入多步执行循环。",
        "primary_metrics": ["task_success_rate", "agent_noise_score", "avg_turn_count"],
    },
    {
        "id": "context_pack",
        "title": "Context Pack",
        "description": "为任务选择相关文件、偏好、记忆和最近变更。",
        "primary_metrics": ["context_hit_rate", "irrelevant_file_read_count", "avg_tool_calls"],
    },
    {
        "id": "project_index",
        "title": "Project Index",
        "description": "索引入口文件、源码目录、测试和配置，减少盲目搜索。",
        "primary_metrics": ["context_hit_rate", "avg_tool_calls"],
    },
    {
        "id": "memory_selection",
        "title": "Memory Selection",
        "description": "选择和当前任务相关的长期偏好与会话记忆。",
        "primary_metrics": ["memory_precision", "task_success_rate"],
    },
    {
        "id": "skills",
        "title": "Skills",
        "description": "为特定任务注入可复用流程和领域说明。",
        "primary_metrics": ["task_success_rate", "avg_turn_count"],
    },
    {
        "id": "mcp_tools",
        "title": "MCP Tools",
        "description": "接入外部工具能力，并在不可用时观察降级表现。",
        "primary_metrics": ["tool_execution_rate", "fallback_success_rate"],
    },
    {
        "id": "go_sidecars",
        "title": "Go Sidecars",
        "description": "Go indexer/filetools/executor/MCP gateway 等高并发或系统 I/O 辅助服务。",
        "primary_metrics": ["avg_duration_ms", "tool_execution_rate", "event_completeness"],
    },
    {
        "id": "failure_recovery",
        "title": "Failure Recovery",
        "description": "命令或测试失败后的分类、恢复计划、受控修复和验证重跑。",
        "primary_metrics": ["failure_recovery_rate", "retry_count", "task_success_rate"],
    },
]


class AblationVariant(BaseModel):
    """One benchmark variant in a baseline + single-disable matrix."""

    variant_id: str
    label: str
    disabled_components: list[str] = Field(default_factory=list)
    config: AblationConfig


class AblationSuite(BaseModel):
    """A serializable ablation suite definition."""

    suite_id: str = Field(default_factory=lambda: f"ablation_{uuid.uuid4().hex[:12]}")
    eval_ids: list[str]
    components: list[str]
    variants: list[AblationVariant]
    repetitions: int = 1
    mode: Literal["deterministic", "command_only", "agent"] = "deterministic"
    created_at: float = Field(default_factory=time.time)


def list_ablation_components() -> list[dict[str, Any]]:
    """Return supported component ids and report-facing descriptions."""

    return [dict(item) for item in COMPONENTS]


def build_ablation_matrix(
    eval_ids: list[str],
    components: list[str],
    *,
    include_baseline: bool = True,
    repetitions: int = 1,
    mode: Literal["deterministic", "command_only", "agent"] = "deterministic",
) -> dict[str, Any]:
    """Build a baseline + single-component-disable benchmark matrix."""

    cleaned_eval_ids = _unique_non_empty(eval_ids)
    cleaned_components = _known_components(components)
    if not cleaned_eval_ids:
        raise ValueError("ablation matrix requires at least one eval id")
    if not cleaned_components:
        raise ValueError("ablation matrix requires at least one known component")

    variants: list[AblationVariant] = []
    if include_baseline:
        variants.append(AblationVariant(
            variant_id="baseline",
            label="Baseline",
            disabled_components=[],
            config=make_ablation_config(variant_id="baseline"),
        ))
    for component in cleaned_components:
        variant_id = f"disable_{component}"
        variants.append(AblationVariant(
            variant_id=variant_id,
            label=f"Disable {component}",
            disabled_components=[component],
            config=make_ablation_config(variant_id=variant_id, disabled_components=[component]),
        ))

    suite = AblationSuite(
        eval_ids=cleaned_eval_ids,
        components=cleaned_components,
        variants=variants,
        repetitions=max(1, int(repetitions or 1)),
        mode=mode,
    )
    matrix = []
    for variant in variants:
        for eval_id in cleaned_eval_ids:
            for repetition in range(suite.repetitions):
                matrix.append({
                    "variant_id": variant.variant_id,
                    "eval_id": eval_id,
                    "repetition": repetition + 1,
                    "disabled_components": list(variant.disabled_components),
                    "config": {
                        **variant.config.model_dump(mode="json"),
                        "eval_id": eval_id,
                    },
                })
    return {
        "suite": suite.model_dump(mode="json"),
        "matrix": matrix,
        "summary": {
            "eval_count": len(cleaned_eval_ids),
            "component_count": len(cleaned_components),
            "variant_count": len(variants),
            "run_count": len(matrix),
        },
    }


def build_component_necessity_report(suite_result: dict[str, Any]) -> dict[str, Any]:
    """Score component contribution from a completed ablation suite result."""

    suite = suite_result.get("suite") if isinstance(suite_result.get("suite"), dict) else {}
    results = suite_result.get("results") if isinstance(suite_result.get("results"), list) else []
    by_variant = _aggregate_variant_scores(results)
    baseline = by_variant.get("baseline", {"score": 0.0, "count": 0})

    components = suite.get("components") if isinstance(suite.get("components"), list) else []
    component_reports = []
    for component in components:
        component_id = normalize_component_name(str(component))
        disabled = by_variant.get(f"disable_{component_id}", {"score": 0.0, "count": 0})
        lift = round(float(baseline["score"]) - float(disabled["score"]), 3)
        component_reports.append({
            "component": component_id,
            "baseline_score": round(float(baseline["score"]), 3),
            "disabled_score": round(float(disabled["score"]), 3),
            "lift": lift,
            "verdict": _component_verdict(lift, int(disabled.get("count") or 0)),
            "evidence": {
                "baseline_runs": int(baseline.get("count") or 0),
                "disabled_runs": int(disabled.get("count") or 0),
                "baseline_pass_rate": baseline.get("pass_rate"),
                "disabled_pass_rate": disabled.get("pass_rate"),
            },
        })

    return {
        "suite_id": suite.get("suite_id") or suite_result.get("suite_id"),
        "generated_at": time.time(),
        "baseline": baseline,
        "components": component_reports,
        "summary": {
            "necessary": sum(1 for item in component_reports if item["verdict"] == "necessary"),
            "useful": sum(1 for item in component_reports if item["verdict"] == "useful"),
            "neutral": sum(1 for item in component_reports if item["verdict"] == "neutral"),
            "negative": sum(1 for item in component_reports if item["verdict"] == "negative"),
        },
    }


def run_ablation_suite(
    workspace_dir: str | None,
    eval_ids: list[str],
    components: list[str],
    *,
    repetitions: int = 1,
    mode: Literal["deterministic", "command_only", "agent"] = "deterministic",
    persist: bool = True,
) -> dict[str, Any]:
    """Run a baseline + single-disable ablation suite through the eval runner.

    The current runtime does not yet make every component flag influence the
    Agent Loop. This runner still records the config per variant so later
    integration can make the same suite contract actually disable components
    without changing callers or report artifacts.
    """

    from src.api.services.eval_runner_service import run_eval_with_command
    from src.api.services.eval_service import run_eval

    matrix_result = build_ablation_matrix(
        eval_ids,
        components,
        include_baseline=True,
        repetitions=repetitions,
        mode=mode,
    )
    workspace = str(_workspace(workspace_dir))
    results: list[dict[str, Any]] = []
    for row in matrix_result["matrix"]:
        started_at = time.time()
        eval_id = str(row["eval_id"])
        variant_id = str(row["variant_id"])
        config = dict(row.get("config") or {})
        config["run_id"] = f"{variant_id}:{eval_id}:{row.get('repetition')}"
        try:
            if mode == "deterministic":
                eval_result = run_eval(eval_id, workspace)
            else:
                eval_result = run_eval_with_command(eval_id, workspace, mode=mode)
            status = "completed"
            error = ""
        except Exception as exc:
            eval_result = {
                "eval_id": eval_id,
                "score": {"overall": "error", "error": str(exc)},
                "error": str(exc),
            }
            status = "error"
            error = str(exc)
        results.append({
            "variant_id": variant_id,
            "eval_id": eval_id,
            "repetition": row.get("repetition"),
            "disabled_components": row.get("disabled_components", []),
            "ablation_config": config,
            "status": status,
            "error": error,
            "duration_ms": int((time.time() - started_at) * 1000),
            "score": eval_result.get("score"),
            "eval_result": eval_result,
        })

    suite_result = {
        "suite": matrix_result["suite"],
        "matrix": matrix_result["matrix"],
        "results": results,
        "summary": {
            **matrix_result["summary"],
            "completed": sum(1 for item in results if item.get("status") == "completed"),
            "errors": sum(1 for item in results if item.get("status") == "error"),
        },
    }
    report = build_component_necessity_report(suite_result)
    suite_result["report"] = report
    if persist:
        suite_result["artifacts"] = save_ablation_artifacts(workspace, suite_result)
    return suite_result


def save_ablation_artifacts(workspace_dir: str | None, suite_result: dict[str, Any]) -> dict[str, Any]:
    """Persist suite, matrix, results, and report under `.nanocursor/evals/ablation`."""

    workspace = _workspace(workspace_dir)
    suite = suite_result.get("suite") if isinstance(suite_result.get("suite"), dict) else {}
    suite_id = str(suite.get("suite_id") or suite_result.get("suite_id") or f"ablation_{uuid.uuid4().hex[:12]}")
    root = _evals_root(workspace) / "ablation" / suite_id
    root.mkdir(parents=True, exist_ok=True)

    report = build_component_necessity_report(suite_result)
    artifacts = {
        "suite": root / "suite.json",
        "matrix": root / "matrix.json",
        "results": root / "results.json",
        "report": root / "report.json",
        "report_md": root / "report.md",
    }
    _write_json(artifacts["suite"], suite)
    _write_json(artifacts["matrix"], suite_result.get("matrix", []))
    _write_json(artifacts["results"], suite_result.get("results", []))
    _write_json(artifacts["report"], report)
    artifacts["report_md"].write_text(_report_markdown(report), encoding="utf-8")
    return {
        "suite_id": suite_id,
        "root": str(root),
        "artifacts": {name: str(path) for name, path in artifacts.items()},
        "report": report,
    }


def _known_components(components: list[str]) -> list[str]:
    known = {item["id"] for item in COMPONENTS}
    return [item for item in _unique_non_empty(components) if item in known]


def _unique_non_empty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        item = normalize_component_name(str(value))
        if item and item not in seen:
            seen.add(item)
            cleaned.append(item)
    return cleaned


def _aggregate_variant_scores(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[float]] = {}
    pass_grouped: dict[str, list[bool]] = {}
    for result in results:
        variant_id = str(result.get("variant_id") or "baseline")
        grouped.setdefault(variant_id, []).append(_result_score(result))
        pass_grouped.setdefault(variant_id, []).append(_result_passed(result))

    aggregate: dict[str, dict[str, Any]] = {}
    for variant_id, scores in grouped.items():
        passes = pass_grouped.get(variant_id, [])
        aggregate[variant_id] = {
            "variant_id": variant_id,
            "score": round(sum(scores) / len(scores), 3) if scores else 0.0,
            "count": len(scores),
            "pass_rate": round(sum(1 for item in passes if item) / len(passes), 3) if passes else None,
        }
    return aggregate


def _result_score(result: dict[str, Any]) -> float:
    if isinstance(result.get("score"), int | float):
        return float(result["score"])
    score = result.get("score")
    if isinstance(score, dict):
        if isinstance(score.get("score"), int | float):
            return float(score["score"])
        overall = score.get("overall")
        if overall == "passed":
            return 1.0
        if overall in {"failed", "error"}:
            return 0.0
    if isinstance(result.get("pass_rate"), int | float):
        return float(result["pass_rate"])
    if isinstance(result.get("summary"), dict) and isinstance(result["summary"].get("pass_rate"), int | float):
        return float(result["summary"]["pass_rate"])
    return 0.0


def _result_passed(result: dict[str, Any]) -> bool:
    score = result.get("score")
    if isinstance(score, dict):
        return score.get("overall") == "passed"
    if isinstance(score, int | float):
        return float(score) >= 0.8
    if isinstance(result.get("passed"), bool):
        return bool(result["passed"])
    return _result_score(result) >= 0.8


def _component_verdict(lift: float, disabled_count: int) -> str:
    if disabled_count <= 0:
        return "insufficient_data"
    if lift >= 0.15:
        return "necessary"
    if lift >= 0.05:
        return "useful"
    if lift <= -0.05:
        return "negative"
    return "neutral"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _report_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Ablation Report: {report.get('suite_id') or 'unknown'}",
        "",
        "| Component | Baseline | Disabled | Lift | Verdict |",
        "|---|---:|---:|---:|---|",
    ]
    for item in report.get("components", []):
        lines.append(
            f"| {item['component']} | {item['baseline_score']} | {item['disabled_score']} | {item['lift']} | {item['verdict']} |"
        )
    return "\n".join(lines) + "\n"
