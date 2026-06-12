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

1. 读 `00-learning-roadmap.md`，先建立系统总图、学习顺序和验收标准。
2. 读 `01-project-overview.md`，建立项目全景。
3. 读 `02-request-lifecycle.md`，知道一次请求怎么从前端走到后端再回到前端。
4. 读 `03-agent-loop.md` 和 `04-agent-orchestration.md`，理解为什么不是固定 DAG。
5. 读 `05-context-management.md` 和 `06-memory-system.md`，理解“聪明程度”主要来自上下文和记忆。
6. 读 `07-tool-governance.md`、`08-event-store-and-sse.md`、`09-runtime-and-async-boundary.md`，理解系统为什么可控、可观察、不会卡死事件循环。
7. 最后读 `10-go-sidecar.md`、`11-mcp-and-skills.md`、`13-testing-and-quality.md`、`14-deployment-and-startup.md`，把工程边界、扩展能力和启动测试补齐。
8. 做 `exercises/02-trace-one-real-run.md`，用问候、只读分析、代码交付三类任务验证链路。
9. 做 `exercises/03-memory-tool-governance-lab.md`，验证记忆选择、规则记忆、shell 分类和 approval。
10. 面试前读 `15-project-retrospective.md`、`interview/01-project-pitch.md`、`interview/03-agent-loop-deep-dive.md` 和 `interview/04-context-and-memory.md`。
11. 临近面试时集中刷 `interview/09-four-day-final-drill.md`，把尖锐追问、源码定位和口述稿练熟。

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

## 2026-06-12 第七轮内容升级记录

本轮把学习包从“分模块材料”继续升级成“课程化理解路径”，重点补足结构图和最新实现口径。

新增文件：

| 文件 | 作用 |
|---|---|
| `chapters/00-learning-roadmap.md` | 系统总图、请求序列图、四条主线、7 天学习路线和学习验收标准 |

升级内容：

| 文件 | 升级重点 |
|---|---|
| `chapters/01-project-overview.md` | 补系统分层图，加入子 Agent 证据合并作为核心亮点 |
| `chapters/02-request-lifecycle.md` | 补意图分流图，说明 direct/read/edit/delivery/risky 的运行差异 |
| `chapters/03-agent-loop.md` | 补 Loop 状态机、子 Agent ContextPack/EvidencePack/merge_agent_result 链路，校准 max_steps=40 |
| `chapters/04-agent-orchestration.md` | 补临时 Agent 编排图和生命周期状态图 |
| `chapters/05-context-management.md` | 补上下文来源流水线和自动压缩流程图 |
| `chapters/06-memory-system.md` | 补 MemoryRecord 选择与注入流程图 |
| `chapters/07-tool-governance.md` | 补工具调用治理管线图 |
| `chapters/08-event-store-and-sse.md` | 补 EventStore 到前端投影图 |
| `chapters/09-runtime-and-async-boundary.md` | 补异步边界图，强调阻塞隔离 |
| `chapters/10-go-sidecar.md` | 补 Python/Go sidecar 边界图 |
| `chapters/11-mcp-and-skills.md` | 补 MCP 与 Skills 能力接入图 |
| `chapters/12-frontend-observability.md` | 补 SSE 到前端状态投影图 |
| `chapters/13-testing-and-quality.md` | 补测试/benchmark/CI 质量链路图 |
| `interview/07-interview-question-bank.md` | 题库扩展到 90 题，新增子 Agent 独立上下文、EvidencePack、上下文窗口、失败自动处理和收尾口径 |

## 2026-06-12 第八轮内容升级记录

本轮继续补齐“查代码、做练习、准备面试”的部分，避免学习包只停留在主章节阅读。

升级内容：

| 文件 | 升级重点 |
|---|---|
| `maps/backend-code-map.md` | 增加后端阅读总图和 run 服务调用时序图 |
| `maps/api-map.md` | 增加 API 使用总图和主链路 API 时序图 |
| `maps/event-map.md` | 增加事件账本到前端投影图，补充子 Agent 证据合并和上下文压缩相关事件 |
| `exercises/01-read-the-request-lifecycle.md` | 增加实验总图和证据记录表 |
| `exercises/02-trace-one-real-run.md` | 增加三类任务对比图和事件证据表 |
| `exercises/03-memory-tool-governance-lab.md` | 增加记忆、工具治理、失败恢复联动实验图 |
| `exercises/04-run-benchmark-and-ablation.md` | 增加消融实验流程图和结果记录模板 |
| `chapters/14-deployment-and-startup.md` | 增加启动拓扑图，明确最小可用和 Go sidecar 增强层 |
| `chapters/15-project-retrospective.md` | 增加项目演化 timeline，帮助复盘不是乱堆功能 |
| `interview/01-project-pitch.md` | 增加项目讲述结构图 |
| `interview/03-agent-loop-deep-dive.md`、`04-context-and-memory.md`、`05-tools-recovery-and-observability.md`、`06-go-mcp-and-project-boundary.md` | 增加面试回答框架图，帮助按“结论-原因-实现-证据-边界”组织回答 |
| `interview/07-interview-question-bank.md`、`08-testing-benchmark-retrospective.md`、`09-four-day-final-drill.md` | 增加刷题路径、质量证明链路和四天冲刺节奏图，帮助面试前快速建立复习顺序 |

