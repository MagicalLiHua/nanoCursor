# 07. 工具治理：Agent 能干活，也必须有边界

最后更新：2026-06-09

## 1. 本章目标

AI 编程工具真正危险的地方不是“模型回答错了”，而是“模型可以写文件、删文件、跑命令、调用外部服务”。所以 nanoCursor 必须回答一组连在一起的问题：Agent 能调用哪些工具、每个工具属于什么权限级别、哪些动作要用户审批、工具失败后怎么恢复、工具调用证据怎么留下。

本章重点看五件事：权限分级、shell 命令分类、Action Policy 管线、approval 机制，以及为什么工具治理比继续堆 Agent 更重要。

## 2. 权限分级

nanoCursor 当前把工具粗分为：

| 权限级别 | 含义 | 示例 |
|---|---|---|
| `read_only` | 只读项目上下文 | 读文件、列目录、搜索、项目索引 |
| `safe_write` | 工作区内安全写入 | 写文件、局部编辑、任务状态更新 |
| `risky_write` | 高风险文件变更 | 删除、移动、回滚、大范围替换 |
| `shell_safe` | 相对安全命令 | pytest、lint、ls、cat、git diff |
| `shell_risky` | 高风险命令 | 安装依赖、删除文件、网络请求、Git 写操作 |
| `mcp_read` | MCP 只读工具 | list / get / search / query |
| `mcp_write` | MCP 外部副作用 | create / update / delete / submit |
| `external_risky` | 未知外部工具 | 无法证明安全的工具 |

高风险级别进入 approval。

## 3. 工具权限运行时

核心文件：

- `src/runtime/tool_policy_runtime.py`
- `src/runtime/action_policy.py`
- `src/api/services/action_execution_service.py`
- `src/api/services/approval_service.py`
- `src/tools/path_safety.py`
- `src/api/services/checkpoint_service.py`

权限常量：

```python
# src/runtime/tool_policy_runtime.py
READ_ONLY_TOOLS = frozenset({
    "read_file",
    "read_file_range",
    "read_function",
    "read_class",
    "list_directory",
    "search_codebase",
    "project_context",
    "git_status",
    "git_diff",
    "task_list",
    "recall_memories",
})

SAFE_WRITE_TOOLS = frozenset({
    "write_file",
    "edit_file",
    "task_create",
    "task_update",
    "add_memory",
    "spawn_agent",
})

RISKY_WRITE_TOOLS = frozenset({
    "delete_file",
    "move_file",
    "rollback_file",
    "restore_snapshot",
    "apply_patch",
})
```

这层设计的意义是：不要让每个工具自己决定安全性，而是统一进一个策略层。

## 4. shell 命令分类

shell 是最容易出事故的工具。`classify_shell_command` 会先用 `shlex.split` 解析命令，然后判断是否命中安全前缀或高风险模式。

```python
# src/runtime/tool_policy_runtime.py
def classify_shell_command(command: str) -> str:
    text = (command or "").strip()
    if not text:
        return "shell_risky"
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return "shell_risky"

    if any(pattern in lowered for pattern in _SHELL_RISKY_PATTERNS):
        return "shell_risky"

    if any(token in {";", "&&", "||", "|", "&"} for token in tokens):
        return "shell_risky"

    if any(_tokens_start_with(tokens, prefix) for prefix in _SHELL_SAFE_PREFIXES):
        return "shell_safe"
    return "shell_risky"
```

这里有一个工程取舍：宁可保守，也不要把复杂 shell 组合命令误判为安全。比如 `pytest && rm -rf tmp` 必须是高风险。

## 5. 文件写入风险

写文件不一定都是安全写。系统会检查敏感路径和大文件写入：

```python
if name in SAFE_WRITE_TOOLS:
    if name in {"write_file", "edit_file"} and _is_sensitive_write_target(payload):
        return "risky_write"
    if name == "write_file" and _is_large_write_payload(payload):
        return "risky_write"
```

这解决了几个真实风险：

- 写 `.env`、token、secret 类文件。
- 修改 lockfile、package 配置、CI 配置。
- 一次写入过大内容，可能是模型把整文件重写了。
- 大范围 edit，可能破坏用户原本代码。

## 6. Action Policy：统一入口

`action_policy.py` 把文件、命令、Git、MCP、恢复动作统一成一种 `ActionRequest`。

```python
# src/runtime/action_policy.py
class ActionKind(str, Enum):
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    DELETE_FILE = "delete_file"
    RUN_COMMAND = "run_command"
    GIT_OPERATION = "git_operation"
    MCP_CALL = "mcp_call"
    RECOVERY_ACTION = "recovery_action"
```

然后统一做权限判断：

