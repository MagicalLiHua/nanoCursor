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

## 13. 深度学习：把 Agent Loop 当成运行时合约

如果只说“我做了一个 Agent Loop”，面试官很容易把它理解成一个普通的 `while` 循环。真正值得讲的是：nanoCursor 的 Loop 不是随意循环，而是一组运行时合约。

这组合约可以拆成五层：

| 层级 | 解决的问题 | 对应实现 |
|---|---|---|
| 输入合约 | 用户这句话到底要不要进入开发流程 | `intent_router.py`、`conversation_run_service.py` |
| 状态合约 | 当前 run 到了哪一步，谁在做事，有没有待审批 | `AgentLoopState` |
| 动作合约 | 下一步动作必须被结构化描述 | `LeadAction`、`AgentLoopStep` |
| 策略合约 | 动作执行前必须被权限、风险和上下文检查 | `check_loop_action`、tool policy |
| 证据合约 | 每一步都要留下事件、任务、工具证据或失败记录 | EventStore、task board、artifact |

这也是它和“写一个大函数顺序调用模型”的区别。顺序调用模型的问题是：失败后很难知道到底哪里错了，也很难在前端展示系统正在干什么。Agent Loop 的核心价值不是“更玄学”，而是把模型决策拆成可记录、可检查、可恢复的小步。

## 14. 真实链路：一句话怎么进入 Loop

一条用户消息大致会经过下面路径：

```text
前端发送消息
  -> POST /api/conversations/{conversation_id}/runs
  -> start_conversation_run
  -> classify_user_intent_async
  -> compose_runtime_team_async
  -> lead_only_execution_plan 或 build_execution_plan_async
  -> start_standard_run
  -> init_agent_loop_state
  -> workflow_thread_service 后台执行
  -> Agent Loop 观察/决策/执行/记录
  -> SSE 推送到前端
```

这里最容易混淆的是 `execution_plan` 和 `AgentLoopState`：

- `execution_plan` 是边界：告诉系统本轮有哪些阶段、验收标准、风险和工具约束。
- `AgentLoopState` 是账本：记录当前实际执行到了哪里，每一步发生了什么。

所以 nanoCursor 后来避免把系统做回 DAG。Plan 可以约束方向，但不应该把每一步写死。比如测试失败后，下一步可能是读取错误、修代码、修测试、请求审批，也可能直接终止并解释风险。这个判断必须看运行时状态。

## 15. 三类任务对比

建议你用下面三类任务理解 Loop 的分叉。

| 用户输入 | 期望路由 | Loop 行为 | 不应该出现什么 |
|---|---|---|---|
| `哈喽` | `lead_direct_reply` | Lead answer -> finish | Coder、Tester、完整交付报告 |
| `帮我看看这个目录下有哪些文件` | read only | Lead 读取工作区/文件索引 -> answer | 写文件、跑安装命令 |
| `帮我写常见排序算法并比较性能` | code delivery | 计划 -> 写文件 -> 运行验证 -> 交付总结 | 无审批的高风险 shell、无限重试 |

这个表面上是交互体验，背后其实是系统成熟度。成熟 AI 编程工具会让用户觉得“它知道什么时候该认真干活，什么时候只是回答我”。这件事不能只靠前端隐藏任务卡，必须在后端意图路由和 Loop 入口就做对。

## 16. 代码阅读任务

你可以按下面方式读一遍源码，不要一上来就读 `engine.py` 这种大文件。

1. 打开 `src/api/services/conversation_run_service.py`，找到 `start_conversation_run`，确认意图判断发生在 execution plan 之前。
2. 打开 `src/api/services/run_start_service.py`，找到 run 如何生成 `thread_id`、如何绑定 workspace、如何创建 EventStore session。
3. 打开 `src/api/services/agent_loop_state_service.py`，看 `AgentLoopState`、`AgentLoopStep`、`append_loop_step`。
4. 打开 `src/api/services/agent_loop_controller_service.py`，看 `run_loop_controller_step` 和 `propose_next_loop_action`。
5. 打开 `src/api/services/workflow_thread_service.py`，确认长任务为什么不阻塞 HTTP 请求。
6. 打开前端聊天和右侧进度组件，观察后端事件如何被渲染成用户能看懂的 Agent 动态。

读完后你应该能画出这张简化图：

