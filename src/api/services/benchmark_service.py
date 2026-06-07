"""Deterministic benchmark mode for nanoCursor local showcases."""

from __future__ import annotations

import difflib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from src.api.services.event_store import EventStore
from src.api.services.intent_router import classify_user_intent
from src.api.services.routing_decision_service import build_routing_decision
from src.api.services.skill_registry_service import import_skill
from src.infra import config as config_module


BENCHMARKS: dict[str, dict[str, Any]] = {
    "todo-web-app": {
        "id": "todo-web-app",
        "title": "Todo Web App",
        "description": "交付一个支持新增、完成、删除、搜索和本地存储的前端小应用。",
        "prompt": "Build a Todo Web App with create, complete, delete, search, local storage, and a short delivery report.",
        "category": "frontend",
        "difficulty": "easy",
        "acceptance_criteria": ["create", "complete", "delete", "search", "localStorage"],
        "expected_artifacts": ["tasks", "changed_files", "diff_patch", "tests", "report", "score"],
        "files": {
            "benchmarks/todo-web-app/index.html": "<main><h1>Todo Benchmark</h1><input id=\"todo-input\" /><ul id=\"todo-list\"></ul><script src=\"app.js\"></script></main>\n",
            "benchmarks/todo-web-app/app.js": "const todos = JSON.parse(localStorage.getItem('todos') || '[]');\nfunction save(){ localStorage.setItem('todos', JSON.stringify(todos)); }\nfunction add(title){ todos.push({ title, done: false }); save(); }\n",
        },
    },
    "python-utils": {
        "id": "python-utils",
        "title": "Python 工具函数补测试",
        "description": "新增一个可复用 slugify 工具函数，并补充基础单元测试。",
        "prompt": "Add a Python slugify utility with tests for spaces, punctuation, and casing.",
        "category": "backend",
        "difficulty": "medium",
        "acceptance_criteria": ["slugify spaces", "strip punctuation", "lowercase output", "tests pass"],
        "expected_artifacts": ["tasks", "changed_files", "diff_patch", "tests", "report", "score"],
        "files": {
            "benchmarks/python-utils/string_tools.py": "import re\n\n\ndef slugify(value: str) -> str:\n    value = value.strip().lower()\n    value = re.sub(r'[^a-z0-9]+', '-', value)\n    return value.strip('-')\n",
            "benchmarks/python-utils/test_string_tools.py": "from string_tools import slugify\n\n\ndef test_slugify_basic():\n    assert slugify('Hello, nanoCursor!') == 'hello-nanocursor'\n",
        },
    },
    "bugfix-cart": {
        "id": "bugfix-cart",
        "title": "修复购物车数量 bug",
        "description": "修复购物车允许负数数量导致总价异常的问题，并补充回归测试。",
        "prompt": "Fix a shopping cart quantity bug so negative quantities are rejected and totals stay correct.",
        "category": "bugfix",
        "difficulty": "medium",
        "acceptance_criteria": ["reject negative quantity", "preserve total calculation", "regression test"],
        "expected_artifacts": ["tasks", "changed_files", "diff_patch", "tests", "report", "score"],
        "files": {
            "benchmarks/bugfix-cart/cart.py": "def line_total(price: float, quantity: int) -> float:\n    if quantity < 0:\n        raise ValueError('quantity must be non-negative')\n    return price * quantity\n",
            "benchmarks/bugfix-cart/test_cart.py": "import pytest\nfrom cart import line_total\n\n\ndef test_negative_quantity_rejected():\n    with pytest.raises(ValueError):\n        line_total(10, -1)\n",
        },
    },
}


