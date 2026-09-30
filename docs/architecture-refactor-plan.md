# NanoCursor 降低耦合重构计划

制定日期：2026-09-30。状态：R0–R6 本轮核心重构与本机验收完成。实现、基线、能力差异、兼容入口和未验证平台见 `docs/architecture-refactor-results.md`。

本计划先稳定现有功能的职责和状态边界，再考虑 Goal 等新功能。目标不是把大文件机械拆小，而是减少跨层状态修改、重复组装和隐含生命周期约定，使一个模块内部变化不再要求多个前端同时了解其细节。

## 1. 已确定的方向

- 暂缓 Goal 实现，保留 `docs/goal-mode-design.md` 作为后置功能提案。
- 沿用当前单进程、事件流、工具注册和模型适配结构，不引入微服务、事件总线平台或依赖注入框架。
- 保持现有功能、权限、恢复、安全边界、持久化格式和对外协议；不借重构名义扩大自动执行能力。
- 小步迁移，每阶段有可观察的交付物、相关测试和回退范围；相关回归没有解决就不继续叠加下一阶段。
- 保留用户当前未提交的 Skill 等改动，以当前工作树建立基线；不自动 stash、reset、创建分支或提交。

## 2. 当前耦合证据

以下来自 2026-09-30 对本地工作树的源码检查和 AST 分析，不能代替运行测试。

| 位置 | 当前证据 | 重构要解决的问题 |
| --- | --- | --- |
| `nanocursor/app.py` | `NanoCursorApp` 类体约 2061 行、84 个方法；直接访问 31 种 Agent 属性，包括 6 种私有成员 | UI 同时承担初始化、状态重置、后台任务管理、会话持久化和渲染 |
| `nanocursor/agent.py` | `Agent` 类体约 974 行、31 个方法，构造函数有 19 个参数 | 执行流程和服务组合关系需要明确；不是立即重写核心循环 |
| `nanocursor/app.py:1417` | `_set_session()` 重置 Agent 内部状态、记忆召回、文件历史、授权和工具引用 | 会话切换的状态所有权落在 UI |
| `nanocursor/app.py:1997` | 事件消费同时负责 compact/memory 持久化和失败回滚 | 展示层必须了解持久化提交语义 |
| `nanocursor/commands/registry.py:28` | `CommandContext` 多个字段为 `Any`；配置字典含业务回调 | 命令实际依赖具体前端而不只是声明的接口 |
| `nanocursor/tools/agent_tool.py:253` | 手动读取父 Agent 多项状态并构造子 Agent | 子任务创建需要清晰的继承、共享和隔离契约 |
| TUI、headless、remote | 各自组装服务、消费运行事件，已有能力与错误输出不完全相同 | 共享业务语义，保留明确的前端差异 |

静态扫描识别了 176 个 Python 模块、566 条内部模块依赖边；函数内延迟导入纳入统计，`TYPE_CHECKING` 分支排除。识别出的顶层导入图没有循环，但全部依赖图存在潜在闭环，包括会话、记忆和恢复相关模块。动态加载、对象共享和回调依赖不在这份导入统计的完整覆盖范围内。

不把 import 数、文件行数或所有闭环数量直接当作健康分数。组装入口合理依赖多个模块，接口和数据类型也可以被大量模块引用；判断重点是依赖方向、状态所有权、行为复用和可测试性。

## 3. 必须保留的行为

重构前先把以下约定登记为回归基线，不假定当前全部测试已经通过。