```text
Intent Decision
  -> Runtime Team
  -> Execution Plan
  -> AgentLoopState
  -> Loop Step
  -> Tool / Answer / Approval / Finish
  -> EventStore + SSE
```

## 17. 面试深挖回答模板

### 30 秒回答

nanoCursor 的核心执行模型是 Lead 驱动的 Agent Loop。它不是固定 DAG，而是让 Lead 根据当前 run 的状态持续观察、决策和执行。为了避免 Loop 失控，我把每一步动作结构化，并加入 dry-run 校验、工具权限、审批、最大步数、EventStore 和任务板完成条件。这样既保留了 Agent 的动态性，也能让运行过程可观测、可恢复。

### 深入回答

我早期尝试过固定流程，但发现 AI 编程任务里的分支太多：简单问候不该进入开发流程，读文件任务不该写代码，测试失败后也不一定直接进入 Reviewer。后来我把 execution plan 定位成边界和验收标准，把实际执行交给 Agent Loop。Loop 每一步会先观察当前任务、上下文、失败、审批和工具状态，再提出结构化动作，动作通过策略检查后才能提交到状态账本，并通过 SSE 展示给前端。

这个设计的重点不是“循环更智能”，而是把模型行为变成可审计的小步。出问题时，我能知道是意图路由错了、工具策略拦截了、上下文没选中关键文件，还是某个 Agent 的动作不合理。

### 诚实边界

当前 Loop 决策还不是完全由模型自主规划，里面仍有一些规则和策略函数。这样做是为了稳定性和可测试性。后续更成熟的方向是把 Lead 的决策升级成“模型判断 + 结构化 schema + deterministic guard + 事后评估”的组合，而不是完全硬编码，也不是完全相信模型。

## 18. 容易被问倒的问题

### Q1：你说不用 DAG，那是不是失去了可控性？

不是。固定 DAG 是一种控制方式，但不是唯一方式。nanoCursor 用运行时合约控制：动作结构化、每步校验、工具分级、审批、最大步数、事件持久化和任务完成条件。它放弃的是固定路径，不是放弃控制。

### Q2：为什么不是让模型每一步都自由决定？

因为本地代码修改涉及文件写入、shell、依赖安装、git 等高风险操作。模型可以提出动作，但动作必须经过策略层检查。成熟系统一般都不会让模型直接裸执行工具。

### Q3：Loop 怎么避免陷入无限修复？

主要靠四类限制：最大步数、失败分类、恢复策略、终止条件。比如命令失败后可以生成恢复任务，但恢复任务也要受到次数和风险约束；如果连续失败或触发高风险，就应该请求用户确认或停止，而不是一直修。

### Q4：多 Agent 是 Loop 的核心吗？

不是。Loop 的核心是状态驱动决策，多 Agent 只是其中一种动作。默认只有 Lead，只有任务需要分工时才创建临时 Agent。这样系统不会为了展示多 Agent 而多 Agent。

## 19. 学完本章你应该能做到

读完这一章后，至少要能做到四件事：

1. 看着一次 run 的事件流，说出它为什么走直接回答或代码交付。
2. 指出 Agent Loop 状态保存在哪、每一步动作保存在哪。
3. 解释为什么 execution plan 不是 DAG，也不是摆设。
4. 面对“为什么不用 LangGraph”这个问题，能从交互式编程任务的不确定性讲到运行时合约。

## 20. 动手练习

1. **跟踪一次完整的 Agent Loop**：打开 `src/api/services/conversation_run_service.py`，从 `start_conversation_run` 开始，追踪到 `start_workflow_thread`，画出从 API 请求到 Agent Loop 启动的调用链。
2. **读 Loop State 的持久化代码**：打开 `src/api/services/agent_loop_state_service.py`，找到 `append_loop_step` 函数，理解每一步动作如何被记录。然后看 `AgentLoopState` 模型，列出所有字段及其含义。
3. **模拟 Lead direct reply**：在 `propose_next_loop_action` 中找到 `lead_direct_reply` 的处理逻辑，用自己的话写出这个分支的决策树。
4. **观察前端 Agent 动态**：启动项目后发送一条开发任务，在前端聊天框里观察 `AgentActivityStream` 的变化——每一步 Loop 动作如何对应前端的一条状态更新。
