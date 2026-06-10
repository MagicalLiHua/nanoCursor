# 04. Agent 编排：该少的时候少，该分工的时候分工

最后更新：2026-06-08

## 1. 本章目标

读完本章，你应该能回答：

- nanoCursor 的多 Agent 设计为什么是"默认只有 Lead"，而不是"默认四个角色一起上"？
- 临时子 Agent（ephemeral agent）和永久团队成员有什么区别？为什么这个区分很重要？
- 并行 Agent 为什么只做只读预分析，不直接写文件？
- 系统如何根据用户 prompt 自动建议需要哪些临时 Agent？
- 子 Agent 的生命周期是如何管理的（创建、执行、完成、归档、过期）？

## 2. 核心设计原则：按需分工，不堆角色

nanoCursor 最早也走过"默认多 Agent"的路——每次请求都出现 Planner、Coder、Reviewer、Tester。但后来发现：

- 用户说"哈喽"，不需要四个 Agent。
- 用户说"Python 和 Java 谁更好"，不需要 Coder 和 Tester。
- 只有真正的开发任务才需要分工。

所以现在的核心原则是：

```text
默认只有 Lead。
Lead 根据任务复杂度判断是否需要临时子 Agent。
临时 Agent 完成本轮任务后归档，不污染长期团队。
```

这个设计比"一上来就四个 Agent"更像真实产品。Codex/Cursor 也不会对每条消息都展示多个 Agent。

### 不同任务的路由策略

| 任务类型 | Agent 配置 | 说明 |
|---------|-----------|------|
| 问候/解释/讨论 | Lead 直接回答 | 不创建任何子 Agent |
| 只读分析 | Lead + 项目索引/只读工具 | Lead 自己读，不分派 |
| 小代码修改 | Lead + Coder | 单一写作者 |
| 中等开发任务 | Lead + Planner + Coder + Reviewer | 有计划和复核 |
| 高风险任务 | 增加 Tester / Security / Migration | 加安全校验层 |

## 3. 代码地图

核心文件：

| 文件 | 职责 |
|------|------|
| `src/api/services/ephemeral_agent_service.py` | 临时 Agent 的完整生命周期管理 |
| `src/api/services/parallel_agent_service.py` | 并行 Agent 的调度、执行、结果合并 |
| `src/api/services/conversation_run_service.py` | 运行时团队组合和意图对齐 |
| `src/agent/agent_pool.py` | Agent 池管理 |

## 4. 临时 Agent 的数据模型

临时 Agent 的完整结构定义在 `ephemeral_agent_service.py` 中。它不只是"一个名字加一个角色"，而是一个有明确边界、权限、生命周期的执行单元：

```python
# src/api/services/ephemeral_agent_service.py
MAX_SUGGESTED_AGENTS = 5
MAX_ACTIVE_AGENTS = 3
DEFAULT_TTL_SECONDS = 30 * 60

ACTIVE_STATUSES = {"suggested", "active", "working", "waiting_input"}
ARCHIVED_STATUSES = {"archived", "expired"}
```

每个临时 Agent 记录以下关键字段（通过 `_normalise_agent_spec` 生成）：

```python
return {
    "agent_id": agent_id,
    "thread_id": thread_id,
    "parent_agent": "Lead",        # 始终由 Lead 派生
    "name": name,
    "role": role,                   # frontend_worker / backend_worker / test_worker / ...
    "status": status,               # suggested -> active -> working -> archived
    "goal": goal,                   # 本轮目标
    "reason": reason,               # 为什么创建这个 Agent
    "tools": tools,                 # 可用工具列表
    "capabilities": capabilities,   # 能力标签
    "mcp_servers": mcp_servers,    # 可用的 MCP server
    "blocked_capabilities": [],     # 被阻止的能力
    "risk_level": risk_level,       # low / medium / high
    "task_scope": task_scope,       # 工作范围（include/exclude/allowed_actions）
    "expected_output": expected_output,
    "created_at": now,
    "started_at": now if active else 0,
    "completed_at": 0,
    "archived_at": 0,
    "expires_at": now + TTL,        # 超时自动归档
    "result": {},
}
```

这里的设计重点：

- **parent_agent 始终是 Lead**：子 Agent 不能再创建子 Agent，避免级联膨胀。
- **task_scope 三元组**：`include`（关注哪些目录）、`exclude`（不碰哪些目录）、`allowed_actions`（能做什么操作）。
- **TTL 自动过期**：30 分钟不活跃就自动归档，不会永久挂着。
- **最大活跃数 3**：防止无限制创建子 Agent。

## 5. 如何根据 prompt 自动建议 Agent

`suggest_ephemeral_agents` 函数通过关键词匹配来判断用户任务需要哪种临时 Agent。这不是 LLM 调用，而是确定性规则——快速、免费、可预测：