1. 普通回复、工具调用、Plan 退出、错误和取消的结束行为不变；不能把异常停止显示成成功。
2. 完整 assistant 工具调用消息的持久化屏障先于工具副作用；流式参数未完整、无效或截断时不执行工具。
3. 取消等待工具和自有任务收尾；已完成结果保留，未知副作用仍需 `/recover`，不能静默重试。
4. 已提交正文或 compact boundary 不因 metadata 更新失败而重放或错误回滚；未提交的候选变更遵循原有失败语义。
5. 会话切换受前台与后台工作约束，旧回调不能改写新会话；批准上下文、工作区 generation、文件版本保护保持有效。
6. 直接用户输入、Skill 展开、hook 和内部通知的授权来源仍可区分，不能因共用入口而提升权限。
7. 子 Agent 和 fork Skill 继承限制，不能突破父权限、sandbox、工具白名单或禁止继续 spawn 的策略。
8. TUI、headless 与 remote 保持各自既有能力和输出契约；共享代码不表示强行实现相同功能。
9. 不改变 session JSONL、recovery SQLite、配置、凭据格式及默认存储位置；不为这轮重构设计新 schema。
10. 用户的未提交文件变化、可选 worktree 的成果保护和共享 MCP 连接不能被清理逻辑误伤。

如果发现现有行为不一致或已有失败，先记录事实、影响和期望，不在重构中顺便修无关问题；与本轮变更相关的回归必须解决。

## 4. 目标边界与所有权

新模块名称是建议，以实施时的最小合理拆分为准。不要先创建大量空壳服务，也不要把 UI 中的所有逻辑原样搬进一个新的巨型 Runtime。

| 边界 | 应拥有的职责 | 不应拥有的职责 |
| --- | --- | --- |
| 前端适配器 | 输入、显示、交互式审批、既有网络或 JSON 编码 | 修改 Agent 私有状态、决定提交后回滚、手工继承子 Agent 配置 |
| 组装入口 | 创建和连接现有服务，声明前端能力、资源归属与关闭顺序 | 渲染、长期任务业务判断、隐含修改权限 |
| 会话控制器 | 创建/恢复/切换会话、生命周期状态重置、前端层的持久化确认 | 调用具体 Textual widget、代替安全执行账本 |
| 运行协调器 | 单次前台运行、取消、事件编排和会话绑定检查 | 重写模型工具循环、实现 Goal 自动续跑、重复管理后台任务注册表 |
| Agent | 模型与工具执行、执行前持久屏障、权限与恢复保护 | 掌握前端私有字段或自行渲染界面 |
| 子 Agent 工厂 | 依据显式上下文构造子 Agent，实施继承与隔离规则 | 任意读取前端状态、放宽父权限、强行合并外部进程启动器 |
| 既有 recovery/task 服务 | 执行事实、owner/generation、未知结果门禁、后台任务账本 | 被替换为新的泛化调度框架 |

重要区分：会话控制器负责宿主层的状态和投影；`RecoveryRuntime` 继续负责安全执行事实。不能把 Agent 内部的执行前持久屏障整体移到消费事件的外层，否则可能变成先执行工具、后保存调用消息。

## 5. 分阶段执行

顺序：R0 → R1 → R2 → R3 → R4 → R5 → R6。每次仅迁移一个可以独立验证的用例；不存在“先搬完全部代码，最后统一测试”的阶段。

### R0 建立行为与工作树基线

**范围**：现有测试、入口能力和当前改动；不重构运行代码。

- 记录当前工作树与已有 Skill 改动，明确后续修改交集。
- 建立 TUI/headless/remote 能力对照，包括审批、会话管理、compact、Skill、MCP、团队和取消。
- 运行相关测试，再运行全量测试；记录原有失败与环境限制，不声称未运行的测试通过。
- 记录事件顺序、异常停止、持久化提交边界和关键依赖指标。
- 对确实缺失的相关边界补 characterization test：约束当前已确认的正确行为，不把已知 bug 固化为标准。

**交付物**：基线记录、能力表、已确认不变量和需要补充的测试。

**验收**：能够区分重构引入的回归与既有失败；权限、取消、会话和恢复的关键测试已有可复现命令。

### R1 明确事件与命令接口

**主要改动**：`agent.py`、`commands/registry.py`、相关 handler 和前端适配器。

