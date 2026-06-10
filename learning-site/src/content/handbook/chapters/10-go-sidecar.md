# 10. Go Sidecar：不是为了炫技，而是处理工程边界

最后更新：2026-06-09

## 1. 本章目标

这一章回答三个问题：

- 为什么 nanoCursor 可以引入 Go，但不应该全量重写后端？
- Go filetools、indexer、executor、MCP gateway 在当前系统里到底承担什么职责？
- 面试里怎么把 Python + Go 的分工讲得像工程选择，而不是为了简历硬塞语言？

## 2. 先给结论

nanoCursor 的主后端仍然应该是 Python。

原因很直接：

- LLM SDK、Prompt 构建、Agent Loop、上下文管理更适合 Python 生态。
- FastAPI 对这个项目的 API 层已经够轻，开发效率高。
- 项目的核心难点不是 HTTP QPS，而是 Agent 如何判断、如何拿上下文、如何安全调用工具。

Go 更适合做边界清楚、行为确定、需要长期稳定运行的 sidecar。

当前最合适的样板是 filetools 和 indexer：文件读写、目录过滤、备份回滚、项目扫描都属于确定性 I/O，适合放到 Go sidecar；Agent 智能决策、上下文选择、审批和事件流仍然留在 Python。

当前 Go 模块状态可以这样记：

| Go 服务 | 默认状态 | 价值 | 备注 |
|---|---|---|---|
| indexer | 默认启用，失败 fallback | 加速项目扫描、隔离索引逻辑 | 适合作为稳定 sidecar |
| filetools | 默认启用，失败 fallback | 文件读写、编辑、备份、回滚 | 最值得重点讲 |
| executor | 默认关闭，智能分流可启用 | 命令超时、取消、进程管理 | 适合测试/构建类长命令 |
| MCP gateway | 默认关闭，可选增强 | 管理 MCP stdio 生命周期 | 适合后续扩展 |
| eventstore / policy 等 | 实验或候选 | 验证 Go 化边界 | 不是主链路必需 |

## 3. 当前 Go filetools 做了什么

目录：

```text
go-services/filetools/
```

核心能力：

- `ReadFile`
- `ListDirectory`
- `WriteFile`
- `EditFile`
- `BackupFile`
- `RollbackFile`
- `ListBackups`
- `Health`

Python 接入层：

```text
src/tools/filetools_client.py
src/tools/file_ops.py
src/api/services/go_filetools_service.py
src/api/routes/runtime.py
```

前端状态展示：

```text
frontend/src/components/context/RunInspector.jsx
```

状态接口：

```http
GET /api/runtime/filetools/status
```

## 4. 调用链怎么走

Agent 并不会直接调用 Go。

真实路径是：

```text
Agent Tool Call
  -> ToolPolicyRuntime 审批/权限判断
  -> src.tools.file_ops
  -> Go filetools client
  -> gRPC sidecar
  -> fallback 到 Python file_ops
  -> RuntimeToolCallbacks 记录 evidence 和事件
```

这样设计的关键点是：Go 只是执行 backend，不负责判断“该不该写文件”。

判断边界仍然在 Python runtime：

- 哪些工具可用？
- 这次写入是不是高风险？
- 是否需要用户 approval？
- 事件怎么进 EventStore？
- 前端怎么展示运行状态？

这避免了 Go sidecar 变成一条绕过审批的旁路。

## 5. 默认启用和 fallback

现在 Go filetools feature flag 默认开启：

```bash
NANOCURSOR_GO_FILETOOLS_ENABLED=true
NANOCURSOR_GO_FILETOOLS_FALLBACK=true
NANOCURSOR_GO_FILETOOLS_ADDR=localhost:50054
NANOCURSOR_GO_FILETOOLS_FAILURE_COOLDOWN_SECONDS=10
```

如果 Go 服务启动了，文件工具优先走 Go。

如果 Go 服务没有启动或调用失败，系统会：

1. 记录结构化日志 `go_filetools_fallback`。
2. 记录事件流 `filetools_backend_fallback`。
3. 自动回退到 Python 文件工具。
4. 进入短暂冷却，避免每次文件操作都重复连接失败。

这就是“默认启用但不脆弱”的关键。

默认启用不等于强依赖。

## 6. 为什么要有契约测试

跨语言 sidecar 最怕的问题是：Python 和 Go 行为慢慢不一致。

所以项目里有 contract test：

```text
tests/contracts/test_filetools_contract.py
```

它验证：

- Python backend 和 Go backend 读写行为一致。
- 目录列表过滤一致。
- 新建文件、覆盖文件、编辑文件语义一致。
- 备份和回滚可用。
- 路径越界不会被放行。

这比“我写了一个 Go 服务”更有工程含金量。

真正值得讲的是：跨语言服务有一致性验证和 fallback 策略。

## 7. 工具治理如何接住 Go

高风险文件操作不会因为 Go 更快就直接执行。

当前规则在：

```text
src/runtime/tool_policy_runtime.py
```

会升级为 `risky_write` 的情况包括：

- `rollback_file`
- 删除、移动、恢复快照
- 写 `.env`
- 写 `package.json`、lock 文件、`pyproject.toml`、`requirements.txt`
- 写 `.github/`、`secrets/`、`credentials/` 等敏感目录
- 单次写入超过 200KB
- 单次 edit 文本过大或行范围超过 200 行

