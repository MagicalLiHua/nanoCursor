# NanoCursor Goal 模式：源码调研与实施计划

调研日期：2026-09-30。状态：设计提案，尚未实现；本轮不修改 agent 运行代码。

执行优先级于 2026-09-30 调整：先完成 `docs/architecture-refactor-plan.md` 中的降低耦合重构及验收，再考虑本提案。Goal 设计保留，但当前不安排功能实现。

## 1. 结论与范围

建议做一个**会话级、可恢复、有预算的目标控制器**，而不是把“不要停下来”加进 system prompt，或者把现有 `max_iterations` 改大。

Goal 模式的契约：用户明确设置目标后，当前回合结束不一定代表目标完成；控制器在安全边界检查目标状态、预算、用户输入、恢复状态和验收证据，必要时启动下一回合。用户暂停、等待审批、恢复保护、预算耗尽和完成验收，均能真正阻止续跑。

三个独立概念：

| 概念 | 解决的问题 | 不应承担的职责 |
| --- | --- | --- |
| Plan | 下一步如何做、任务如何分解 | 不保证继续执行，也不证明目标完成 |
| 工具循环 | 模型调用工具后继续接收结果 | 不决定整个目标是否完成 |
| Goal 控制器 | 何时继续、何时等待、何时真正结束 | 不绕过权限，不自动重放未知副作用 |

本版不做：后台定时唤醒、多 Goal 并行、独立多 agent 调度平台、自动提交 Git、自动放宽权限、美元成本预测、额外向量数据库。

## 2. 代表性开源实现

以下是机制对照，不是项目排名。只有确认存在原生目标对象的实现才称为原生 Goal；普通 agent loop、hook 续跑和 recipe retry 不混称为 Goal 模式。来源路径与固定提交见第 9 节。

### 2.1 Codex：原生持久化 Goal + 空闲续跑

- `ThreadGoal` 保存目标、状态、预算、累计 token 和执行时间；状态包括 `active`、`paused`、`blocked`、`usage_limited`、`budget_limited`、`complete`。[C2]
- `get_goal` / `create_goal` / `update_goal` 给模型读取与更新目标的接口，但更新权限受限制，不是模型任意修改状态。[C3]
- Goal extension 参与 thread/turn 生命周期；`continue_if_idle()` 检查状态与 deferral，通过宿主的 `start_turn_if_idle()` 原子接纳续跑，而不是递归无条件发送“继续”。[C4]
- 会计模块按增量归集主线程与后代 token；当前源码预算口径是非缓存输入加输出，不能误说成所有输入 token 或美元成本。[C5]
- 完成审计、重复阻塞审计在 steering 提示中有详细要求；这些提示不是一个能独立证明任意代码目标正确性的确定性验证器。[C6]

**适合借鉴**：目标是一等状态、暂停与结束由宿主管控、运行空闲时再续跑、目标更新和续跑之间防竞态、用量独立于上下文压缩。

### 2.2 OpenCode：成熟工具循环 + 重复调用保护

- 会话循环会检查 assistant 的结束原因以及未完成的工具调用；普通最终回复且没有待处理工具时可结束，不等于所有情况都会无限续跑。[O1]
- `agent.steps` 约束执行步数；`doom_loop` 根据近期相同工具及相同参数触发权限询问，而不是只看工具名。[O1/O2]
- processor 能给出 `compact` / `stop` / `continue` 等执行决策。[O2]

**适合借鉴**：显式停止原因、不要遗漏未收尾工具、重复动作检测与权限系统联动。本次核对的这些模块不能作为“OpenCode 已有与 Codex 相同原生 Goal”的证据。

### 2.3 Gemini CLI：AfterAgent 续跑 + 多层防循环

- 在没有待处理工具的回合边界运行 `AfterAgent`；阻止停止的 hook 可以把原因送回模型，启动后续调用，并携带 `stopHookActive`。[G1]
- 有回合上限；防循环服务检查工具参数重复、文本重复，并包含语义层循环检查。[G1/G2]
- 源码特别区分“同一结果不断重复”和“改完代码后重新运行测试”，后者不能直接判成死循环。[G2]

**适合借鉴**：边界检查、防循环不只看调用次数、续跑需要携带来源。本项目已有 hook 不应直接假设具备同样的停止否决能力。

### 2.4 Goose：外部成功检查驱动有限重试