```python
def check_action(...):
    permission_level = classify_action_permission(kind, target, payload=payload)
    risk = _classify_action_risk(kind, target, payload=payload)

    if risk == "high":
        return ActionDecision(
            allowed=True,
            requires_approval=True,
            reason=f"{kind.value} 操作权限为 {permission_level}，风险等级为 {risk}，需要用户审批。",
            risk=risk,
            permission_level=permission_level,
        )
```

这就是“先检查，再执行”的管线。

## 7. ToolPolicyRuntime：允许、拒绝、预算、审批

`ToolPolicyRuntime.check` 会按顺序判断：

1. 工具是否被显式禁止。
2. 如果有 allowed list，工具是否在允许列表中。
3. 当前 run 的预算是否超限。
4. shell 命令是否缺少 command。
5. 权限级别是否要求审批。
6. 若通过，则记录 auto_allowed。

核心片段：

```python
# src/runtime/tool_policy_runtime.py
if tool_name in self.denied_tools:
    return ToolPolicyDecision(
        tool=tool_name,
        allowed=False,
        reason=f"{tool_name} 在当前策略中被禁止。",
        permission_level=permission_level,
    )

if self.allowed_tools and tool_name not in self.allowed_tools:
    return ToolPolicyDecision(
        tool=tool_name,
        allowed=False,
        reason=f"{tool_name} 不在允许列表中。",
        permission_level=permission_level,
    )
```

这是把“模型想做什么”和“系统允许做什么”分开。模型可以提出动作，但系统不必执行。

## 8. 和 Agent Loop 的关系

Agent Loop 里每一步动作都会经过 `check_loop_action`。如果动作是工具调用，最后也会进入工具策略。

所以控制链路是：

```text
LeadAction
  -> loop action schema validation
  -> loop state gate
  -> tool permission classification
  -> budget check
  -> approval if needed
  -> execute
  -> evidence / event
```

这就是为什么 nanoCursor 不只是一个聊天 UI。它有一个清晰的执行边界。

## 9. 失败恢复不是绕过权限

失败恢复模块会把工具失败归类成读失败、写失败、命令失败、缺依赖、测试失败、策略阻断等场景，然后生成恢复建议。但这些恢复动作仍然要经过同一套工具治理，不会因为“系统在自救”就自动拿到更高权限。

| 失败类型 | 典型恢复 | 权限边界 |
|---|---|---|
| 读文件失败 | 重新列目录、检查路径、读取相邻文件 | `read_only` |
| 写文件失败 | 检查父目录、重新写入小范围内容 | `safe_write` 或升级审批 |
| 命令失败 | 读取错误输出、尝试安全检查命令 | `shell_safe` |
| 缺依赖 | 建议安装依赖或修改环境 | `shell_risky`，需要 approval |
| 策略阻断 | 向用户解释原因，等待批准或改用低风险方案 | 不自动绕过 |

这一点很关键。成熟系统的失败恢复不是“模型再试一次”，而是在权限边界内重新规划。否则恢复机制会变成安全系统的后门。

## 10. 当前不足和后续方向

还可以继续增强：

- 所有服务层异常都应结构化上报，避免静默吞掉。
- shell 分类可以引入更严格的 shell parser，而不只是 `shlex`。
- approval token 可以进一步绑定 workspace、command hash、过期时间和用户身份。
- 文件写入可以做更细粒度 diff risk：是否修改入口、依赖、CI、迁移脚本。
- MCP 工具可以按 server 信任级别动态调整权限。

## 11. 面试预备问题

### Q1：为什么工具治理比多 Agent 更重要？

多 Agent 只是决策形态，工具治理决定系统能否安全地执行真实动作。没有工具边界的 Agent 只能当聊天机器人；有工具但没治理的 Agent 很危险。

### Q2：怎么判断 shell 命令是否安全？

nanoCursor 用白名单安全前缀和黑名单风险模式结合。测试、lint、只读命令通常是 `shell_safe`；安装依赖、删除、网络、Git 写操作、复合 shell 命令通常是 `shell_risky`。

### Q3：高风险动作为什么不是直接禁止？

有些高风险动作是用户明确需要的，比如安装依赖、恢复快照、调用外部 MCP 写操作。成熟系统应该进入 approval，而不是一刀切禁止。

### Q4：文件写入如何可恢复？

文件写入进入统一工具管线，配合路径防护、备份、Diff、evidence、checkpoint 和恢复入口。这样即使模型改错，也能回看和回滚。

## 12. 设计取舍

### 为什么工具权限不是每个工具自己声明？