这些操作会进入 approval。

用户拒绝后，Runtime 不会记录为完成的工具调用。

## 8. Python file_ops 和 legacy file_tools 的关系

当前主链路是：

```text
src.tools.file_ops
```

它是 Agent Runtime 的 canonical 文件工具层，负责：

- 读文件
- 写文件
- 编辑文件
- 列目录
- Go sidecar fallback
- 自动语法验证
- backend 诊断事件

旧模块：

```text
src.tools.file_tools
```

仍然保留，但定位是 legacy / AST 兼容模块。

它不能重新成为模型主工具入口。项目用契约测试锁住这个边界：

```text
tests/test_backend_contract.py
```

面试里可以这样解释：

> 我没有立刻删除旧模块，因为它还有 AST 读取和兼容测试价值。但我把主 Agent Runtime 收敛到新的 `file_ops`，并加了 contract test 防止 legacy 模块重新进入模型工具面。这样比一刀切删除更稳。

## 9. 为什么不是全 Go 重写

全 Go 重写并不能解决 Agent 系统最难的部分。

Agent 系统的核心不是：

- API 框架换成 Go
- gRPC 服务越多越好
- 所有模块都微服务化

核心是：

- 上下文怎么选
- 工具怎么治理
- Agent Loop 怎么收敛
- 失败怎么恢复
- 用户怎么知道系统正在做什么

Go 适合作为工程增强，不适合替代智能编排层。

## 10. 面试回答模板

### Q：为什么你的项目里要引入 Go？

可以答：

> 我没有把 Go 当成替代 Python 后端的方案，而是把它放在边界清晰的 sidecar 位置。比如 filetools 负责文件读写、编辑、备份、回滚这些确定性 I/O 操作；Python 仍然负责 Agent Runtime、上下文、审批和事件流。这样做的好处是文件工具层可以独立测试、独立健康检查、独立 fallback，并通过 gRPC contract test 保证跨语言行为一致。

### Q：Go 服务挂了怎么办？

可以答：

> Go filetools 默认启用，但不是强依赖。Python 调用 Go 失败时会记录结构化日志和事件流，然后 fallback 到 Python 文件工具，并进入短暂冷却，避免每次工具调用都重复连接失败。所以它是增强层，不是单点故障。

### Q：Go 会不会绕过权限审批？

可以答：

> 不会。Agent 先经过 `ToolPolicyRuntime` 做权限分级和 approval 判断，只有通过后才会进入 `file_ops` 执行。Go sidecar 只是 `file_ops` 的 backend，不负责决定是否允许操作。敏感文件、大规模写入、rollback 等都会先升级成 `risky_write`。

## 11. 自己应该吃透的检查点

读代码时按这个顺序：

1. `src/runtime/runtime_feature_flags.py`
2. `src/tools/file_ops.py`
3. `src/tools/filetools_client.py`
4. `src/api/services/go_filetools_service.py`
5. `src/api/services/runtime_tool_callback_service.py`
6. `src/runtime/tool_policy_runtime.py`
7. `tests/contracts/test_filetools_contract.py`
8. `go-services/filetools/internal/server/grpc.go`
9. `go-services/filetools/internal/filetools/`

看完后你应该能回答：

- 默认启用 Go filetools 是在哪里决定的？
- Go 不可用时为什么不会让系统崩？
- evidence 是在哪里生成的？
- fallback 事件是在哪里进入 EventStore 的？
- 为什么敏感文件写入需要 approval？
- 为什么 `file_tools.py` 不能直接删？

## 12. 自测题

1. nanoCursor 为什么选择 Python 主后端 + Go sidecar，而不是全 Go 重写？
2. Go filetools 承担了哪些职责？哪些职责仍然留在 Python 端？
3. Agent 调用文件工具时，Go sidecar 在调用链的哪个位置？它负责判断"该不该写文件"吗？
4. Go filetools 不可用时，系统会怎么处理？fallback 机制包含哪些步骤？
5. contract test 验证了什么？为什么对跨语言 sidecar 特别重要？
6. 哪些文件写入操作会被升级为 `risky_write` 并进入 approval？
7. `file_ops.py` 和 `file_tools.py` 的区别是什么？为什么不能直接删掉旧模块？

## 13. 动手练习

1. **读 Go filetools 的 gRPC proto 定义**：打开 `go-services/filetools/` 目录，找到 `.proto` 文件，列出所有 RPC 方法及其请求/响应类型。然后打开 `src/tools/filetools_client.py`，看 Python 端如何调用这些 RPC。
2. **手动触发 fallback**：在 Go filetools 没有启动的情况下启动项目，执行一次文件操作。在日志/事件流中找到 `go_filetools_fallback` 事件，确认 fallback 链路的每一步。
3. **跑 contract test**：运行 `pytest tests/contracts/test_filetools_contract.py -v`，看每个测试用例验证了什么行为。如果某个用例失败，分析是 Python 行为变了还是 Go 行为变了。
4. **追踪 feature flag 的判断逻辑**：打开 `src/runtime/runtime_feature_flags.py`，找到 `go_filetools_enabled()` 和 `go_filetools_fallback_enabled()` 函数，理解它们如何读取环境变量。然后修改 `.env` 中的对应配置，观察系统行为变化。
