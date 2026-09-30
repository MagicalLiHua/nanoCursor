# NanoCursor 重构验收记录

日期：2026-09-30。范围：`architecture-refactor-plan.md` 的 R0–R6 降低耦合重构，不包括 Goal 或其他新功能。状态：本轮核心迁移、全量回归与发布产物安装验收完成；平台验证范围见第 6 节。

结论：将事件契约、公共组装、会话绑定与投影、前台事件流以及子 Agent 创建从前端细节中分离。保留现有模型工具循环、恢复账本、权限语义和存储格式。此次不是全仓库重写，也不以所有文件变短作为验收标准。

## 1 基线和保留范围

- 修改前运行当前工作树的全量测试：1459 passed，90.92 秒；没有基线失败。
- 基线包含用户已有的 Agent、TUI、remote、Skill 命令与执行器、Skill 工具和相关测试修改，以及 `skills/catalog.py`。这些改动没有被 stash、reset 或旧版本覆盖。
- 没有创建分支、提交、变更依赖版本、修改配置或持久化 schema。
- 原有 Goal 设计与图保留为后置提案，没有加入自动续跑、预算调度或新的自动授权能力。

### 前端能力基线

| 能力 | TUI | headless prompt | remote |
| --- | --- | --- | --- |
| 主运行 | 交互展示与事件消费 | text 或 stream-json | 浏览器执行保持禁用 |
| 审批 | 人工交互及已配置自动审批 | 缺少所需授权时终止，不补授权限 | 不新增审批或启动能力 |
| 会话 | 创建、恢复、切换、清除及命令 | 每次 prompt 建立受恢复保护的会话 | 不启用浏览器会话执行 |
| compact | 手动命令和自动事件 | 既有自动压缩，保持输出协议 | 不启用新路径 |
| 记忆 | 既有主会话召回与可选整理 | 不新增交互召回或整理能力 | 不启用新路径 |
| Skill 与 MCP | 保留现有配置、命令和连接行为 | 保留原有工具能力，不补齐 TUI 功能 | 不启用新路径 |
| 子 Agent 与团队 | 既有可选能力和交互设置 | 既有可选能力及 in-process 配置 | 不启用新路径 |
| 取消与收尾 | 保留前台门禁和自有任务管理 | 结果输出前完成自有资源收尾 | 代码使用同一关闭边界，执行门禁不变 |

remote 的 `_init_agent()` 原本即抛出禁用错误。本次删除其后不可达的重复初始化代码；没有解除这一限制，也没有把 TUI 能力自动移植到浏览器。

## 2 新边界与状态所有权

| 模块 | 负责什么 | 保留的边界 |
| --- | --- | --- |
| `nanocursor/events.py` | 单一事件与压缩边界类型 | 不加载 Agent 引擎或前端；旧导入路径重导出同一对象 |
| `application/bootstrap.py` | typed AgentSettings、AgentDependencies、权限与主 Agent 组装 | client 由调用者提供，不提前创建连接或取得共享资源所有权 |
| `application/background.py` | AgentLoader、TeamManager 和子任务工具的共用注册 | 显式声明 fork、团队、交互和协调器能力，不创建后台运行任务 |
| `application/session.py` | 恢复绑定、会话发布顺序、文件历史关联与会话投影 | 不替代 RecoveryRuntime；提交前失败才恢复候选状态 |
| `application/execution.py` | 单次前台事件消费、关闭和投影刷盘顺序 | 不创建另一个 Agent Loop，不重复接管 TaskManager |
| `agents/factory.py` | 显式 SpawnContext 和共用子 Agent 构造 | 保留共享、复制、隔离、禁止嵌套 spawn 和 Skill authority guard |
| `commands/ports.py` | 显式会话、记忆、Skill、状态能力 | handler 不读 UI 私有字段或回调字典 |
| `commands/compat.py` | 旧 CommandContext 字典调用的薄兼容转换 | 生产前端使用 typed services；兼容层不承担业务策略 |

Agent 现在提供 session binding、usage reset、history reset、file-version invalidation、plan path、hook 和记忆收尾的公共入口。UI 只发布当前会话和清理展示状态，不逐项重置 Agent 内部字段。

### 关键时序

1. 会话切换先完成恢复绑定，再发布新会话，之后更新 Agent 状态和授权记录。授权保存回调必须看到新会话，不能写入旧句柄。
2. 自动 compact 和 memory 候选由 SessionPersistence 确认。正文未提交时恢复完整历史与用量锚点；正文已提交但元数据失败时保留新状态并报告错误。
3. ForegroundRun 在 TurnComplete 和 LoopComplete 前确认投影；结束或取消时先关闭 Agent 流、等待其工具收尾，再刷盘剩余结果。
4. 完整 assistant 工具调用 envelope 的持久屏障继续留在 Agent/RecoveryRuntime 内部，仍先于工具副作用。外层投影不能代替该屏障。
5. 子 Agent 工厂只构造实例；trace、worktree 成果保护、后台登记、通知发布和外部 pane 启动继续由既有生命周期服务负责。

## 3 实际耦合变化

