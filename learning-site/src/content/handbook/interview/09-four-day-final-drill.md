# 面试前 4 天冲刺：尖锐追问、源码定位与口述训练

最后更新：2026-06-11

这份文档不是继续补概念，而是给面试前最后几天用的。目标是让你从“我看过文档”变成“我能现场说清楚、被质疑也不慌、能指出源码位置”。

## 1. 四天复习节奏

如果只剩四天，不要平均看所有文档。按优先级来。

| 天数 | 目标 | 必看内容 | 产出 |
|---|---|---|---|
| 第 1 天 | 建立项目叙事 | `01-project-pitch.md`、`15-project-retrospective.md`、本文件 2/3/4 节 | 能说 1 分钟和 3 分钟介绍 |
| 第 2 天 | 吃透核心后端 | `03-agent-loop-deep-dive.md`、`04-context-and-memory.md`、`05-tools-recovery-and-observability.md` | 每个核心模块能说出 2-3 个源码路径 |
| 第 3 天 | 准备攻防追问 | `06-go-mcp-and-project-boundary.md`、`08-testing-benchmark-retrospective.md`、本文件 5/6 节 | 能回答尖锐质疑 |
| 第 4 天 | 口述和查漏 | `07-interview-question-bank.md`、本文件 7/8/9 节 | 录音或开口完整讲 3 遍 |

当天不要追求“全会”。更重要的是：

```text
能说清主线
能承认边界
能指出源码
能讲出取舍
```

## 2. 1 分钟口述稿

> nanoCursor 是我做的一个本地 AI Coding Agent 工作台。它不是简单的聊天 UI，而是把一次代码任务拆成可观察、可审批、可恢复、可评估的 Agent Run。项目核心是 Python 后端的 Agent Loop：Lead 先判断用户意图，简单问题直接回答，复杂任务再按需创建临时 Coder、Tester、Reviewer。为了让 Agent 真正能干活，我做了 ContextPack、Project Index、MemoryRecord、工具权限分级、EventStore、SSE、失败恢复和 Go sidecar。这个项目最大的价值不是替代 Codex/Cursor，而是我把 AI 编程工具背后的上下文、工具、安全、事件、恢复和评测机制都拆开实现了一遍。

这个版本适合自我介绍后被问“介绍一下项目”。

## 3. 3 分钟口述稿

> 项目最开始是 LangGraph 风格的固定多 Agent 流程，但我后来发现 AI 编程任务不适合固定 DAG。比如用户只是问候，不应该触发 Planner/Coder/Tester；测试失败后下一步也不一定是预设节点。所以我把核心改成受控 Agent Loop。Lead 每一轮观察状态，判断是直接回答、只读分析、小代码改动，还是创建临时 Agent。Loop 本身有最大步数、完成条件、工具策略、approval 和 EventStore 约束，避免变成失控的 while loop。
>
> 第二个重点是上下文管理。Agent 能不能做对，关键不是有几个 Agent，而是它看到了什么。项目里 ContextPack 会组织当前任务、会话摘要、运行摘要、项目索引、相关文件、最近失败、记忆、Skills 和工具策略；ContextBudget 控制各部分 token；ContextLedger 记录上下文窗口使用情况，在压力过高时触发压缩，并保护用户请求、当前计划、工具策略这些 P0 锚点。
>
> 第三个重点是工具治理。Agent 写文件、跑命令、调 MCP 都是真实副作用，所以我做了 read_only、safe_write、risky_write、shell_safe、shell_risky、mcp_read、mcp_write 等分级。高风险动作进入 approval。失败恢复也不能绕过权限，缺依赖、测试失败、权限阻断都会先分类，再生成受控恢复计划。
>
> 后期我还引入了 Go sidecar，但不是全量重写。Python 负责 Agent 编排和 LLM 生态，Go 负责文件工具、索引、命令执行、MCP stdio 这些确定性系统边界。最后用 benchmark、contract test 和 ablation 证明这些组件不是简单堆功能。

这个版本适合主面开始后，面试官愿意听你展开讲。

## 4. 8 分钟深挖结构

如果面试官说“详细讲讲”，不要流水账。按四段讲：

### 4.1 背景和问题

先讲为什么普通 LLM 应用不够：

- 不知道该读哪些文件。
- 工具调用没有权限边界。
- 运行过程不可观察。
- 失败后只能报错。
- 多 Agent 很容易变成噱头。

### 4.2 核心架构

按链路讲：

```text
前端会话
  -> FastAPI run API
  -> intent router
  -> Lead direct answer 或 Agent Loop
  -> ContextPack / ToolPolicy / EventStore
  -> 工具调用 / 子 Agent / 失败恢复
  -> SSE 推送前端
  -> Diff / 报告 / benchmark evidence
```

