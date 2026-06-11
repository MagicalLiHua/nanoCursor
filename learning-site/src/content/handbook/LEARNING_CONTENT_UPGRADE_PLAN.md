# nanoCursor 学习站高质量内容升级计划

最后更新：2026-06-11

## 0. 这份计划解决什么问题

现在学习站已经不是空壳，里面有章节、代码地图、API 地图和面试 pitch。但如果目标是“真正吃透整个项目”，当前资料还需要从项目文档升级成学习课程。

这次升级的目标不是把每篇文章写得更长，而是让每一章都做到：

| 维度 | 要达到的效果 |
|---|---|
| 能学 | 读完知道这个模块解决什么问题，为什么需要它 |
| 能找 | 知道相关源码在哪，入口函数、核心数据结构、测试文件分别是什么 |
| 能跑 | 能跟着章节做一次验证或实验 |
| 能改 | 知道如果要改这个模块，应该从哪里下手，哪些地方容易出错 |
| 能讲 | 能把模块讲成面试里的工程亮点，而不是背概念 |

学习站最终应该像一份“项目解剖手册”：既能帮你复习，也能帮你面试前快速进入状态。

## 1. 当前资料现状

### 1.1 已有内容

当前学习资料位于：

```text
learning-site/src/content/handbook/
```

主要包括：

| 类型 | 当前文件 |
|---|---|
| 主章节 | `chapters/01` 到 `chapters/15` |
| 面试材料 | `interview/01-project-pitch.md` |
| 练习 | `exercises/01-read-the-request-lifecycle.md` |
| 代码地图 | `maps/backend-code-map.md` |
| API 地图 | `maps/api-map.md` |
| 事件地图 | `maps/event-map.md` |
| 审校记录 | `CONTENT_REVIEW.md` |

这些内容已经能支撑“初步理解项目”，但还没有完全达到“系统吃透项目”的标准。

### 1.2 当前不足

| 问题 | 具体表现 | 后果 |
|---|---|---|
| 深浅不均 | 有些章节已经很深，有些章节更像概览 | 学习节奏不稳定 |
| 面试材料偏少 | 只有 pitch，没有系统追问库、攻防问答、简历 bullet 拆解 | 面试准备不够扎实 |
| 缺少任务式学习 | 多数章节是解释，不是“读代码 -> 运行 -> 修改 -> 复盘” | 看完容易忘 |
| 代码引用需要校准 | 部分章节提到的文件可能随着重构变化 | 学习资料会慢慢过时 |
| 缺少图谱统一口径 | Agent Loop、上下文、工具、事件、Go sidecar 的关系还可以画得更清楚 | 面试时讲架构容易散 |
| 缺少“反问与缺陷”准备 | 当前资料偏讲优点，不够系统讲边界和 trade-off | 面试官追问时容易被动 |

## 2. 内容升级总原则

### 2.1 每章必须有固定结构

每个主章节统一改成下面的结构。不是机械填空，而是保证信息完整。

```text
# 标题

## 1. 本章要解决的问题
这章对应项目里的哪个真实工程问题。

## 2. 先用人话理解
不用术语，先解释这个模块为什么存在。

## 3. 真实运行链路
用户请求或系统事件如何经过这个模块。

## 4. 核心源码地图
列出入口文件、核心类/函数、数据结构、测试文件。

## 5. 关键实现拆解
讲核心代码，不贴太长代码，只贴关键片段并解释。

## 6. 设计取舍
为什么这样做，不这样做会怎样，有哪些替代方案。

## 7. 常见问题和坑
这个模块最容易出什么 bug，怎么排查。

## 8. 动手练习
给一个可以实际运行或阅读的任务。

## 9. 面试表达
30 秒回答、深入回答、可能追问、诚实边界。

## 10. 学完自测
列出 8-12 个问题，用来确认你是否真的掌握。
```

### 2.2 每章要有三种阅读模式

同一章要同时服务三种场景：

| 模式 | 读者目标 | 章节应该提供什么 |
|---|---|---|
| 快速复习 | 面试前 10 分钟回顾 | 开头摘要、关键图、面试回答 |
| 深度学习 | 真正吃透模块 | 源码路径、流程拆解、设计取舍 |
| 开发维护 | 下次继续改代码 | 入口文件、测试命令、风险点 |

### 2.3 少写空泛形容词，多写可验证事实

不写：

