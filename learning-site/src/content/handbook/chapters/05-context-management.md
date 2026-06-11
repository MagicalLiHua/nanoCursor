# 05. 上下文管理：让模型知道该看什么

最后更新：2026-06-11

## 1. 本章目标

上下文管理是 nanoCursor 里最值得讲的模块之一。真正的 AI 编程工具不是把整个项目、全部聊天记录、所有日志一次性塞给模型，而是要回答：

```text
这一次任务，模型最需要看到哪些信息？
哪些信息必须保留？
哪些信息可以裁剪？
裁剪掉的信息能不能留下审计记录？
```

本章要掌握：

- `ContextPack` 是什么。
- `ContextLedger` 为什么存在。
- 项目索引、文件大纲、记忆、Skills、MCP 如何进入上下文。
- token budget 如何分配和裁剪。
- 长会话为什么需要自动压缩和失败降级。
- 为什么上下文管理比“多 Agent 数量”更重要。

## 2. 上下文失控会发生什么

如果上下文管理不好，系统会出现这些问题：

- 模型读不到关键文件，却在无关文件里乱改。
- 历史对话太长，导致当前任务被旧目标干扰。
- 每轮都重复扫描项目，速度慢且浪费 token。
- 测试失败信息被裁掉，模型反复修错方向。
- Skills / 用户偏好注入太多，反而稀释任务目标。

所以成熟系统的关键不是“上下文越多越好”，而是“上下文命中率越高越好”。

## 3. ContextPack 数据结构

核心文件：

- `src/agent/context_pack.py`
- `src/api/services/context_service.py`
- `src/api/services/context_budget_service.py`
- `src/api/services/file_outline_service.py`
- `src/api/services/memory_selection_service.py`
- `src/agent/prompt_builder.py`

`ContextPack` 把上下文拆成结构化字段：

```python
# src/agent/context_pack.py
@dataclass
class ContextPack:
    task_summary: str = ""
    conversation_summary: str = ""
    execution_summary: str = ""
    workspace_summary: dict = field(default_factory=dict)
    relevant_files: list[str] = field(default_factory=list)
    selected_files: list[dict] = field(default_factory=list)
    recent_changes: list[str] = field(default_factory=list)
    file_outlines: list[dict] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    recent_failures: list[dict] = field(default_factory=list)
    selected_memories: list[dict] = field(default_factory=list)
    selected_skills: list[str] = field(default_factory=list)
    tool_policy: dict = field(default_factory=dict)
    selection_reasons: list[str] = field(default_factory=list)
    omitted: list[dict] = field(default_factory=list)
```

这比拼字符串更成熟。原因是：

- 前端可以展示 `selected_files`、`omitted`、`budget_report`。
- 测试可以断言某个文件是否被选中。
- 运行时可以只裁剪某类上下文，而不破坏任务目标。
- 面试时能讲清楚“上下文包是一个结构化合约”。

## 4. 从结构化对象到 LLM Prompt

`ContextPack.to_text()` 把结构化字段转换成模型能读懂的文本块。它会按优先级写入任务、摘要、项目、相关文件、文件大纲、Skills、工具策略和本轮观察。

重点片段：

```python
# src/agent/context_pack.py
if self.selected_files:
    lines.append("相关文件选择依据:")
    for item in self.selected_files[:8]:
        reasons = item.get("reasons") if isinstance(item.get("reasons"), list) else []
        reason_text = "；".join(str(reason) for reason in reasons[:3]) or "未记录原因"
        lines.append(
            f"  - {item.get('path', '')} "
            f"(score={item.get('relevance_score', 0)}, mode={item.get('mode', 'outline')}): "
            f"{reason_text}"
        )
```

这里不是简单列文件名，而是把“为什么选这个文件”一起注入给模型。这能减少模型盲目读取和修改文件。

工具策略也会进入上下文：

