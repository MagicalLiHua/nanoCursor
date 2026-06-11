# nanoCursor 学习资料包

这个目录不是普通项目文档，而是给项目作者自己准备的学习手册。它的目标是把 nanoCursor 从“我做了很多功能”整理成“我能说清楚每个模块为什么存在、怎么运行、怎么维护”的知识体系。

## 怎么使用

建议按下面顺序阅读：

1. 推荐先打开学习站：`cd learning-site && npm run dev`，按终端输出访问对应地址（通常是 `http://127.0.0.1:5174`，如果 5173 未被主前端占用也可能是 5173）。
2. 如果只是快速扫一眼，也可以打开旧版静态入口 `index.html`。
3. 阅读 `chapters/01-project-overview.md`，理解项目定位。
4. 阅读 `chapters/02-request-lifecycle.md`，追踪一次真实请求。
5. 重点吃透 `03 Agent Loop`、`05 上下文管理`、`07 工具治理`、`11 MCP/Skills`。
6. 读 `LEARNING_CONTENT_UPGRADE_PLAN.md`，确认下一轮要如何把资料升级成“能吃透项目”的课程化学习包。
7. 读 `CONTENT_REVIEW.md`，确认每章当前状态和推荐学习顺序。
8. 对照 `maps/backend-code-map.md` 找后端入口。
9. 做 `exercises/01-read-the-request-lifecycle.md`，验证自己是否真的看懂。
10. 做 `exercises/02-trace-one-real-run.md`，用三个真实任务追踪 run、事件、上下文和工具调用。
11. 做 `exercises/03-memory-tool-governance-lab.md`，把记忆选择、工具权限和失败恢复串起来验证。
12. 准备面试时先看 `interview/01-project-pitch.md`，再看 `interview/03-agent-loop-deep-dive.md`、`interview/04-context-and-memory.md`、`interview/05-tools-recovery-and-observability.md`、`interview/06-go-mcp-and-project-boundary.md`。
13. 最后刷 `interview/07-interview-question-bank.md`，按模块快速过一遍高频追问。

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
- API 地图
- 事件地图
- 第一个动手练习
- 面试项目讲述模板
- Agent Loop 深度章节
- 上下文管理深度章节
- 工具治理深度章节
- MCP/Skills 深度章节
- React 学习站：导航、搜索、大纲、阅读进度、Markdown 渲染
- 本轮校订：压缩低密度列表，校准 Go sidecar 启动命令、Agent Loop、上下文、失败恢复和学习站阅读体验

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