## 2026-06-12 第九轮内容升级记录

本轮补齐“从文档走向源码”和“如何判断自己是否真正掌握”的最后学习闭环。

新增文件：

| 文件 | 作用 |
|---|---|
| `maps/source-navigation-index.md` | 从问题反查源码入口、核心 service、验证方式和修改安全流程 |
| `exercises/05-mastery-audit.md` | 用简单问答、只读分析、小代码修改、上下文压测、失败恢复和口述检查验证最终掌握度 |
| `maps/concept-glossary.md` | 统一 Run、Conversation、ContextPack、ToolPolicy、EventStore、MCP/Skills 等核心概念，降低读源码时的术语混乱 |
| `maps/debugging-playbook.md` | 从简单问候误路由、只读任务误写、无 Diff 假完成、连续对话丢历史、Agent 动态碎片、上下文压缩、Go/MCP 接入等真实现象反查源码和证据 |

升级内容：

| 文件 | 升级重点 |
|---|---|
| `README.md` | 把正式学习路线改成“路线图 -> 主章节 -> 代码地图 -> 练习 -> 面试”，并把旧计划标为建设记录 |
| `index.html` | 增加源码定位索引、概念词典、实战排障手册和最终掌握度检查入口 |
| `check_learning_package.py` | 扩展为覆盖全部主章节、地图、练习和面试文档，并要求学习文档具备结构图 |

## 2026-06-12 第十轮内容升级记录

本轮新增架构决策章节，把项目从“功能列表”进一步整理成“有取舍的工程系统”。

新增文件：

| 文件 | 作用 |
|---|---|
| `chapters/16-architecture-decisions.md` | 系统解释为什么从固定 DAG 转向 Agent Loop、为什么 ExecutionPlan 只做边界、为什么默认 Lead、为什么上下文优先于 Agent 数量、为什么 EventStore 不是日志、为什么工具策略独立于模型、为什么 Go 是 sidecar、为什么 MCP/Skills 是扩展层，以及 benchmark/ablation 如何证明组件价值 |

升级内容：

| 文件 | 升级重点 |
|---|---|
| `README.md` | 把架构决策章节加入正式学习顺序，放在主章节和源码索引之间 |
| `index.html` | 增加架构决策入口 |
| `contentLoader.js` | 增加架构决策的学习目标描述 |
| `check_learning_package.py` | 将第 16 章纳入强制检查和首页链接检查 |

## 2026-06-12 第十一轮内容升级记录

本轮新增真实 Run walkthrough，把分散知识串成可执行的全链路案例。

新增文件：

| 文件 | 作用 |
|---|---|
| `exercises/06-real-run-walkthroughs.md` | 用 direct answer、read-only analysis、small edit 三类任务串起用户消息、意图路由、ContextPack、Agent Loop、ToolPolicy、EventStore、前端投影和最终交付 |

升级内容：

| 文件 | 升级重点 |
|---|---|
| `README.md` | 把真实 Run walkthrough 加入正式学习顺序，放在毕业检查之后、面试准备之前 |
| `index.html` | 增加真实 Run walkthrough 入口 |
| `contentLoader.js` | 增加真实 Run walkthrough 的学习目标描述 |
| `check_learning_package.py` | 将第 6 个练习纳入强制检查 |

## 2026-06-13 第十二轮内容升级记录

本轮新增“模块证据矩阵”，把学习站从章节式阅读继续推进到证据链式复盘。

新增文件：

| 文件 | 作用 |
|---|---|
| `maps/module-evidence-matrix.md` | 按意图路由、Agent Loop、子 Agent、上下文、记忆、工具治理、失败恢复、EventStore、前端投影、Go sidecar、MCP/Skills、Benchmark/消融等模块，串起痛点、源码入口、运行事件、验证方式和面试表达 |

升级内容：

| 文件 | 升级重点 |
|---|---|
| `README.md` | 把模块证据矩阵放到概念词典之后、源码定位之前，作为从“懂概念”走向“能讲证据”的过渡 |
| `index.html` | 增加模块证据矩阵入口 |
| `contentLoader.js` | 增加模块证据矩阵的学习目标描述 |
| `check_learning_package.py` | 将模块证据矩阵纳入强制检查和首页链接检查 |