### 4.3 三个重点模块

只重点讲三个：

1. Agent Loop：为什么不用固定 DAG。
2. ContextPack：为什么上下文比 Agent 数量更重要。
3. ToolPolicy + Recovery：为什么能真实干活但不失控。

Go sidecar、MCP/Skills、Benchmark 作为扩展亮点补充，不要一开始就把所有东西摊开。

### 4.4 复盘和边界

最后主动讲：

- 它不能替代 Codex/Cursor。
- 复杂任务成功率仍依赖模型和上下文命中率。
- 多用户安全不是当前目标。
- Go 并不是所有场景都更快。
- 如果重做，会更早做指标和 benchmark。

## 5. 尖锐追问攻防

### Q1：这个项目是不是 AI 帮你写的？你自己真的懂吗？

答法：

> 这个项目确实大量使用了 AI 辅助开发，但不是把代码交给模型就结束。我自己做的是架构取舍、模块边界、功能验收、真实任务测试和反复重构。比如我从 LangGraph 改成 Agent Loop，是因为实际体验发现固定流程不适合交互式编程任务；Go sidecar 也不是全量替换，而是选择文件工具、索引、命令执行这些确定性边界。现在我能指出核心链路、源码位置、测试和 benchmark，这也是我整理学习包的原因。

不要否认 AI 辅助。要强调你承担的是 owner 角色。

### Q2：这个项目 GitHub 上类似的很多，你的差异是什么？

答法：

> 如果只看“AI 编程助手”这个标题，确实很多。但 nanoCursor 的重点不是做一个聊天壳，而是把 AI Coding Agent 的控制面做完整：Agent Loop、上下文预算、工具权限、EventStore、失败恢复、Go sidecar、MCP/Skills 和消融实验。我的差异不在模型比别人强，而在把这些工程机制拆开实现并能解释取舍。

### Q3：它和 Codex/Cursor 比有什么意义？

答法：

> 它不能替代 Codex/Cursor。意义在于我通过复刻核心机制理解成熟工具背后的系统问题：上下文怎么选、工具怎么控、失败怎么恢复、运行怎么可观察、组件怎么评估。商业工具是产品，nanoCursor 是我理解 AI Agent 工程底座的个人系统项目。

### Q4：多 Agent 是不是噱头？

答法：

> 如果默认给每个任务都创建 Planner/Coder/Tester，那确实是噱头。所以项目后面改成默认只有 Lead。只有任务需要计划、实现、测试或复核时才创建临时 Agent，并且完成后归档。我的观点是 Agent 越少越好，只有职责分离真的降低风险时才创建。

### Q5：为什么不用 LangGraph？是不是你驾驭不了？

答法：

> 不是 LangGraph 不好，而是它更适合稳定流程或图式编排。交互式编程任务经常根据中间结果改变下一步，固定 DAG 容易把流程写死。我早期用过类似思路，后来发现简单问候也会触发完整流程，debug 路径也不自然，所以改成受控 Agent Loop。Loop 仍然有最大步数、任务板、approval、EventStore，不是无约束 while。

### Q6：为什么引入 Go？是不是为了简历硬凑？

答法：

> 我没有全量 Go 重写，也没有把业务策略放到 Go。Go 只做适合它的系统边界，比如 filetools、indexer、executor、MCP gateway。Python 继续做 LLM、Agent Loop、上下文和工具策略。并且 Go sidecar 有 feature flag、health check、fallback 和 contract test。小任务不一定走 Go，因为 RPC 开销可能不划算。

### Q7：项目最大失败或弯路是什么？

答法：

> 最大弯路是早期太容易继续堆功能，尤其前端和多 Agent 展示，后来才意识到核心应该是上下文、工具治理、Agent Loop 和评测。另一个弯路是文档和 benchmark 做得太晚，导致很长一段时间只能说“我实现了很多”，但不能很好证明每个模块为什么有必要。

### Q8：如果上线给别人用，第一件事补什么？

答法：

> 第一是安全和隔离，比如认证、workspace sandbox、shell 沙盒、secret 管理。第二是更严格的工具审批和审计。第三是稳定的 benchmark 和回归测试。当前项目定位是本地单用户工作台，不是多用户 SaaS。

### Q9：为什么不用数据库，用 JSONL？

答法：

> 本地单用户工具优先简单可维护。EventStore 用 JSONL 追加写，方便 grep、人工排查、恢复和前端重放。缺点是跨 run 聚合、分页和并发写能力有限。如果做多用户或大规模历史检索，会迁移到 SQLite/Postgres 或专门事件存储。

### Q10：你怎么证明上下文管理真的有用？

