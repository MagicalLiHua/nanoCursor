# 08. EventStore 与 SSE：让运行过程可观察

最后更新：2026-06-09

## 1. 本章目标

读完本章，你应该能回答：

- nanoCursor 如何把后端运行过程变成前端可见的实时状态流？
- EventStore 的数据模型是什么？session.json 和 events.jsonl 分别存什么？
- SSE（Server-Sent Events）是怎么实现的？和 WebSocket 有什么区别？
- 事件如何被写入、持久化、推送给监听者、再被前端消费？
- 前端 SSE 断开后如何恢复？reconciliation 机制是什么？

## 2. 为什么需要事件流

AI 编程任务不是请求-响应式的。一次 run 可能持续几十秒甚至几分钟，期间系统会经历意图判断、执行计划、临时 Agent 创建、工具调用、审批等待、Diff 生成和报告总结。

如果用户只能看到"正在处理中..."然后等 30 秒出结果，体验会很差。事件流解决的就是：**让用户实时感知系统正在做什么**。

```text
没有事件流： 用户发送 → [等待30秒...] → 结果
有事件流：   用户发送 → Lead判断 → 读文件 → 写文件 → 测试 → 报告 → 完成
                       ↑         ↑        ↑       ↑      ↑
                    前端实时展示每一步
```

## 3. EventStore 的数据模型

EventStore 是事件持久化的核心。每个 run 在文件系统上有一个目录：

```text
.nanocursor/runs/<thread_id>/
  session.json      # 运行会话元数据
  events.jsonl      # 追加式事件日志
```

### 3.1 Session

```python
# src/api/services/event_store.py
def create_session(self, thread_id, prompt, workspace_dir, status="running", mode="agenthub_delivery"):
    session = {
        "thread_id": thread_id,
        "workspace_dir": str(Path(workspace_dir).resolve()),
        "status": status,          # running / completed / failed / cancelled
        "prompt": prompt,
        "mode": mode,
        "created_at": now,
        "updated_at": now,
    }
    self.session_path(thread_id, workspace_dir).write_text(
        json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
```

Session 是运行的总控记录。后续可以通过 `update_session` 追加字段：
- `intent_decision`
- `conversation_id`
- `team`
- `execution_plan`
- `execution_summary`

### 3.2 事件追加

```python
# src/api/services/event_store.py
def append_event(self, thread_id, event_type, title="", content="",
                  agent="lead", payload=None, workspace_dir=None) -> AgentEvent:
    event = AgentEvent(
        id=str(uuid.uuid4()),
        thread_id=thread_id,
        type=event_type,
        timestamp=time.time(),
        agent=agent,
        title=title,
        content=content,
        payload=payload or {},
    )
    with self._lock:
        # 追加到 JSONL 文件
        with self.events_path(thread_id, workspace_dir).open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
        # 通知所有注册的监听者
        listeners = tuple(self._listeners)

    for listener in listeners:
        try:
            listener(event)
        except Exception:
            logger.warning("event_store_listener_failed", ...)
    return event
```

关键设计：
- **JSONL 格式**：每行一个 JSON 对象，方便追加和流式读取。
- **线程安全**：用 `threading.RLock` 保护写操作。
- **监听者模式**：写入后通知所有注册的 listener（通常用于 SSE 推送）。

### 3.3 事件读取

```python
def list_events(self, thread_id, workspace_dir=None, after=0) -> list[AgentEvent]:
    path = self.events_path(thread_id, workspace_dir)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines[max(after, 0):]:
        if not line.strip():
            continue
        events.append(AgentEvent(**json.loads(line)))
    return events
```

`after` 参数支持增量读取——前端拿到最后一个事件的时间戳后，只读取增量。

### 3.4 thread_workspace 索引

EventStore 维护一个全局索引，记录每个 thread_id 对应的 workspace_dir：

```python
def _remember_thread_workspace(self, thread_id, workspace_dir):
    # 写入 .nanocursor/thread_workspaces.json
    data[thread_id] = str(Path(workspace_dir).resolve())
```

这解决了"只知道 thread_id，不知道怎么找到它的 workspace_dir"的问题。

## 4. 统一事件发射服务

