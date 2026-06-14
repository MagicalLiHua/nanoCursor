# nanoCursor 学习资料包

这个目录不是普通项目文档，而是给项目作者自己准备的学习手册。它的目标是把 nanoCursor 从“我做了很多功能”整理成“我能说清楚每个模块为什么存在、怎么运行、怎么维护”的知识体系。

## 怎么使用

建议按下面顺序阅读：

1. 推荐先打开学习站：`cd learning-site && npm run dev`，按终端输出访问对应地址（通常是 `http://127.0.0.1:5174`，如果 5173 未被主前端占用也可能是 5173）。
2. 如果只是快速扫一眼，也可以打开旧版静态入口 `index.html`。
3. 阅读 `chapters/00-learning-roadmap.md`，先建立学习顺序和系统心智图。
4. 阅读 `chapters/01-project-overview.md`，理解项目定位。
5. 阅读 `chapters/02-request-lifecycle.md`，追踪一次真实请求。
6. 重点吃透 `03 Agent Loop`、`05 上下文管理`、`07 工具治理`、`11 MCP/Skills`。
7. 阅读 `chapters/16-architecture-decisions.md`，把关键技术取舍讲清楚。
8. 读 `maps/concept-glossary.md`，统一 Run、Conversation、ContextPack、ToolPolicy、EventStore 等核心概念。
9. 读 `maps/module-evidence-matrix.md`，把模块、源码、事件、测试和面试表达串成证据链。
10. 对照 `maps/backend-code-map.md` 和 `maps/source-navigation-index.md` 找源码入口。
11. 读 `maps/debugging-playbook.md`，学习从真实现象定位 EventStore、意图路由、上下文、工具和前端投影。
12. 做 `exercises/01-read-the-request-lifecycle.md`，验证自己是否真的看懂。
13. 做 `exercises/02-trace-one-real-run.md`，用三个真实任务追踪 run、事件、上下文和工具调用。
14. 做 `exercises/03-memory-tool-governance-lab.md`，把记忆选择、工具权限和失败恢复串起来验证。
15. 做 `exercises/04-run-benchmark-and-ablation.md`，学会用 benchmark 和消融实验证明组件价值。
16. 做 `exercises/05-mastery-audit.md`，用毕业检查确认自己是否能追链路、定位源码、解释取舍。
17. 做 `exercises/06-real-run-walkthroughs.md`，用 direct/read-only/small-edit 三类真实任务把链路串起来。
18. 准备面试时先看 `interview/10-resume-core-mastery.md`，把简历四条主线融成一套完整口述。
19. 然后刷 `interview/11-nanocursor-top-50-qa.md`，用 50 个高频问题覆盖项目定位、架构、Agent Loop、上下文、工具、Go 和复盘。
20. 再看 `interview/01-project-pitch.md`、`interview/03-agent-loop-deep-dive.md`、`interview/04-context-and-memory.md`、`interview/05-tools-recovery-and-observability.md`、`interview/06-go-mcp-and-project-boundary.md`、`interview/08-testing-benchmark-retrospective.md`。
21. 面试前 4 天重点刷 `interview/09-four-day-final-drill.md`，练尖锐追问、源码定位和 1/3/8 分钟口述。
22. 最后刷 `interview/07-interview-question-bank.md`，按模块快速过一遍高频追问。

`LEARNING_PACKAGE_PLAN.md`、`LEARNING_CONTENT_UPGRADE_PLAN.md` 和 `CONTENT_REVIEW.md` 更像建设记录和审校记录，不是正式学习主线。真正学习时优先按上面的顺序走。

## 学习闭环

每个模块都按这个闭环学习：

```text
读章节 -> 找代码 -> 跑任务 -> 看事件/日志 -> 回答问题 -> 做练习 -> 更新笔记
```

如果只是读完文档但不能指出对应代码位置，说明还没有真正掌握。

## 当前内容状态

这一版已经从“文档骨架”升级成“可阅读学习包”。文档不追求把每个源码文件都复述一遍，而是优先解释架构决策、关键链路、常见面试问题和你需要亲手验证的代码位置：

- 项目全景
- 请求生命周期
- 后端代码地图
- 核心概念词典
- 模块证据矩阵：按模块串起源码入口、运行事件、测试验证和面试表达
- 实战排障手册
- API 地图
- 事件地图
- 第一个动手练习
- 三类真实 Run 全链路 Walkthrough
- 面试项目讲述模板
- Agent Loop 深度章节
- 上下文管理深度章节
- 工具治理深度章节
- MCP/Skills 深度章节
- 测试、Benchmark、消融实验与项目复盘深度章节
- 架构决策章节：整理 Agent Loop、ContextPack、EventStore、ToolPolicy、Go sidecar 等关键取舍
- 面试前四天冲刺：尖锐追问、源码定位、口述训练
- React 学习站：导航、搜索、大纲、阅读进度、Markdown 渲染
- 本轮校订：压缩低密度列表，校准 Go sidecar 启动命令、Agent Loop、上下文、失败恢复和学习站阅读体验
- 最新校订：同步默认启用 LLM 语义意图路由，补充 hard guard、deterministic hints、normalizer 和 intent eval 的学习与面试口径
- 课程化补强：新增 `00-learning-roadmap.md`，补充系统总图、请求序列图、四条主线、7 天学习路线和学习验收标准
- 面试主线补强：新增 `interview/10-resume-core-mastery.md`，把 Agent Loop、Context Pack、Python + Go 运行时和可观测执行四条简历内容串成可复述、可追问、可复盘的学习材料
- 高频追问补强：新增 `interview/11-nanocursor-top-50-qa.md`，把原始题库压缩成 50 个口语化问题和回答，用于面试前三天集中复习

## 文件组织

```text
learning-site/src/content/handbook/
  index.html                      # 学习门户
  LEARNING_PACKAGE_PLAN.md         # 学习包建设计划
  LEARNING_CONTENT_UPGRADE_PLAN.md # 高质量内容和面试资料升级计划
  CONTENT_REVIEW.md                # 学习资料审校记录
  README.md                        # 当前目录说明
  chapters/                        # 深度章节
  maps/                            # 代码/API/事件地图
  exercises/                       # 动手练习
  interview/                       # 面试表达材料
  assets/                          # 图片、截图、流程图
  scripts/                         # 轻量检查脚本

learning-site/
  src/                              # React 学习站
  package.json
```
