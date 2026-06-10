# 06. 记忆机制：不是保存聊天记录那么简单

最后更新：2026-06-08

## 1. 本章目标

读完本章，你应该能回答：

- nanoCursor 的记忆系统如何区分 scope（global / workspace / conversation / run / file / rule）？
- 记忆的选择（selection）是如何工作的——为什么不是所有记忆都注入上下文？
- `MemoryRecord` 有哪些关键字段？confidence、freshness、importance 分别代表什么？
- FailureLearner 和 ExperienceLearner 如何自动从运行中学习？
- 文件指纹（file_fingerprint）有什么用？为什么记忆需要"新鲜度"？

## 2. 为什么记忆不是简单的"保存聊天记录"

普通聊天应用的记忆通常是：

```text
保存用户消息 → 下次加载历史 → 拼进 prompt
```

这个模式在 AI 编程工具里会出问题：

- **历史太长**：几百条消息塞进上下文，token 爆炸。
- **信息过时**：项目重构后，旧的"这个文件是入口"已经不对了。
- **错误记忆污染**：上一次失败的策略被记住，下一次还会犯。
- **范围混乱**：A 项目的偏好不应该出现在 B 项目里。

nanoCursor 的记忆系统解决的是：**什么信息应该被记住、什么时候应该被回忆、什么信息已经过时可以丢弃**。

## 3. 记忆的数据模型

核心是 `MemoryRecord`（Pydantic 模型），定义在 `memory_governance_service.py`：

```python
# src/api/services/memory_governance_service.py
MemoryScope = Literal["global", "workspace", "conversation", "run", "file", "rule"]
MemoryStatus = Literal["active", "disabled", "stale", "deleted"]
MemoryFreshness = Literal["fresh", "stale", "unknown"]
MemorySource = Literal["user", "rule_file", "system_summary", "run_evidence", "failure_recovery", "legacy"]

class MemoryRecord(BaseModel):
    id: str                                    # mem_<uuid>
    schema_version: int = 1
    scope: MemoryScope                         # 作用范围
    workspace_id: str                          # 工作区绑定
    conversation_id: str | None = None         # 会话绑定（conversation scope 必需）
    run_id: str | None = None                  # 运行绑定（run scope 必需）
    file_path: str | None = None               # 文件绑定（file/rule scope 必需）
    kind: str                                  # failure_pattern / workflow_note / decision / ...
    content: str                               # 记忆正文（最多 8000 字符）
    summary: str = ""                          # 摘要（最多 500 字符）
    tags: list[str]                            # 搜索标签
    source: MemorySource                       # 来源
    source_ref: str | None = None              # 来源引用
    confidence: float = 0.7                    # 置信度 0.0-1.0
    importance: int = 5                        # 重要性 0-10
    status: MemoryStatus = "active"
    freshness: MemoryFreshness = "unknown"
    created_at: float
    updated_at: float
    expires_at: float | None = None            # 过期时间
    last_used_at: float | None = None
    use_count: int = 0
    evidence_refs: list[str]                   # 证据引用
    file_fingerprint: str | None = None        # 文件内容指纹
```

### 关键字段的含义

**scope（作用范围）**：决定了这条记忆在什么场景下会被考虑。
- `rule`：来自 AGENTS.md / CLAUDE.md 的项目规则，最高优先级。
- `workspace`：跨会话的工作区记忆，如"这个项目使用 pytest"。
- `conversation`：绑定到某个会话，只在同一会话内可用。
- `run`：绑定到某次运行，用于跨 run 失败模式学习。
- `file`：绑定到某个文件，当该文件进入上下文时才考虑。
- `global`：全局偏好，最低优先级。

**confidence（置信度）**：从 `rule_file` 的 1.0 到 `legacy` 的 0.55。
- `rule_file`（1.0）：来自静态文件，非常可信。
- `user`（0.95）：用户手动创建，高度可信。
- `run_evidence`（0.9）：来自成功运行，高度可信。
- `failure_recovery`（0.85）：来自失败恢复，较可信。
- `system_summary`（0.7）：系统自动摘要，中等可信。
- `legacy`（0.55）：旧格式导入，需要重新验证。

