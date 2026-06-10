# 03. Agent Loop：从固定流程到持续决策

最后更新：2026-06-08

## 1. 本章目标

这一章要吃透 nanoCursor 的核心执行模型：它不是 LangGraph 风格的固定 DAG，也不是“几个 Agent 顺序喊话”的演示系统，而是围绕一次用户请求持续循环：

```text
观察当前状态 -> 决定下一步 -> 校验动作是否合法 -> 执行或等待审批 -> 记录证据 -> 判断继续还是结束
```

你需要掌握三件事：

- 为什么 AI 编程更适合 Agent Loop，而不是一开始就把所有步骤画死。
- nanoCursor 如何让 Loop 可控：最大步数、工具策略、任务板、事件日志、完成条件。
- 简单问答、读文件任务、代码修改任务在同一个 Loop 体系里如何走不同路线。

## 2. 为什么不是固定 DAG

早期项目曾经使用过 LangGraph。它的好处是流程清楚，但在真实代码任务中会遇到几个问题：

- 用户可能只问一句“Python 和 Java 谁更好”，这不应该启动 Planner / Coder / Tester。
- 代码修改后测试失败，下一步不是固定的“进入 Reviewer”，而是要先判断失败原因。
- 某个工具被安全策略拦截时，系统应该等待审批、换只读方案，或者询问用户。
- 连续对话里，用户第二轮可能改变任务范围，不能简单复用上一轮流程。

所以 nanoCursor 后来转成 Agent Loop：**流程不是提前写死，而是在边界内动态决策**。

## 3. 运行入口代码

一次会话内的用户消息先进入 `start_conversation_run`。这层会做意图判断、团队组合、执行计划构造，然后再交给标准 run 启动逻辑。

关键文件：

- `src/api/services/conversation_run_service.py`
- `src/api/services/run_start_service.py`
- `src/api/services/workflow_thread_service.py`
- `src/api/services/agent_loop_state_service.py`
- `src/api/services/agent_loop_controller_service.py`

核心片段：

```python
# src/api/services/conversation_run_service.py
intent_decision = await classify_user_intent_async(
    request.prompt,
    conversation_summary=str(conversation.get("conversation_summary") or ""),
)
is_simple = intent_decision.get("execution_route") == "lead_direct_reply"
runtime_composition = await compose_runtime_team_async(request.prompt, workspace_dir, conversation_id)

execution_plan = (
    lead_only_execution_plan(request.prompt, workspace_dir, members)
    if is_simple
    else await build_execution_plan_async(
        prompt=request.prompt,
        team=members,
        workspace_dir=workspace_dir,
    )
)
execution_plan["intent_decision"] = intent_decision
align_tool_policy_with_intent(execution_plan, intent_decision)
```

这段代码体现了一个重要取舍：**意图判断先于 Agent 编排**。如果是轻量消息，系统直接构造 `lead_only_execution_plan`，避免无意义地创建 Coder / Tester。

## 4. Loop State：这是 ledger，不是 graph

Agent Loop 的状态由 `AgentLoopState` 持久化。它记录当前 run 的意图、最大步数、当前步骤、活跃 Agent、待审批项和每一步动作。

```python
# src/api/services/agent_loop_state_service.py
class AgentLoopState(BaseModel):
    thread_id: str
    conversation_id: str | None = None
    workspace_dir: str
    user_request: str
    intent: IntentDecision
    current_step: int = 0
    max_steps: int = 20
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    active_agent: str = "Lead"
    context_pack_id: str | None = None
    pending_approval_id: str | None = None
    terminal_status: str | None = None
    steps: list[AgentLoopStep] = Field(default_factory=list)
```

这里的 `steps` 是 ledger：每一步都能被前端、测试和调试工具复盘。它不要求“下一步必须是哪条边”，只要求每个动作都可解释、可校验、可追踪。

为什么这点重要？

- 面试时可以说清楚：nanoCursor 避开了 LangGraph 的固定图，但没有放弃可观测性。
- 出错时可以定位：第几步、哪个 Agent、什么动作、为什么被拦截。
- 前端可以显示：Lead 正在判断、Coder 正在写文件、Tester 正在验证。