```python
# src/api/services/ephemeral_agent_service.py
def suggest_ephemeral_agents(
    prompt: str,
    mcp_plan: list[dict[str, Any]] | None = None,
    workspace_dir: str | None = None,
    max_agents: int | None = 4,
) -> dict[str, Any]:
    text = str(prompt or "").lower()

    suggestions: list[dict[str, Any]] = []

    if _contains_any(text, ["前端", "界面", "ui", "样式", "交互", "页面", "组件", "frontend"]):
        suggestions.append(_suggestion(
            "Frontend Action Agent",
            "frontend_worker",
            "实现或修复前端界面、交互和状态展示。",
            "检测到前端、界面或交互需求，需要独立前端执行者。",
            ["tool.file_ops", "tool.project_index", "skill.frontend-polish"],
            task_scope=_scope(["frontend", "tests", "docs"], ["src/api"], ...),
            expected_output=_expected(tests=True),
        ))

    if _contains_any(text, ["接口", "api", "服务", "数据库", "状态", "路由", "fastapi", "后端", "backend"]):
        suggestions.append(_suggestion(
            "Backend Action Agent", "backend_worker", ...
        ))

    if _contains_any(text, ["测试", "验证", "回归", "覆盖", "pytest", "smoke", "质量"]):
        suggestions.append(_suggestion(
            "Test Action Agent", "test_worker", ...
        ))

    if _contains_any(text, ["readme", "文档", "说明", "计划", "接口文档", "docs"]):
        suggestions.append(_suggestion(
            "Docs Action Agent", "docs_worker", ...
        ))

    if _contains_any(text, ["重构", "完整", "系统", "产品级", "端到端", "复杂"]):
        suggestions.append(_suggestion(
            "Reviewer", "reviewer",
            "复核跨模块变更、风险、测试证据和最终交付可信度。",
            ...
        ))
```

几个设计细节值得注意：

1. **去重机制**：相同 role 的 Agent 只保留一个（`seen_roles`）。
2. **数量上限**：最多 `MAX_SUGGESTED_AGENTS`（5个），实际执行时最多 `MAX_ACTIVE_AGENTS`（3个）。
3. **兜底策略**：如果没匹配到任何领域，创建一个通用的 `implementation_worker`。
4. **MCP 感知**：如果用户提到 GitHub/Figma，会检查对应 MCP server 是否可用，不可用则加入 `blocked_capabilities`。

## 6. 临时 Agent 的生命周期

完整的生命周期是一个状态机：

```text
suggested → active → working → completed → archived
                 ↘       ↘
                  expired   failed → archived
```

### 6.1 创建（spawn）

```python
# src/api/services/ephemeral_agent_service.py
def spawn_ephemeral_agent(thread_id: str, spec: dict[str, Any], workspace_dir: str) -> dict[str, Any]:
    state = _read_state(thread_id, workspace_dir)
    agents = state["agents"]
    if _active_count(agents) >= MAX_ACTIVE_AGENTS:
        raise ValueError(f"临时子 Agent 数量已达到上限: {MAX_ACTIVE_AGENTS}")

    existing_ids = {str(agent.get("agent_id")) for agent in agents}
    agent = _normalise_agent_spec(thread_id, spec, existing_ids, status="active", workspace_dir=workspace_dir)
    agents.append(agent)
    _write_state(thread_id, workspace_dir, state)
    _emit_agent_event(thread_id, workspace_dir, "ephemeral_agent_spawned", agent, agent.get("reason", ""))
    return agent
```

创建前会检查：
- 活跃 Agent 数量是否已达上限（3个）。
- Agent ID 是否冲突，冲突时自动追加序号。
- task_scope 是否与当前 run intent 一致（`_normalize_task_scope_for_run`）。

### 6.2 任务范围校验

`_normalize_task_scope_for_run` 会根据当前 run 的 intent 限制子 Agent 的权限：

```python
# src/api/services/ephemeral_agent_service.py
def _normalize_task_scope_for_run(thread_id, workspace_dir, task_scope):
    intent = _current_intent_decision(thread_id, workspace_dir)
    route = str(intent.get("route") or "")

    if execution_route == "lead_direct_reply":
        raise ValueError("当前是 Lead 直接回答任务，不允许创建临时子 Agent。")

    if route in {"read_only", "review_only"} or (intent and not requires_write):
        # 只读任务：子 Agent 也只能读
        allowed_actions = [a for a in allowed_actions if a in (READ_ONLY_ACTIONS | MCP_ACTIONS)]
```

这很重要：即使是临时 Agent，也不能绕过 run 级别的权限限制。

### 6.3 完成与归档