**freshness（新鲜度）**：随文件变更自动更新。当关联的文件被修改后，记忆会被标记为 `stale`。

**file_fingerprint（文件指纹）**：SHA256 哈希，用于检测文件变更。当文件内容改变时，指纹不匹配，记忆被标记为 stale。

## 4. 记忆的创建与安全

`create_memory_record` 是唯一创建入口：

```python
# src/api/services/memory_governance_service.py
def create_memory_record(
    workspace_dir: str,
    *,
    scope: MemoryScope,
    kind: str,
    content: str,
    source: MemorySource = "user",
    automatic: bool = False,
    ...
) -> dict[str, Any]:
    clean = str(content or "").strip()
    if not clean:
        raise ValueError("memory content cannot be empty")

    # 安全检查：自动记忆不能包含密钥/隐私
    issues = memory_safety_issues(clean)
    if automatic and issues:
        raise ValueError(f"automatic memory rejected: {', '.join(issues)}")

    # 自动项目事实必须有证据
    if automatic and source == "system_summary" and kind == "project_fact" and not evidence_refs:
        raise ValueError("automatic project facts require evidence_refs")
```

三条安全规则：
1. **自动记忆不能包含密钥**：用正则匹配 API key、token、密码等模式。
2. **自动项目事实必须有证据引用**：不能凭空创建"这个项目用 React"这样的记忆。
3. **手动记忆（`automatic=False`）不受这些限制**：用户明确创建的记忆，即使包含密钥检测模式也会被允许（但会记录 issues）。

### 密钥和隐私检测

```python
# src/api/services/memory_governance_service.py
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|private[_-]?key)\b\s*[:=]\s*\S+"),
    re.compile(r"\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]
_PRIVACY_PATTERNS = [
    re.compile(r"(?i)\b(?:ssn|social security|身份证号|银行卡号)\b"),
]
```

## 5. 记忆选择（Memory Selection）：不是所有记忆都注入

这是记忆系统最核心的部分。`select_memories` 决定在当前任务中注入哪些记忆：

```python
# src/api/services/memory_selection_service.py
def select_memories(
    workspace_dir: str,
    *,
    prompt: str,
    conversation_id: str | None = None,
    run_id: str | None = None,
    selected_files: list[str] | None = None,
    active_task: dict[str, Any] | None = None,
    budget_tokens: int = 1200,
    persist_audit: bool = True,
) -> dict[str, Any]:
```

### 5.1 候选来源

记忆选择不是只从 `records.json` 读取。它还动态生成 transient 候选：

```python
candidates = [
    *_governed_candidates(workspace_dir),    # 来自 records.json 的持久记忆
    *_rule_candidates(workspace_dir),        # AGENTS.md / CLAUDE.md 作为 transient rule
    *_conversation_candidate(workspace_dir, conversation_id),  # 当前会话摘要
    *_run_candidate(workspace_dir, run_id),  # 当前运行摘要
]
```

`_rule_candidates` 会把 AGENTS.md 和 CLAUDE.md 当作最高优先级的"记忆"：

```python
def _rule_candidates(workspace_dir: str) -> list[dict[str, Any]]:
    paths = [workspace / "AGENTS.md", workspace / "CLAUDE.md"]
    cursor_rules = workspace / ".cursor" / "rules"
    if cursor_rules.is_dir():
        paths.extend(sorted(cursor_rules.glob("*.md")))
    # ... 每个文件生成一个 MemoryRecord，source="rule_file"，confidence=1.0
```

### 5.2 过滤

不是所有候选都能进入上下文。过滤条件包括：

```python
def _filter_candidates(candidates, *, conversation_id, run_id, files):
    eligible, omitted = [], []
    for item in candidates:
        # 不可选状态
        if status in {"disabled", "deleted", "stale"} or freshness == "stale":
            omitted.append(_omitted(item, f"not selectable"))
            continue
        # 过期
        if expires_at and expires_at < time.time():
            omitted.append(_omitted(item, "expired"))
            continue
        # Scope 匹配
        if scope == "conversation" and conversation_id != item["conversation_id"]:
            omitted.append(_omitted(item, "conversation scope mismatch"))
            continue
        if scope == "file" and files and item["file_path"] not in files:
            omitted.append(_omitted(item, "file not selected for current context"))
            continue
        eligible.append(item)
    return eligible, omitted
```

