# 学习资料审校记录

最后更新：2026-06-11

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
| 03 Agent Loop | 已升级 | 补充运行时合约、真实链路、三类任务对比、面试深挖和源码阅读任务 |
| 04 Agent 编排 | 已升级 | 补充“Agent 是运行时资源”的解释、创建决策升级路线、子 Agent 输出规范和面试模板 |
| 05 上下文管理 | 已升级 | 补充 ContextPack / Budget / Ledger 区分、Agent 视图差异、压缩心智和面试追问 |
| 06 记忆机制 | 已升级 | 补充“记忆是被治理的长期上下文”、生命周期、记忆/上下文关系和面试表达 |
| 07 工具治理 | 已升级 | 补充工具调用五段式、shell/写文件风险、失败恢复边界和面试表达 |
| 08 EventStore 与 SSE | 已升级 | 补充事件账本、session/event/snapshot 区别、前端投影和失败恢复关系 |
| 09 异步边界 | 已升级 | 补充 async 正确性、线程/协程/Go sidecar 分层、排查路径和面试表达 |
| 10 Go Sidecar | 已升级 | 补充 Go sidecar 边界、成熟度分层、性能取舍、跨语言一致性和面试表达 |
| 11 MCP 与 Skills | 已升级 | 补充 MCP/Skills 区别、成熟工具对比、GitHub Skills 风险、上下文和权限治理 |
| 12 前端可观测 | 已校准 | 校准 React + Zustand 路径，明确前端是事件消费层 |
| 13 测试与质量 | 已升级 | 补充 benchmark、context window、ablation、CI 失败排查和质量体系面试表达 |
| 14 启动与配置 | 已校准 | 修正 Go 启动命令，从旧 `cmd/server` 改为脚本和 `cmd/nanocursor-*` |
| 15 项目复盘 | 已升级 | 补充组件价值证明、最终项目定位、收尾优先级和面试复盘模板 |

## 当前最建议的学习顺序

不要从最长的章节开始。推荐先按这条线走：

1. 读 `01-project-overview.md`，先建立项目全景。
2. 读 `02-request-lifecycle.md`，知道一次请求怎么从前端走到后端再回到前端。
3. 读 `03-agent-loop.md` 和 `04-agent-orchestration.md`，理解为什么不是固定 DAG。
4. 读 `05-context-management.md` 和 `06-memory-system.md`，理解“聪明程度”主要来自上下文和记忆。
5. 读 `07-tool-governance.md`、`08-event-store-and-sse.md`、`09-runtime-and-async-boundary.md`，理解系统为什么可控、可观察、不会卡死事件循环。
6. 最后读 `10-go-sidecar.md`、`11-mcp-and-skills.md`、`13-testing-and-quality.md`、`14-deployment-and-startup.md`，把工程边界、扩展能力和启动测试补齐。
7. 做 `exercises/02-trace-one-real-run.md`，用问候、只读分析、代码交付三类任务验证链路。
8. 做 `exercises/03-memory-tool-governance-lab.md`，验证记忆选择、规则记忆、shell 分类和 approval。
9. 面试前读 `15-project-retrospective.md`、`interview/01-project-pitch.md`、`interview/03-agent-loop-deep-dive.md` 和 `interview/04-context-and-memory.md`。
10. 临近面试时集中刷 `interview/09-four-day-final-drill.md`，把尖锐追问、源码定位和口述稿练熟。

## 还需要人工深读的地方

这些地方不是错误，而是学习时不能只看文档：

- Agent Loop 的完成条件要结合 `agent_loop_controller_service.py` 和真实 run 事件看。
- 上下文选择要结合 `context_service.py`、`context_budget_service.py` 和实际 `selected_files` 看。
- Go sidecar 要结合状态接口和 contract test 看，不能只看“Go 更快”这个表面结论。
- MCP/Skills 目前是可用能力，不是成熟生态；学习时要重点看安全边界和 fallback。
- 前端可观测章节主要帮助你理解事件如何被消费，不代表当前 UI 已经没有设计问题。

## 2026-06-11 内容升级记录

本轮开始按 `LEARNING_CONTENT_UPGRADE_PLAN.md` 执行“高质量学习资料”升级，优先处理最有项目区分度和面试价值的主线。

新增文件：

| 文件 | 作用 |
|---|---|
| `exercises/02-trace-one-real-run.md` | 用三个真实任务追踪 run、事件、上下文、工具调用和交付结果 |
| `interview/03-agent-loop-deep-dive.md` | 准备 Agent Loop、多 Agent 协同、为什么不用固定 DAG 等高频追问 |

升级章节：