- retry 模块在配置的成功检查不满足时准备后续尝试，执行次数受配置约束。[B1]
- 该模块支持 shell 成功检查、超时和 `on_failure`；当前实现会把对话恢复到初始消息再重试，这不等于回滚工作区或外部副作用。[B1]
- 外部检查能给出退出码等实际结果，但命令执行成功仍不必然证明整个自然语言目标完成。

**适合借鉴**：把用户认可的测试或检查当作可核查的完成条件，失败时将实际结果反馈给 agent。NanoCursor 应保留执行历史与恢复账本，不照搬对话重置。当前文档只据已打开的 retry 模块描述机制，不声称已经核对所有调用路径。

### 2.5 Ralph Wiggum：轻量 Stop hook 对照

- 官方插件通过 Stop hook 读取本地迭代状态；未达到上限或指定 completion promise 时，返回阻止停止的结果，并把原始 prompt 重新送回。[R1/R2]
- completion promise 来自模型输出匹配，不是独立测试通过证明。
- 这是开源插件机制，不把 Claude Code 的完整 harness 称为开源。[R1]

**适合借鉴**：快速验证“最终回复之后再启动一次”的垂直切片。不照搬“反复发原 prompt + 匹配 DONE”作为可靠产品方案。

## 3. NanoCursor 当前基础与缺口

以下依据本地工作树分析，包含用户已有未提交修改；这些修改未被覆盖。

| 位置 | 已有能力 | 与 Goal 的关系 |
| --- | --- | --- |
| `nanocursor/agent.py:586` | `Agent.run()` 管理恢复上下文与待处理工具收尾 | 每次 Goal 回合必须复用，不可绕过 |
| `nanocursor/agent.py:667` | `_run()` 已有工具循环、压缩、错误保护、iteration 限制 | 保留作为内层执行器 |
| `nanocursor/agent.py:893` | 无工具调用时进入最终回复分支，随后产生 `LoopComplete` | 当前回合完成与目标完成之间的关键分界 |
| `nanocursor/agent.py:966` | `ExitPlanMode` 也会结束循环 | 不能误判为 Goal 完成或跳过用户的计划确认 |
| `nanocursor/agent.py:112` | `UsageEvent` 暴露累计输入、输出 | 不能直接用作跨回合预算账本，缺缓存口径与缺失用量信息 |
| `nanocursor/tools/base.py:95` | `StreamEnd` 已有缓存和 `usage_available` | 可扩展规范化单请求用量，不必丢失现有数据 |
| `nanocursor/app.py:1799` | TUI 的消息发送与事件消费 | 需接入共享 Goal runner，不单独复制续跑逻辑 |
| `nanocursor/__main__.py:502` | headless 的 `consume_turn()` | 需输出明确终态与机器可读 Goal 事件 |
| `nanocursor/remote.py:367` | remote 直接消费 `Agent.run()` | 和 TUI/headless 共享同一控制器 |
| `nanocursor/recovery/store.py:44` | SQLite 运行、操作、outbox、metadata 与 owner 记录 | 可复用持久化和排他所有权 |
| `nanocursor/recovery/store.py:239` | 通用 metadata 读写 | 第一版可承载有版本的 Goal 数据，不另建数据库 |
| `nanocursor/memory/session.py:374` | session metadata、JSONL 和 resume | 目标与预算不能依赖会被压缩的对话文本 |
| `nanocursor/hooks/events.py:6` | 生命周期事件 | 目前并没有可直接否决最终停止的专用协议 |
| `nanocursor/driver.py:12` | Textual 终端 driver | 不是 agent 调度器，不应在这里实现 Goal |

还有一条重要语义：恢复层的 run 结束记录不是业务目标完成证明；`LoopComplete` 也不是验收证据。

## 4. 推荐结构

```text
TUI / headless / remote
        ↓ 共享入口；直接用户输入与内部续跑分开
GoalRunner —— GoalController —— GoalStore / UsageLedger
        ↓                    ↘ AcceptanceGate / ProgressGuard
现有 Agent.run() → LLM + tools + permissions + recovery
        ↓ 正常回合结束、错误、中断、计划边界
控制器重新检查 → 完成 / 等待 / 停止 / 接纳一次续跑
```

建议新建 `nanocursor/goals/`，先保持模块小而聚焦：

