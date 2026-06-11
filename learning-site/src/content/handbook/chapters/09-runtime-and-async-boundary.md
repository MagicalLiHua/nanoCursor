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
