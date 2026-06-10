# 练习 01：读懂一次请求生命周期

目标：通过两个任务验证你是否理解 nanoCursor 的主请求链路。

## 练习 A：简单问候

发送：

```text
哈喽
```

你需要记录：

- 前端调用了哪个 API？
- conversation_id 是否保持不变？
- 后端返回的 thread_id 是什么？
- `intent_decision.route` 是什么？
- `execution_route` 是否为 `lead_direct_reply`？
- 是否创建了子 Agent？
- 右侧进度是否为空或极简？
- EventStore 是否保存了这次 run？

期望结论：

```text
简单问候应该由 Lead 直接回答，不应该创建完整开发任务。
```

## 练习 B：真实代码任务

发送：

```text
帮我用 Python 写常见排序算法并比较性能
```

你需要记录：

- API 入口
- thread_id
- runtime team
- execution plan 的 stages
- tool_policy
- 是否写入文件
- 是否运行测试或 benchmark
- 事件流中出现了哪些 agent activity
- Diff 是否统计新增文件
- 最终交付物是什么

期望结论：

```text
代码任务应该进入 AgentHub delivery，允许受控写文件和验证，并产生可观察的运行过程。
```

## 对照代码

阅读顺序：

1. `src/api/routes/run_entry.py`
2. `src/api/services/conversation_run_service.py`
3. `src/api/services/intent_router.py`
4. `src/api/services/run_start_service.py`
5. `src/api/run_state.py`
6. `src/api/services/event_store.py`

## 自我检查

如果你能不看答案解释下面问题，就说明练习完成：

1. 为什么问候不应该创建 Coder？
2. 为什么代码任务需要 execution plan？
3. 为什么 run 要绑定 conversation？
4. EventStore 和内存 queue 分别解决什么问题？
5. 如果新会话显示旧任务，最可能是哪一层出问题？