```text
本模块极大提升了系统智能程度。
```

改成：

```text
ContextPack 会记录 selected_files、selection_reasons 和 omitted。这样测试可以断言目标文件是否被选中，前端也能展示哪些上下文被注入或裁剪。
```

### 2.4 每个结论尽量绑定代码、接口或测试

每章至少包含：

- 5 个以上源码路径。
- 1 个运行命令或测试命令。
- 1 个真实数据结构或事件样例。
- 1 个面试追问。
- 1 个当前系统边界。

## 3. 学习站内容总架构

建议把内容分为五层。

### 3.1 第一层：项目全景

目标：让读者知道 nanoCursor 到底是什么，不是什么。

对应内容：

- `chapters/01-project-overview.md`
- `chapters/15-project-retrospective.md`
- `interview/01-project-pitch.md`

需要强化：

- 项目定位：不是成熟商业工具替代品，而是本地 AI 编程工作台实验。
- 核心亮点：Agent Loop、上下文预算、工具治理、失败恢复、Go sidecar、MCP/Skills。
- 项目边界：真实代码能力依赖模型，MCP/Skills 生态兼容还不完整，前端仍有打磨空间。
- 为什么项目有价值：展示 AI Coding Agent 背后的工程机制。

### 3.2 第二层：请求生命周期

目标：能完整讲清楚“用户发一句话后，系统内部发生了什么”。

对应内容：

- `chapters/02-request-lifecycle.md`
- `maps/api-map.md`
- `maps/event-map.md`
- `exercises/01-read-the-request-lifecycle.md`

需要强化：

- 前端如何提交消息。
- conversation、thread、workspace 如何绑定。
- 后端如何进入 intent router。
- Lead direct reply 和 Agent Loop 的分叉点。
- EventStore 如何记录运行。
- SSE 如何驱动前端状态。
- 最终回复和交付物如何形成。

建议新增练习：

```text
exercises/02-trace-one-real-run.md
```

练习内容：启动前后端，发送一个只读任务和一个代码任务，记录两者的事件流差异。

### 3.3 第三层：核心系统模块

这是最重要的一层，决定项目是否“有东西讲”。

#### Agent Loop

对应文件：

- `chapters/03-agent-loop.md`
- `chapters/04-agent-orchestration.md`
- `maps/backend-code-map.md`

需要讲透：

- 为什么不用固定 DAG。
- Agent Loop 的 observe、decide、check、execute、record、finish。
- Lead direct reply 怎么避免简单问题跑完整流程。
- 临时 Agent 如何创建、退出、归档。
- max steps、completion condition、approval wait 怎么防止无限循环。
- Agent Loop 和 Execution Plan 的关系：Plan 是边界，不是死流程。

面试要准备的问题：

1. 你为什么从 LangGraph 改成 Agent Loop？
2. Agent Loop 会不会不可控？
3. 多 Agent 是否真的提升效果？
4. 为什么默认只有 Lead？
5. 子 Agent 的结果如何合并？

#### 上下文管理

对应文件：

- `chapters/05-context-management.md`
- `chapters/06-memory-system.md`
- `maps/backend-code-map.md`

需要讲透：

- ContextPack、ContextLedger、ContextBudget 的区别。
- selected_files、file_outline、recent_failures、skills、memory 如何进入上下文。
- 为什么上下文管理比多 Agent 数量更重要。
- 90% 上下文窗口阈值和自动压缩如何工作。
- deterministic summary、LLM summary、fallback 的差异。
- 会话摘要、运行摘要、长期偏好记忆分别解决什么问题。

面试要准备的问题：

1. 为什么不能把完整项目都塞给模型？
2. 你怎么判断哪些文件相关？
3. 上下文压缩会不会丢关键内容？
4. 记忆机制和普通聊天历史有什么区别？
5. 多 Agent 场景下上下文如何分发？

#### 工具治理与失败恢复

对应文件：

- `chapters/07-tool-governance.md`
- `chapters/13-testing-and-quality.md`
- `docs/failure-recovery-and-ablation-plan.md`，只作为历史参考，不放入学习站主路径

需要讲透：

- 工具权限分级：read only、safe write、risky write、shell safe、shell risky。
- approval 为什么必须在工具执行前。
- 文件写入为什么要 backup、diff、evidence。
- 命令失败后如何分类、生成恢复计划、创建 recovery task。
- 为什么失败恢复不能绕过工具治理。
- ablation 和 benchmark 如何证明组件有价值。

