# 面试深挖：Go Sidecar、MCP/Skills 与项目边界

最后更新：2026-06-11

这份材料用于准备项目最后一组容易被追问的内容：为什么引入 Go、MCP/Skills 做到了什么程度、项目和成熟工具的边界在哪里。

## 1. 一句话版本

nanoCursor 不是把后端强行微服务化，而是用 Python 承担 Agent 编排和上下文治理，用 Go sidecar 承担文件工具、索引、命令执行、MCP stdio 这类边界清楚的确定性系统能力；MCP/Skills 则作为可治理的扩展能力接入，而不是无脑加载外部工具和提示词。

## 2. 30 秒版本

项目里引入 Go 不是为了替换 Python，而是为了处理边界清楚、确定性强、适合独立进程的模块，比如 indexer、filetools、executor 和 MCP gateway。Python 仍然负责 Agent Loop、上下文、审批和事件流。MCP 解决外部工具调用，Skills 解决任务规范注入，两者都要进入权限治理和上下文预算，不能绕过安全边界。

## 3. Go 相关追问

### Q1：为什么要在 Python 项目里引入 Go？

答法：

> 因为有些模块更像系统边界，而不是智能逻辑。比如文件扫描、备份回滚、命令执行超时、MCP stdio 生命周期，这些行为确定、需要稳定进程管理，适合放到 Go sidecar。Python 保留 Agent Runtime 和业务编排。

### Q2：是不是为了简历硬塞 Go？

答法：

> 我没有全量 Go 化，而是做了边界选择。默认启用的是 indexer/filetools，因为它们适合确定性 I/O；executor/MCP gateway 是可选增强；eventstore/policy 这类还只是候选。这样能说明我不是为了语言而拆服务，而是按模块特性做取舍。

### Q3：Go 一定比 Python 快吗？

答法：

> 不一定。跨进程 gRPC 有固定成本，小命令、小文件操作可能 Python 更快。Go 的价值在大目录扫描、长命令、进程管理、超时取消和稳定服务边界。项目里 executor 做智能分流，就是因为不是所有命令都适合走 Go。

### Q4：Go 服务挂了怎么办？

答法：

> sidecar 是增强层，不是单点故障。系统有 feature flag、health check、fallback 和 cooldown。Go 不可用时回退 Python，并通过 EventStore 记录 fallback 事件。

### Q5：Go 会不会绕过工具审批？

答法：

> 不会。权限判断和 approval 在 Python ToolPolicyRuntime。Go sidecar 只是执行 backend。比如写敏感文件、rollback、安装依赖等动作仍然先被 Python 判为高风险。

## 4. MCP/Skills 相关追问

### Q6：MCP 和 Skills 的区别是什么？

答法：

> MCP 是工具协议，解决 Agent 能调用什么外部能力；Skills 是任务规范，解决 Agent 遇到某类任务应该按什么标准做。MCP 偏执行，Skills 偏行为指导。

### Q7：你们的 MCP 原理和成熟工具一致吗？

答法：

> 主流程一致：配置 server、启动或连接 server、发现工具、注入工具 schema、执行工具调用、返回结果，并对高风险工具做权限控制。差距在生态成熟度，比如 OAuth/secret 管理、长连接复用、更多 server 兼容和前端体验。

### Q8：为什么 GitHub 上的 Skills 不能直接加载？

答法：

> 因为 Skills 是外部指令，可能包含读取密钥、删除文件、安装依赖、绕过审批等危险内容。正确流程是导入、解析、扫描、规范化、显示风险、用户确认启用，再按 intent 选择注入。

### Q9：MCP 写工具失败后能不能自动 fallback？

答法：

> 只读 MCP 可以 fallback 到本地 read/search。写 MCP 可能产生外部副作用，不能自动替代，否则可能重复创建远程资源或造成状态不一致。

### Q10：Go MCP Gateway 的意义是什么？

答法：

> MCP stdio server 的进程生命周期、超时、取消、stderr 捕获和隔离更适合独立 sidecar。Python 负责业务决策，Go 负责进程边界。

## 5. 项目边界相关追问

### Q11：这个项目和 Codex/Cursor 的差距在哪里？

答法：

> 差距很大。成熟工具有更强模型、更好的编辑器集成、更稳定的沙盒、安全策略和真实生产反馈。nanoCursor 的价值不是替代它们，而是把 Agent Loop、上下文管理、工具治理、事件流、失败恢复、Go sidecar 和 MCP/Skills 这些机制拆开实现，作为工程能力展示。

### Q12：它现在还是玩具吗？

答法：

> 如果按商业产品标准，它还不是成熟工具；但按个人项目和简历项目标准，它已经不是玩具 Demo。因为它不只是聊天 UI，而是有 run 生命周期、上下文预算、工具权限、EventStore、SSE、失败恢复、Go sidecar 和 benchmark 的完整工程系统。

### Q13：最大短板是什么？

答法：

> 一是复杂任务成功率还依赖模型能力和上下文命中率；二是 MCP/Skills 生态兼容还不完整；三是多用户安全和隔离不是当前目标；四是前端体验仍不如成熟工具。

### Q14：下一步最值得做什么？

答法：

> 如果继续做技术深度，我会做三件事：context hit rate / miss audit，Agent decision eval，failure recovery benchmark。它们能证明系统组件是否真的有价值，而不是继续堆功能。

## 6. 回答原则

面试时尽量遵循三条：

1. 不夸大：不要说替代 Codex/Cursor。
2. 讲取舍：为什么这部分用 Go，为什么不全量微服务。
3. 讲证据：contract test、fallback、EventStore、benchmark、approval 都是证据。

## 7. 快速复习卡片

| 问题 | 一句话答法 |
|---|---|
| Go 为什么存在 | 边界清楚的确定性系统能力适合 sidecar |
| 为什么不全 Go | Agent 编排和 LLM 生态更适合 Python |
| Go 失败怎么办 | feature flag + health check + fallback + cooldown |
| MCP 是什么 | 外部工具协议 |
| Skills 是什么 | 任务规范和领域知识 |
| GitHub Skills 风险 | 外部指令可能包含危险操作 |
| 项目亮点 | Agent Loop、上下文、工具治理、事件、恢复、Go sidecar |
| 项目边界 | 不是商业工具，MCP/Skills 生态和真实任务稳定性仍有限 |