### 5.3 打分

每条候选记忆会被多维度打分：

```python
# src/api/services/memory_selection_service.py
SOURCE_PRIORITY = {
    "rule_file": 1.0, "user": 0.95, "run_evidence": 0.9,
    "failure_recovery": 0.85, "system_summary": 0.7, "legacy": 0.55,
}
SCOPE_PRIORITY = {
    "rule": 1.0, "workspace": 0.9, "conversation": 0.85,
    "file": 0.85, "run": 0.65, "global": 0.6,
}

def _score_memory(item, *, query_terms, selected_files, conversation_id, run_id):
    score = (
        0.30 * keyword     # 关键词匹配度
        + 0.16 * scope_score   # scope 优先级
        + 0.14 * file_match    # 文件是否在上下文选中列表中
        + 0.10 * recency       # 最近更新时间（指数衰减，60天半衰期）
        + 0.10 * confidence    # 置信度
        + 0.08 * importance    # 重要性
        + 0.12 * source        # 来源优先级
    )
```

权重设计说明：
- **关键词匹配（30%）**：最重要——和当前任务无关的记忆不应该出现。
- **Scope（16%）+ Source（12%）**：规则文件和用户手动记忆优先。
- **文件匹配（14%）**：当前选中的文件有相关记忆时提权。
- **时间衰减（10%）**：太久没用过的记忆降权，但不是直接丢弃。
- **置信度（10%）+ 重要性（8%）**：可信且重要的记忆优先。

### 5.4 最低分阈值

不同 scope 有不同的最低要求：

```python
def _minimum_selection_score(item, ...):
    if scope == "rule":
        return 0.18       # 规则文件几乎总是注入
    if scope == "conversation" and matches:
        return 0.40       # 当前会话记忆容易通过
    if kind == "failure_pattern":
        return 0.52       # 失败模式需要较高相关性
    if source == "legacy":
        return 0.58       # 旧格式记忆需要更高相关性
    return 0.55            # 默认阈值
```

### 5.5 Token 预算和审计

```python
# 按分数排序后，在预算内选择
for item in ranked:
    tokens = int(item.get("token_estimate") or 0)
    if used + tokens > budget_tokens:
        omitted.append(_omitted(item, "memory budget exhausted"))
        continue
    selected.append(_selection_item(item))
    used += tokens
```

每次选择都会持久化审计记录，保存在 `.nanocursor/memory/selections/` 下。这意味着可以回溯"为什么这次运行选了这些记忆"。

## 6. 文件指纹和新鲜度管理

当文件变更时，基于该文件的记忆需要被标记为 stale：

```python
# src/api/services/memory_governance_service.py
def refresh_memory_freshness(workspace_dir: str) -> dict[str, Any]:
    for item in records:
        if not item.get("file_path"):
            continue
        current = file_fingerprint(workspace_dir, item.get("file_path"))
        previous = item.get("file_fingerprint")
        if current and previous == current:
            item["freshness"] = "fresh"
            continue
        if previous and current != previous:
            item["freshness"] = "stale"
            if item.get("kind") == "failure_pattern":
                item["confidence"] = min(float(item.get("confidence") or 0.7), 0.45)
            else:
                item["status"] = "stale"
```

文件指纹的实现：

```python
def file_fingerprint(workspace_dir: str, file_path: str | None) -> str | None:
    workspace = Path(workspace_dir).resolve()
    candidate = (workspace / file_path).resolve()
    # 安全检查：确保路径在 workspace 内
    candidate.relative_to(workspace)
    # SHA256(相对路径 + 文件内容)
    digest = hashlib.sha256()
    digest.update(str(candidate.relative_to(workspace)).encode("utf-8"))
    digest.update(candidate.read_bytes())
    return digest.hexdigest()
```

这解决了"重构后旧记忆还生效"的问题。当文件内容改变后，基于旧内容的记忆会自动降级。