- 将纯事件契约放到低依赖模块，例如 `nanocursor/events.py`，不依赖 TUI、remote 或具体运行协调器。
- 在原 `nanocursor.agent` 路径暂时重导出事件，保持类型对象身份与旧调用方兼容，不创建同名的第二套类。
- 保留现有事件字段、permission future 语义、错误代码和事件顺序；不在接口整理中加 Goal 事件。
- 命令使用实际需要的窄接口；分批替换关键 `Any`、配置字典回调和 UI 私有字段访问。
- 区分显示接口与业务能力，避免把所有服务方法塞进一个越来越大的 `UIController`。
- 对暂时保留的兼容访问明确位置与后续移除阶段，不让新调用继续扩散。

**交付物**：独立事件契约、可检查的命令能力接口、兼容重导出。

**验收**：原事件导入仍指向同一类型；命令新增功能不依赖前端私有属性；现有命令和展示测试不出现相关回归。

### R2 收敛初始化与服务组装

**主要改动**：`app.py::_select_provider()`、`__main__.py::_run_prompt_owned()`、`remote.py::_init_agent()`。

- 提取共享组装逻辑，例如 `nanocursor/application/bootstrap.py`；返回有明确字段的服务组合对象，不返回无约束字典。
- 用显式前端能力和适配器表达差异，不通过 `hasattr` 猜测支持情况。
- 先迁移通用 client、registry、权限与 Agent 组装，再迁移真正语义一致的附加服务。
- 保留恢复门禁先于模型 client 创建的既有顺序，不能为了统一 builder 提前创建或调用外部服务。
- 明确资源归属与失败清理，避免部分初始化失败时泄漏连接、任务或 workspace owner。
- 保留共享 MCP 连接的生命周期；取消单次工作不能顺便取消共享连接。

**交付物**：共享组装入口、前端能力表、初始化和关闭的资源所有权规则。

**验收**：同一组装规则不再在多个前端复制；差异可显式测试；启动失败、provider 切换和 shutdown 不产生相关回归。

### R3 移出会话生命周期与宿主持久化判断

**主要改动**：`app.py::_set_session()`、session/clear/compact handler、已有 session/recovery 接缝。

- 建立小型会话控制器，例如 `nanocursor/application/session.py`，接管会话切换、状态重置与宿主层提交确认。
- 把 Agent 状态重置变成明确的公开生命周期操作；UI 不再逐项修改 `_loop_count`、`_file_versions`、replacement/recovery state 等内部字段。
- 保留 Agent 执行时对对话的必要更新；“会话控制器拥有生命周期”不等于把所有对话读写复制到一个大服务。
- 迁移 compact 和 memory context 的提交确认及候选回滚；保留正文已提交但 metadata 失败的区别。
- 绑定 session、generation 和现有后台任务归属，陈旧通知与旧 session 回调不能落到新会话。
- 迁移幂等保存时保留现有 durable record identity；新服务不能重复提交，旧路径也不能提前删除安全屏障。
- 恢复与日志投影继续复用现有实现，不重做数据库，也不重新解释历史未知结果。

**交付物**：可不启动 Textual 测试的会话生命周期服务；前端只调用接口和展示结果。

**验收**：切换、恢复、compact 和取消不再要求 UI 修改 Agent 私有状态；提交失败、metadata 失败和重复通知测试保持原有正确语义。

### R4 统一前台运行入口与事件处理

**主要改动**：`app.py::_run_message()`、headless `consume_turn()`、`remote.py::_handle_user_message()`。

- 建立共享运行协调器，例如 `nanocursor/application/execution.py`，包装现有 `Agent.run()`；不改为另一套模型工具循环。
- 协调器拥有单次前台任务和取消流程，复用已有后台 TaskManager/owner 机制，不新建一套相互竞争的任务账本。
- 收敛业务事件处理与会话确认；前端保留不同的渲染、审批呈现和协议编码。
- 所有前端通过同一业务契约运行，但只暴露各自既有能力；不顺便新增 remote 团队、会话或交互能力。
- 保留终态、失败、permission wait、通知来源以及 async generator 的关闭和工具收尾。
- TUI 和 headless/remote 的输出字段、退出码、结果条数与相对顺序以 R0 基线为准。若需要改变对外行为，另列兼容性决定，不夹在重构中。