```python
# src/api/services/ephemeral_agent_service.py
def complete_ephemeral_agent(thread_id, agent_id, result, workspace_dir):
    agent["terminal_status"] = "completed"
    agent["status"] = "archived"
    agent["completed_at"] = now
    agent["archived_at"] = now
    agent["result"] = {
        "summary": str(result.get("summary") or ""),
        "evidence": result.get("evidence") if isinstance(result.get("evidence"), list) else [],
        "risks": result.get("risks") if isinstance(result.get("risks"), list) else [],
        "artifacts": result.get("artifacts") if isinstance(result.get("artifacts"), list) else [],
        "recommended_next_actions": ...,
    }
```

完成和归档是原子操作：一旦完成，状态直接变成 `archived`。这样 Lead 在后续循环中不会看到已完成的历史 Agent。

### 6.4 超时过期

```python
# src/api/services/ephemeral_agent_service.py
def cleanup_expired_ephemeral_agents(thread_id, workspace_dir):
    for agent in state["agents"]:
        if agent.get("status") in ARCHIVED_STATUSES:
            continue
        expires_at = float(agent.get("expires_at") or 0)
        if expires_at and expires_at <= now:
            agent["terminal_status"] = "expired"
            agent["status"] = "expired"
```

每次 `list_ephemeral_agents` 调用都会先清理过期 Agent。这是被动 GC，不需要后台线程。

## 7. 并行 Agent：只读预分析，不抢着写文件

并行 Agent 的核心设计原则是：**子 Agent 只做只读调研，不直接修改文件**。

### 7.1 什么时候启动并行 Agent

```python
# src/api/services/parallel_agent_service.py
def should_run_parallel_briefing(execution_plan: dict[str, Any] | None) -> bool:
    if not isinstance(execution_plan, dict) or not execution_plan:
        return False
    if execution_plan.get("strategy") == "lead_direct_reply":
        return False
    stages = execution_plan.get("stages")
    if not isinstance(stages, list) or len(stages) <= 1:
        return False
    return True
```

三个条件缺一不可：
1. 有有效的 execution_plan。
2. 不是 Lead 直接回答。
3. 执行阶段多于 1 个（说明任务有一定复杂度）。

### 7.2 并行执行流程

```python
# src/api/services/parallel_agent_service.py
async def run_parallel_agent_briefing(*, thread_id, prompt, workspace_dir,
                                       execution_plan, runner, emit_event, tools, max_agents=3):
    suggestions = suggest_ephemeral_agents(prompt, ...).get("suggestions", [])
    specs = suggestions[:max(1, min(max_agents, DEFAULT_PARALLEL_LIMIT))]

    # 1. 批量创建 Agent
    agents = [spawn_ephemeral_agent(thread_id, spec, workspace_dir) for spec in specs]

    # 2. 发射"并行启动"事件
    emit_event(thread_id=thread_id, event_type="parallel_agents_started", ...)

    # 3. 并发执行（用 Semaphore 限流）
    semaphore = asyncio.Semaphore(max(1, min(len(agents), DEFAULT_PARALLEL_LIMIT)))
    results = await asyncio.gather(*[_run_one(agent) for agent in agents])

    # 4. 汇总贡献
    contributions = summarize_ephemeral_agent_contributions(thread_id, workspace_dir)

    # 5. 生成合并策略
    merge_plan = build_parallel_merge_plan(thread_id, workspace_dir, execution_plan, proposal_artifact)
```

关键设计点：

- **Semaphore 限流**：最多 3 个并行，防止资源耗尽。
- **只读约束**：每个子 Agent 的 system prompt 里明确写了"不要写文件、不要修改代码"。
- **文件冲突检测**：如果多个 Agent 关注同一文件，`_detect_proposal_conflicts` 会标记为风险。
- **合并策略**：结果不直接应用，而是生成 `merge_plan` 供 Lead 参考。

### 7.3 子 Agent 的 system prompt

```python
# src/api/services/parallel_agent_service.py
def _worker_system(agent: dict[str, Any], workspace_dir: str) -> str:
    return (
        f"你是 nanoCursor 的临时子 Agent：{agent.get('name')} ({agent.get('role')})。\n"
        f"工作区: {workspace_dir}\n"
        "你只能做只读分析、搜索、阅读和风险判断；不要写文件、不要修改代码、不要执行会改变项目状态的命令。\n"
        "输出要短而结构化，包含 Summary、Evidence、Risks、Recommended Next Actions。"
    )
```

这个约束非常明确。它保证了并行 Agent 不会在 Lead 不知情的情况下修改代码。

### 7.4 结果合并与冲突检测