`event_service.py` 提供了 `emit_event`，这是所有新代码应该使用的统一入口：

```python
# src/api/services/event_service.py
def emit_event(
    thread_id: str,
    event_type: str,
    *,
    title: str = "",
    content: str = "",
    agent: str = "system",
    payload: dict[str, Any] | None = None,
    workspace_dir: str | None = None,
    event_store: EventStore | None = None,
) -> RunEvent:
    store = event_store or get_event_store()
    payload = dict(payload or {})
    payload = validate_event_payload(event_type, payload)  # Schema 校验

    legacy = store.append_event(...)
    return RunEvent(
        schema_version=SCHEMA_VERSION,
        id=legacy.id, ...
    )
```

为什么要在 `append_event` 之上包一层？
- 统一 `schema_version`：所有事件都有版本号，方便后续迁移。
- payload 校验：在写入前检查 payload 是否符合预期结构。
- 归一化输出：无论底层存储格式如何变化，上层始终拿到 `RunEvent`。

## 5. SSE 实现

SSE 基于 HTTP 长连接，服务端向客户端单向推送事件：

```python
# src/api/services/legacy_sse_service.py
def stream_legacy_run_events(thread_id: str, active_runs: dict[str, Any]) -> StreamingResponse:
    run_info = active_runs.get(thread_id)
    event_queue = run_info["queue"]

    def event_generator():
        while True:
            try:
                item = event_queue.get(timeout=300)  # 5分钟超时
                if item is None:
                    break
                event_type = json.loads(item).get("type", "message")
                yield f"event: {event_type}\ndata: {item}\n\n"
                if event_type in ("done", "error"):
                    break
            except queue.Empty:
                yield ": heartbeat\n\n"  # 心跳防止连接断开

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        },
    )
```

### SSE vs WebSocket

| 维度 | SSE | WebSocket |
|------|-----|-----------|
| 方向 | 服务端→客户端单向 | 双向 |
| 协议 | HTTP | 独立协议（ws://） |
| 重连 | 浏览器自动 | 需手动实现 |
| 复杂度 | 低 | 中 |
| 适用场景 | 状态推送 | 实时双向通信 |

nanoCursor 的场景是"服务端推状态给前端"，不需要前端推消息给服务端（前端通过 REST API 发送消息），所以 SSE 更合适。

## 6. 前端 SSE 消费

前端的 `useSSE` hook 管理 SSE 连接的生命周期：

```javascript
// frontend/src/hooks/useSSE.js
function connectEvents(threadId) {
    const es = new EventSource(url);

    // 按事件类型注册监听器
    SSE_EVENT_TYPES.forEach((type) => {
        es.addEventListener(type, (event) => {
            handleParsedEvent(JSON.parse(event.data));
        });
    });

    // 连接断开时的恢复逻辑
    es.onerror = () => {
        es.close();
        if (!["running", "waiting_approval", "cancelling"].includes(status)) {
            return;  // 已结束，不需要恢复
        }
        // 尝试从 session 恢复
        loadRunSession({ fetchJson, threadId }).then(async (session) => {
            if (TERMINAL_RUN_STATUSES.has(session.status)) {
                await hydrateAfterDone(threadId, apiClient, session.status);
            }
        });
    };
}
```

前端注册了 40+ 种事件类型。关键类别：

| 事件类别 | 示例 | 前端展示 |
|---------|------|---------|
| 运行生命周期 | `run_started`, `done`, `error` | 聊天框状态行 |
| Agent 活动 | `agent_activity`, `agent_complexity_assessed` | Agent 动态列表 |
| 工具调用 | `tool_call_finished`, `file_changed` | 工具调用气泡 |
| 审批 | `approval_requested`, `approval_resolved` | 审批按钮 |
| 临时 Agent | `ephemeral_agent_spawned`, `ephemeral_agent_completed` | 子 Agent 面板 |
| 并行 Agent | `parallel_agents_started`, `parallel_agents_completed` | 并行进度 |
| 交付物 | `report_ready`, `diff_updated`, `benchmark_finished` | 底部证据抽屉 |

## 7. Reconciliation：SSE 断开后的状态恢复

SSE 连接可能断开（网络波动、服务重启）。前端有 reconciliation 定时器：

