# 09. Runtime 与异步边界：别让 async 只停留在语法上

最后更新：2026-06-08

## 1. 本章目标

读完本章，你应该能回答：

- nanoCursor 的 FastAPI 后端里，哪些操作是 async 的，哪些是同步阻塞的？
- 为什么 Agent 执行要放在独立线程里，而不是直接 await？
- `asyncio.to_thread` 在项目中是怎么用的？
- command_runner 的后端路由策略是什么？（Python subprocess → Go executor → fallback）
- Go sidecar 和 Python runtime 之间的异步边界是怎么处理的？

## 2. 异步边界为什么重要

在 async FastAPI 中，如果直接在 async endpoint 里执行阻塞操作：

```python
# ❌ 错误做法
@app.post("/api/run")
async def run():
    result = subprocess.run("pytest", shell=True, capture_output=True)  # 阻塞事件循环!
    return result
```

会导致：
- **事件循环被卡住**：所有其他请求都在等待。
- **SSE 推送延迟**：事件循环被阻塞，心跳发不出去。
- **取消和超时不可靠**：`subprocess.run` 不接受 asyncio 取消信号。
- **并发能力下降**：一个阻塞操作拖慢所有并发请求。

## 3. 代码地图

| 文件 | 职责 |
|------|------|
| `src/api/services/workflow_thread_service.py` | 将 Agent 执行从 API 线程分离到独立线程 |
| `src/runtime/command_runner.py` | 命令执行、超时控制、后端路由 |
| `src/tools/bash.py` | Bash 工具定义和执行入口 |
| `src/runtime/go_runtime_client.py` | Go sidecar HTTP/gRPC 客户端 |
| `src/api/app.py` | FastAPI lifespan、中间件 |
| `src/infra/background.py` | 后台任务管理 |

## 4. Agent 执行线程分离

API 请求不应该阻塞到 Agent 完成。`workflow_thread_service.py` 把 Agent 执行放到独立线程：

```python
# src/api/services/workflow_thread_service.py
def start_workflow_thread(
    *,
    thread_id: str,
    initial_messages: list[Any],
    workspace_dir: str,
    run_context: Any | None = None,
    workflow_runner: Callable[..., Any] | None = None,
) -> threading.Thread:
    if workflow_runner is None:
        from src.api.runtime_facade import run_workflow
        workflow_runner = run_workflow

    context = run_context or get_run_manager().get(thread_id)
    if context is None:
        raise ValueError(f"Run 不在活跃列表中: {thread_id}")

    return _start_thread(context, workflow_runner, (thread_id, initial_messages, workspace_dir))


def _start_thread(run_context, target, args):
    worker = threading.Thread(target=target, args=args, daemon=True)
    run_context.thread = worker
    worker.start()
    return worker
```

关键设计：
- **daemon=True**：主进程退出时，工作线程自动终止，不会阻止进程退出。
- **线程引用保存在 RunContext**：可以跟踪运行状态，支持取消。
- **API 立即返回 thread_id**：前端拿到 thread_id 后通过 SSE 观察进展。

整个流程：

```text
POST /api/conversations/{id}/runs
  → API handler（async）
  → 创建 RunContext + EventStore session
  → start_workflow_thread（不 await Agent 完成）
  → 返回 { thread_id: "..." }
  → 前端拿到 thread_id → 连接 SSE → 观察进展
```

## 5. 命令执行的异步边界

`command_runner.py` 提供两个版本：

```python
# src/runtime/command_runner.py

# 同步版本：内部使用 subprocess.run（阻塞）
def run_command(
    command: str,
    cwd: str | Path,
    timeout_seconds: int = 120,
    permission_level: str = "shell_safe",
    ...
) -> dict:
    ...

# 异步版本：用 asyncio.to_thread 包裹同步版本
async def run_command_async(
    command: str,
    cwd: str | Path,
    timeout_seconds: int = 120,
    ...
) -> dict:
    return await asyncio.to_thread(
        run_command,
        command=command, cwd=cwd, timeout_seconds=timeout_seconds,
        ...
    )
```

`asyncio.to_thread` 把同步阻塞操作放到线程池执行，释放事件循环去处理其他请求。这是 Python 异步编程里的标准模式：

