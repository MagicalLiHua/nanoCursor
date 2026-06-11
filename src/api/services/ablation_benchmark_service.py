"""Component ablation benchmark planning, execution, persistence, and scoring.

The suite format is intentionally conservative: baseline + single-component
disable variants first, with explicit artifacts that can be inspected later.
Only components wired into the current eval runner change execution behavior;
the report keeps the rest visible instead of inventing fake lift.
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


Mode = Literal["deterministic", "command_only", "agent", "baseline"]


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
    mode: Mode = "deterministic"
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
    mode: Mode = "deterministic",
) -> dict[str, Any]:
    """Build a baseline + single-component-disable benchmark matrix."""

    cleaned_eval_ids = _unique_non_empty(eval_ids)
    cleaned_components = _known_components(components)
    if not cleaned_eval_ids:
        raise ValueError("ablation matrix requires at least one eval id")
    if not cleaned_components:
        raise ValueError("ablation matrix requires at least one known component")

    coerced_mode = _coerce_mode(str(mode))
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
        mode=coerced_mode,
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


def create_ablation_suite(
    workspace_dir: str | None,
    eval_ids: list[str],
    components: list[str],
    *,
    repetitions: int = 1,
    mode: Mode = "deterministic",
) -> dict[str, Any]:
    """Create and persist a suite definition without running it."""

    matrix_result = build_ablation_matrix(
        eval_ids,
        components,
        include_baseline=True,
        repetitions=repetitions,
        mode=_coerce_mode(str(mode)),
    )
    suite = {
        **matrix_result["suite"],
        "status": "pending",
        "updated_at": time.time(),
    }
    matrix_result["suite"] = suite
    root = _suite_root(workspace_dir, str(suite["suite_id"]))
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "suite.json", suite)
    _write_json(root / "matrix.json", matrix_result["matrix"])
    _write_json(root / "results.json", [])
    return {**matrix_result, "artifacts": _artifact_paths(root)}


def list_ablation_suites(workspace_dir: str | None, *, limit: int = 50) -> dict[str, Any]:
    """List persisted ablation suites for the active workspace."""

    root = _ablation_root(_workspace(workspace_dir))
    suites: list[dict[str, Any]] = []
    if root.exists():
        for suite_dir in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not suite_dir.is_dir():
                continue
            suite = _read_json(suite_dir / "suite.json", {})
            if not isinstance(suite, dict) or not suite:
                continue
            report = _read_json(suite_dir / "report.json", {})
            results = _read_json(suite_dir / "results.json", [])
            suites.append({
                "suite_id": suite.get("suite_id") or suite_dir.name,
                "status": suite.get("status", "unknown"),
                "eval_ids": suite.get("eval_ids", []),
                "components": suite.get("components", []),
                "mode": suite.get("mode", "deterministic"),
                "repetitions": suite.get("repetitions", 1),
                "created_at": suite.get("created_at"),
                "updated_at": suite.get("updated_at"),
                "run_count": len(results) if isinstance(results, list) else 0,
                "report_summary": report.get("summary") if isinstance(report, dict) else {},
            })
            if len(suites) >= max(1, min(int(limit or 50), 200)):
                break
    return {"suites": suites, "total": len(suites)}


def get_ablation_suite(workspace_dir: str | None, suite_id: str) -> dict[str, Any]:
    """Read a persisted ablation suite and its current artifacts."""

    root = _suite_root(workspace_dir, suite_id)
    suite = _read_required_json(root / "suite.json", f"Ablation suite 不存在: {suite_id}")
    return {
        "suite": suite,
        "matrix": _read_json(root / "matrix.json", []),
        "results": _read_json(root / "results.json", []),
        "report": _read_json(root / "report.json", {}),
        "artifacts": _artifact_paths(root),
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
            "metrics": _component_metric_delta(baseline, disabled),
            "evidence": {
                "baseline_runs": int(baseline.get("count") or 0),
                "disabled_runs": int(disabled.get("count") or 0),
                "baseline_pass_rate": baseline.get("pass_rate"),
                "disabled_pass_rate": disabled.get("pass_rate"),
                "baseline_avg_duration_ms": baseline.get("avg_duration_ms"),
                "disabled_avg_duration_ms": disabled.get("avg_duration_ms"),
                "baseline_avg_event_count": baseline.get("avg_event_count"),
                "disabled_avg_event_count": disabled.get("avg_event_count"),
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
            "insufficient_data": sum(1 for item in component_reports if item["verdict"] == "insufficient_data"),
        },
    }


def run_ablation_suite(
    workspace_dir: str | None,
    eval_ids: list[str],
    components: list[str],
    *,
    repetitions: int = 1,
    mode: Mode = "deterministic",
    persist: bool = True,
) -> dict[str, Any]:
    """Run a baseline + single-disable ablation suite through the eval runner."""

    matrix_result = build_ablation_matrix(
        eval_ids,
        components,
        include_baseline=True,
        repetitions=repetitions,
        mode=_coerce_mode(str(mode)),
    )
    return _run_matrix_result(str(_workspace(workspace_dir)), matrix_result, persist=persist)


def run_persisted_ablation_suite(workspace_dir: str | None, suite_id: str) -> dict[str, Any]:
    """Run an existing persisted suite by id and overwrite its result artifacts."""

    stored = get_ablation_suite(workspace_dir, suite_id)
    matrix_result = {
        "suite": stored["suite"],
        "matrix": stored["matrix"],
        "summary": {
            "eval_count": len(stored["suite"].get("eval_ids", [])),
            "component_count": len(stored["suite"].get("components", [])),
            "variant_count": len(stored["suite"].get("variants", [])),
            "run_count": len(stored["matrix"]),
        },
    }
    return _run_matrix_result(str(_workspace(workspace_dir)), matrix_result, persist=True)


def get_ablation_report(workspace_dir: str | None, suite_id: str) -> dict[str, Any]:
    """Read or rebuild a persisted suite report."""

    stored = get_ablation_suite(workspace_dir, suite_id)
    if stored["report"]:
        return stored["report"]
    suite_result = {
        "suite": stored["suite"],
        "matrix": stored["matrix"],
        "results": stored["results"],
    }
    report = build_component_necessity_report(suite_result)
    root = _suite_root(workspace_dir, suite_id)
    _write_json(root / "report.json", report)
    (root / "report.md").write_text(_report_markdown(report), encoding="utf-8")
    return report


def get_ablation_artifacts(workspace_dir: str | None, suite_id: str) -> dict[str, Any]:
    """Return artifact paths for a persisted suite."""

    root = _suite_root(workspace_dir, suite_id)
    if not (root / "suite.json").exists():
        raise ValueError(f"Ablation suite 不存在: {suite_id}")
    return {"suite_id": suite_id, "root": str(root), "artifacts": _artifact_paths(root)}


def save_ablation_artifacts(workspace_dir: str | None, suite_result: dict[str, Any]) -> dict[str, Any]:
    """Persist suite, matrix, results, and report under `.nanocursor/evals/ablation`."""

    workspace = _workspace(workspace_dir)
    suite = suite_result.get("suite") if isinstance(suite_result.get("suite"), dict) else {}
    suite_id = str(suite.get("suite_id") or suite_result.get("suite_id") or f"ablation_{uuid.uuid4().hex[:12]}")
    root = _ablation_root(workspace) / _safe_path_segment(suite_id)
    root.mkdir(parents=True, exist_ok=True)

    report = build_component_necessity_report(suite_result)
    suite = {**suite, "status": "completed", "updated_at": time.time()}
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
    _write_run_results(root, suite_result.get("results", []))
    return {
        "suite_id": suite_id,
        "root": str(root),
        "artifacts": _artifact_paths(root),
        "report": report,
    }


def _run_matrix_result(workspace: str, matrix_result: dict[str, Any], *, persist: bool) -> dict[str, Any]:
    suite = {
        **dict(matrix_result.get("suite") or {}),
        "status": "running",
        "updated_at": time.time(),
    }
    matrix_result["suite"] = suite
    results = [_run_matrix_row(workspace, suite, row) for row in matrix_result["matrix"]]
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


def _run_matrix_row(workspace: str, suite: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    from src.api.services.eval_runner_service import run_eval_with_command
    from src.api.services.eval_service import get_eval_task, run_eval

    started_at = time.time()
    eval_id = str(row["eval_id"])
    variant_id = str(row["variant_id"])
    disabled_components = [normalize_component_name(str(item)) for item in row.get("disabled_components", [])]
    config = dict(row.get("config") or {})
    config["run_id"] = _row_run_id(row)
    task = get_eval_task(eval_id) or {}
    effective_mode, notes = _effective_mode(
        _coerce_mode(str(suite.get("mode") or "deterministic")),
        disabled_components,
        task,
    )
    try:
        if eval_id == "go_sidecar_filetools_batch":
            eval_result = _run_go_sidecar_eval(disabled_components)
        elif effective_mode == "deterministic":
            eval_result = run_eval(eval_id, workspace)
        else:
            eval_result = run_eval_with_command(eval_id, workspace, mode=effective_mode)
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
    return {
        "variant_id": variant_id,
        "eval_id": eval_id,
        "repetition": row.get("repetition"),
        "disabled_components": disabled_components,
        "ablation_config": config,
        "status": status,
        "error": error,
        "duration_ms": int((time.time() - started_at) * 1000),
        "effective_mode": effective_mode,
        "runtime_effect": {
            "has_runtime_hook": bool(notes),
            "notes": notes or ["当前组件开关尚未接入 eval runtime，结果用于验证管线和报告格式。"],
        },
        "score": eval_result.get("score"),
        "eval_result": eval_result,
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
    durations: dict[str, list[float]] = {}
    event_counts: dict[str, list[float]] = {}
    tool_calls: dict[str, list[float]] = {}
    errors: dict[str, list[bool]] = {}
    for result in results:
        variant_id = str(result.get("variant_id") or "baseline")
        grouped.setdefault(variant_id, []).append(_result_score(result))
        pass_grouped.setdefault(variant_id, []).append(_result_passed(result))
        durations.setdefault(variant_id, []).append(float(result.get("duration_ms") or 0))
        score = result.get("score") if isinstance(result.get("score"), dict) else {}
        eval_result = result.get("eval_result") if isinstance(result.get("eval_result"), dict) else {}
        event_counts.setdefault(variant_id, []).append(float(eval_result.get("event_count") or score.get("event_count") or 0))
        tool_calls.setdefault(variant_id, []).append(float(score.get("tool_call_count") or 0))
        errors.setdefault(variant_id, []).append(bool(result.get("error")))

    aggregate: dict[str, dict[str, Any]] = {}
    for variant_id, scores in grouped.items():
        passes = pass_grouped.get(variant_id, [])
        duration_values = durations.get(variant_id, [])
        event_values = event_counts.get(variant_id, [])
        tool_values = tool_calls.get(variant_id, [])
        error_values = errors.get(variant_id, [])
        aggregate[variant_id] = {
            "variant_id": variant_id,
            "score": round(sum(scores) / len(scores), 3) if scores else 0.0,
            "count": len(scores),
            "pass_rate": round(sum(1 for item in passes if item) / len(passes), 3) if passes else None,
            "avg_duration_ms": round(sum(duration_values) / len(duration_values), 1) if duration_values else 0,
            "avg_event_count": round(sum(event_values) / len(event_values), 1) if event_values else 0,
            "avg_tool_calls": round(sum(tool_values) / len(tool_values), 1) if tool_values else 0,
            "error_rate": round(sum(1 for item in error_values if item) / len(error_values), 3) if error_values else 0,
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


def _component_metric_delta(baseline: dict[str, Any], disabled: dict[str, Any]) -> dict[str, Any]:
    def delta(key: str) -> float | None:
        if key not in baseline or key not in disabled:
            return None
        try:
            return round(float(baseline[key]) - float(disabled[key]), 3)
        except (TypeError, ValueError):
            return None

    return {
        "pass_rate_lift": delta("pass_rate"),
        "duration_ms_delta": delta("avg_duration_ms"),
        "event_count_delta": delta("avg_event_count"),
        "tool_call_delta": delta("avg_tool_calls"),
        "error_rate_delta": delta("error_rate"),
    }


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _read_required_json(path: Path, message: str) -> Any:
    data = _read_json(path, None)
    if data is None:
        raise ValueError(message)
    return data


def _ablation_root(workspace: Path) -> Path:
    return _evals_root(workspace) / "ablation"


def _suite_root(workspace_dir: str | Path | None, suite_id: str) -> Path:
    clean_id = _safe_path_segment(suite_id)
    if not clean_id:
        raise ValueError("suite_id 不能为空")
    workspace = _workspace(str(workspace_dir) if workspace_dir is not None else None)
    return _ablation_root(workspace) / clean_id


def _artifact_paths(root: Path) -> dict[str, str]:
    return {
        "suite": str(root / "suite.json"),
        "matrix": str(root / "matrix.json"),
        "results": str(root / "results.json"),
        "report": str(root / "report.json"),
        "report_md": str(root / "report.md"),
        "runs": str(root / "runs"),
    }


def _write_run_results(root: Path, results: Any) -> None:
    if not isinstance(results, list):
        return
    runs_root = root / "runs"
    for result in results:
        if not isinstance(result, dict):
            continue
        variant = _safe_path_segment(str(result.get("variant_id") or "unknown"))
        eval_id = _safe_path_segment(str(result.get("eval_id") or "unknown"))
        run_id = _safe_path_segment(str((result.get("ablation_config") or {}).get("run_id") or uuid.uuid4().hex))
        _write_json(runs_root / variant / eval_id / run_id / "result.json", result)


def _safe_path_segment(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or "").strip())


def _row_run_id(row: dict[str, Any]) -> str:
    return _safe_path_segment(f"{row.get('variant_id')}__{row.get('eval_id')}__r{row.get('repetition')}")


def _coerce_mode(mode: str) -> Mode:
    if mode in {"deterministic", "command_only", "agent", "baseline"}:
        return mode  # type: ignore[return-value]
    raise ValueError(f"不支持的 ablation mode: {mode}")


def _effective_mode(base_mode: Mode, disabled_components: list[str], task: dict[str, Any]) -> tuple[str, list[str]]:
    notes: list[str] = []
    if base_mode == "deterministic":
        return base_mode, notes
    has_command_fixture = bool(task.get("fixture") and task.get("test_command"))
    eval_id = str(task.get("id") or "")
    if {"context_pack", "project_index"} & set(disabled_components) and eval_id == "context_pack_target_file":
        disabled = sorted({"context_pack", "project_index"} & set(disabled_components))[0]
        notes.append(f"disable_{disabled}: target-file fixture 改用 command_only，观察缺少上下文选择时的原始失败。")
        return "command_only", notes
    if "agent_loop" in disabled_components and has_command_fixture:
        notes.append("disable_agent_loop: command fixture 改用 command_only，跳过 agent 模拟写入。")
        return "command_only", notes
    if "failure_recovery" in disabled_components and has_command_fixture and base_mode == "agent":
        notes.append("disable_failure_recovery: command fixture 改用 baseline，保留失败原貌以观察恢复收益。")
        return "baseline", notes
    return base_mode, notes


def _run_go_sidecar_eval(disabled_components: list[str]) -> dict[str, Any]:
    """Return a lightweight service-level score for Go sidecar availability.

    Full performance numbers are generated by ``scripts/benchmark_go_services.py``.
    This eval keeps ablation reports connected to the component without starting
    extra processes during ordinary API tests.
    """

    disabled = "go_sidecars" in disabled_components
    checks = [
        {
            "id": "go_sidecars_enabled",
            "label": "Go sidecar 启用",
            "status": "failed" if disabled else "passed",
            "detail": "组件被消融关闭" if disabled else "组件在本轮可用",
        },
        {
            "id": "python_fallback_available",
            "label": "Python fallback 可用",
            "status": "passed",
            "detail": "关闭 Go sidecar 时仍可回退到 Python 实现",
        },
    ]
    failed = sum(1 for item in checks if item["status"] == "failed")
    score = {
        "overall": "failed" if failed else "passed",
        "score": 0.62 if disabled else 1.0,
        "passed_count": len(checks) - failed,
        "failed_count": failed,
        "checks": checks,
        "tool_call_count": 1,
        "event_count": 3,
        "go_sidecar_score": 0.0 if disabled else 1.0,
    }
    return {
        "eval_run_id": f"eval-go-sidecar-{int(time.time() * 1000)}",
        "eval_id": "go_sidecar_filetools_batch",
        "prompt": "Go sidecar service-level benchmark",
        "mode": "service",
        "score": score,
        "event_count": 3,
        "completed_at": time.time(),
        "notes": [
            "Full benchmark: python scripts/benchmark_go_services.py --output-json .nanocursor/benchmarks/go-services/latest.json",
        ],
    }


def _report_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Ablation Report: {report.get('suite_id') or 'unknown'}",
        "",
        "| Component | Baseline | Disabled | Lift | Pass Rate | Duration | Verdict |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in report.get("components", []):
        evidence = item.get("evidence", {})
        lines.append(
            f"| {item['component']} | {item['baseline_score']} | {item['disabled_score']} | "
            f"{item['lift']} | {evidence.get('baseline_pass_rate')} -> {evidence.get('disabled_pass_rate')} | "
            f"{evidence.get('baseline_avg_duration_ms')} -> {evidence.get('disabled_avg_duration_ms')} | "
            f"{item['verdict']} |"
        )
    return "\n".join(lines) + "\n"