```javascript
// frontend/src/hooks/useSSE.js
function startStatusReconciliation(threadId, apiClient, es) {
    reconciliationTimerRef.current = setInterval(async () => {
        const state = useStore.getState();
        // 只有运行中的 run 才需要 reconciliation
        if (!["running", "waiting_approval", "cancelling"].includes(state.status)) {
            stopStatusReconciliation();
            return;
        }
        const session = await loadRunSession({ fetchJson, threadId });
        const sessionStatus = session.status || "running";
        if (TERMINAL_RUN_STATUSES.has(sessionStatus)) {
            // 运行已结束，SSE 连接失效，从 artifacts 恢复
            stopStatusReconciliation();
            es.close();
            await hydrateAfterDone(threadId, apiClient, sessionStatus);
        }
    }, 2000);  // 每 2 秒检查一次
}
```

`hydrateAfterDone` 在运行结束后从快照和 artifacts 恢复完整状态——报告、diff、metrics、交付物等。

## 8. 事件归一化

`event_schema.py` 中的 `normalize_event` 确保从磁盘读取的旧格式事件也能被正确处理：

```python
# src/api/services/event_service.py
def get_normalized_events(thread_id, *, workspace_dir=None) -> list[RunEvent]:
    raw_events = store.list_events(thread_id, workspace_dir)
    return [normalize_event(e.model_dump()) for e in raw_events]
```

`build_event_summary` 从事件列表生成轻量摘要，用于报告头部和快速状态检查：

```python
def build_event_summary(events: list[RunEvent]) -> dict[str, Any]:
    return {
        "event_count": len(events),
        "tool_calls": tool_calls,
        "tool_failures": tool_failures,
        "stages_touched": sorted(stages),
        "files_changed_count": len(files_changed),
    }
```

## 9. 事件写入的完整链路

一次工具调用的完整事件链路：

```text
Agent 决定调用工具
  → ToolPolicyRuntime 检查权限
  → 执行工具（如 write_file）
  → 生成 evidence（备份、diff、错误信息）
  → emit_event("tool_call_finished", payload={ok, result, evidence})
  → EventStore.append_event → 写入 events.jsonl
  → 通知 listener → SSE 推送给前端
  → 前端 handleAgentEvent → 更新 Zustand store
  → React 重渲染 → 用户看到工具调用结果
```

## 10. 当前不足和后续方向

- SSE 的 legacy 实现在 `legacy_sse_service.py`，新系统在 `event_service.py`。两条路径的整合还有空间。
- 当前从 `events.jsonl` 全量读取，大量事件时不够高效。Go eventstore sidecar 已经作为实验模块存在，但还不是主链路依赖。
- 前端 reconcile 目前是 2 秒轮询，未来可以接入更强的事件索引或流式订阅以减少延迟。
- 事件没有索引，跨 run 的聚合分析（如"过去 10 次 run 的工具失败率"）需要全量扫描。

## 11. 面试预备问题

### Q1：为什么用 SSE 而不是 WebSocket？

nanoCursor 的场景是单向推送（服务端→前端），SSE 比 WebSocket 更简单：浏览器原生支持自动重连，不需要自定义心跳，基于 HTTP 更容易穿透代理和防火墙。前端向服务端发送消息走 REST API，不需要双向通道。

### Q2：EventStore 为什么用 JSONL 而不是数据库？

对本地单用户工具来说，JSONL 文件更简单：可以直接 `cat` 查看、`grep` 搜索、不需要运维数据库。追加式写入天然线程安全，不需要事务。当需要跨 run 聚合分析时再考虑数据库。

### Q3：SSE 连接断开后怎么恢复？

前端有 reconciliation 定时器（每 2 秒），检查 session status 是否已经变成终态（completed/failed/cancelled）。如果是终态，从 session.json 和 artifacts 恢复完整状态。如果仍在运行，提示用户"事件流已断开，可通过同步恢复状态"。

### Q4：EventStore 的 listener 模式有什么用？

Listener 允许在事件写入时同步通知其他组件，而不需要各组件轮询 JSONL 文件。当前主要用于 SSE 推送（写入事件 → 通知 listener → 推送给前端）。未来可以扩展为 webhook、审计日志等。