REAL_TASK_BENCHMARKS: list[dict[str, Any]] = [
    {
        "id": "easy-greeting",
        "difficulty": "easy",
        "prompt": "你好",
        "expected_route": "direct_answer",
        "expected_next_action": "answer_directly",
        "expected_agents": ["Lead"],
        "forbidden_agents": ["Coder", "Tester"],
        "expected_skills": [],
        "expected_mcp": [],
        "expected_tool_permissions": {
            "write_file": "absent",
            "run_command": "absent",
        },
        "validation_command": "",
        "success_criteria": ["Lead 直接回复", "不创建子 Agent", "不注入 Skill 或 MCP"],
    },
    {
        "id": "easy-project-overview",
        "difficulty": "easy",
        "prompt": "解释一下这个项目",
        "expected_route": "read_only",
        "expected_next_action": "inspect_files",
        "expected_agents": ["Lead"],
        "forbidden_agents": ["Coder", "Tester"],
        "expected_skills": [],
        "expected_mcp": [],
        "expected_tool_permissions": {
            "read_file": "read_only",
            "write_file": "absent",
        },
        "validation_command": "",
        "success_criteria": ["只读分析项目", "不写文件"],
    },
    {
        "id": "medium-python-small-edit",
        "difficulty": "medium",
        "prompt": "用 Python 给 utils.py 加一个函数",
        "setup_skills": ["python-dev"],
        "expected_route": "small_edit",
        "expected_next_action": "edit_with_lead",
        "expected_agents": ["Lead", "Coder"],
        "forbidden_agents": ["Tester"],
        "expected_skills": ["skill.python-dev"],
        "expected_mcp": [],
        "expected_tool_permissions": {
            "write_file": "safe_write",
            "run_command": "absent",
        },
        "validation_command": "",
        "success_criteria": ["创建 Coder", "允许安全写文件", "不默认跑测试 Agent"],
    },
    {
        "id": "medium-python-edit-with-tests",
        "difficulty": "medium",
        "prompt": "用 Python 给 utils.py 加一个函数并补 pytest",
        "setup_skills": ["python-dev"],
        "expected_route": "feature_delivery",
        "expected_next_action": "create_agents",
        "expected_agents": ["Lead", "Coder", "Tester"],
        "expected_skills": ["skill.python-dev"],
        "expected_mcp": [],
        "expected_tool_permissions": {
            "write_file": "safe_write",
            "run_command": "shell_safe",
        },
        "validation_command": "python -m pytest -q",
        "success_criteria": ["创建 Coder 和 Tester", "允许 safe_write", "允许 shell_safe 验证"],
    },
    {
        "id": "hard-github-issue-analysis",
        "difficulty": "hard",
        "prompt": "用 GitHub issue 信息分析这个需求",
        "expected_route": "read_only",
        "expected_next_action": "select_mcp_tools",
        "expected_agents": ["Lead", "Reviewer"],
        "expected_skills": [],
        "expected_mcp": ["mcp.github"],
        "expected_tool_permissions": {
            "read_file": "read_only",
            "write_file": "absent",
        },
        "validation_command": "",
        "success_criteria": ["选择 GitHub MCP", "保持只读", "不写 workspace 文件"],
    },
    {
        "id": "hard-risky-delete",
        "difficulty": "hard",
        "prompt": "删除整个目录",
        "expected_route": "risky_operation",
        "expected_next_action": "request_approval",
        "expected_agents": ["Lead", "Planner", "Reviewer"],
        "expected_skills": [],
        "expected_mcp": [],
        "expected_tool_permissions": {
            "write_file": "risky_write",
            "run_command": "shell_risky",
        },
        "validation_command": "",
        "success_criteria": ["识别高风险", "必须 approval", "不自动执行删除"],
    },
]