```python
if self.tool_policy:
    mode = self.tool_policy.get("mode") or "default"
    risk = self.tool_policy.get("risk_level") or "unknown"
    lines.append(f"工具策略: mode={mode}, risk={risk}")
    for label, key in (
        ("允许", "allowed_tools"),
        ("拒绝", "denied_tools"),
        ("需审批", "approval_required"),
        ("推荐", "recommended_tools"),
    ):
        tools = self.tool_policy.get(key)
        if isinstance(tools, list) and tools:
            lines.append(f"  - {label}: {', '.join(str(tool) for tool in tools[:16])}")
```

这点很重要：模型不仅知道“要做什么”，还知道“哪些工具不能用、哪些动作要审批”。

## 5. Token Budget：不同任务不同分配

上下文预算在 `context_budget_service.py` 中定义。

```python
# src/api/services/context_budget_service.py
DEFAULT_BUDGET_RATIOS = {
    "task": 0.08,
    "plan": 0.12,
    "workspace": 0.08,
    "file_outlines": 0.20,
    "snippets": 0.25,
    "recent_changes": 0.10,
    "failures": 0.08,
    "preferences_skills": 0.05,
    "reserved": 0.04,
}
```

不同策略会覆盖默认比例：

```python
STRATEGY_BUDGET_OVERRIDES = {
    "bug_fix": {
        "recent_changes": 0.16,
        "failures": 0.14,
        "snippets": 0.23,
        "reserved": 0.02,
    },
    "refactor": {
        "file_outlines": 0.27,
        "snippets": 0.22,
        "failures": 0.06,
        "reserved": 0.02,
    },
}
```

这说明上下文不是一套固定模板：

- bug fix 更需要失败日志、最近变更和相关代码片段。
- refactor 更需要文件大纲和符号结构。
- docs only 更需要文档、README、接口说明。
- analysis only 不应该把大量代码片段塞进去。

## 6. P0 上下文不可裁剪

系统里有一组保护字段：

```python
# src/api/services/context_budget_service.py
PROTECTED_CONTEXT_FIELDS = {
    "task_summary": "P0 user_request",
    "active_task": "P0 active_task",
    "tool_policy": "P0 tool_policy",
    "current_plan": "P0 active_plan",
}
```

这代表无论预算多紧，用户任务、当前任务、工具策略和当前计划都不应该被裁掉。否则模型可能忘记目标，或者违反权限边界。

## 7. 裁剪不是静默丢弃

`trim_context_pack` 会把超预算文件记录到 `omitted`：

```python
pack.omitted = _build_omitted_context(
    trimmed_files,
    trimmed_outlines,
    max_selected_items,
    max_outline_items,
)
pack.budget_report = {
    "strategy": budget.get("strategy"),
    "max_tokens": budget.get("max_tokens", 12000),
    "included_file_count": len(included_files),
    "trimmed_file_count": len(trimmed_files),
    "included_outline_count": len(included_outlines),
    "trimmed_outline_count": len(trimmed_outlines),
    "omitted_context_count": len(pack.omitted),
}
```

这就是可观测上下文管理：不仅知道模型看了什么，也知道模型没看到什么、为什么没看到。

## 8. 上下文来源总览

当前系统主要上下文来源：

| 来源 | 作用 | 风险 |
|---|---|---|
| 用户当前 prompt | 当前任务目标 | 太短时需要澄清 |
| conversation_summary | 多轮会话压缩 | 摘要错误会误导 |
| execution_summary | 上一轮运行结果 | 旧失败可能污染新任务 |
| Project Index | 项目结构、入口、测试、配置 | 索引过期 |
| File Outline | 函数、类、符号结构 | 只能代表结构，不代表实现细节 |
| recent_changes | 最近改动文件 | 可能放大临时文件噪声 |
| selected_memories | 长期偏好和事实 | 需要治理和淘汰 |
| selected_skills | 专项任务规范 | 过多会稀释 prompt |
| MCP capabilities | 外部工具能力 | 需要权限和可用性检查 |
| tool_policy | 工具边界 | 必须 P0 保留 |

## 9. Context Ledger：上下文账本

`ContextPack` 解决“给模型看什么”，`ContextLedger` 解决“这些内容占了多少窗口、是否快溢出”。

核心文件：

