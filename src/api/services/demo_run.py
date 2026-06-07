"""Deterministic nanoCursor demo run used for stable local showcases."""

from __future__ import annotations

import difflib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from src.api.services.event_store import EventStore


DEMO_PROMPT = (
    "Build a Todo Web App with create, complete, delete, search, local storage, "
    "and a short delivery report."
)

DEMO_TASKS = [
    {
        "id": "demo-001",
        "title": "Clarify requirements and acceptance criteria",
        "description": "Confirm create, complete, delete, search, local storage, and test notes.",
        "status": "pending",
        "owner": "Planner",
        "dependencies": [],
    },
    {
        "id": "demo-002",
        "title": "Implement Todo UI",
        "description": "Create the single page Todo interface and interaction model.",
        "status": "pending",
        "owner": "Coder",
        "dependencies": ["demo-001"],
    },
    {
        "id": "demo-003",
        "title": "Persist todos locally",
        "description": "Store Todo items in localStorage and restore them on reload.",
        "status": "pending",
        "owner": "Coder",
        "dependencies": ["demo-002"],
    },
    {
        "id": "demo-004",
        "title": "Verify and prepare delivery report",
        "description": "Check the core workflow and summarize delivery results.",
        "status": "pending",
        "owner": "Tester",
        "dependencies": ["demo-003"],
    },
]

DEMO_TEAM = [
    {
        "name": "Lead",
        "role": "lead",
        "status": "working",
        "goal": "Coordinate the nanoCursor delivery and keep every artifact aligned with the request.",
        "tools": ["plan", "delegate", "report"],
        "last_action": "接管用户需求并启动交付流程。",
        "artifacts": ["report", "score"],
    },
    {
        "name": "Planner",
        "role": "planner",
        "status": "working",
        "goal": "Turn the request into tasks, dependencies, and acceptance criteria.",
        "tools": ["task_create", "task_update"],
        "last_action": "生成四阶段交付计划。",
        "artifacts": ["tasks", "requirements"],
    },
    {
        "name": "Coder",
        "role": "coder",
        "status": "idle",
        "goal": "Implement the Todo workspace and keep file changes reviewable.",
        "tools": ["write_file", "edit_file", "bash"],
        "last_action": "创建 Todo 页面、样式和交互脚本。",
        "artifacts": ["changed_files", "diff_patch"],
    },
    {
        "name": "Tester",
        "role": "tester",
        "status": "idle",
        "goal": "Verify core workflows and surface delivery risks.",
        "tools": ["bash", "manual_check"],
        "last_action": "验证新增、完成、删除、搜索和本地存储。",
        "artifacts": ["tests", "quality"],
    },
]

DEMO_REQUIREMENTS = [
    {
        "id": "REQ-001",
        "title": "Create todo items",
        "description": "Users can add a non-empty todo item from the input form.",
        "status": "covered",
        "tasks": ["demo-002"],
        "files": ["demo-todo/index.html", "demo-todo/app.js"],
        "tests": ["create"],
        "evidence": {"acceptance": "Submit the add form and see a new item in the list."},
    },
    {
        "id": "REQ-002",
        "title": "Complete and delete todo items",
        "description": "Users can mark todos complete and remove items they no longer need.",
        "status": "covered",
        "tasks": ["demo-002"],
        "files": ["demo-todo/index.html", "demo-todo/app.js", "demo-todo/styles.css"],
        "tests": ["complete", "delete"],
        "evidence": {"acceptance": "Toggle a checkbox, then delete an item from the list."},
    },
    {
        "id": "REQ-003",
        "title": "Search todo items",
        "description": "Users can filter the visible todo list by entering search text.",
        "status": "covered",
        "tasks": ["demo-002"],
        "files": ["demo-todo/index.html", "demo-todo/app.js"],
        "tests": ["search"],
        "evidence": {"acceptance": "Typing into search hides non-matching todo items."},
    },
    {
        "id": "REQ-004",
        "title": "Persist todos locally",
        "description": "Todos are saved to localStorage and restored after reload.",
        "status": "covered",
        "tasks": ["demo-003"],
        "files": ["demo-todo/app.js"],
        "tests": ["localStorage"],
        "evidence": {"acceptance": "Reload the page and existing todos remain visible."},
    },
    {
        "id": "REQ-005",
        "title": "Prepare delivery evidence",
        "description": "The run records changed files, verification notes, and a delivery report.",
        "status": "covered",
        "tasks": ["demo-004"],
        "files": ["demo-todo/index.html", "demo-todo/styles.css", "demo-todo/app.js"],
        "tests": ["report_ready", "diff_updated"],
        "evidence": {"acceptance": "Report, Diff, and traceability artifacts are available."},
    },
]