## 7. FailureLearner：从失败中学习

`FailureLearner` 在每次工具失败时被调用：

```python
# src/agent/learner.py
class FailureLearner:
    def on_tool_failure(self, tool_name: str, tool_input: dict, error_output: str, session_id: str = ""):
        error_sig = extract_error_signature(error_output)
        existing = _search_similar_failure(tool_name, error_sig)

        if existing:
            # 已存在的失败模式，提升重要性
            new_imp = min(existing.get("importance", 1) + 2, 10)
            update_memory_record(workspace, existing["id"], importance=new_imp)
        else:
            # 新的失败模式，创建记忆
            create_memory_record(
                workspace, scope="workspace", kind="failure_pattern",
                content=f"工具 {tool_name} 失败\n**输入**: {context}\n**错误**: {error_sig}",
                source="failure_recovery", confidence=0.55, importance=7,
                tags=[tool_name, "error", ...],
                automatic=True,
            )
```

核心逻辑：如果同一失败模式反复出现，重要性逐步提升（+2，最高 10），最终会被记忆选择器优先注入，让 Agent 提前知道"这个操作之前失败过"。

错误签名提取：

```python
def extract_error_signature(output: str) -> str:
    m = re.search(r'(\w+Error):\s*(.+?)(?:\n|$)', output)
    if m:
        return f"{m.group(1)}: {m.group(2)[:80]}"
    # 回退到常见模式匹配
    patterns = [
        (r"(command not found)", "Command not found"),
        (r"(Permission denied)", "Permission denied"),
        (r"(ModuleNotFoundError)", "Module not found"),
        (r"(SyntaxError)[:\s]*(.+?)(?:\n|$)", "Syntax"),
        ...
    ]
```

## 8. ExperienceLearner：记录成功方案

`ExperienceLearner` 把成功的工具调用链记录为可复用的"episode"：

```python
# src/agent/learner.py
class ExperienceLearner:
    def start_episode(self, task_description: str = ""):
        self._current_episode = []
        self._episode_active = True

    def record_call(self, tool_name: str, tool_input: dict, output: str):
        self._current_episode.append({
            "tool": tool_name,
            "input": {k: str(v)[:100] for k, v in tool_input.items()},
            "output_preview": output[:200],
            "success": not is_tool_error_output(output),
        })

    def complete_episode(self, outcome: str = "success", summary: str = ""):
        # 只有 ≥2 个工具调用 且 有文件修改 的成功 episode 才会被保存
        if len(self._current_episode) < 2:
            return None
        has_file_change = any(c.get("tool") in ("write_file", "edit_file") for c in self._current_episode)
        if not has_file_change and outcome == "success":
            return None

        signature = extract_episode_signature(self._current_episode)
        # 保存为 workflow_note，tags=["episode", "success", ...]
```

episode 签名是工具调用序列，如 `"read_file>edit_file>bash"`。后续相似任务可以通过 `retrieve_relevant` 找到历史 episode 作为参考。

## 9. 从 Run 中提取记忆

当一次 run 完成时，`extract_run_memory` 决定是否创建 run memory：

```python
# src/api/services/memory_governance_service.py
def extract_run_memory(workspace_dir: str, run_id: str) -> dict[str, Any]:
    session = get_event_store().get_session(run_id, workspace_dir) or {}
    plan = session.get("execution_plan") if isinstance(session.get("execution_plan"), dict) else {}

    # Lead 直接回答不创建记忆
    if plan.get("strategy") == "lead_direct_reply":
        return {"created": False, "reason": "lead_direct_reply does not create run memory"}

    # 没有执行摘要不创建
    summary = str(session.get("execution_summary") or "").strip()
    if not summary:
        return {"created": False, "reason": "run has no execution summary"}

    # 已存在不重复创建
    existing = list_memory_records(workspace_dir, scope="run", run_id=run_id, include_deleted=True)
    if existing:
        return {"created": False, "reason": "run memory already exists"}

    record = create_memory_record(
        workspace_dir,
        scope="run", run_id=run_id,
        kind="failure_pattern" if session.get("status") == "failed" else "workflow_note",
        content=summary,
        source="failure_recovery" if failed else "run_evidence",
        confidence=0.9 if completed else 0.65,
        automatic=True,
    )
```