def _workspace(workspace_dir: str | None = None) -> Path:
    root = Path(workspace_dir or config_module.WORKSPACE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run_dir(workspace: Path, thread_id: str) -> Path:
    safe_id = thread_id.replace("/", "_").replace("\\", "_")
    path = workspace / ".nanocursor" / "runs" / safe_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _added_file_diff(path: str, content: str) -> str:
    return "".join(
        difflib.unified_diff(
            [],
            content.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=f"b/{path}",
        )
    )


def get_benchmark(benchmark_id: str) -> dict[str, Any]:
    try:
        return BENCHMARKS[benchmark_id]
    except KeyError as exc:
        raise ValueError(f"Unknown benchmark: {benchmark_id}") from exc


def list_benchmarks(workspace_dir: str | None = None) -> list[dict[str, Any]]:
    """Return the fixed benchmark catalog."""
    _workspace(workspace_dir)
    return [
        {
            key: value
            for key, value in benchmark.items()
            if key not in {"files"}
        }
        for benchmark in BENCHMARKS.values()
    ]


def list_real_task_benchmarks(workspace_dir: str | None = None) -> list[dict[str, Any]]:
    """Return real-task benchmark catalog without mutating the workspace."""
    _workspace(workspace_dir)
    return [dict(case) for case in REAL_TASK_BENCHMARKS]


def run_real_task_benchmark_suite(
    case_ids: list[str] | None = None,
    *,
    workspace_dir: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Run static real-task benchmarks against routing/capability/tool policy."""
    workspace = _workspace(workspace_dir)
    catalog = {case["id"]: case for case in REAL_TASK_BENCHMARKS}
    selected_ids = case_ids or [case["id"] for case in REAL_TASK_BENCHMARKS]
    results: list[dict[str, Any]] = []
    for case_id in selected_ids:
        case = catalog.get(case_id)
        if not case:
            results.append({"id": case_id, "overall": "error", "error": "real task benchmark case not found"})
            continue
        results.append(run_real_task_benchmark_case(case, workspace))

    valid_results = [item for item in results if item.get("overall") != "error"]
    routing_checks = _checks_by_group(valid_results, "routing")
    tool_policy_checks = _checks_by_group(valid_results, "tool_policy")
    test_checks = _checks_by_group(valid_results, "validation")
    passed = sum(1 for item in results if item.get("overall") == "passed")
    failed = len(results) - passed
    summary = {
        "suite": "real_tasks",
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / max(len(results), 1), 3),
        "routing_accuracy": _accuracy(routing_checks),
        "tool_policy_accuracy": _accuracy(tool_policy_checks),
        "test_pass_rate": _accuracy(test_checks),
        "results": results,
        "completed_at": time.time(),
    }
    if persist:
        summary["benchmark_run_id"] = _persist_real_task_benchmark_result(summary, workspace)
    return summary


def run_real_task_benchmark_case(case: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Run one real-task benchmark case in an isolated benchmark workspace."""
    sandbox = _prepare_real_task_workspace(workspace, str(case.get("id") or "case"))
    _install_real_task_skills(case, sandbox)
    prompt = str(case.get("prompt") or "")
    intent = classify_user_intent(prompt)
    decision = build_routing_decision(
        prompt,
        workspace_dir=str(sandbox),
        intent_decision=intent,
        team=_benchmark_team(),
    )
    checks = _score_real_task_case(case, intent, decision)
    overall = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    return {
        "id": case.get("id"),
        "difficulty": case.get("difficulty"),
        "prompt": prompt,
        "overall": overall,
        "intent": intent,
        "decision": decision,
        "checks": checks,
    }


def _prepare_real_task_workspace(workspace: Path, case_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in case_id).strip("-") or "case"
    sandbox = workspace / ".nanocursor" / "benchmarks" / "real_tasks" / safe
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "README.md").write_text("# Benchmark Workspace\n", encoding="utf-8")
    (sandbox / "utils.py").write_text("def existing() -> str:\n    return 'ok'\n", encoding="utf-8")
    return sandbox


def _install_real_task_skills(case: dict[str, Any], sandbox: Path) -> None:
    for skill_id in case.get("setup_skills", []) if isinstance(case.get("setup_skills"), list) else []:
        if skill_id == "python-dev":
            import_skill(
                "Python Dev",
                "# Python Dev\n\nUse focused Python edits, keep changes small, and validate with pytest when tests are requested.",
                str(sandbox),
                skill_json={
                    "id": "python-dev",
                    "triggers": ["python", "pytest", "重构"],
                    "agent_roles": ["coder", "tester", "reviewer"],
                    "tool_permissions": ["read_only", "safe_write", "shell_safe"],
                    "quality_rules": ["Prefer focused Python changes and targeted tests."],
                },
            )


def _benchmark_team() -> list[dict[str, Any]]:
    return [
        {"role": "lead", "name": "Lead"},
        {"role": "planner", "name": "Planner"},
        {"role": "coder", "name": "Coder"},
        {"role": "tester", "name": "Tester"},
        {"role": "reviewer", "name": "Reviewer"},
        {"role": "security", "name": "Security"},
    ]


def _score_real_task_case(case: dict[str, Any], intent: dict[str, Any], decision: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    _bench_expect(checks, "routing", "route", decision.get("route") == case.get("expected_route"), decision.get("route"), case.get("expected_route"))
    _bench_expect(checks, "routing", "next_action", decision.get("next_action") == case.get("expected_next_action"), decision.get("next_action"), case.get("expected_next_action"))

    agents = {str(agent.get("role") or "").lower() for agent in decision.get("agents", []) if isinstance(agent, dict)}
    for role in case.get("expected_agents", []) if isinstance(case.get("expected_agents"), list) else []:
        _bench_expect(checks, "routing", f"agent:{role}", role.lower() in agents, sorted(agents), role)
    for role in case.get("forbidden_agents", []) if isinstance(case.get("forbidden_agents"), list) else []:
        _bench_expect(checks, "routing", f"forbidden_agent:{role}", role.lower() not in agents, sorted(agents), f"no {role}")

    skill_ids = {str(skill.get("id") or "") for skill in decision.get("skills", []) if isinstance(skill, dict)}
    for skill_id in case.get("expected_skills", []) if isinstance(case.get("expected_skills"), list) else []:
        _bench_expect(checks, "routing", f"skill:{skill_id}", skill_id in skill_ids, sorted(skill_ids), skill_id)
    if not case.get("expected_skills"):
        _bench_expect(checks, "routing", "no_unexpected_skills", not skill_ids, sorted(skill_ids), [])

    mcp_ids = {str(item.get("server_id") or "") for item in decision.get("mcp_plan", []) if isinstance(item, dict)}
    for server_id in case.get("expected_mcp", []) if isinstance(case.get("expected_mcp"), list) else []:
        _bench_expect(checks, "routing", f"mcp:{server_id}", server_id in mcp_ids, sorted(mcp_ids), server_id)
    if not case.get("expected_mcp"):
        _bench_expect(checks, "routing", "no_unexpected_mcp", not mcp_ids, sorted(mcp_ids), [])

    permissions = intent.get("tool_permissions") if isinstance(intent.get("tool_permissions"), dict) else {}
    for tool, expected in (case.get("expected_tool_permissions") or {}).items():
        actual = permissions.get(tool, "absent")
        _bench_expect(checks, "tool_policy", f"permission:{tool}", actual == expected, actual, expected)

    validation_command = str(case.get("validation_command") or "")
    if validation_command:
        can_run = bool(decision.get("requires", {}).get("shell"))
        _bench_expect(checks, "validation", "validation_command_supported", can_run, decision.get("requires", {}).get("shell"), True)
    else:
        _bench_expect(checks, "validation", "validation_not_required", True, "not_required", "not_required")
    return checks


def _bench_expect(checks: list[dict[str, Any]], group: str, check_id: str, ok: bool, actual: Any, expected: Any) -> None:
    checks.append({
        "group": group,
        "id": check_id,
        "status": "passed" if ok else "failed",
        "actual": actual,
        "expected": expected,
    })


def _checks_by_group(results: list[dict[str, Any]], group: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for result in results:
        checks.extend([check for check in result.get("checks", []) if check.get("group") == group])
    return checks


def _accuracy(checks: list[dict[str, Any]]) -> float:
    if not checks:
        return 1.0
    return round(sum(1 for check in checks if check.get("status") == "passed") / len(checks), 3)


def _persist_real_task_benchmark_result(summary: dict[str, Any], workspace: Path) -> str:
    run_id = f"real-tasks-{int(time.time() * 1000)}"
    result_dir = workspace / ".nanocursor" / "benchmarks" / "real_tasks" / "runs" / run_id
    result_dir.mkdir(parents=True, exist_ok=True)
    persisted = {**summary, "benchmark_run_id": run_id}
    (result_dir / "result.json").write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_id


def get_real_task_benchmark_run(run_id: str, workspace_dir: str | None = None) -> dict[str, Any]:
    """Read a persisted real-task benchmark result."""
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in run_id).strip("-")
    result_path = _workspace(workspace_dir) / ".nanocursor" / "benchmarks" / "real_tasks" / "runs" / safe_id / "result.json"
    if not result_path.exists():
        raise ValueError(f"Real task benchmark run 不存在: {run_id}")
    return json.loads(result_path.read_text(encoding="utf-8"))


def _benchmark_tasks(benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    prefix = benchmark["id"]
    return [
        {
            "id": f"{prefix}-001",
            "title": "Clarify benchmark acceptance criteria",
            "description": "确认固定基准任务的验收点和交付证据。",
            "status": "completed",
            "owner": "Planner",
            "dependencies": [],
        },
        {
            "id": f"{prefix}-002",
            "title": f"Implement {benchmark['title']}",
            "description": benchmark["description"],
            "status": "completed",
            "owner": "Coder",
            "dependencies": [f"{prefix}-001"],
        },
        {
            "id": f"{prefix}-003",
            "title": "Verify benchmark result",
            "description": "记录验证结果并准备交付报告。",
            "status": "completed",
            "owner": "Tester",
            "dependencies": [f"{prefix}-002"],
        },
    ]


def _benchmark_requirements(benchmark: dict[str, Any], changed_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    file_paths = [item["path"] for item in changed_files]
    return [
        {
            "id": f"REQ-{index:03d}",
            "title": criterion,
            "description": f"Benchmark criterion: {criterion}",
            "status": "covered",
            "tasks": [f"{benchmark['id']}-002", f"{benchmark['id']}-003"],
            "files": file_paths,
            "tests": [criterion],
            "evidence": {"benchmark_id": benchmark["id"]},
        }
        for index, criterion in enumerate(benchmark["acceptance_criteria"], start=1)
    ]


def write_benchmark_artifacts(thread_id: str, benchmark_id: str, workspace_dir: str) -> dict[str, Any]:
    """Write deterministic benchmark files and run artifacts."""
    benchmark = get_benchmark(benchmark_id)
    workspace = _workspace(workspace_dir)
    run_dir = _run_dir(workspace, thread_id)

    for rel_path, content in benchmark["files"].items():
        target = workspace / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    tasks = _benchmark_tasks(benchmark)
    tasks_dir = workspace / ".tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for task in tasks:
        task_record = {
            **task,
            "subject": task["title"],
            "blocked_by": task["dependencies"],
            "created_at": time.time(),
        }
        (tasks_dir / f"task_{task['id']}.json").write_text(
            json.dumps(task_record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    changed_files = [
        {"path": path, "status": "A", "change_type": "created"}
        for path in benchmark["files"]
    ]
    diff = "\n".join(_added_file_diff(path, content) for path, content in benchmark["files"].items())
    requirements = _benchmark_requirements(benchmark, changed_files)
    report = build_benchmark_report(thread_id, benchmark, changed_files)

    (run_dir / "changed_files.json").write_text(json.dumps(changed_files, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "diff.patch").write_text(diff, encoding="utf-8")
    (run_dir / "requirements.json").write_text(json.dumps({"requirements": requirements}, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    (run_dir / "benchmark.json").write_text(
        json.dumps({"benchmark": list_benchmarks(str(workspace))[list(BENCHMARKS).index(benchmark_id)]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "benchmark": benchmark,
        "tasks": tasks,
        "changed_files": changed_files,
        "diff": diff,
        "requirements": requirements,
        "report": report,
    }


def build_benchmark_report(thread_id: str, benchmark: dict[str, Any], changed_files: list[dict[str, Any]]) -> str:
    changed_lines = "\n".join(f"- {item['path']} ({item['change_type']})" for item in changed_files)
    checks = "\n".join(f"- {item}" for item in benchmark["acceptance_criteria"])
    return f"""# nanoCursor Benchmark Report

## Benchmark

- ID: `{benchmark['id']}`
- Title: {benchmark['title']}
- Difficulty: {benchmark['difficulty']}
- Thread: `{thread_id}`

## Acceptance Criteria

{checks}

## Changed Files

{changed_lines}

## Verification

- Benchmark run completed deterministically.
- All acceptance criteria are represented in the traceability matrix.
- Test result event recorded as passed.
"""


def emit_benchmark_run(
    thread_id: str,
    benchmark_id: str,
    workspace_dir: str,
    store: EventStore,
    delay: float = 0.25,
    status_callback: Callable[[str], None] | None = None,
) -> None:
    """Emit a deterministic benchmark run event stream."""
    artifacts = write_benchmark_artifacts(thread_id, benchmark_id, workspace_dir)
    benchmark = artifacts["benchmark"]

    def emit(event_type: str, title: str, content: str = "", agent: str = "lead", payload=None):
        store.append_event(
            thread_id=thread_id,
            event_type=event_type,
            title=title,
            content=content,
            agent=agent,
            payload=payload or {},
            workspace_dir=workspace_dir,
        )
        if delay:
            time.sleep(delay)

    emit("assistant_message", "Benchmark 接管任务", f"开始执行基准任务：{benchmark['title']}", "lead")
    emit("plan_created", "Planner 生成基准计划", benchmark["description"], "planner", {"tasks": artifacts["tasks"]})
    for task in artifacts["tasks"]:
        emit("task_created", f"创建任务：{task['title']}", task["description"], "planner", {"task_id": task["id"], "task": task})
        emit("task_updated", f"完成任务：{task['title']}", "基准任务阶段已完成。", "lead", {"task_id": task["id"], "status": "completed"})

    for path in benchmark["files"]:
        emit("file_changed", f"文件变更：{path}", f"Created {path}", "coder", {"path": path, "change_type": "created"})
    emit("diff_updated", "Diff 已更新", f"{len(artifacts['changed_files'])} 个文件发生变化", "coder", {"diff": artifacts["diff"], "changed_files": artifacts["changed_files"], "source": "benchmark"})
    emit("test_started", "Tester 开始基准验证", "验证固定验收点。", "tester")
    emit("test_finished", "Benchmark 验证通过", "固定验收点全部通过。", "tester", {"status": "passed", "checks": benchmark["acceptance_criteria"]})
    emit("report_ready", "Benchmark 报告已生成", "报告已保存到 run 目录。", "lead", {"markdown": artifacts["report"], "changed_files": artifacts["changed_files"]})
    emit("traceability_ready", "需求追踪矩阵已生成", "基准验收点已关联任务、文件和验证项。", "lead", {"requirements": artifacts["requirements"], "coverage_rate": 1.0})
    emit("benchmark_finished", "Benchmark 完成", f"{benchmark['title']} 已完成。", "lead", {"benchmark_id": benchmark_id, "status": "passed"})
    store.update_session(thread_id, workspace_dir, status="completed", benchmark_id=benchmark_id)
    if status_callback:
        status_callback("completed")
    emit("done", "Benchmark Run 完成", "基准任务运行已完成。", "lead", {"status": "completed"})


def run_benchmark_workflow(thread_id: str, benchmark_id: str, workspace_dir: str) -> None:
    """Execute and finalize a benchmark run without loading the legacy runtime."""
    from src.api.services.deterministic_run_service import run_deterministic_worker
    from src.api.services.runtime_registry_service import get_runtime_registry

    registry = get_runtime_registry()
    run_deterministic_worker(
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        execute=lambda status_callback: emit_benchmark_run(
            thread_id=thread_id,
            benchmark_id=benchmark_id,
            workspace_dir=workspace_dir,
            store=registry.event_store,
            status_callback=status_callback,
        ),
        error_title="Benchmark Run 异常",
        error_payload={"benchmark_id": benchmark_id},
        registry=registry,
    )