```text
async endpoint
  → await run_command_async(...)
  → asyncio.to_thread(run_command, ...)
  → 在线程池中执行 subprocess.run
  → 事件循环继续处理其他请求
  → subprocess 完成 → 返回结果
```

## 6. 命令执行的后端路由

`command_runner` 不只是调 `subprocess.run`。它有一条后端选择链：

```python
# src/runtime/command_runner.py
def run_command(command, cwd, ...):
    # 第0步：危险命令直接拦截
    blocked = _is_dangerous(command)
    if blocked:
        return _base_result(..., stderr=f"危险命令被拦截 (匹配 '{blocked}')")

    # 第1步：路由决策
    decision = choose_executor_backend(command=command, timeout_seconds=timeout_seconds,
                                        permission_level=permission_level, ...)

    # 第2步：尝试 Go executor（如果决策是 go_executor）
    if decision.backend == "go_executor":
        try:
            result = executor_client.run_command(command, cwd=str(cwd), ...)
            return result
        except Exception as exc:
            if not go_executor_fallback_enabled():
                raise
            # Fallback 到 Python

    # 第3步：尝试 Go runtime HTTP（legacy 后端）
    if go_runtime_enabled() and not go_executor_enabled():
        try:
            result = run_command_via_go_runtime(command, cwd=cwd, ...)
            return result
        except GoRuntimeUnavailable:
            if not go_runtime_fallback_enabled():
                raise

    # 第4步：Python subprocess（兜底）
    result = subprocess.run(command, shell=True, cwd=str(cwd),
                            capture_output=True, timeout=timeout_seconds)
    return { "backend": "python_subprocess", ... }
```

后端路由决策由 `choose_executor_backend` 根据以下条件判断：
- **命令模式**：`pytest`, `npm test`, `go test` 等适合 Go executor（更好的超时和隔离）。
- **简单命令**：`pwd`, `ls`, `cat`, `echo` 等直接走 Python subprocess（开销更小）。
- **超时要求**：长时间命令优先 Go executor（进程组管理更好）。
- **权限级别**：shell_risky 命令可能需要额外隔离。

```python
# .env.example 中的路由配置
NANOCURSOR_EXECUTOR_ROUTING_MODE=auto
NANOCURSOR_EXECUTOR_GO_MIN_TIMEOUT_SECONDS=2
NANOCURSOR_EXECUTOR_GO_COMMAND_PATTERNS=pytest,npm test,npm run build,go test,...
NANOCURSOR_EXECUTOR_PYTHON_COMMAND_PATTERNS=pwd,ls,cat,echo,python -c,node -e,git status
```

## 7. Fallback 策略

每一次 fallback 都是显式的、被记录的：

```python
# src/runtime/command_runner.py
def _emit_command_backend_fallback(callback, command, *, from_backend, to_backend, reason):
    if callback is None:
        return
    callback({
        "type": "command_backend_fallback",
        "command": command,
        "from_backend": from_backend,
        "to_backend": to_backend,
        "reason": reason,
    })
```

这产生事件流中的 `command_backend_fallback` 事件，前端可以展示"Go executor 不可用，已回退到 Python 子进程"。

冷却机制（针对 filetools，同样适用于 executor）：

```bash
NANOCURSOR_GO_FILETOOLS_FAILURE_COOLDOWN_SECONDS=10
```

Go 服务连接失败后，10 秒内不再尝试重连，避免每次工具调用都重复连接失败。

## 8. API 层的异步正确性

FastAPI 的 lifespan 事件：

```python
# src/api/app.py (概念结构)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时
    initialize_runtime_services()
    recover_active_runs()
    yield
    # 关闭时
    persist_runtime_state()
    cleanup_threads()
```

在 lifespan 的 startup 阶段：
- 恢复上次未完成的活跃 run。
- 初始化 EventStore。
- 启动后台任务管理器。

shutdown 阶段的异步边界注意：
- 不需要 `await` 同步的 `threading.Thread.join()` —— 线程自然结束。
- 但需要 `await` 异步的资源清理（如关闭 HTTP 客户端）。
- Go sidecar 的连接通常用同步 gRPC client，需要在 to_thread 或独立线程中管理。

## 9. 线程 vs 协程 vs 进程

nanoCursor 中不同操作的执行模型：

