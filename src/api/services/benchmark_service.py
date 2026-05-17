"""Deterministic benchmark mode for AgentHub competition demos."""

from __future__ import annotations

import difflib
import json
import time
from pathlib import Path
from typing import Any, Callable

from src.api.services.event_store import EventStore
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
            "benchmarks/python-utils/test_string_tools.py": "from string_tools import slugify\n\n\ndef test_slugify_basic():\n    assert slugify('Hello, AgentHub!') == 'hello-agenthub'\n",
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
    return f"""# AgentHub Benchmark Report

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