- `models.py`：`GoalState`、预算、验收条件、停止原因、单回合结果。
- `store.py`：基于 `RecoveryStore` 的状态、revision/CAS 和幂等用量写入。
- `controller.py`：确定性状态转移、准入与预算判断。
- `runner.py`：现有 `Agent.run()` 外层的回合调度与事件适配。
- `verification.py`：完成申请校验、已有工具结果和可选检查命令。
- `progress.py`：轻量重复动作与无进展保护；先不新增专用 LLM 判定器。

Goal 不是新的 `PermissionMode`：它与默认、审批、Plan、sandbox 等策略正交。Goal 开启不授予任何额外权限。未开启 Goal 时，保持普通聊天行为。

### 4.1 数据与状态契约

每个 session 同时最多一个当前 Goal；替换未完成目标需要用户明确操作。归档旧目标，而不是悄悄覆盖。

核心数据建议：

```text
goal_id, session_key, objective, revision, status, reason
acceptance_criteria[], evidence_refs[], progress_summary
token_budget?, max_requests, max_active_seconds
measured_tokens, estimated_tokens, missing_usage_requests
request_count, active_seconds, continuation_count
created_at, updated_at, last_round_id, workspace_generation
```

其中 `token_budget?` 只在用户提供时设定；`max_requests` 和 `max_active_seconds` 是显式配置、UI 可见的默认安全上限，不伪装成用户指定的 token 预算。建议首轮试验值为 100 次请求、30 分钟活动执行时间，之后通过本项目评测调整。

| 状态 | 含义 | 可以由谁设置 |
| --- | --- | --- |
| `active` | 可在所有准入条件满足时工作 | 用户创建或明确恢复 |
| `paused` | 用户主动暂停 | 用户操作，包括 Esc |
| `blocked` | 目标确实需要用户输入或外部变化 | 控制器认可的阻塞报告 |
| `budget_limited` | token、请求数或时间安全上限耗尽 | 控制器 |
| `suspended` | 执行故障、恢复待核查、防循环触发或待人工验收 | 控制器，并带可操作的 reason |
| `complete` | 完成申请通过约定验收 | 控制器；模型不能直接强制写入 |

暂停、阻塞、限额、挂起均保留目标，不继续调度。`clear` 是用户归档并移除当前目标指针，不等于宣称目标完成。提高预算和恢复必须是明确的用户动作。

### 4.2 续跑准入与优先级

1. 正在关闭、用户暂停或取消：先禁止新调度，再取消当前任务并收尾。
2. 恢复账本有 `outcome_unknown`、写入失败、owner/工作区异常：立即停止，要求现有 `/recover` 流程。
3. 用户输入、审批、AskUser、计划确认与会话切换优先于内部续跑；等待交互不累计为无进展失败。
4. 限额耗尽、provider 无法执行、重复故障达到阈值：产生明确状态与 reason。
5. 正常最终回复：审核完成申请；未通过且仍能安全推进时，才接纳一次续跑。

每次准入绑定 `(session_key, goal_id, revision, workspace_generation)`，用单一前台任务锁和持久化 revision 检查防止：旧任务继续、新输入被抢跑、同一回合启动两次、切换会话后写到旧会话。

内部续跑使用 `source="goal_continuation"`；不调用把直接用户输入登记为授权的路径，不自动扩张 `@` 文件授权，也不把 hook/工具输出当作新的用户指令。来源应在运行账本和 UI 中可区分。

恢复工作区与关闭异步生成器的现有契约必须保留；只有上一轮待处理工具已收尾、恢复状态安全时才开始下一轮。`ExitPlanMode`、AskUser 和审批边界不是可以被 Goal 自动越过的普通 final response。

### 4.3 区分回合结束与目标结束

先引入明确的 `AgentRunOutcome`：`final_response`、`plan_boundary`、`max_iterations`、`output_limit`、`compact_failed`、`permission_wait`、`recovery_required`、`cancelled`、`provider_error` 等，并保留原错误信息。

第一版可以在 runner 适配现有事件，配合补齐不能从现有事件可靠区分的出口；不要求立即重写整个 agent。现有 `max_iterations` 仍是单次内层运行限制，跨回合上限由 Goal 预算独立管理；触发内部异常停止不能被当作普通最终回复反复重启。

外层拦截中间的 `LoopComplete`，可输出 `GoalRoundComplete`；真正停止时输出 `GoalStopped(status, reason, usage)`。三个前端共享语义，headless/remote 不应在中间回合报告整个目标已结束。