## 5. Loop Controller：观察、提议、校验、提交

`agent_loop_controller_service.py` 保持得很薄。它不负责大模型调用，不直接变成一个“超级工作流框架”，只做控制层该做的事：

```python
# src/api/services/agent_loop_controller_service.py
def run_loop_controller_step(...):
    observation = get_loop_observation(thread_id, workspace_dir)
    candidate = action if isinstance(action, dict) and action else propose_next_loop_action(observation)
    initial_check = check_loop_action(thread_id, workspace_dir, candidate)

    selected_action = candidate
    if auto_repair and not initial_check.get("allowed"):
        repaired_action = initial_check["repaired_action"]
        repair_check = check_loop_action(thread_id, workspace_dir, repaired_action)
        if repair_check.get("allowed"):
            selected_action = repaired_action

    if commit and selected_check.get("allowed"):
        state = append_loop_step(...)
```

这段逻辑对应：

```text
观察 observation
  -> 候选动作 candidate
  -> dry-run 校验 check_loop_action
  -> 如果不合法，尝试自动修复
  -> 合法才 append_loop_step
```

这里最值得学习的是“dry-run action check”。一个成熟 Agent 系统不能等工具已经执行了才发现不该做。动作应该先被描述成结构化对象，再经过策略检查。

## 6. 简单任务如何直接回答

在 `propose_next_loop_action` 里，轻量请求会走 Lead direct route：

```python
if state.intent.execution_route == "lead_direct_reply":
    if last_action_type == "answer":
        return LeadAction(type="finish", goal="Finish after direct answer.", agent="Lead").model_dump()
    return LeadAction(
        type="answer",
        goal="Answer directly without creating tasks.",
        agent="Lead",
    ).model_dump()
```

这解决了之前前端体验里的一个核心问题：用户只是打招呼，系统不应该右侧生成一堆任务，也不应该出现 Tester。

成熟工具看起来“聪明”的原因之一，就是这种路由做得自然：

- 问候、概念解释：Lead 直接回答。
- 看文件、解释项目：Lead 读上下文，必要时调用只读工具。
- 小代码改动：Lead + Coder。
- 中等开发任务：Lead + Planner + Coder + Reviewer。
- 高风险任务：再引入 Tester / Security / Migration。

## 7. 完成条件与最大步数

Loop 不是无限循环。`append_loop_step` 会检查最大步数：

```python
if (
    state.current_step >= state.max_steps
    and action_model.type not in {"finish", "fail"}
):
    state = _mark_step_limit_exceeded(state)
    _save(state)
    raise LoopStepLimitExceeded(...)
```

这类限制在 Agent 系统里很关键。没有步数上限和预算上限，模型可能陷入“修一个错，又产生另一个错”的循环。

完成判断不是只看模型说“完成了”，而是结合：

- 任务板是否还有 pending / running / failed。
- 是否有待审批动作。
- 是否有风险或失败事件。
- 是否已经生成交付说明。
- 是否达到 Loop 的最大步数或预算。

## 8. Agent Loop 与多 Agent 的关系

在 nanoCursor 里，多 Agent 不是默认排场，而是 Loop 的一种动作选择。Lead 可以根据任务复杂度创建临时子 Agent，但子 Agent 完成后应归档，不污染长期团队。

对应代码：

- `src/api/services/ephemeral_agent_service.py`
- `src/api/services/parallel_agent_service.py`

临时 Agent 的状态是 run scoped：

```python
# src/api/services/ephemeral_agent_service.py
MAX_SUGGESTED_AGENTS = 5
MAX_ACTIVE_AGENTS = 3
DEFAULT_TTL_SECONDS = 30 * 60

ACTIVE_STATUSES = {"suggested", "active", "working", "waiting_input"}
ARCHIVED_STATUSES = {"archived", "expired"}
```

并行 Agent 的定位也很克制：先做只读 briefing，不直接抢着写文件。

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

这个设计点可以作为面试亮点：**多 Agent 的价值在于并行获取证据和分工复核，不在于把所有任务都拆成很多人说话**。