| 操作 | 模型 | 原因 |
|------|------|------|
| API 请求处理 | asyncio 协程 | FastAPI 原生支持，高并发 |
| Agent Loop 执行 | 独立线程 | 是 CPU 密集型 + LLM 网络调用混合，不应阻塞事件循环 |
| LLM API 调用 | asyncio（httpx） | 网络 IO，协程高效 |
| 子进程命令 | 线程池（to_thread） | subprocess.run 是阻塞的 |
| Go sidecar gRPC | 线程池 / 独立线程 | gRPC Python client 同步调用 |
| 文件读写 | 同步（小文件）/ to_thread（大文件） | 小文件同步开销可忽略 |

## 10. 并发控制

```python
# .env.example
MAX_CONCURRENT_RUNS=5
```

`run_start_service.py` 在启动新 run 前检查当前活跃 run 数量：

```python
active_count = sum(1 for ctx in run_manager.list_active() if ctx.status == "running")
if active_count >= max_concurrent_runs:
    raise RuntimeError(f"已达到最大并发运行数: {max_concurrent_runs}")
```

这是简单的并发限流，防止太多 Agent Loop 同时运行耗尽系统资源。

并行 Agent 也有内部的并发控制：

```python
# src/api/services/parallel_agent_service.py
DEFAULT_PARALLEL_LIMIT = 3
semaphore = asyncio.Semaphore(max(1, min(len(agents), DEFAULT_PARALLEL_LIMIT)))
```

## 11. 设计取舍

### 为什么 Agent Loop 不用 asyncio？

Agent Loop 内部有复杂的同步逻辑：循环决策、工具调用链、文件操作、subprocess 调用。如果全用 asyncio，代码复杂度会大幅增加。用独立线程 + daemon=True 更简单，且符合"一个 run 一个线程"的心智模型。

### 为什么不用 multiprocessing？

进程隔离更强，但开销也更大。nanoCursor 作为本地单用户工具，线程隔离已经足够。Go sidecar 提供进程级隔离（独立进程运行命令），是更工程化的方案。

### 为什么 Go executor 是 preferred 而非 required？

因为项目的核心价值是 Agent 智能编排，不是命令执行引擎。Go executor 提供更好的隔离和超时控制，但如果它没启动，Python subprocess 也能完成工作。`fallback_enabled=true` 确保系统不会因为一个 sidecar 没启动就崩。

## 12. 当前不足和后续方向

- `workflow_thread_service.py` 目前调用 legacy `run_workflow`，新 Agent Loop controller 路径仍在迁移中。
- `asyncio.to_thread` 的线程池大小是 Python 默认的（CPU 核数 + 4），高并发时可能需要调整。
- 没有线程级别的取消机制——Agent Loop 线程只能靠检查标志位来响应取消。
- Go executor 的命令路由目前基于模式匹配，可以做更细粒度的资源感知路由。

## 13. 面试预备问题

### Q1：为什么 Agent 执行要放在独立线程？

因为 Agent Loop 是长时间运行的任务（秒到分钟级），包含 LLM 网络调用、文件操作、子进程执行等混合负载。如果 await 在 async handler 里，整个请求期间事件循环都被占用，其他 API 请求和 SSE 心跳都会延迟。独立线程 + daemon=True 让 API 立即返回 thread_id，前端通过 SSE 观察进展。

### Q2：asyncio.to_thread 和 run_in_executor 有什么区别？

`asyncio.to_thread` 是 Python 3.9+ 的便捷函数，内部调用 `run_in_executor(None, ...)`，使用默认的 ThreadPoolExecutor。项目中用它来包装同步的 subprocess.run 和文件操作，保持 API 层面的 async 正确性。

### Q3：Go executor 不可用时系统会不会崩？

不会。命令执行有完整的 fallback 链：Go executor → Go runtime HTTP → Python subprocess。每一步失败且 fallback 启用时，自动降级到下一步。所有 fallback 都产生事件流中的 `command_backend_fallback` 事件，前端可见。

### Q4：为什么不用 Celery/RQ 做任务队列？

nanoCursor 是本地单用户工具，不需要分布式任务队列。单进程内的线程和 asyncio 已经足够管理并发。引入 Celery 需要 Redis/RabbitMQ，增加了运维复杂度，对当前场景是过度设计。

### Q5：daemon=True 的线程有什么风险？