### 4.4 预算与用量

- 使用 client 边界上的规范化单请求用量，不用上下文长度或累计 UI 计数的简单差值代替账本。
- 请求绑定 `request_id`、`goal_id`、运行来源和父子关系；使用唯一键去重，兼容流式重发事件和崩溃恢复。
- 第一版明确预算口径为 provider 规范化的总输入加输出；缓存读写不能漏计或重复计，reasoning token 若已包含在 output 中也不能重复相加。这个口径与 Codex 当前非缓存预算口径不同。
- Goal 内主 agent、子任务、压缩和验收 LLM 调用共用账本；与 Goal 无关的独立会话摘要或记忆维护不能误算，Goal 生命周期内的维护启动应明确归属或延后。
- 用量缺失记为 unknown，展示估算与缺失数量，不能记成实测零；请求数、时间安全上限仍须有效。
- 请求前检查预算，必要时预留预计消耗；调用结束后按实测结算。子任务共享预留锁，防止同时突破准入。
- 限额禁止启动新的实质工作，但不能阻止已有结果、用量与终态的保存；完成与限额同时出现时，只能用已取得的证据判断，不能为补验收再越限调用模型。
- token 限额不是精确计费硬帽：供应商用量通常在请求结束后返回，一次在途请求或取消边界可能超出预算。必须在文档和 UI 中披露，不承诺精确零超支。
- SDK 内部不可观测重试不能伪装成已精确核算；计数规则与未知用量要透明。
- 活动时间用单调时钟累计，不包含用户暂停、等待审批或关闭应用后的离线时间。

### 4.5 完成验收与防假完成

不以“模型说完成”“输出 DONE”“没有 tool call”或“某一个测试成功”单独宣布完成。

模型通过 `request_goal_completion` 提交条件对应的结构化审计，引用实际文件与工具操作证据；控制器核对 Goal revision、工作区 generation、证据存在性、每个必需条件的覆盖，以及是否有未完成子任务或未知结果。

用户确认的检查命令或 artifact 条件作为硬条件，通过现有工具、权限与恢复保护执行；不能直接开 subprocess 绕开现有保护。验收失败的真实输出作为下一轮的定向反馈。

验收证据需要工作区版本锚点；验收后又改动相关代码，则对应证据失效，不能沿用旧的绿色测试。只跑窄测试也不能支持“全仓库所有要求均满足”的声明。

重要边界：确定性控制器能核查命令退出、产物和证据对应关系，但不能通用证明自然语言目标的语义正确性。没有硬条件时依赖结构化模型审计，明确展示这种证据强度；重要或模糊要求允许停在人工验收，不默认调用另一个 LLM就当作独立证明。

`goal_complete` 是通过验收后的状态变化，不是模型可强制调用成功的工具。所有必需项仍未知时不能标记完成，也不能无限重复申请。

### 4.6 无进展保护与恢复

第一版比较近期动作：规范化工具名与参数、返回结果摘要、工作区变化、验收进展、重复错误。连续 3 个有效工作回合没有可观察进展时停止续跑，保留可配置阈值；这是安全保护，不是对“思考是否有价值”的完美判定。

另加一个更便宜的纯文本保护：自动续跑没有工具调用且没有新证据时，最多允许一次定向重规划；再次只重复最终回复则挂起，不等到请求预算耗尽。

同一测试在修改代码后重跑不算死循环；处理不同文件的批量操作也不算。等待审批/用户输入不算无进展回合。外部缺凭证、已知权限拒绝、未知副作用等明确边界立即等待，不能为了凑满三次去重试危险操作。

启动时加载状态但不自动执行历史 `active` Goal：显示“待确认恢复”，先核查 recovery，再由用户 `/goal resume` 接纳新回合。不会重放旧的 Bash/MCP 调用或发送动作。

Goal 存储建议第一版使用现有 SQLite metadata：有版本的状态、当前目标指针、request 用量记录。状态 CAS、结算与调度意图需事务化；仅复用 `put_metadata()` 并不能自动解决所有并发与崩溃问题。恢复丢失的 UI 投影只重发事件，不重执行工具。

## 5. 用户入口建议

```text
/goal set <目标> [--tokens N] [--max-requests N] [--max-seconds N]
/goal status
/goal pause
/goal resume
/goal clear
```