```python
# src/api/services/parallel_agent_service.py
def _detect_proposal_conflicts(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    file_to_agents: dict[str, list[str]] = {}
    for proposal in proposals:
        agent_name = proposal.get("name") or proposal.get("role") or "unknown"
        for path in proposal.get("suggested_files", []):
            if path:
                file_to_agents.setdefault(str(path), []).append(agent_name)
    return [
        {"file": file, "agents": agents}
        for file, agents in file_to_agents.items()
        if len(agents) > 1
    ]
```

当多个 Agent 都建议修改同一文件时，这被标记为冲突。Lead 需要决定谁来写，或者合并两边的建议。

## 8. 设计取舍：为什么不是"默认四个 Agent"

### 取舍 1：默认少 vs 默认多

成熟工具的体验是"看起来只有一个 AI 在帮你"。nanoCursor 选择默认只有 Lead，是因为：
- 大多数用户消息不是开发任务。
- 多个 Agent 会制造前端噪声。
- 临时创建比默认创建更能体现"按需分工"。

### 取舍 2：确定性建议 vs LLM 判断

`suggest_ephemeral_agents` 使用关键词匹配，没有调用 LLM。原因是：
- 关键词匹配快速、免费、确定性。
- 不需要消耗 token 来判断"这是不是前端任务"。
- LLM 判断可以留给 Lead 在实际执行时做。

### 取舍 3：只读并行 vs 读写并行

并行 Agent 只做只读预分析，不写文件。原因是：
- 并行写文件会带来冲突、覆盖和回滚复杂度。
- 文件修改应该串行化，由 Lead 统一调度。
- 只读分析"先收集证据，再统一修改"更安全。

## 9. 当前不足和后续方向

- 临时 Agent 的创建目前基于关键词，对复杂语义判断（如"重构整个认证模块"具体需要哪些 Agent）还不够精确。
- 并行 Agent 的结果目前是 Lead 手动合并，没有自动化的冲突解决策略。
- Agent 之间的通信目前通过 EventStore 和结果对象，缺乏直接的 Agent-to-Agent 消息通道。
- 临时 Agent 的执行结果评估（是否真的帮到了任务）目前没有量化。

## 10. 面试预备问题

### Q1：为什么不是每个请求都创建四个 Agent？

因为大多数用户消息（问候、讨论、简单问答）根本不需要多 Agent。默认四个 Agent 只会制造前端噪声，也让系统看起来像"演示系统"而非"实用工具"。nanoCursor 选择默认只有 Lead，再按需创建临时 Agent，更接近成熟 AI 编程工具的交互习惯。

### Q2：临时 Agent 和永久团队成员有什么区别？

临时 Agent 是 run-scoped：完成本轮任务后自动归档，不会出现在下次会话的团队列表中。永久团队成员需要用户明确确认才会保留。这个区分的核心价值是：不让临时分工污染长期团队结构。

### Q3：并行 Agent 为什么不直接写文件？

并行写文件会带来冲突、覆盖和回滚的复杂度。当前设计是"先并行调研，再串行修改"——并行 Agent 做只读分析，Lead 统一决策和写入。这兼顾了效率和安全性。

### Q4：Agent 数量上限为什么是 3 个？

MAX_ACTIVE_AGENTS = 3 是一个工程取捨：太少（1个）等于没有并行，太多（比如 10 个）会导致上下文分散、前端难以展示、并行协调复杂度急剧上升。3 个刚好覆盖最常见的前端/后端/测试三分工。

### Q5：如果 LLM 建议了一个不需要的 Agent 怎么办？

当前的建议机制是关键词匹配而非 LLM，不会凭空创建 Agent。如果关键词误匹配（如用户说"前端"但不是要改前端代码），`_normalize_task_scope_for_run` 会根据当前 run 的 intent 限制子 Agent 的权限，且 Lead 始终可以选择不使用子 Agent 的结果。

## 11. 自测题

1. `MAX_SUGGESTED_AGENTS` 和 `MAX_ACTIVE_AGENTS` 的区别是什么？为什么建议数量可以大于活跃数量？
2. 如果用户说"帮我写个 README"，系统会建议哪些临时 Agent？
3. 如果当前任务是 `lead_direct_reply`，为什么不能创建临时 Agent？
4. 并行 Agent 的 system prompt 里最关键的一句约束是什么？
5. 两个并行 Agent 都建议修改 `src/api/server.py`，系统会怎么处理？

## 12. 动手练习

1. 阅读 `suggest_ephemeral_agents` 的完整实现，尝试输入不同的 prompt，看系统会建议哪些 Agent。
2. 跟踪一次并行 Agent 的执行流程：从 `should_run_parallel_briefing` 到 `build_parallel_merge_plan`。
3. 在 EventStore 中找到 `ephemeral_agent_spawned`、`ephemeral_agent_completed`、`ephemeral_agent_archived` 三种事件，理解 Agent 生命周期。