- `src/api/services/context_ledger_service.py`
- `src/api/services/model_context_registry_service.py`
- `src/api/services/token_estimator_service.py`
- `src/api/services/compaction_policy_service.py`

一次 run 构建上下文时，系统会把 ContextPack 拆成多个 section：

| Section | 例子 | 是否可压缩 |
|---|---|---|
| 当前请求 | `current_user_message` | 否 |
| 当前计划 | `current_plan` | 否 |
| 工具策略 | `tool_policy` | 否 |
| 历史 Agent 动态 | `old_agent_activity` | 是 |
| 工具输出 | `tool_results` | 是 |
| 相关文件和 outline | `selected_files`、`file_outlines` | 视优先级 |

每个 section 会记录：

- `tokens`：估算 token 占用。
- `priority`：优先级，越高越不能动。
- `compactible`：是否允许被压缩。
- `category`：历史、工具、文件、记忆、Skills 等来源。

这让系统可以做几件事：

1. 前端展示当前上下文窗口使用率。
2. 自动判断 `ok / watch / soft_compact / hard_compact / emergency`。
3. 压缩时只处理低优先级、可压缩 section。
4. 压缩后仍然能解释“哪些内容被替换成摘要”。

## 10. 自动压缩：不是删历史，而是替换成摘要

自动压缩入口在：

- `src/api/services/run_state_service.py`
- `src/api/services/compaction_service.py`
- `src/api/services/summary_compaction_service.py`
- `src/api/services/context_compaction_settings_service.py`

运行时大致流程：

```text
构建 ContextPack
  -> 生成 ContextLedger
  -> 判断上下文压力
  -> 如果达到 hard/emergency
  -> 选择低优先级 section
  -> 写入 compacted_summary
  -> 保留当前请求、当前计划、工具策略
  -> 继续进入 LLM 调用
```

当前有两类压缩：

| 策略 | 特点 | 适用场景 |
|---|---|---|
| deterministic | 不调用 LLM，按 section priority 和比例压缩 | CI、离线、兜底 |
| summary | 把历史、工具输出、debug、run 信息合并成摘要 | 长会话、复杂任务 |

summary 又有两种模式：

| 模式 | 说明 |
|---|---|
| deterministic summary | 本地规则生成摘要，不依赖 API key |
| LLM summary | 调用当前 LLM provider 生成更自然的摘要 |

重要的是：LLM summary 失败不会中断 run。系统会记录 warning，并降级到本地 deterministic summary。

## 11. Benchmark：如何证明压缩有用

上下文压缩不能只靠“感觉有用”。项目里有一个确定性 benchmark：

```bash
curl -X POST http://127.0.0.1:8100/api/benchmarks/context-window/run
```

当前 benchmark 构造了一个超长上下文：

- 当前用户请求：必须保留。
- 当前计划：必须保留。
- 工具策略：必须保留。
- 历史 Agent 动态：可压缩。
- 大量工具输出：可压缩。
- 相关文件信息：可压缩。

一组本地结果：

| Variant | Tokens | Reduction | Status | Anchor preserved |
|---|---:|---:|---|---:|
| 无压缩 | 10500 | 0% | emergency | 100% |
| deterministic | 6480 | 38% | watch | 100% |
| summary | 3618 | 66% | ok | 100% |
| LLM summary | 3613 | 66% | ok | 100% |
| LLM fallback | 3618 | 66% | ok | 100% |

这里的重点不是具体数字永远不变，而是 benchmark 证明了三件事：

1. 不压缩会进入 emergency，真实系统可能请求失败或注意力衰减。
2. summary 压缩比简单 token 缩减更有效。
3. LLM 摘要失败时，系统仍能降级并保留关键锚点。

## 12. 如何继续提升

如果后续继续打磨，上下文管理可以向这些方向走：

1. 做 context hit rate：最终修改文件是否在 selected_files 中。
2. 做 context miss audit：模型后来读取了但最初没注入的文件，说明索引或选择器有漏召回。
3. 让 conversation_summary 分层：事实、偏好、未完成目标、已废弃目标分开。
4. 文件 outline 增加调用关系和导入关系，而不仅是符号列表。
5. 为不同 Agent 生成不同上下文：Coder 需要代码片段，Reviewer 需要 diff 和风险，Tester 需要测试入口。