| 章节 | 升级重点 |
|---|---|
| `chapters/03-agent-loop.md` | 把 Agent Loop 解释成运行时合约，而不是普通 while loop |
| `chapters/04-agent-orchestration.md` | 强化“默认 Lead、按需临时 Agent、完成后归档”的产品化思路 |
| `chapters/05-context-management.md` | 强化上下文选择、预算、账本、压缩和不同 Agent 视图 |

下一轮建议继续升级：

1. `chapters/12-frontend-observability.md`：可适当压缩，不要让前端材料喧宾夺主。
2. `maps/backend-code-map.md`：后续如代码继续调整，需要校准最新服务入口。
3. `interview/01-project-pitch.md`：最后可按简历版本再微调项目讲述。

## 2026-06-11 第二轮内容升级记录

本轮继续执行高质量内容计划，把上下文主线补到记忆和工具治理。

新增文件：

| 文件 | 作用 |
|---|---|
| `interview/04-context-and-memory.md` | 准备上下文管理、记忆机制、压缩和可解释选择的面试追问 |
| `exercises/03-memory-tool-governance-lab.md` | 用实验串联 MemoryRecord、规则记忆、shell 分类、approval 和失败恢复 |

升级章节：

| 章节 | 升级重点 |
|---|---|
| `chapters/06-memory-system.md` | 把记忆从“保存历史”改讲成“受治理的长期上下文候选池” |
| `chapters/07-tool-governance.md` | 把工具治理从权限表扩展成 propose/classify/decide/execute/record 管线 |

## 2026-06-11 第三轮内容升级记录

本轮继续把工程化主线补齐，重点处理可观测运行和异步边界。

新增文件：

| 文件 | 作用 |
|---|---|
| `interview/05-tools-recovery-and-observability.md` | 准备工具治理、失败恢复、EventStore、SSE、异步边界相关面试追问 |

升级章节：

| 章节 | 升级重点 |
|---|---|
| `chapters/08-event-store-and-sse.md` | 把事件从“日志”提升为运行时账本，补充前端投影和恢复链路 |
| `chapters/09-runtime-and-async-boundary.md` | 把 async 从语法层讲到线程、to_thread、Go sidecar 和排查方法 |

## 2026-06-11 第四轮内容升级记录

本轮完成 Go sidecar、MCP/Skills 和综合面试题库。

新增文件：

| 文件 | 作用 |
|---|---|
| `interview/06-go-mcp-and-project-boundary.md` | 准备 Go 微服务、MCP/Skills、项目边界和成熟工具差距相关追问 |
| `interview/07-interview-question-bank.md` | 72 个高频面试问题，按模块覆盖项目总览、Agent Loop、上下文、记忆、工具、恢复、事件、异步、Go、MCP/Skills、测试和简历价值 |

升级章节：

| 章节 | 升级重点 |
|---|---|
| `chapters/10-go-sidecar.md` | 把 Go 从“语言占比”讲成“确定性系统边界”，补充性能取舍和 sidecar 成熟度分层 |
| `chapters/11-mcp-and-skills.md` | 把 MCP/Skills 从“功能菜单”讲成“工具协议 + 行为规范 + 安全治理” |

## 2026-06-11 第五轮内容升级记录

本轮完成测试、Benchmark、消融实验和项目复盘的收束。

新增文件：

| 文件 | 作用 |
|---|---|
| `exercises/04-run-benchmark-and-ablation.md` | 通过真实任务 benchmark、上下文窗口 benchmark 和组件消融实验验证系统模块价值 |
| `interview/08-testing-benchmark-retrospective.md` | 准备测试体系、benchmark、消融实验、CI 排查和项目复盘相关追问 |

升级章节：

| 章节 | 升级重点 |
|---|---|
| `chapters/13-testing-and-quality.md` | 从测试清单升级为“正确性测试 + 有效性 benchmark + 组件消融”的质量体系 |
| `chapters/15-project-retrospective.md` | 把项目复盘收束为最终定位、组件价值证明、主线讲法和诚实边界 |

## 2026-06-11 第六轮内容升级记录

本轮补充面试前四天冲刺材料，重点解决“会看文档但现场说不出来”的问题。

新增文件：

| 文件 | 作用 |
|---|---|
| `interview/09-four-day-final-drill.md` | 面试前 4 天复习节奏、1/3/8 分钟口述稿、尖锐追问攻防、源码定位速查、系统设计延伸题 |

升级内容：

| 文件 | 升级重点 |
|---|---|
| `README.md` | 把四天冲刺文档加入最终面试学习顺序 |
| `CONTENT_REVIEW.md` | 补充第六轮升级记录和临场复习建议 |