**交付物**：共享的单次运行入口和前端适配器。

**验收**：前端不再各自决定 compact 是否提交、错误是否成功、取消是否收尾；业务契约可以用同一批脚本式模型事件测试。

### R5 明确子 Agent 与 fork Skill 的创建契约

**主要改动**：`tools/agent_tool.py`、`agents/tool_filter.py`、`skills/executor.py` 与 `skills/runtime.py`。

- 提取 `AgentFactory` 和窄的 `SpawnContext`，显式声明权限、工作目录、工具范围、provider、hooks、trace、替换状态和恢复关联。
- 逐项定义复制、共享或重新构造规则；配置快照与可变运行状态分开，不盲目深拷贝权限或共享完整父 Agent。
- 保留父权限拒绝、sandbox/worktree 隔离、禁止嵌套 spawn、fork 上下文来源和旧权限失效门禁。
- 仅合并语义相同的组装；前台、后台、teammate 和 Skill 的差异由明确策略表达。
- 不把 tmux/iTerm2 等外部进程启动路径强行塞进进程内工厂。
- 保留子任务启动前意图屏障、持久化通知、终态和清理顺序。

**交付物**：可独立测试的子 Agent 构造接口，以及共享但不混淆语义的组装逻辑。

**验收**：新增 Agent 依赖不需要同步手动修改多处继承清单；父 Agent 的权限、client 和会话不会被子任务意外改变。

### R6 验证架构边界与结束本轮重构

- 用现有 pytest 和轻量 AST 检查约束关键依赖方向，不新增架构检查工具依赖。
- 前端和命令层不能新增 Agent 私有状态读写；兼容路径逐项移除，不永久保留大规模 allowlist。
- 事件/接口模块不能依赖具体前端，应用服务不能导入 Textual 或 WebSocket 渲染实现。
- 在合适的数据契约边界处理剩余跨层循环；不以“所有导入闭环归零”作为形式目标，合理的包接口与实现关系单独说明。
- 保留有实际安全作用的 ContextVar 和执行上下文；不把上下文绑定机制一概视为应删除的全局变量。
- 运行相关测试、全量测试、既有打包和安装 smoke 检查；远端 CI 未运行则明确标为未验证。
- 复核初始化、session 切换、运行取消、compact 和子任务创建是否仍需前端理解内部状态。

**交付物**：架构约束测试、回归结果、兼容说明和已完成边界清单。

**验收**：第 8 节所有必需门槛都有证据；没有被本次重构引入的未解决相关回归。

## 6. 测试映射

以下是阶段验证入口，不表示已经执行或通过。实施时先跑相关集合，再扩到全量；不删除、降低断言或加 xfail 来掩盖重构回归。

| 验证边界 | 现有测试入口 |
| --- | --- |
| 事件与模型终态 | `tests/test_agent.py`、`test_response_terminals.py`、`test_turn_presentation.py`、`test_stream_usage.py` |
| 命令与前端能力 | `tests/test_commands.py`、`test_skill_ui.py`、`test_memory_ui.py`、`test_mcp_management_ui.py` |
| session 生命周期与提交 | `tests/test_run_lifecycle.py`、`test_session_batch_persistence.py`、`test_committed_session_metadata.py`、`test_lifecycle_edges.py` |
| 取消与执行屏障 | `tests/test_execution_boundaries.py`、`test_tui_recovery_cleanup.py`、`test_prompt_recovery_cleanup.py` |
| 恢复、投影与未知结果 | `tests/test_recovery_store.py`、`test_recovery_integration.py`、`test_recovery_failures.py`、`test_session_recovery.py` |
| 权限与审批 | `tests/test_permission_consistency.py`、`test_plan_permissions.py`、`test_auto_approval.py`、`test_approval_replay.py` |
| 子任务、Skill 与工作区 | `tests/test_spawn_boundaries.py`、`test_durable_child_lifecycle.py`、`test_skill_execution.py`、`test_subagent.py`、`test_worktree_reliability.py` |
| headless 与输出契约 | `tests/test_prompt_mode.py`、`test_prompt_recovery_cleanup.py`、`test_usage_status.py` |