### Q5：为什么事件需要 schema_version？

因为事件格式可能演化。旧 run 的事件可能缺少新字段（如 `schema_version`、`evidence`）。schema_version 和 `normalize_event` 确保向前兼容——旧事件能被新前端正确展示，不需要迁移历史数据。

## 12. 自测题

1. EventStore 的 `session.json` 和 `events.jsonl` 分别存储什么？为什么用 JSONL 而不是数据库？
2. SSE 和 WebSocket 的区别是什么？nanoCursor 为什么选择 SSE？
3. `emit_event` 在 `append_event` 之上增加了什么？为什么不能直接调 `append_event`？
4. 前端 `useSSE` hook 注册了多少种事件类型？为什么 `onmessage` 和 `addEventListener` 都要处理？
5. reconciliation 定时器是做什么的？它每多少秒检查一次？检查什么？
6. `hydrateAfterDone` 的恢复优先级是什么？（snapshot → artifacts → replay）
7. EventStore 的 listener 模式有什么用？当前主要用于什么场景？

## 13. 动手练习

1. **读一次真实运行的 events.jsonl**：启动项目，执行一次任务。在 `.nanocursor/runs/<thread_id>/events.jsonl` 中查看所有事件。统计每种事件类型的数量，画出事件类型的时间线。
2. **用 curl 测试 SSE 端点**：`curl -N http://127.0.0.1:8100/api/runs/<thread_id>/events`，观察 SSE 的原始输出格式（`event: xxx\ndata: {...}\n\n`）。
3. **断开 SSE 连接测试恢复**：在任务运行中强制关闭浏览器标签页，然后重新打开。观察前端是否通过 reconciliation 恢复状态。
4. **阅读 event_schema 的 validate 逻辑**：打开 `src/runtime/event_schema.py`，找到 `validate_event_payload` 函数，理解它如何校验不同事件类型的 payload 结构。

## 14. 深度学习：事件不是日志，是运行时账本

很多项目也会“打日志”，但 EventStore 不是普通日志。普通日志主要给开发者排错，EventStore 同时服务四个对象：

| 对象 | 需要事件做什么 |
|---|---|
| 前端 | 实时展示 Agent 正在做什么 |
| 后端 | 从事件恢复 run 状态、生成报告和快照 |
| 评测 | 统计工具失败、文件变更、耗时和完成质量 |
| 面试讲述 | 证明系统不是黑盒，而是可复盘的执行过程 |

所以事件要尽量满足三件事：

1. 可追加：run 过程中持续写入。
2. 可重放：前端断线后可以从事件恢复状态。
3. 可解释：每条事件能说明谁、何时、做了什么、结果如何。

这也是为什么 `events.jsonl` 比单纯内存队列重要。内存队列只解决实时推送，EventStore 解决运行历史和复盘。

## 15. 事件、状态、快照三者区别

学习这章时要区分三个概念。

| 概念 | 作用 | 例子 |
|---|---|---|
| Event | 发生过的一件事 | `file_changed`、`tool_call_finished` |
| Session State | 当前 run 的元信息和终态 | `status=completed`、`execution_plan` |
| Snapshot | 某个时刻恢复 UI 所需的聚合视图 | timeline、conversation、activity、diff summary |

它们不是互相替代的关系：

- 事件负责完整历史。
- session 负责快速查询 run 当前状态。
- snapshot 负责前端快速恢复。

如果只有事件，恢复 UI 要每次全量 replay；如果只有 snapshot，丢失中间细节；如果只有 session，完全看不到执行过程。

## 16. 一个事件应该包含什么

一个高质量事件至少要回答：

```text
谁发出的？
属于哪个 run？
发生在什么时候？
事件类型是什么？
用户能不能看懂 title/content？
机器能不能读懂 payload？
是否关联文件、工具、任务、审批或失败？
```

例如文件写入事件，不应该只写：

```json
{"type": "file_changed", "content": "done"}
```

更好的结构是：

```json
{
  "type": "file_changed",
  "agent": "coder",
  "title": "文件已修改",
  "content": "更新 README.md 的后端启动命令",
  "payload": {
    "path": "README.md",
    "operation": "edit",
    "additions": 3,
    "deletions": 1,
    "evidence_id": "tool_..."
  }
}
```