面试要准备的问题：

1. Agent 可以直接执行 shell 吗？
2. 删除文件、安装依赖、git 操作如何处理？
3. 命令失败后系统怎么自动恢复？
4. 怎么避免无限修 bug？
5. 你怎么证明失败恢复模块不是摆设？

#### EventStore 与 SSE

对应文件：

- `chapters/08-event-store-and-sse.md`
- `maps/event-map.md`
- `chapters/12-frontend-observability.md`

需要讲透：

- 为什么 Agent 运行不能只靠最后一条回复。
- EventStore 存哪些东西：session、event、approval、tool evidence、diff、delivery、failure。
- SSE 事件如何驱动前端。
- 前端如何把工具事件折叠进 Agent 动态。
- 为什么可观测性是用户信任的一部分。

面试要准备的问题：

1. 为什么不用 WebSocket？
2. SSE 断开后怎么恢复？
3. 历史会话怎么回放？
4. 前端如何知道系统不是卡住了？
5. 事件太多会不会污染聊天区？

### 3.4 第四层：工程边界与跨语言

对应内容：

- `chapters/09-runtime-and-async-boundary.md`
- `chapters/10-go-sidecar.md`
- `chapters/14-deployment-and-startup.md`

需要强化：

- Python 负责 Agent 决策、上下文、API 和事件。
- Go 负责边界清楚的 sidecar：Indexer、Filetools、Executor、MCP Gateway。
- 不是所有 Go 服务都值得默认启用。
- 为什么一些 Go 服务 benchmark 反而不一定更快。
- fallback 策略如何保证 Go sidecar 不会拖垮主链路。
- `scripts/dev.py` 如何统一启动前后端和 Go sidecars。

面试要准备的问题：

1. 为什么不是全 Python？
2. 为什么不是全 Go？
3. Go sidecar 的收益在哪里？
4. gRPC 在这里解决什么问题？
5. Go 服务失败时系统会怎样？

### 3.5 第五层：MCP、Skills 和扩展生态

对应内容：

- `chapters/11-mcp-and-skills.md`
- `maps/api-map.md`

需要强化：

- MCP 是工具协议，Skills 是能力说明和任务规范。
- MCP server 的生命周期和工具发现。
- Skills 如何被导入、索引、选择、注入上下文。
- MCP/Skills 和工具治理如何结合。
- 为什么支持开源 Skills 不等于无脑加载任意仓库。

面试要准备的问题：

1. MCP 和普通函数调用有什么区别？
2. Skills 和 prompt 模板有什么区别？
3. 用户导入 Skill 的安全风险是什么？
4. MCP 工具调用如何进入 approval？
5. 这个实现和成熟工具还有什么差距？

## 4. 需要新增或重写的文件

### 4.1 新增面试材料

建议新增：

```text
learning-site/src/content/handbook/interview/
  02-core-questions.md
  03-agent-loop-deep-dive.md
  04-context-memory-deep-dive.md
  05-tool-runtime-failure-recovery.md
  06-go-sidecar-and-architecture-tradeoff.md
  07-resume-bullets-and-project-story.md
  08-mock-interview-script.md
```

每个文件定位：

| 文件 | 内容 |
|---|---|
| `02-core-questions.md` | 30-50 个高频问答，覆盖项目定位、架构、技术选择 |
| `03-agent-loop-deep-dive.md` | 专门准备 Agent Loop 深挖问题 |
| `04-context-memory-deep-dive.md` | 专门准备上下文、记忆、压缩问题 |
| `05-tool-runtime-failure-recovery.md` | 工具治理、命令执行、失败恢复、测试质量 |
| `06-go-sidecar-and-architecture-tradeoff.md` | Python + Go 分工、gRPC、sidecar 是否有必要 |
| `07-resume-bullets-and-project-story.md` | 简历 bullet、项目故事线、不同岗位版本 |
| `08-mock-interview-script.md` | 10 分钟、20 分钟、45 分钟模拟面试脚本 |

### 4.2 新增练习材料

建议新增：

```text
learning-site/src/content/handbook/exercises/
  02-trace-one-real-run.md
  03-debug-a-failed-command.md
  04-add-a-read-only-tool.md
  05-add-a-context-section.md
  06-add-a-skill-and-inject-it.md
  07-compare-python-go-filetools.md
  08-write-one-ablation-case.md
```