DEMO_FILES = {
    "demo-todo/index.html": """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>nanoCursor Todo Demo</title>
    <link rel="stylesheet" href="./styles.css" />
  </head>
  <body>
    <main class="todo-shell">
      <section class="todo-panel">
        <p class="eyebrow">nanoCursor delivery demo</p>
        <h1>Todo Workspace</h1>
        <form id="todo-form" class="todo-form">
          <input id="todo-input" placeholder="Add a task" autocomplete="off" />
          <button type="submit">Add</button>
        </form>
        <input id="todo-search" class="search" placeholder="Search tasks" />
        <ul id="todo-list" class="todo-list"></ul>
      </section>
    </main>
    <script src="./app.js"></script>
  </body>
</html>
""",
    "demo-todo/styles.css": """:root {
  font-family: Inter, system-ui, sans-serif;
  color: #17202a;
  background: #f6f7f9;
}

body {
  margin: 0;
}

.todo-shell {
  display: grid;
  place-items: center;
  min-height: 100vh;
  padding: 24px;
}

.todo-panel {
  width: min(680px, 100%);
  padding: 24px;
  border: 1px solid #d9e0e8;
  border-radius: 8px;
  background: #ffffff;
  box-shadow: 0 12px 30px rgba(30, 41, 59, 0.08);
}

.eyebrow {
  margin: 0 0 8px;
  color: #657386;
  font-size: 13px;
}

h1 {
  margin: 0 0 18px;
  font-size: 28px;
}

.todo-form {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
}

input,
button {
  height: 40px;
  border-radius: 8px;
  font: inherit;
}

input {
  border: 1px solid #d9e0e8;
  padding: 0 12px;
}

button {
  border: 0;
  padding: 0 14px;
  background: #2563eb;
  color: #ffffff;
  font-weight: 700;
}

.search {
  width: 100%;
  margin: 12px 0;
}

.todo-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.todo-item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  padding: 10px;
  border: 1px solid #d9e0e8;
  border-radius: 8px;
}

.todo-item.done span {
  color: #657386;
  text-decoration: line-through;
}
""",
    "demo-todo/app.js": """const STORAGE_KEY = "nanocursor-demo-todos";

const form = document.querySelector("#todo-form");
const input = document.querySelector("#todo-input");
const search = document.querySelector("#todo-search");
const list = document.querySelector("#todo-list");

let todos = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");

function save() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(todos));
}

function render() {
  const query = search.value.trim().toLowerCase();
  const visible = todos.filter((todo) => todo.title.toLowerCase().includes(query));
  list.innerHTML = "";

  for (const todo of visible) {
    const item = document.createElement("li");
    item.className = `todo-item ${todo.done ? "done" : ""}`;
    item.innerHTML = `
      <input type="checkbox" ${todo.done ? "checked" : ""} />
      <span></span>
      <button type="button">Delete</button>
    `;
    item.querySelector("span").textContent = todo.title;
    item.querySelector("input").addEventListener("change", () => {
      todo.done = !todo.done;
      save();
      render();
    });
    item.querySelector("button").addEventListener("click", () => {
      todos = todos.filter((entry) => entry.id !== todo.id);
      save();
      render();
    });
    list.appendChild(item);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const title = input.value.trim();
  if (!title) return;
  todos.unshift({ id: crypto.randomUUID(), title, done: false });
  input.value = "";
  save();
  render();
});

search.addEventListener("input", render);
render();
""",
}


def _workspace(workspace_dir: str) -> Path:
    root = Path(workspace_dir).resolve()
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