答法：

> 一是 context window benchmark：构造超长 ContextLedger，验证压缩后 token 下降，并且 P0 锚点保留率为 1.0。二是 context hit rate 思路：最终读取或修改的文件是否在初始 selected_files 里。三是 ablation：关闭 context_pack 后，某些 eval 会从 passed 变 failed。

### Q11：失败恢复是不是让模型继续猜？

答法：

> 不是。失败恢复先从事件里提取 command、stderr、exit_code、相关文件，再分类成缺依赖、语法错误、测试断言失败、权限问题、超时等。恢复计划也受工具策略限制，比如安装依赖仍然需要 approval。它不是无限 retry。

### Q12：你这个项目有没有过度设计？

答法：

> 有些部分如果按产品 MVP 看确实偏重，比如 MCP/Skills、Go sidecar、ablation。但作为个人项目，我的目标是展示 AI Coding Agent 的核心工程机制。真正主链路还是收敛在 Agent Loop、上下文、工具治理、EventStore 和失败恢复，其他模块我会明确讲成可选增强或实验层。

## 6. 源码定位速查卡

面试时不需要背每一行，但核心模块要能说出入口文件。

| 模块 | 你要记住的文件 |
|---|---|
| FastAPI 主入口 | `src/api/server.py`，兼容入口 `api_server.py` |
| Run 启动 | `src/api/services/run_start_service.py`、`src/api/services/conversation_run_service.py` |
| 意图路由 | `src/api/services/intent_router.py`、`src/api/services/intent_correction_service.py` |
| Agent Loop | `src/agent/engine.py`、`src/api/services/agent_loop_controller_service.py`、`src/api/services/agent_loop_state_service.py` |
| 临时 Agent | `src/api/services/ephemeral_agent_service.py`、`src/api/services/orchestration_service.py` |
| ContextPack | `src/agent/context_pack.py`、`src/api/services/run_state_service.py` |
| ContextBudget | `src/api/services/context_budget_service.py` |
| ContextLedger | `src/api/services/context_ledger_service.py`、`src/api/services/compaction_service.py`、`src/api/services/summary_compaction_service.py` |
| 记忆机制 | `src/api/services/memory_governance_service.py`、`src/api/services/memory_selection_service.py` |
| 工具策略 | `src/runtime/tool_policy_runtime.py`、`src/api/services/action_policy_service.py`、`src/api/services/shell_policy_service.py` |
| 文件工具 | `src/tools/file_ops.py`、`src/tools/filetools_client.py` |
| 失败恢复 | `src/api/services/failure_classifier_service.py`、`src/api/services/failure_recovery_loop_service.py`、`src/api/services/context_recovery_service.py` |
| EventStore | `src/api/services/event_store.py` |
| SSE / 流式 | `src/api/services/sse_broker.py`、`src/agent/engine.py` |
| Go filetools | `go-services/filetools/`、`src/api/services/go_filetools_service.py` |
| Go executor | `go-services/executor/`、`src/api/services/go_executor_service.py` |
| Go indexer | `go-services/indexer/`、`src/api/services/go_indexer_service.py` |
| Go MCP gateway | `go-services/mcp/`、`src/api/services/go_mcp_gateway_service.py` |
| MCP Runtime | `src/api/services/mcp_runtime_service.py`、`src/api/services/mcp_catalog_service.py` |
| Skills | `src/api/services/skill_registry_service.py`、`src/api/services/routing_decision_service.py` |
| Benchmark | `src/api/services/benchmark_service.py`、`tests/test_benchmark_routes.py` |
| Ablation | `src/api/services/ablation_benchmark_service.py`、`tests/test_ablation_benchmark_service.py` |

## 7. 面试前必须能说出的 12 个源码回答

### 1. Agent Loop 入口在哪？

可以说：

> 核心流式 loop 在 `src/agent/engine.py`，API 侧的状态控制和任务板更新在 `agent_loop_controller_service.py` 和 `agent_loop_state_service.py`。

### 2. 用户意图在哪里判断？

> 在 `src/api/services/intent_router.py`，后面还有 `intent_correction_service.py` 处理运行时纠偏。

### 3. ContextPack 在哪里构建？

> 结构定义在 `src/agent/context_pack.py`，run 级构建在 `src/api/services/run_state_service.py` 的 `build_run_context_pack` 附近。

### 4. 上下文压缩在哪里做？

> `context_ledger_service.py` 记录上下文窗口，`compaction_service.py` 做确定性压缩，`summary_compaction_service.py` 做摘要式压缩。

### 5. 工具权限在哪里控制？

> 运行时工具策略在 `src/runtime/tool_policy_runtime.py`，shell 分类和 action policy 相关逻辑在 API service 里。