练习不追求多，而是每个都要能让你真的动手。

### 4.3 新增源码地图

建议新增：

```text
learning-site/src/content/handbook/maps/
  frontend-code-map.md
  data-model-map.md
  go-service-map.md
  test-map.md
```

重点：

- `frontend-code-map.md`：前端 store、SSE、聊天区、右侧栏、底部证据区。
- `data-model-map.md`：conversation、thread、run、task、event、context pack、approval、artifact。
- `go-service-map.md`：indexer、filetools、executor、mcp gateway 的 proto、server、client、fallback。
- `test-map.md`：哪些测试验证哪个模块。

## 5. 逐章升级任务清单

### 5.1 P0：先改最核心的 5 章

这 5 章最值得先做，因为它们决定项目面试价值。

| 优先级 | 章节 | 改造重点 |
|---|---|---|
| P0 | `03-agent-loop.md` | 增加真实事件样例、完成条件、失败路径、面试深挖 |
| P0 | `05-context-management.md` | 增加 ContextLedger、压缩策略、token 面板、上下文污染案例 |
| P0 | `06-memory-system.md` | 区分规则记忆、用户偏好、会话摘要、长期经验 |
| P0 | `07-tool-governance.md` | 和失败恢复、Go filetools、approval 打通讲 |
| P0 | `09-runtime-and-async-boundary.md` | 解释 `asyncio.to_thread`、Go executor 分流、事件循环阻塞风险 |

每章验收标准：

- 至少 3000-5000 中文字，信息密度高。
- 至少 8 个源码路径。
- 至少 1 个流程图或文本流程。
- 至少 1 个“错误设计会怎样”的反例。
- 至少 8 个自测问题。
- 至少 5 个面试追问。

### 5.2 P1：再改工程化章节

| 优先级 | 章节 | 改造重点 |
|---|---|---|
| P1 | `02-request-lifecycle.md` | 一次 run 的完整端到端链路 |
| P1 | `08-event-store-and-sse.md` | EventStore 数据、SSE 事件、前端消费关系 |
| P1 | `10-go-sidecar.md` | Go 服务矩阵、benchmark、fallback、取舍 |
| P1 | `11-mcp-and-skills.md` | MCP 和 Skills 的区别、安全、生态边界 |
| P1 | `13-testing-and-quality.md` | 测试矩阵、benchmark、消融实验、CI |

### 5.3 P2：最后改产品展示和复盘章节

| 优先级 | 章节 | 改造重点 |
|---|---|---|
| P2 | `01-project-overview.md` | 更像高质量课程开篇 |
| P2 | `04-agent-orchestration.md` | 和 Agent Loop 合并口径，避免重复 |
| P2 | `12-frontend-observability.md` | 讲前端如何消费事件，不夸大 UI 成熟度 |
| P2 | `14-deployment-and-startup.md` | 启动方式、配置、Go sidecar 可选项 |
| P2 | `15-project-retrospective.md` | 项目价值、边界、简历表达和收尾建议 |

## 6. 面试资料升级方案

面试资料要分为四类，而不是只写一份 pitch。

### 6.1 讲述模板

保留并升级：

```text
interview/01-project-pitch.md
```

新增版本：

| 场景 | 要准备的内容 |
|---|---|
| 30 秒 | 一句话定位 + 2 个亮点 |
| 1 分钟 | 架构 + 核心模块 |
| 3 分钟 | 从用户请求讲到工具执行和前端观测 |
| 5 分钟 | 加入设计取舍、Go sidecar、上下文压缩 |
| 简历展开 | 面试官指着简历 bullet 问时如何展开 |

### 6.2 高频问答库

新增：

```text
interview/02-core-questions.md
```

问题分类：

| 分类 | 数量 |
|---|---:|
| 项目定位 | 8 |
| Agent Loop / 多 Agent | 10 |
| 上下文 / 记忆 | 10 |
| 工具治理 / 失败恢复 | 8 |
| Go sidecar / 架构取舍 | 8 |
| MCP / Skills | 6 |
| 测试 / 质量 / benchmark | 6 |
| 项目边界 / 反思 | 6 |

回答格式：

```text
问题：
推荐回答：
展开回答：
可以提到的代码：
不要这么答：
面试官可能继续追问：
```

### 6.3 深挖专题

每个专题都要准备“图 + 源码 + 反问”。