def write_demo_artifacts(thread_id: str, workspace_dir: str) -> dict[str, Any]:
    """Write deterministic demo files, tasks, team config, diff, and report."""
    workspace = _workspace(workspace_dir)
    run_dir = _run_dir(workspace, thread_id)

    for rel_path, content in DEMO_FILES.items():
        target = workspace / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    tasks_dir = workspace / ".tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    for task in DEMO_TASKS:
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

    team_dir = workspace / ".team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"team_name": "nanocursor-demo", "members": DEMO_TEAM}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    changed_files = [
        {"path": path, "status": "A", "change_type": "created"}
        for path in DEMO_FILES
    ]
    diff = "\n".join(_added_file_diff(path, content) for path, content in DEMO_FILES.items())
    (run_dir / "changed_files.json").write_text(
        json.dumps(changed_files, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "diff.patch").write_text(diff, encoding="utf-8")
    (run_dir / "requirements.json").write_text(
        json.dumps({"requirements": DEMO_REQUIREMENTS}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report = build_demo_report(thread_id, workspace, changed_files)
    (run_dir / "report.md").write_text(report, encoding="utf-8")

    return {
        "changed_files": changed_files,
        "diff": diff,
        "report": report,
        "requirements": DEMO_REQUIREMENTS,
        "preview_url": f"{workspace / 'demo-todo' / 'index.html'}",
    }


def build_demo_report(thread_id: str, workspace: Path, changed_files: list[dict[str, Any]]) -> str:
    changed_lines = "\n".join(f"- {item['path']} ({item['change_type']})" for item in changed_files)
    return f"""# nanoCursor Demo Delivery Report

## Summary

Delivered a deterministic Todo Web App demo with create, complete, delete, search, and localStorage persistence.

## Request

{DEMO_PROMPT}

## Run Status

- Thread: `{thread_id}`
- Status: `completed`
- Workspace: `{workspace}`

## Changed Files

{changed_lines}

## Verification

- Manual workflow: add, complete, delete, search, and reload persistence.
- Demo output is deterministic and does not require an LLM API key.

## Next Steps

- Keep this stable demo flow aligned with the README showcase path.
- Add automated browser checks when the preview service is added.
"""


def emit_demo_run(
    thread_id: str,
    workspace_dir: str,
    store: EventStore,
    delay: float = 0.35,
    status_callback: Callable[[str], None] | None = None,
    approval_waiter: Callable[[float], str | None] | None = None,
    artifacts: dict[str, Any] | None = None,
) -> None:
    """Emit a complete deterministic nanoCursor run."""
    artifacts = artifacts or write_demo_artifacts(thread_id, workspace_dir)

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

    emit(
        "assistant_message",
        "Lead 接管需求",
        "我将按 nanoCursor 演示流程完成 Todo Web App 的需求拆解、实现、验证和报告。",
        "lead",
    )
    emit(
        "plan_created",
        "Planner 生成交付计划",
        "计划包含需求整理、界面实现、本地存储、验证报告四个阶段。",
        "planner",
        {"tasks": DEMO_TASKS},
    )
    emit(
        "approval_requested",
        "等待用户审批计划",
        "请确认 Planner 的四阶段交付计划。批准后进入 Coder 和 Tester 阶段。",
        "planner",
        {
            "plan_id": "demo-plan",
            "tasks": DEMO_TASKS,
            "risk_level": "low",
            "default_decision": "approved",
        },
    )

    approval_decision = approval_waiter(45) if approval_waiter else "approved"
    if approval_decision is None:
        approval_decision = "approved"
        emit(
            "approval_resolved",
            "计划已自动批准",
            "Demo 模式超过等待时间，已自动批准计划以保证演示继续。",
            "lead",
            {"plan_id": "demo-plan", "decision": approval_decision, "auto": True},
        )
    if approval_decision == "rejected":
        store.update_session(thread_id, workspace_dir, status="cancelled")
        if status_callback:
            status_callback("cancelled")
        emit(
            "done",
            "Demo Run 已取消",
            "用户拒绝了计划，演示运行已停止。",
            "lead",
            {"status": "cancelled"},
        )
        return
    if approval_decision == "revise":
        emit(
            "assistant_message",
            "Planner 收到修订意见",
            "Demo 模式已记录修订意见；当前演示继续按固定计划执行，真实运行可在这里进入重新规划。",
            "planner",
            {"plan_id": "demo-plan", "decision": approval_decision},
        )
    emit(
        "approval_resolved",
        "计划已批准",
        "Demo 计划已确认，继续进入执行阶段。",
        "lead",
        {"plan_id": "demo-plan", "decision": approval_decision},
    )
    emit("team_updated", "团队状态已更新", "Lead 和 Planner 开始工作。", "lead", {"members": DEMO_TEAM})

    for task in DEMO_TASKS:
        emit("task_created", f"创建任务：{task['title']}", task["description"], "planner", {"task_id": task["id"], "task": task})
        emit("task_updated", f"开始任务：{task['title']}", "任务进入处理中。", "lead", {"task_id": task["id"], "status": "in_progress"})
        emit("task_updated", f"完成任务：{task['title']}", "任务已完成。", "lead", {"task_id": task["id"], "status": "completed"})

    for path, content in DEMO_FILES.items():
        emit(
            "tool_call_finished",
            "工具调用：write_file",
            f"Wrote {len(content)} bytes",
            "coder",
            {"tool": "write_file", "input": {"path": path}, "output": f"Wrote {len(content)} bytes"},
        )
        emit(
            "file_changed",
            f"文件变更：{path}",
            f"Created {path}",
            "coder",
            {"path": path, "change_type": "created", "tool": "write_file"},
        )
        emit(
            "diff_updated",
            "Diff 已更新",
            f"{len(artifacts['changed_files'])} 个文件发生变化",
            "coder",
            {"diff": artifacts["diff"], "changed_files": artifacts["changed_files"], "source": "demo"},
        )

    emit("test_started", "Tester 开始验证", "验证新增、完成、删除、搜索和刷新保留。", "tester")
    emit(
        "test_finished",
        "Tester 验证通过",
        "手动验收路径全部通过。",
        "tester",
        {"status": "passed", "checks": ["create", "complete", "delete", "search", "localStorage"]},
    )
    emit(
        "preview_started",
        "预览已准备",
        "Demo Todo 页面已写入 workspace/demo-todo/index.html。",
        "lead",
        {"preview_url": artifacts["preview_url"]},
    )
    emit(
        "report_ready",
        "交付报告已生成",
        "报告已保存到 run 目录。",
        "lead",
        {"markdown": artifacts["report"], "changed_files": artifacts["changed_files"]},
    )
    emit(
        "traceability_ready",
        "需求追踪矩阵已生成",
        "需求、任务、文件和验证项已关联归档。",
        "lead",
        {"requirements": artifacts["requirements"], "coverage_rate": 1.0},
    )
    emit(
        "assistant_message",
        "Lead 完成交付",
        "Todo Web App Demo 已完成。你可以查看 Diff、预览和交付报告。",
        "lead",
    )
    store.update_session(thread_id, workspace_dir, status="completed")
    if status_callback:
        status_callback("completed")
    emit("done", "Demo Run 完成", "稳定演示流程已完成。", "lead", {"status": "completed"})


def demo_event_delay() -> float:
    """Return the bounded delay used by deterministic demo events."""
    try:
        return max(0.0, min(float(os.getenv("NANOCURSOR_DEMO_EVENT_DELAY", "0.08")), 2.0))
    except ValueError:
        return 0.08


def run_demo_workflow(thread_id: str, workspace_dir: str, artifacts: dict[str, Any] | None = None) -> None:
    """Execute and finalize a demo run without loading the legacy runtime."""
    from src.api.services.deterministic_run_service import run_deterministic_worker
    from src.api.services.runtime_registry_service import get_runtime_registry

    registry = get_runtime_registry()
    run_deterministic_worker(
        thread_id=thread_id,
        workspace_dir=workspace_dir,
        execute=lambda status_callback: emit_demo_run(
            thread_id=thread_id,
            workspace_dir=workspace_dir,
            store=registry.event_store,
            delay=demo_event_delay(),
            status_callback=status_callback,
            artifacts=artifacts,
        ),
        error_title="Demo Run 异常",
        registry=registry,
    )
