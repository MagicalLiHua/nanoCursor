# 学习资料审校记录

最后更新：2026-06-09

这份记录用来回答一个很实际的问题：学习包里哪些内容可以直接学，哪些内容只是历史计划，哪些地方需要结合源码验证。

## 审校原则

本轮整理没有把所有章节都改成短文。学习资料需要同时满足两件事：一是读起来不散，二是还能当代码地图检索。所以入口章节会更像文章，深水区章节会保留必要的清单、表格和代码路径。

| 原则 | 具体要求 |
|---|---|
| 事实优先 | 启动命令、目录路径、默认 feature flag 必须和当前代码一致 |
| 密度优先 | 一行几个字的清单尽量合并成段落或表格 |
| 可验证 | 每个核心结论要能回到源码、测试或运行事件里验证 |
| 不美化 | 明确写出当前不成熟的地方，例如 Go sidecar 不是所有场景都更快 |
| 不混历史 | LangGraph、Vue、根目录旧入口等历史内容只作为复盘，不作为当前学习路径 |

## 章节状态

| 章节 | 当前状态 | 本轮处理 |
|---|---|---|
| 01 项目全景 | 可直接学习 | 重写为高密度入口，突出 Agent Loop、上下文、工具治理、失败恢复、Go sidecar |
| 02 请求生命周期 | 可直接学习 | 重写为真实请求链路，校准前端 store action、run API、EventStore 和恢复路径 |
| 03 Agent Loop | 可直接学习 | 内容方向正确，保留为核心深水区章节 |
| 04 Agent 编排 | 可直接学习 | 内容较长但属于代码地图型章节，暂不压缩过度 |
| 05 上下文管理 | 可直接学习 | 内容正确，保留预算、裁剪、ContextPack 细节 |
| 06 记忆机制 | 可直接学习 | 内容正确，注意把 AGENTS.md / CLAUDE.md 理解为规则记忆，不是聊天历史 |
| 07 工具治理 | 已校准 | 补充“失败恢复不能绕过权限”，压缩低密度目标说明 |
| 08 EventStore 与 SSE | 已校准 | 修正 Go eventstore 状态：它是实验 sidecar，不是当前主链路依赖 |
| 09 异步边界 | 可直接学习 | 内容方向正确，重点理解 `asyncio.to_thread` 和 Go executor 分流 |
| 10 Go Sidecar | 已校准 | 增加当前 Go 服务矩阵：indexer/filetools 默认启用，executor/MCP 默认关闭 |
| 11 MCP 与 Skills | 可直接学习 | 内容方向正确，重点理解 MCP 是工具协议，Skills 是任务规范 |
| 12 前端可观测 | 已校准 | 校准 React + Zustand 路径，明确前端是事件消费层 |
| 13 测试与质量 | 可直接学习 | 内容方向正确，重点读 contract test 和真实任务 smoke test |
| 14 启动与配置 | 已校准 | 修正 Go 启动命令，从旧 `cmd/server` 改为脚本和 `cmd/nanocursor-*` |
| 15 项目复盘 | 已校准 | 补充失败恢复、组件价值评估，修正 Go eventstore 表述 |

## 当前最建议的学习顺序

不要从最长的章节开始。推荐先按这条线走：

1. 读 `01-project-overview.md`，先建立项目全景。
2. 读 `02-request-lifecycle.md`，知道一次请求怎么从前端走到后端再回到前端。
3. 读 `03-agent-loop.md` 和 `04-agent-orchestration.md`，理解为什么不是固定 DAG。
4. 读 `05-context-management.md` 和 `06-memory-system.md`，理解“聪明程度”主要来自上下文和记忆。
5. 读 `07-tool-governance.md`、`08-event-store-and-sse.md`、`09-runtime-and-async-boundary.md`，理解系统为什么可控、可观察、不会卡死事件循环。
6. 最后读 `10-go-sidecar.md`、`11-mcp-and-skills.md`、`13-testing-and-quality.md`、`14-deployment-and-startup.md`，把工程边界、扩展能力和启动测试补齐。
7. 面试前读 `15-project-retrospective.md` 和 `interview/01-project-pitch.md`。

## 还需要人工深读的地方

这些地方不是错误，而是学习时不能只看文档：

- Agent Loop 的完成条件要结合 `agent_loop_controller_service.py` 和真实 run 事件看。
- 上下文选择要结合 `context_service.py`、`context_budget_service.py` 和实际 `selected_files` 看。
- Go sidecar 要结合状态接口和 contract test 看，不能只看“Go 更快”这个表面结论。
- MCP/Skills 目前是可用能力，不是成熟生态；学习时要重点看安全边界和 fallback。
- 前端可观测章节主要帮助你理解事件如何被消费，不代表当前 UI 已经没有设计问题。