| 文件 | 专题 |
|---|---|
| `03-agent-loop-deep-dive.md` | 为什么不是 DAG，Agent Loop 如何可控 |
| `04-context-memory-deep-dive.md` | 上下文预算、压缩、记忆、污染防控 |
| `05-tool-runtime-failure-recovery.md` | 工具权限、审批、失败恢复、证据链 |
| `06-go-sidecar-and-architecture-tradeoff.md` | Python + Go 分工，sidecar 是否值得 |

### 6.4 简历 bullet 拆解

新增：

```text
interview/07-resume-bullets-and-project-story.md
```

内容包括：

- 当前简历 3 条版本。
- 偏后端岗位版本。
- 偏 AI Agent 岗位版本。
- 偏工程平台岗位版本。
- 每条 bullet 对应的追问。
- 每条 bullet 的源码证据。
- 哪些内容不要写得太满。

### 6.5 模拟面试脚本

新增：

```text
interview/08-mock-interview-script.md
```

脚本分三档：

| 时长 | 内容 |
|---|---|
| 10 分钟 | 项目介绍 + 两个核心追问 |
| 20 分钟 | 项目链路 + Agent Loop + 上下文 |
| 45 分钟 | 架构、源码、失败恢复、Go sidecar、系统边界全面追问 |

## 7. 每章质量验收标准

以后每写完或重写一章，都按这个 checklist 检查。

### 7.1 内容完整性

- [ ] 是否讲清楚这个模块解决的问题？
- [ ] 是否解释了为什么需要这个模块？
- [ ] 是否有真实源码路径？
- [ ] 是否有运行链路？
- [ ] 是否有关键数据结构？
- [ ] 是否有失败场景？
- [ ] 是否有测试或验证方式？
- [ ] 是否有面试回答？
- [ ] 是否有系统边界？

### 7.2 可读性

- [ ] 是否避免一行几个字的低密度列表？
- [ ] 是否避免大段贴代码不解释？
- [ ] 是否有表格整理复杂概念？
- [ ] 是否有“先用人话理解”的段落？
- [ ] 是否区分当前实现和未来计划？

### 7.3 事实准确性

- [ ] 文件路径是否存在？
- [ ] 启动命令是否能跑？
- [ ] 接口路径是否和当前代码一致？
- [ ] Go sidecar 默认启用状态是否写准确？
- [ ] MCP/Skills 是否没有被夸大成熟度？
- [ ] 旧 LangGraph / CLI / Streamlit 是否只作为历史，不作为当前主链路？

### 7.4 面试可用性

- [ ] 是否能提炼成 30 秒回答？
- [ ] 是否有深入追问？
- [ ] 是否准备了“为什么不这么做”的回答？
- [ ] 是否准备了“这个方案有什么问题”的回答？
- [ ] 是否能指向具体源码证明？

## 8. 学习站 UI 层面的小改进

这份计划主要是内容，但学习站 UI 也可以配合内容升级。

### 8.1 章节页增加学习辅助区

每章右侧栏建议展示：

- 本章目标
- 核心源码
- 自测问题
- 面试重点
- 预计阅读时间

### 8.2 支持阅读状态

可以保留轻量实现：

- 已读 / 未读。
- 收藏。
- 面试重点标记。
- 最近阅读章节。

不要做复杂账号体系，localStorage 足够。

### 8.3 支持代码路径快速复制

章节里的源码路径可以统一样式，点击复制，例如：

```text
src/api/services/conversation_run_service.py
```

### 8.4 增加“面试模式”

面试模式只展示：

- 30 秒回答。
- 深入追问。
- 源码证据。
- 项目边界。

这样面试前不会被长文淹没。

## 9. 执行顺序

建议按 6 轮完成。

### 第 1 轮：建立标准和目录

任务：

- 新增本计划。
- 更新 `README.md`，说明学习包升级目标。
- 新增缺失目录和占位文件。
- 更新检查脚本，让它检查新增 interview / maps / exercises 文件。

验收：

- 学习站仍能 build。
- 所有新增 Markdown 能被加载。

### 第 2 轮：重写 Agent Loop 和编排

任务：

- 重写 `03-agent-loop.md`。
- 校准 `04-agent-orchestration.md`。
- 新增 `interview/03-agent-loop-deep-dive.md`。
- 新增 `exercises/02-trace-one-real-run.md`。