## 10. 设计取舍

### 为什么记忆不直接用向量数据库？

当前阶段用 JSON 文件 + 关键词匹配就够了。原因是：
- 记忆量还不大（几百条级别），不需要向量搜索。
- 关键词匹配是可解释的——审计记录里能说清楚"为什么选了这条记忆"。
- 向量数据库增加了运维复杂度和 token 成本。

### 为什么要分 6 种 scope？

因为"记住这个项目用 pytest"（workspace scope）和"在刚才那个会话里我让你用 SQLAlchemy"（conversation scope）是不同的信息生命周期。Scope 决定了这条记忆什么时候过期、什么时候应该被考虑。

### 为什么自动记忆需要证据引用？

防止系统凭空创建"事实"。比如，如果 Agent 猜测"这个项目应该用 React"但实际上用的是 Vue，这条错误记忆会污染后续所有任务。`evidence_refs` 要求自动记忆必须关联到具体的 run 或文件。

## 11. 面试预备问题

### Q1：记忆和历史记录有什么区别？

历史记录是"发生了什么"，记忆是"应该记住什么"。历史是时序的、不可修改的原始记录；记忆是被提取、评分、可被新鲜度管理淘汰的结构化信息。不能把聊天记录直接当记忆用。

### Q2：为什么需要 file_fingerprint？

因为项目在持续变化。如果文件被重构了，基于旧文件内容的记忆（如"这个函数在 line 42"）就过时了。file_fingerprint 将记忆和文件内容哈希绑定，文件改变后记忆自动降级为 stale。

### Q3：记忆选择为什么要记录 omitted？

因为可观测性。如果模型没有按照预期行为做，需要能回溯"是不是缺少了某条关键记忆"。omitted 列表告诉你什么信息被排除了、为什么被排除。

### Q4：FailureLearner 的 importance 递增策略有什么用？

同一种失败反复出现时，这条记忆应该越来越"显眼"。importance 从 7 开始，每次 +2，最高 10。这确保 Agent 会优先看到"这个操作你之前失败过 3 次"的警告。

### Q5：为什么 AGENTS.md 被当作"记忆"处理？

因为 AGENTS.md / CLAUDE.md 本质上是"项目级别的持久规则记忆"。用同一套 MemoryRecord 模型处理可以统一选择、打分和审计逻辑，而不是在 prompt builder 里另开一条特殊路径。

## 12. 自测题

1. 记忆的 6 种 scope 分别是什么？哪种优先级最高？哪种最低？
2. `MemoryRecord` 的 `confidence`、`freshness`、`importance` 分别代表什么？它们的值从哪里来？
3. 记忆选择（`select_memories`）的候选来源有哪四类？transient 候选和持久化候选有什么区别？
4. 记忆打分公式的 7 个维度是什么？为什么关键词匹配占 30% 的权重？
5. `file_fingerprint` 是什么？它如何帮助系统检测"过时记忆"？
6. `FailureLearner` 发现重复失败时，会直接创建新记忆还是更新已有记忆的 importance？
7. `extract_run_memory` 在什么情况下会拒绝创建 run memory？至少说出两种。

## 13. 动手练习

1. **创建一条记忆**：通过 API 或前端创建一条 workspace 级别的记忆（如"这个项目使用 pytest 进行测试"），然后在 `.nanocursor/memory/records.json` 中找到它，列出所有字段的值。
2. **触发新鲜度刷新**：修改一个文件（如 README.md），然后调用 `refresh_memory_freshness`（或通过 API），观察与该文件关联的记忆是否被标记为 stale。
3. **追踪一次记忆选择**：在运行任务时，找到 `.nanocursor/memory/selections/` 下的审计文件，阅读 `selected` 和 `omitted` 列表。理解为什么某些记忆被选中、某些被排除。
4. **阅读 FailureLearner 的错误签名提取**：打开 `src/agent/learner.py`，找到 `extract_error_signature` 函数。自己构造 5 条不同的 Python/Shell 错误信息，测试签名提取是否准确。