按仓库现有开发约定，全量命令为 `uv run pytest -q`。共享应用服务和 remote 差异的测试如有缺口，只补与本次迁移直接相关的合同测试，不新建泛化测试框架。

## 7. 推进与回退规则

- 一次只迁移一个用例，例如先 session new，再 resume，再 compact；先建立接口，接入调用方，再删除旧重复实现。
- 兼容重导出和薄适配器仅用于过渡；适配器不长期隐藏新的业务判断。
- 每阶段更新基线对照和完成证据，不用“文件行数减少”作为完成证据。
- R3/R4 是高风险区，涉及提交顺序与取消语义；需要故障注入和时序测试，不只测 happy path。
- 相关回归时停止下一阶段，缩小当前 patch 或恢复这一阶段的实现方式；不运行会丢失用户修改的全仓库 reset。
- 不趁迁移更换模型协议、默认权限、持久化格式、依赖版本或产品功能。
- 如果需要改变既有能力、事件字段或存储 schema，单独提出范围与兼容方案；未明确决定之前不混入重构。

## 8. 可以恢复新功能开发的门槛

以下门槛已有本机回归和安装验收证据，具体范围及平台限制见验收记录：

- [x] 普通聊天、Plan、权限、取消、恢复、Skill 和已有 worktree 行为没有本轮引入的相关回归。
- [x] TUI 不再负责逐项重置 Agent 私有状态或判断 compact/memory 候选是否回滚。
- [x] 会话生命周期和前台运行有公共入口与状态所有者；TUI/headless 共用运行边界，remote 保留禁用门禁。
- [x] 共用的权限、主 Agent、子任务服务组装与子 Agent 构造规则不再重复散落。
- [x] 生产命令的关键业务能力不通过 UI 私有字段和无约束回调字典获取；旧字典调用仅保留薄兼容入口。
- [x] 执行前持久屏障、幂等结果、未知副作用门禁和 session/generation 隔离有回归证据。
- [x] 新的业务服务可以不启动 Textual、不连接真实模型服务进行合同测试。
- [x] 架构边界测试能阻止检查覆盖内的同类跨层依赖；动态依赖的检查局限已注明。
- [x] 相关测试与全量测试结果、现有失败和未验证平台已记录，打包入口和原事件导入保持兼容。

不要求每个文件都很短，也不要求消除全部潜在依赖闭环。要求新增功能可以接入共享业务入口，不再迫使多个前端修改核心状态细节。

## 9. 执行状态与下一步

| 阶段 | 状态 |
| --- | --- |
| R0 行为与工作树基线 | 完成：修改前 1459 项全量测试通过 |
| R1 事件与命令接口 | 完成：单一事件契约、typed command ports、旧导入兼容 |
| R2 初始化与组装 | 完成：主 Agent、权限与共用后台服务组装，保留前端差异 |
| R3 会话生命周期 | 完成：恢复绑定、session 发布、状态重置及候选投影确认 |
| R4 共享前台运行入口 | 完成：ForegroundRun 统一消费/关闭顺序，核心屏障不外移 |
| R5 子 Agent 与 fork 构造 | 完成：SpawnContext/AgentFactory，保留外部启动器 |
| R6 验证与收口 | 完成：新增 50 个用例，全量 1509 passed，wheel/sdist 独立安装通过 |
| Goal 与其他新功能 | 后置，另行确认功能语义和实现范围 |

本轮重构已完成本机验收，后续开发应使用共享应用边界而不是重新向 UI 注入状态重置或持久化策略。Goal 与其他新功能仍需另行确认，不混入本轮重构。