daemon 线程在主进程退出时会被强制终止，不管是否完成。如果有正在写入的文件，可能导致数据不完整。nanoCursor 通过以下方式缓解：
- 写文件用原子操作（写临时文件 → replace）。
- EventStore 用 JSONL 追加，单行写入失败只影响最后一条。
- 关键状态在每次变更后立即持久化，不是只存在内存。

## 14. 自测题

1. 为什么 Agent Loop 要放在独立线程而不是 asyncio 协程？这样做的好处和代价是什么？
2. `asyncio.to_thread` 在项目中用在哪里？它解决了什么问题？
3. 命令执行的后端路由链是什么？（Go executor → Go runtime → Python subprocess）
4. `choose_executor_backend` 根据哪些条件决定命令用 Go executor 还是 Python subprocess？
5. `fallback_enabled=true` 和 `false` 的区别是什么？什么时候应该设为 false？
6. 项目中有哪些地方用到并发控制？`MAX_CONCURRENT_RUNS` 和并行 Agent 的 `Semaphore` 分别在什么层面限流？
7. daemon=True 的线程有什么风险？项目如何缓解这些风险？

## 15. 动手练习

1. **读 command_runner 的完整后端选择逻辑**：打开 `src/runtime/command_runner.py`，从 `run_command` 函数入口开始，用注释标注每一步的后端选择逻辑。画出 flowchart。
2. **测试 fallback 行为**：在 Go executor 未启动的情况下，运行一个命令。在日志/事件流中找到 `command_backend_fallback` 事件，确认从哪个后端 fallback 到哪个后端。
3. **观察线程行为**：在项目运行时，用 `ps -M <pid>` 或 Activity Monitor 观察 Python 进程的线程数。启动一个新 run，看线程数变化。
4. **阅读 executor 路由配置**：修改 `.env` 中的 `NANOCURSOR_EXECUTOR_ROUTING_MODE`（auto → always → never），观察同一命令在不同模式下的后端选择结果。

## 16. 深度学习：async 不是写了 async def 就对了

FastAPI 项目里最常见的误区是：函数写成 `async def`，就以为系统是异步的。实际上，只要在 async 函数里做阻塞操作，事件循环仍然会卡住。

需要特别警惕的阻塞操作：

| 操作 | 为什么会阻塞 |
|---|---|
| `subprocess.run` | 等子进程结束前当前线程不返回 |
| 大文件读写 | 文件 IO 是同步系统调用 |
| 同步 gRPC/HTTP client | 网络等待期间占住线程 |
| 长时间 LLM 流处理 | 如果实现不当会阻塞事件分发 |
| CPU 密集解析 | 大量 AST、diff、索引计算会占 CPU |

判断一个操作该怎么放，可以用下面规则：

```text
短小、可控、同步成本低 -> 可以同步
长时间 IO 或 subprocess -> asyncio.to_thread / 独立线程 / Go sidecar
需要持续后台运行 -> workflow thread
需要更强进程隔离 -> Go sidecar / subprocess manager
```

## 17. nanoCursor 的异步分层

当前系统可以按层理解：

| 层 | 执行模型 | 作用 |
|---|---|---|
| API handler | asyncio | 快速接收请求、返回 thread_id |
| Agent Run | threading.Thread | 长任务后台执行 |
| LLM 调用 | async / streaming | 网络 IO，不应阻塞其他请求 |
| 命令执行 | to_thread + subprocess / Go executor | 阻塞命令隔离出去 |
| 文件工具 | 同步小操作 + Go filetools fallback | 简化实现，关键路径可 sidecar |
| SSE | StreamingResponse | 持续推送事件和 heartbeat |
| Go sidecar | 独立进程 | 高风险/高耗时能力隔离 |

这套分层不是“越异步越好”，而是让不同任务放在合适的执行边界里。

## 18. 为什么 Run 用线程而不是纯 asyncio

从工程角度看，一个 Agent Run 不是单一网络 IO，而是混合负载：

- LLM streaming。
- 文件读写。
- shell 命令。
- EventStore 写入。
- 工具策略和恢复判断。
- 可能的 Go sidecar 调用。

全部写成纯 asyncio 会让每个工具、每个客户端、每个文件操作都要异步化，复杂度很高。当前用“API 协程 + run 线程 + 命令 to_thread”的组合，心智模型更简单：

```text
API 不等 run 完成。
run 在线程里推进。
事件持续写入 EventStore。
前端通过 SSE 观察。
```