### 6. 文件写入如何接 Go？

> Python 的统一入口是 `src/tools/file_ops.py`，Go client 在 `src/tools/filetools_client.py`，状态服务在 `go_filetools_service.py`。Go 不可用会 fallback。

### 7. EventStore 写在哪里？

> `src/api/services/event_store.py`，它持久化 session 和 events，SSE 只是实时投影。

### 8. 失败恢复在哪里分类？

> `failure_classifier_service.py` 做失败分类，`failure_recovery_loop_service.py` 处理恢复循环，`context_recovery_service.py` 把失败证据注入上下文。

### 9. MCP 在哪里接入？

> Python runtime 在 `mcp_runtime_service.py`，catalog/cache 在 `mcp_catalog_service.py`，Go sidecar 方向在 `go_mcp_gateway_service.py` 和 `go-services/mcp/`。

### 10. Skills 在哪里导入和选择？

> `skill_registry_service.py` 负责导入、规范化和安全扫描，`routing_decision_service.py` 把 Skills、MCP、intent 和工具策略合成 routing decision。

### 11. Benchmark 在哪里？

> `benchmark_service.py` 里有固定 benchmark、真实任务 benchmark、context window benchmark；测试在 `tests/test_benchmark_routes.py`。

### 12. 消融实验在哪里？

> `ablation_benchmark_service.py` 构建 baseline + disable component matrix，`tests/test_ablation_benchmark_service.py` 验证组件 lift 和 verdict。

## 8. 面试官可能继续深挖的系统设计题

### Q1：如果要支持多人协作，你怎么改？

答法：

> 需要先把 workspace、conversation、run、memory 都加上 user/team scope；EventStore 从 JSONL 迁移到数据库或事件存储；工具执行进入沙箱；approval 绑定用户身份；SSE 增加权限校验；secret 管理和审计日志也要补齐。

### Q2：如果要支持远程执行或容器沙箱？

答法：

> 把 shell/filetools 从本地执行迁移成 executor service，workspace mount 到隔离容器，命令执行有 CPU、内存、时间和网络限制。Python Agent Runtime 只发受控执行请求，不直接跑宿主机 shell。

### Q3：如果模型上下文窗口变成 1M，还需要上下文管理吗？

答法：

> 仍然需要。大窗口解决容量，不解决相关性、成本、延迟和注意力分散。ContextPack 的价值是选择、排序、压缩和审计，不只是省 token。

### Q4：如果要做商业产品，最先补哪三件事？

答法：

> 认证和权限、沙箱隔离、稳定评测。没有这三件，真实用户数据和代码执行都不安全，质量也不可控。

### Q5：如果不用 Go sidecar，系统还能跑吗？

答法：

> 能。Go sidecar 是增强层。Python fallback 保留主链路可用性。Go 的价值是系统边界和可测试性，不是让项目依赖它才能启动。

## 9. 反向提问准备

面试最后如果让你问问题，可以问和岗位相关的问题：

1. 团队现在做 Agent 时，更关注工具安全、上下文管理还是模型效果？
2. 你们内部 AI Agent 是偏固定 workflow，还是偏 autonomous loop？
3. 对于执行 shell、修改代码这类能力，团队一般如何做审批和沙箱？
4. 如果岗位涉及 Go，更多是做 Agent runtime、平台服务，还是高并发工具服务？
5. 团队如何评估 Agent 功能是否真的有效？有没有 eval 或 benchmark 体系？

这些问题能把话题引回你的项目优势。

## 10. 四天内不要做的事

面试前几天最忌讳继续无边界加功能。

不要做：

- 大规模重构前端。
- 临时加一个新 Agent 角色。
- 临时重写 Go 服务。
- 背完整代码实现。
- 试图把所有问题都准备成标准答案。

应该做：

- 每天开口讲项目。
- 每天对照源码走 2 个模块。
- 每天刷 15-20 个问题。
- 每天整理 3 个答不好的问题。
- 最后一天只复习主线和尖锐追问。

## 11. 最后一天检查清单

你需要能不看文档回答：

1. 1 分钟介绍项目。
2. 为什么不用 LangGraph。
3. 为什么默认只有 Lead。
4. ContextPack / ContextBudget / ContextLedger 区别。
5. 工具权限分级。
6. 失败恢复为什么不是 retry。
7. EventStore 和 SSE 的关系。
8. 为什么引入 Go，为什么不全 Go。
9. MCP 和 Skills 的区别。
10. Benchmark 和 ablation 证明什么。
11. 项目最大短板。
12. 如果重做一次会怎么设计。

如果这 12 个问题都能自然说出来，项目面试这一块就会稳很多。