如果每个工具自己声明"我是安全的"，恶意或配置错误的工具可能谎报。统一在 `ToolPolicyRuntime` 中集中管理权限，可以保证一致性——新增工具不会因为忘记声明权限而绕过安全检查。

### 为什么 shell 分类宁可保守？

`classify_shell_command` 默认把所有无法识别的命令都归为 `shell_risky`。这意味着一个安全但未知的命令（如项目自定义脚本）会被拦截进入审批。这个代价是可接受的——用户审批一次后可以设为信任。但反过来，如果把危险命令误判为安全，后果严重得多。

### 为什么高风险操作不直接禁止？

有些高风险操作是用户明确需要的——安装依赖、恢复快照、调用外部 MCP。如果直接禁止，用户就完全用不了这些功能。进入 approval 是更好的折中：系统提醒风险，让用户做最终决定。

### 为什么 approval token 要绑定到具体操作？

防止重放攻击：如果一个 approval 可以用于任意操作，攻击者（或出错的 Agent）可能在用户不知情的情况下反复执行高风险操作。Token 绑定到具体 command hash、workspace 和过期时间，确保一次审批只对一次特定操作有效。

## 13. 自测题

1. nanoCursor 的工具权限分哪几级？`read_only`、`safe_write`、`risky_write` 分别包含哪些工具？
2. `classify_shell_command` 如何判断一个命令是否安全？为什么 `pytest && rm -rf tmp` 会被拦截？
3. `ActionPolicy` 的 `check_action` 函数做了什么？它和 `ToolPolicyRuntime.check` 是什么关系？
4. 文件写入在什么情况下会从 `safe_write` 升级为 `risky_write`？举出至少 3 种情况。
5. Agent Loop 里的动作从提出到执行经过了哪些检查步骤？
6. 如果用户拒绝了一个 approval request，系统会记录什么？工具调用会被标记为"已完成"吗？
7. MCP 工具的权限是怎么分类的？为什么只读 MCP 失败可以 fallback，写 MCP 失败不能自动替代？

## 14. 动手练习

1. **读 ToolPolicyRuntime 的完整检查链**：打开 `src/runtime/tool_policy_runtime.py`，找到 `check` 方法，列出它按顺序执行的 6 个检查步骤。
2. **手写一个 shell 分类测试**：自己写 10 个 shell 命令（包括安全的和危险的），用 `classify_shell_command` 测试它们，记录哪些被正确分类、哪些被误判，思考如何在不过度复杂的情况下改进。
3. **追踪一次文件写入的完整路径**：从 Agent 决定 `write_file` 开始，追踪到 `ToolPolicyRuntime.check` → `file_ops.write_file` → 备份 → evidence 生成。用代码路径画出每一步在哪个文件。
4. **检查 approval token 的实现**：打开 `src/api/services/approval_service.py` 和相关测试 `tests/test_approval_token.py`，理解 approval token 如何生成、校验和过期。

## 15. 深度学习：工具治理是 Agent 从聊天变成工具的分界线

Agent 系统的成熟度，不是看它能不能说“我会帮你修改”，而是看它真的要修改时有没有边界。

一个本地编程 Agent 至少会碰到这些高风险能力：

```text
读文件
写文件
删除/移动/回滚文件
执行 shell
安装依赖
操作 Git
调用 MCP 外部工具
写记忆
恢复失败
```

如果这些能力都直接暴露给模型，系统就只是一个“会执行命令的聊天机器人”。nanoCursor 的工具治理要解决的是：**模型可以提出动作，但系统决定动作能不能执行、要不要审批、如何留下证据**。

## 16. 工具调用的五段式

一次工具调用最好按五段理解：

```text
1. propose：模型或 Lead 提出动作
2. classify：系统判断权限级别和风险
3. decide：允许、拒绝、等待审批、要求修复
4. execute：真正执行文件、shell、MCP 或恢复动作
5. record：记录事件、evidence、diff、approval、失败分类
```

不要把工具治理理解成一个简单 if 判断。它是一条执行管线。

| 阶段 | 典型代码 | 失败时应该怎样 |
|---|---|---|
| propose | Agent Loop / runtime executor | 动作 schema 不合法就要求修复 |
| classify | `classify_tool_permission` / `classify_shell_command` | 无法证明安全则按高风险 |
| decide | `ToolPolicyRuntime.check` / `check_action` | 拒绝或进入 approval |
| execute | `file_ops` / `command_runner` / MCP client | 捕获输出和错误 |
| record | EventStore / audit log / recovery evidence | 后续恢复和前端展示依赖这里 |

## 17. 为什么 shell 特别危险

shell 的危险不在单个命令，而在组合能力：