| 检查项 | 修改前 | 修改后 |
| --- | --- | --- |
| TUI 直接访问的 Agent 私有成员种类 | 6 | 0 |
| TUI 直接访问的 Agent 属性种类 | 31 | 26 |
| NanoCursorApp 类体行数与方法数 | 2061 行、84 方法 | 1938 行、87 方法 |
| Agent 类体行数与方法数 | 974 行、31 方法 | 1028 行、40 方法 |
| Python 模块数量 | 176 | 185 |

Agent 类体增大是因为状态重置回到实际拥有状态的对象，而不是 UI 继续理解私有字段。数字仅用于解释迁移，不代表量化健康评分。

事件消费者不再为了获得事件类型导入整个 Agent。fork registry 不再反向从 AgentTool 实现取常量。没有重写现有记忆/恢复模块，也不要求消灭所有潜在延迟导入闭环。

## 4 回归保护

新增 50 个合同和架构检查用例，包括参数化场景：

- 原 Agent 事件与 context CompactBoundary 导入保持对象身份；字段、permission future 和事件注解解析保持兼容。
- 共用组装保留显式 client、registry、权限、工作区和前端可选工具差异。
- session 发布先于授权回调；恢复绑定失败不发布新会话；前端与 Agent 的工具注册表都关联正确文件历史。
- compact/memory 区分正文失败、已提交元数据失败和报告错误；刷盘重试不重复已确认的消息。
- 每轮投影先于展示；提前关闭和取消等待 Agent 收尾，再保存结果。
- SpawnContext 保留共享 client/hooks、独立 registry、复制替换状态、Plan 与禁止嵌套 spawn。
- handler、应用服务和契约不依赖具体前端；handler 不再探测 UI 私有能力或读取配置回调字典。
- 三个前端的事件入口使用 ForegroundRun；普通子 Agent、Skill fork 与进程内队友不直接调用 Agent 构造函数。

既有权限、审批 replay、流式参数完整性、执行前屏障、恢复故障注入、generation/session 隔离、worktree、取消、Skill 和 UI 终态测试均继续参与全量回归。测试替身仅补齐新的公共方法合同，没有删断言、降低断言或增加 xfail。

AST 约束不是完整的动态依赖证明，无法穷尽反射、任意回调和外部插件。命令补全 widget 是前端组件，仍可依赖 Textual，不把它误判为业务契约。

## 5 验证记录

| 验证 | 结果 |
| --- | --- |
| 重构前全量 pytest | 1459 passed，90.92 秒 |
| 事件接口相关测试 | 53 passed |
| 命令、会话和 prompt 相关测试 | 117 passed |
| 运行、取消与子任务相关测试 | 274 passed |
| UI 事件、终态及提交相关测试 | 37 passed |
| 共享后台组装相关测试 | 80 passed |
| 最后 session 初始化迁移相关测试 | 70 passed |
| 最终全量 pytest | 1509 passed，91.41 秒 |
| runtime constraints 与 uv.lock 一致性 | 已通过 |
| wheel 和 sdist 构建及内容检查 | 最终代码已通过，必需资源、版本和 CLI 入口完整 |
| 独立 wheel 安装 smoke | 最终代码已通过，独立命令、setup、doctor、工作区和读写工具 |
| 独立 sdist 安装 smoke | 最终代码已通过，移走安装源后可独立运行 |
| git diff --check | 已通过 |

测试使用本地 mock/fixture 模型，没有真实模型费用。完整测试命令为 `uv run --offline pytest -q`。uv 缓存位于工作区外，执行使用授权的现有缓存访问。

构建和验收沿用仓库脚本：

```bash
uv run --offline python scripts/export_constraints.py --check
uv build --offline --build-constraints packaging/build-constraints.txt --out-dir /tmp/nanocursor-refactor-dist
uv run --offline python scripts/check_distribution.py /tmp/nanocursor-refactor-dist
uv run --offline python scripts/smoke_install.py /tmp/nanocursor-refactor-dist/nanocursor-3.0.0-py3-none-any.whl --python 3.12
uv run --offline python scripts/smoke_install.py /tmp/nanocursor-refactor-dist/nanocursor-3.0.0.tar.gz --python 3.12
```

smoke runner 离线启动，但脚本内的独立安装会在临时 uv 环境下载锁定依赖。验证不复用开发 `.venv`；sdist 安装后移走安装源目录；setup、doctor、独立工作目录和读写工具只调用本地 fixture 服务。

## 6 限制和后续

- 本机为 macOS、Python 3.12。没有代替远端 CI、Windows/Linux 矩阵、所有真实 provider 或长时间真人交互验收。
- TUI 仍包含较多 widget、输入、渲染和任务展示逻辑；本轮先移走状态所有权与运行边界，不声称所有代码都已完全解耦。
- client 的创建、连接关闭、共享 MCP 和 worktree 成果保护继续沿用各宿主的既有归属。没有引入第二套通用调度/资源管理框架。
- 旧事件导入、CompactBoundary 导入及字典 CommandContext 保留兼容入口。新的生产代码应使用 events、typed services 和共享应用边界，而非继续扩散旧写法。
- Goal 仍未实现。完成本轮验收后，可单独确认它的结束、阻塞、暂停、恢复及预算语义，再接入共享运行入口。