人看 `title/content`，系统看 `payload`。这是事件设计里很重要的分工。

## 17. 前端可感知运行的正确边界

前端不应该凭空猜测 Agent 状态。它应该消费后端事件，把事件投影成 UI：

```text
EventStore 事实
  -> SSE 推送
  -> 前端 store 归一化
  -> 聊天消息 / Agent 动态 / 右侧进度 / 底部详情
```

如果 UI 出现“任务卡莫名出现”“旧任务跑到新会话”“Agent 动态碎片很多”，通常不是单纯 CSS 问题，而是事件和状态绑定出了问题：

| 现象 | 优先排查 |
|---|---|
| 新会话显示旧任务 | conversation_id / thread_id / workspace 绑定 |
| 简单问候出现完整任务板 | intent route 或前端未过滤旧 run |
| Agent 动态碎片过多 | 工具事件和 Agent activity 没聚合 |
| 最终回复混入工具日志 | assistant_message 聚合逻辑过粗 |
| SSE 断开后状态丢失 | reconciliation / hydrateAfterDone |

## 18. EventStore 和失败恢复的关系

失败恢复不是从空气里生成的。它依赖事件证据：

```text
tool_call_failed / command_failed / error
  -> failure_recovery_loop_service 提取 evidence
  -> classify failure
  -> build recovery plan
  -> emit failure_recovery_planned
  -> 执行恢复动作
  -> emit validation / result events
```

所以事件质量会直接影响恢复质量。如果错误事件里没有 command、cwd、stderr、exit_code、related_files，恢复模块就只能猜。

这也是为什么工具治理和 EventStore 是一套系统：工具调用不只要执行，还要把结果以结构化事件写出来。

## 19. 面试表达模板

### 30 秒回答

nanoCursor 用 EventStore + SSE 把长时间运行的 Agent 任务变成可观察过程。每个 run 有 session.json 记录元信息和终态，events.jsonl 追加记录 Agent 活动、工具调用、文件变化、审批、失败和交付结果。前端通过 SSE 实时消费事件，断线后再通过 session 和 snapshot 恢复状态。

### 深入回答

我没有把运行过程只存在内存里，因为 AI 编程任务经常需要复盘和恢复。EventStore 是追加式账本，SSE 只是它的实时投影。事件写入后既能推给前端，也能用于报告生成、失败恢复、benchmark 和历史会话恢复。为了兼容事件格式演进，我还保留 schema_version 和 normalize_event，避免旧 run 事件无法被新前端读取。

### 当前边界

当前主链路仍以本地 JSONL 为主，跨 run 聚合分析不如数据库高效。Go eventstore sidecar 是实验模块，还不是默认主链路。这个取舍适合本地单用户工具，但如果要做多用户服务，需要换成数据库或专门的事件存储。

## 20. 容易被追问的问题

### Q1：为什么 EventStore 不直接用数据库？

本地单用户工具优先简单可靠。JSONL 可以追加、可读、可 grep，不需要额外运维。缺点是跨 run 聚合和大规模查询不强，但当前项目规模够用。

### Q2：SSE 断了会不会丢事件？

SSE 断了只影响实时推送，不影响 EventStore 写入。前端可以通过 reconciliation 查询 session 状态，并从 snapshot/artifacts/events 恢复。

### Q3：为什么事件要区分 title/content 和 payload？

title/content 给人看，payload 给机器处理。前端展示、失败恢复、报告生成都依赖 payload 的结构化字段。

### Q4：旧事件格式变化怎么办？

通过 `schema_version` 和 `normalize_event` 兼容。新代码读取旧事件时补齐缺失字段，而不是强制迁移历史 run。

## 21. 本章自测增强

1. 为什么说 EventStore 是账本，不是普通日志？
2. session、event、snapshot 三者分别解决什么问题？
3. SSE 只是实时投影，这句话是什么意思？
4. 工具失败事件缺少 stderr 会影响哪些后续模块？
5. 如果新会话显示旧任务，为什么要优先查 conversation/thread/workspace 绑定？
6. 如果未来做多用户版本，EventStore 哪些地方需要重构？