- `pytest` 可能是安全的，但 `pytest && rm -rf tmp` 不安全。
- `python script.py` 可能安全，但脚本内部可能做网络或删除。
- `npm install` 看起来常见，但会改 lockfile、执行 postinstall、访问网络。
- `curl | sh` 是典型高风险模式。

所以系统宁可保守：

```text
能证明安全 -> shell_safe
不能证明安全 -> shell_risky
```

这不是为了“拦用户”，而是为了让用户知道系统准备做什么。

## 18. 文件写入为什么也分风险

很多人会以为“只要在 workspace 内写文件就安全”。其实不是。

| 场景 | 风险 |
|---|---|
| 写 `.env` | 泄露或覆盖密钥 |
| 改 `pyproject.toml` / `package.json` | 影响安装、启动、测试 |
| 改 CI 配置 | 影响 GitHub Actions |
| 大文件整写 | 模型可能覆盖用户代码 |
| 删除/移动文件 | 破坏项目结构 |
| 回滚快照 | 覆盖用户最新改动 |

所以 `safe_write` 和 `risky_write` 的边界不是“能不能写”，而是“这个写入会不会改变项目的安全、依赖、入口或大量内容”。

## 19. 失败恢复和工具治理的关系

失败恢复经常被误解成“失败了就让 Agent 再试”。成熟做法不是这样。

失败恢复应该先分类：

| 失败 | 可能原因 | 合理恢复 |
|---|---|---|
| `FileNotFoundError` | 路径错、工作区错、文件未创建 | 列目录、查索引、重新定位 |
| `Permission denied` | 权限不足或策略阻断 | 请求审批或改用只读方案 |
| `ModuleNotFoundError` | 缺依赖 | 先检查 pyproject/requirements，再请求安装审批 |
| `SyntaxError` | 生成代码错误 | 读错误位置，小范围修复 |
| 测试断言失败 | 实现或测试预期错 | 分析失败用例，决定修实现还是修测试 |
| approval denied | 用户拒绝高风险动作 | 停止或提供低风险替代方案 |

恢复动作仍然必须经过同一套工具策略。否则“自动恢复”会绕过安全系统，变成更危险的后门。

## 20. 工具治理的面试表达模板

### 30 秒回答

nanoCursor 的工具治理核心是把模型决策和真实执行分开。模型可以提出读文件、写文件、跑命令或调用 MCP 的动作，但动作先进入统一策略层，按 read_only、safe_write、risky_write、shell_safe、shell_risky、mcp_write 等级分类，高风险动作进入 approval，并且每次执行都记录 evidence、事件和 diff，方便恢复和审计。

### 深入回答

我把工具调用看成 propose、classify、decide、execute、record 五段。比如 shell 命令会先用规则分类，复合命令、安装依赖、网络请求、Git 写操作都会被判为 shell_risky；文件写入会检查敏感文件、大范围写入和路径安全；MCP 写操作会按外部副作用处理。失败恢复也不会绕过这些策略，只能在同样权限边界内重新规划。

### 当前边界

当前 shell 分类主要基于 `shlex`、安全前缀和风险模式，还不是完整 shell AST。它是保守可用的实现，但未来可以接入更严格的 parser，或者把 Go executor 的结构化命令执行能力继续做深。

## 21. 容易被追问的问题

### Q1：为什么不直接禁止所有高风险工具？

因为真实开发任务有时确实需要安装依赖、恢复快照或调用外部服务。直接禁止会让工具不可用，直接允许又太危险。approval 是折中：系统解释风险，用户做最终决定。

### Q2：approval 只是前端弹窗吗？

不是。前端只是展示层。后端必须记录 approval request、绑定具体操作、校验 token、处理过期和拒绝。否则用户点一次批准可能被错误复用到别的命令。

### Q3：工具失败后为什么不能直接重试？

盲目重试会放大错误。比如路径错了，重试同一个路径没有意义；缺依赖时，直接执行安装可能越权；测试失败时，可能是测试预期错，不一定是实现错。所以要先分类，再规划恢复。

### Q4：如何证明工具治理有价值？

可以通过三类测试证明：权限分类单元测试、approval 流程测试、真实任务 smoke test。还可以做消融实验：关闭审批或路径防护后，高风险动作是否被错误执行。

## 22. 本章自测增强

1. 为什么工具治理比“多加几个 Agent”更重要？
2. `safe_write` 在什么情况下会升级成 `risky_write`？
3. 为什么 `npm test` 可能安全，但 `npm install` 应该高风险？
4. 失败恢复为什么不能绕过 approval？
5. approval token 应该绑定哪些信息？
6. 如果工具 stdout 很长，应该进入最终回复还是 evidence？为什么？
7. 如果未来接入第三方 MCP，如何判断它的写工具是否可信？