代价是：线程取消不如协程优雅，线程数也需要限制。所以系统要有 `MAX_CONCURRENT_RUNS`、取消标志和终态持久化。

## 19. Go sidecar 在异步边界里的价值

Go sidecar 不是为了“简历上有 Go”，更合理的价值是把适合进程隔离的能力移出 Python 主事件循环。

| Go 服务 | 适合原因 | 注意点 |
|---|---|---|
| indexer | 文件扫描、索引构建适合高性能 IO | 小项目可能收益不明显 |
| filetools | 文件读写、备份、回滚可以做成稳定服务 | 跨进程调用有固定开销 |
| executor | 命令执行、超时、进程组管理更适合 Go | 不应全量替换简单命令 |
| MCP gateway | 外部工具连接、协议桥接适合独立边界 | 权限和审计仍由主系统治理 |

关键不是“Go 一定更快”，而是“把高风险或阻塞能力放到更合适的边界里”。简单 `pwd`、`ls`、小文件读取走 Go 反而可能更慢，因为 RPC 开销超过执行收益。

## 20. 异步正确性的排查方法

如果前端感觉卡住或 SSE 不刷新，可以按这个顺序排查：

1. 后端 API 是否还能响应 `/health`。
2. SSE 是否还有 heartbeat。
3. EventStore 是否还在追加事件。
4. run 线程是否还活着。
5. 是否有同步 subprocess 卡住。
6. 是否有 Go sidecar 请求超时没 fallback。
7. 是否前端 store 没消费新事件。

这个顺序很重要。不要一上来改前端 UI。先判断是事件没产生、事件没推送，还是事件推到了但前端没渲染。

## 21. 面试表达模板

### 30 秒回答

nanoCursor 的异步边界是 API 协程负责快速返回，Agent Run 在线程里后台执行，阻塞命令通过 `asyncio.to_thread` 或 Go executor 隔离，运行过程通过 EventStore 和 SSE 推给前端。这样避免长时间代码任务阻塞 FastAPI 事件循环。

### 深入回答

我没有简单把所有函数都写成 async，而是区分不同负载。HTTP 请求和 SSE 适合 asyncio；Agent Loop 是长任务，放到独立线程；subprocess 是阻塞的，所以异步入口用 `asyncio.to_thread` 包装；命令执行可以按策略分流到 Go executor，再 fallback 到 Python subprocess。这样系统即使在跑测试或等待 LLM 时，健康检查、状态查询和 SSE heartbeat 仍能正常响应。

### 当前边界

当前 run 线程取消还不够细粒度，更多依赖状态标志和终态持久化；线程池大小也主要用 Python 默认值。后续如果要做更强的调度，可以引入统一任务调度器或把长任务拆成更细的可取消 step。

## 22. 容易被追问的问题

### Q1：为什么不全用 asyncio？

全 async 需要所有依赖都异步化，包括文件工具、subprocess、gRPC、LLM SDK 和部分 legacy 逻辑。对本地单用户工具来说，线程隔离更简单可靠。关键是不要在事件循环里直接跑阻塞操作。

### Q2：为什么不是 Celery？

Celery 适合分布式任务队列，但需要 Redis/RabbitMQ。nanoCursor 是本地单用户工具，引入 Celery 会大幅增加部署复杂度。当前线程 + EventStore 已经能覆盖需求。

### Q3：Go executor 为什么不能全量替换 Python subprocess？

因为跨进程/RPC 有固定开销。简单命令走 Python 更快，复杂长命令、测试、构建、超时控制更适合 Go executor。成熟路线是智能分流，不是全量替换。

### Q4：如何证明异步边界做对了？

可以做 smoke test：长任务运行中，`/health`、事件查询、SSE heartbeat、前端状态刷新仍然正常。也可以写 benchmark 比较 Go executor 和 Python subprocess 在简单命令、测试命令、超时命令上的差异。

## 23. 本章自测增强

1. `async def` 里调用 `subprocess.run` 为什么仍然会阻塞？
2. Agent Run 放线程的好处和代价分别是什么？
3. `asyncio.to_thread` 适合包装哪些操作？
4. Go executor 为什么适合复杂命令，不适合所有命令？
5. SSE 不刷新时，如何区分后端没事件、SSE 断开、前端没消费？
6. 如果未来做多用户 SaaS，当前异步模型哪些地方必须升级？