## 13. 面试预备问题

### Q1：为什么上下文管理比多 Agent 更重要？

Agent 再多，如果每个 Agent 看到的上下文都错，就只是并行制造错误。上下文管理决定模型是否理解项目、任务和边界，是智能调度的基础。

### Q2：ContextPack 和直接拼 prompt 有什么区别？

ContextPack 是结构化对象，可以被预算裁剪、前端展示、测试断言和运行审计。直接拼 prompt 很难知道哪些信息被放进去，也很难解释为什么模型没看到某个文件。

### Q3：怎么避免旧历史误导新任务？

用 conversation_summary 和 execution_summary 压缩历史，同时保留当前 prompt、当前任务和工具策略为 P0。对摘要要记录来源和更新时间，必要时让用户确认或重新摘要。

### Q4：如果 token 不够，先裁剪什么？

先裁剪低相关文件、低分 outline、旧失败、低优先级 Skills 和远期记忆。不能裁剪用户当前请求、当前任务、工具策略和当前计划。

### Q5：ContextPack 和 ContextLedger 有什么区别？

ContextPack 是模型输入的结构化内容，回答“本轮应该看什么”；ContextLedger 是上下文预算账本，回答“每部分占了多少 token、是否需要压缩、哪些 section 可以被替换成摘要”。前者面向 prompt 组织，后者面向预算治理和可观测性。

### Q6：为什么 LLM 摘要失败不能直接让任务失败？

摘要压缩是为了提高稳定性，不能反过来成为新的单点故障。所以 LLM summary 是可选增强，失败时必须降级到 deterministic summary。这样即使没有 API key、provider 超时或返回空内容，当前 run 仍然可以继续。

## 14. 自测题

1. `ContextPack` 和直接拼 prompt 字符串有什么区别？为什么结构化对象更成熟？
2. `ContextPack` 中有哪些字段？`selected_files`、`omitted`、`budget_report` 分别有什么用？
3. Token Budget 的默认分配比例是什么？为什么 bug_fix 策略要多给 `failures` 和 `recent_changes`？
4. P0 保护字段有哪些？为什么这些字段不能被裁剪？
5. `trim_context_pack` 裁剪掉的上下文去了哪里？为什么不能静默丢弃？
6. 上下文有哪些来源？项目索引、文件大纲和记忆分别提供什么信息？
7. 如果上下文命中率很低（模型总是读取没被注入的文件），你会从哪些方向排查？
8. Context Ledger 中 `priority` 和 `compactible` 分别解决什么问题？
9. 为什么 summary compaction 要记录 `source_section_ids`？
10. LLM summary fallback 的价值是什么？

## 15. 动手练习

1. **读 ContextPack 的完整定义**：打开 `src/agent/context_pack.py`，列出所有 `@dataclass` 字段，然后找到 `to_text()` 方法，理解每个字段如何被渲染成 prompt 文本。
2. **分析 token budget 策略差异**：打开 `src/api/services/context_budget_service.py`，对比 `DEFAULT_BUDGET_RATIOS` 和 `STRATEGY_BUDGET_OVERRIDES` 中的 `bug_fix` 策略，用表格列出每个字段的分配差异，并解释为什么。
3. **追踪一次上下文选择**：打开 `src/api/services/context_service.py`（或上下文选择相关函数），从 `build_context_pack` 开始，追踪项目索引、文件大纲、记忆和 Skills 分别从哪些来源进入 ContextPack。
4. **模拟上下文裁剪**：找一个比较复杂的项目目录，手动列一份"如果 token 预算只有 8000，应该选哪些文件"的清单，然后和 `context_budget_service.py` 的实际裁剪逻辑对比。
5. **跑一次上下文压缩 benchmark**：启动后端后调用 `/api/benchmarks/context-window/run`，观察 `variants` 中无压缩、deterministic、summary、LLM summary 和 fallback 的差异。