验收：

- 能完整讲清楚为什么不是 DAG。
- 能指向 loop state、controller、intent route、task board。

### 第 3 轮：重写上下文和记忆

任务：

- 重写 `05-context-management.md`。
- 重写或校准 `06-memory-system.md`。
- 新增 `interview/04-context-memory-deep-dive.md`。
- 新增 `exercises/05-add-a-context-section.md`。

验收：

- 能讲清 ContextPack、ContextLedger、ContextBudget、压缩、记忆选择。

### 第 4 轮：工具治理、失败恢复和测试

任务：

- 重写 `07-tool-governance.md`。
- 校准 `13-testing-and-quality.md`。
- 新增 `interview/05-tool-runtime-failure-recovery.md`。
- 新增 `exercises/03-debug-a-failed-command.md`。
- 新增 `exercises/08-write-one-ablation-case.md`。

验收：

- 能讲清权限、approval、backup、diff、evidence、recovery task、ablation。

### 第 5 轮：Go sidecar、MCP、Skills

任务：

- 重写 `10-go-sidecar.md`。
- 重写 `11-mcp-and-skills.md`。
- 新增 `interview/06-go-sidecar-and-architecture-tradeoff.md`。
- 新增 `maps/go-service-map.md`。

验收：

- 能讲清 Python + Go 分工、sidecar 默认启用策略、MCP/Skills 区别。

### 第 6 轮：面试包和收尾

任务：

- 新增 `interview/02-core-questions.md`。
- 新增 `interview/07-resume-bullets-and-project-story.md`。
- 新增 `interview/08-mock-interview-script.md`。
- 更新 `15-project-retrospective.md`。
- 更新学习站首页，把“学习路径”和“面试路径”分开。

验收：

- 面试前可以只看 interview 目录完成复习。
- 每个简历 bullet 都有源码证据和追问准备。

## 10. 最终验收标准

学习资料升级完成后，应该能做到下面这些事。

### 10.1 你能讲清楚的内容

- nanoCursor 为什么不是普通聊天应用。
- 为什么从固定 DAG 转向 Agent Loop。
- 为什么上下文管理是多 Agent 系统的核心。
- 如何避免简单问答误触发完整开发流程。
- 如何控制工具风险。
- 命令失败后如何恢复。
- Go sidecar 为什么只做部分模块。
- MCP 和 Skills 的区别。
- 前端如何让用户感知运行过程。
- 项目当前还有哪些不成熟地方。

### 10.2 你能现场定位的代码

- 用户消息提交入口。
- run 创建入口。
- intent router。
- Agent Loop state。
- ContextPack 构造。
- ContextLedger / 压缩。
- Tool Policy。
- file ops / Go filetools fallback。
- command runner / Go executor。
- EventStore / SSE。
- 前端消息渲染和右侧栏。

### 10.3 你能回答的面试追问

- 为什么不用 LangGraph？
- 为什么不用全 Go？
- 为什么多 Agent 不一定越多越好？
- 如何证明上下文模块有效？
- 如何证明失败恢复有效？
- 如何避免 Agent 乱改文件？
- 这个项目和 Codex / Cursor 差在哪里？
- 这个项目最有价值和最不足的地方分别是什么？

## 11. 本计划的工作边界

这份计划只规划学习资料和面试资料，不重新规划产品功能。

不在本计划范围内：

- 大规模重构后端。
- 重做前端 UI。
- 新增复杂文档框架。
- 继续扩展 Go 服务。
- 把学习站做成独立商业产品。

在本计划范围内：

- 重写和扩充学习章节。
- 校准源码路径和当前实现。
- 补充练习、代码地图、面试资料。
- 优化学习站的阅读体验。
- 建立文档质量验收标准。

## 12. 下一步建议

下一步不要同时改所有章节。建议先做第 2 轮：Agent Loop 和 Agent 编排。

原因：

1. Agent Loop 是项目故事线的中心。
2. 它能解释为什么不用 LangGraph。
3. 它能串起意图判断、任务板、上下文、工具和事件。
4. 面试里最容易被问。
5. 写透这一章后，后面上下文、工具、失败恢复都会更好展开。

推荐下一轮任务：

```text
重写 03-agent-loop.md，校准 04-agent-orchestration.md，
新增 interview/03-agent-loop-deep-dive.md，
新增 exercises/02-trace-one-real-run.md。
```