- `set` 显示目标、验收条件与安全上限；替换未完成目标要求确认。
- Goal 下仍能聊天、调整计划与补充信息；改变目标须明确编辑，不能把任意新消息自动替换目标。
- Esc 先暂停 Goal 的续跑许可，再走现有取消收尾；不能取消一轮后立刻自动重新启动。
- status bar 显示目标状态、实测/估算用量、请求数与阻塞原因，不用“完成百分比”制造无法证明的精度。
- 模型工具最小集合：`get_goal`、`report_goal_progress`、`request_goal_completion`、`report_goal_blocker`。首版仅由直接用户命令创建、暂停、恢复和修改预算。
- headless 建议显式 `--goal`；新增 `goal_updated` / `goal_round_complete` / `goal_stopped` 等 JSON 事件，退出码区分完成与非完成停止。
- remote 复用控制器与命令解析，不再实现另一套状态机。

上述命令和字段都是提案，不表示当前版本已支持。

## 6. 分阶段实施与交付门槛

### Phase 0：锁定运行语义与回归基线

- 确认所有正常/错误/计划/取消出口，定义 `AgentRunOutcome` 与 Goal 事件。
- 建立普通聊天、Plan、恢复与取消的回归基线，不重构无关模块。
- 门槛：所有停止原因可区分，Goal 关闭时行为不变。

### Phase 1：最小安全垂直切片

- 实现模型、状态 store、前台 runner；接入 TUI 与 headless，remote 明确暂不启用或同步接入，不能声称全端完成。
- 支持 set/status/pause/resume；正常 final 后可安全再跑一轮。
- 先落实请求数/时间上限、Esc、防重复调度、审批与恢复边界；验收先走明确人工确认，不把模型自述当作自动完成。
- 门槛：能够演示“任务只做一半 → 正常 final → 定向续跑”；也能证明 Esc、限额、恢复保护真正停止后续 dispatch。

### Phase 2：统一预算与持久恢复

- client 规范化单请求用量，Goal 内调用共用 ledger，覆盖子任务、压缩、流式异常与 missing usage。
- 完成 revision/CAS、幂等结算、重启加载与用户确认恢复。
- 门槛：重复用量事件不重复计数，压缩不重置预算，崩溃不自动重放工具。

### Phase 3：完成审计与无进展保护

- 结构化完成申请、硬验收条件、证据版本锚点、失败反馈与轻量防循环。
- 接入三个前端的终态、状态栏、文档和配置校验。
- 门槛：假完成、过期测试、重复错误不会继续被包装成成功；相同命令在新代码状态下复测不被误杀。

### Phase 4：真实仓库试验

- 用现有 `nanocursor/eval/` 和 `evaluation/` 体系添加 Goal 场景，对比普通模式与 Goal 模式。
- 统计要求覆盖率、验收通过率、无效续跑次数、token/请求开销、误判阻塞与人工介入次数。
- 根据结果调默认限额，再考虑可选 verifier agent 和更高级调度。第一版不以多 agent 数量为优化目标。

## 7. 必须覆盖的测试

| 场景 | 必须满足 |
| --- | --- |
| Goal 关闭 | 普通 final 一次结束，现有聊天测试保持通过 |
| 目标未完成的 final | 在安全状态只接纳一轮续跑，不重复注入 |
| 真正完成 | 满足所有约定条件，仅产生一次完成终态 |
| 模型口头宣称完成 | 缺条件或证据时拒绝自动完成 |
| 窄测试、过期测试 | 不支撑广泛目标；修改后失效证据必须重验 |
| `ExitPlanMode` / AskUser / 权限审批 | 正确等待，不把人工边界自动越过 |
| Esc 与新用户输入的竞态 | 旧 continuation 不再 dispatch；新输入不被抢跑 |
| session 切换、工作区 generation 改变 | 陈旧结果不能改写新目标 |
| missing usage、Anthropic/OpenAI cache | 不显示未知为零，不漏算或双算 |
| 子任务与 compact | 使用共享预算；不因上下文压缩清空消耗 |
| 重复流式 usage / 重启重发 | 按 request_id 幂等结算 |
| API、工具参数、输出上限异常 | 保留真实停止原因，不无条件重新开一轮 |
| `outcome_unknown` / recovery 存储失败 | 停止，要求核查，不重放动作 |
| Goal 写入成功但 UI 通知丢失 | 恢复状态与事件，不重复执行工具 |
| 连续无进展 / 同测试改代码后重跑 | 前者有限停止，后者允许正常调试 |
| TUI / headless / remote | 同一终态契约；中间回合不伪装成目标完成 |