## 9. 你应该怎么读代码

建议按下面顺序读：

1. `conversation_run_service.py`：看一次消息如何被分类和启动。
2. `run_start_service.py`：看 run 如何注册、持久化、发事件。
3. `agent_loop_state_service.py`：看 loop ledger 如何存。
4. `agent_loop_controller_service.py`：看每一步如何观察、决策、校验。
5. `parallel_agent_service.py`：看临时 Agent 如何并行执行和合并。
6. 前端事件流：观察这些状态如何被渲染成聊天框里的 Agent 动态。

## 10. 面试预备问题

### Q1：Agent Loop 和 DAG 最大区别是什么？

DAG 强调预先定义节点和边，Agent Loop 强调运行时根据状态做下一步决策。AI 编程任务中，中间结果会频繁改变后续动作，所以 Loop 更自然。nanoCursor 用 ledger、工具策略、预算和审批补足 Loop 的可控性。

### Q2：不用 LangGraph 会不会不可控？

如果只是 while loop 确实不可控。nanoCursor 的做法是：动作结构化、每步 dry-run 校验、最大步数限制、工具权限分级、事件持久化、任务板完成度判断。它不是放弃控制，而是把控制从“固定图结构”转成“运行时合约”。

### Q3：为什么默认只有 Lead？

因为很多用户消息根本不是开发任务。默认四个 Agent 会制造噪声，也容易让前端看起来像演示系统。默认 Lead，再由 Lead 根据任务创建临时 Agent，更接近成熟 AI 编程工具的交互习惯。

### Q4：并行 Agent 为什么不直接写文件？

直接并行写文件会带来冲突、覆盖和回滚复杂度。nanoCursor 先让并行 Agent 做只读调研、风险发现、文件建议，再由 Lead 合并。真正写文件时仍进入工具治理和 evidence 流程。

## 11. 当前不足和后续方向

- Agent Loop 的决策质量目前缺乏量化评估：Lead 的判断是否正确？是否应该创建子 Agent？这些需要事后分析。
- `propose_next_loop_action` 目前是规则驱动的，对复杂场景的判断（如"测试失败后应该修代码还是改测试"）还不够智能。
- Loop 的最大步数和 token 预算是硬限制，缺乏动态调整机制——简单任务可能 5 步就够了，复杂任务可能需要 30 步。
- 完成条件目前偏保守（"所有任务完成 + 没有待审批 + 没有失败"），可能导致 Agent 在边界情况反复尝试。

## 12. 自测题

1. Agent Loop 和固定 DAG 的最大区别是什么？为什么 AI 编程更适合 Loop？
2. `AgentLoopState` 的 `steps` 字段为什么被称为 ledger？它和 LangGraph 的 state graph 有什么不同？
3. `agent_loop_controller_service.py` 中的 "dry-run action check" 是什么意思？为什么动作要先校验再执行？
4. 简单问候（`lead_direct_reply`）在 `propose_next_loop_action` 里走什么路径？最多几步完成？
5. Loop 的完成条件有哪些？为什么不能只看模型说"完成了"？
6. 如果 Loop 达到 `max_steps` 还没完成，系统会怎么做？
7. 临时 Agent 的最大数量、TTL 和状态转换分别是什么？

## 13. 动手练习

1. **跟踪一次完整的 Agent Loop**：打开 `src/api/services/conversation_run_service.py`，从 `start_conversation_run` 开始，追踪到 `start_workflow_thread`，画出从 API 请求到 Agent Loop 启动的调用链。
2. **读 Loop State 的持久化代码**：打开 `src/api/services/agent_loop_state_service.py`，找到 `append_loop_step` 函数，理解每一步动作如何被记录。然后看 `AgentLoopState` 模型，列出所有字段及其含义。
3. **模拟 Lead direct reply**：在 `propose_next_loop_action` 中找到 `lead_direct_reply` 的处理逻辑，用自己的话写出这个分支的决策树。
4. **观察前端 Agent 动态**：启动项目后发送一条开发任务，在前端聊天框里观察 `AgentActivityStream` 的变化——每一步 Loop 动作如何对应前端的一条状态更新。