新测试建议集中在 `tests/test_goal_state.py`、`test_goal_runner.py`、`test_goal_budget.py`、`test_goal_verification.py`，并扩展已有 agent、恢复和前端测试。命名仅为建议，本轮没有添加测试或运行测试。

## 8. 下一步建议

降低耦合的核心迁移已按 `docs/architecture-refactor-plan.md` 落地；实现与验收证据见 `docs/architecture-refactor-results.md`。本提案仍作为后置功能设计，不混入重构；恢复 Goal 开发前先单独确认运行语义和功能范围。

恢复 Goal 开发后，先确认产品语义：Goal 是**明确授权的持续执行**，不是每条普通聊天都自动变成 Goal。随后优先实施 Phase 0 与 Phase 1，跑通有安全停止的闭环，再完善 token 账本和自动验收。

这比先改大型 `agent.py` 的循环更稳妥：能保持原有工具执行、权限、恢复与普通聊天的契约，并避免把模型输出当作唯一调度依据。

## 9. 可复现来源与核对范围

调研读取官方仓库的分支树并记录以下提交；不是对所有项目所有文件的完整审计。GitHub 上的可变 `main` / `dev` 页面只作浏览入口，以下路径可按固定 revision 复查。

| 项目 | 分支树快照 revision | commit API 返回时间（UTC） |
| --- | --- | --- |
| Codex | `d8f69ea8bc998a33c19a08176cfc22fd4352c592` | `2026-09-30T01:44:14Z` |
| OpenCode | `2fa3363c924c5c3e367b84a87ae478296a0ed59b` | `2026-09-29T21:43:58Z` |
| Gemini CLI | `38700b4b38bf387dafded6c97c3f190d084b49e9` | `2026-09-29T21:40:04Z` |
| Ralph 官方插件所在仓库 | `2282079d6ac8824ec4b72a432a03e0c636e0512f` | `2026-09-30T01:03:19Z` |
| Goose | `82b4398605d6c04d0011112e1e720ab16fcc8a63` | `2026-09-30T00:58:03Z` |

固定源码 URL 构造：`https://github.com/<owner>/<repo>/blob/<revision>/<path>`。Goose 的 raw 下载超时后，通过 GitHub 官方 contents API 按上述 revision 成功读取 `retry.rs`。各提交时间已由 commit API 核对；它们是源码提交时间，不是产品发布日期。

| 标记 | 官方来源 | 实际核对路径或页面 |
| --- | --- | --- |
| C1 | OpenAI Cookbook | `https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex` |
| C2 | `openai/codex` | `codex-rs/state/src/model/thread_goal.rs`、`codex-rs/state/src/runtime/goals.rs` |
| C3 | `openai/codex` | `codex-rs/ext/goal/src/tool.rs`、`codex-rs/ext/goal/src/spec.rs` |
| C4 | `openai/codex` | `codex-rs/ext/goal/src/runtime.rs`、`codex-rs/ext/goal/src/extension.rs` |
| C5 | `openai/codex` | `codex-rs/ext/goal/src/accounting.rs` |
| C6 | `openai/codex` | `codex-rs/ext/goal/templates/goals/continuation.md` |
| O1 | `anomalyco/opencode` | `packages/opencode/src/session/prompt.ts` |
| O2 | `anomalyco/opencode` | `packages/opencode/src/session/processor.ts` |
| G1 | `google-gemini/gemini-cli` | `packages/core/src/core/client.ts` |
| G2 | `google-gemini/gemini-cli` | `packages/core/src/services/loopDetectionService.ts` |
| B1 | `aaif-goose/goose` | `crates/goose/src/agents/retry.rs`；网页浏览入口：`https://github.com/aaif-goose/goose/blob/main/crates/goose/src/agents/retry.rs` |
| R1 | `anthropics/claude-code` | `plugins/ralph-wiggum/README.md` |
| R2 | `anthropics/claude-code` | `plugins/ralph-wiggum/hooks/stop-hook.sh` |

配套结构图：`docs/goal-mode.drawio`；PNG 为提案预览，不代表已实现系统。
