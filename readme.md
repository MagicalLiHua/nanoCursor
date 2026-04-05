# nanoCursor

基于 LangGraph 与 Docker 的多智能体自动编程框架。

nanoCursor 将用户请求转化为可运行代码，通过四个职责独立的 Agent（Planner、Coder、Sandbox、Reviewer）在状态机驱动的工作流中自动完成"需求理解 -> 架构规划 -> 代码修改 -> 沙盒测试 -> 诊断修复"的全闭环流转。

---

## 核心特性

### 四 Agent 协作闭环

| Agent | 职责 |
|-------|------|
| **Planner** (架构师) | 理解需求，探索工作区，制定分步开发计划 |
| **Coder** (工程师) | 精准执行代码修改，作为"代码手术刀" |
| **Sandbox** (沙盒) | Docker 隔离运行，自动发现测试文件并执行 |
| **Reviewer** (审查员) | 分析错误栈 + diff，生成诊断报告并打回修复 |

### 上下文管理器 v2.0 — 三层记忆策略

| 层级 | 内容 | 策略 |
|------|------|------|
| 核心记忆 | 用户需求、执行计划、报错信息 | 永久保留，不可裁剪 |
| 工作记忆 | 最近 N 轮对话 | 滑动窗口，动态调整 |
| 参考记忆 | 历史对话结构摘要 | LLM 智能压缩，Fallback 基于规则 |

- **tiktoken 精确计数** — `cl100k_base` encoding，不可用时自动回退估算
- **动态窗口** — Token 使用率 >80% 缩减上下文，<30% 自动扩展
- **优先级槽位裁剪** — `ContextSlot` 按优先级管理，Token 紧张时从低优先级开始裁剪

### Docker 隔离沙盒

- 无网 + 限存的临时容器 (`network_disabled=True`)
- 自动发现 `test_*.py` / `*_test.py`，优先 pytest 运行
- `requirements.txt` 自动 pip install
- 超时熔断 + `auto_remove` 防资源泄漏

### AST 感知的文件操作

- 大文件 (>5000 字符) 返回 AST 结构大纲而非原始内容
- `read_function` / `read_class` / `read_file_range` 精确定位
- `edit_file` 三级匹配：精确 → 去空匹配 → difflib 模糊匹配 (90% 阈值)
- 每次修改自动备份，支持一键回滚

### 可观测性

- **MetricsCollector** — 线程安全的指标收集器：LLM 调用次数/Token 消耗/延迟、工具成功率、修复循环结果
- **错误恢复快照** — 达到熔断时自动将工作区 `.py` 文件、active_files 内容和修改日志打包到 `workspace/.snapshots/`
- **结构化日志** — 每节点独立 logger，便于排查

---

## 架构设计

### 工作流图

```
START ──► Planner ──(tool_calls)──► planner_tools ──► Planner
                   └─(计划完成)────► Coder ──(tool_calls)──► coder_step_counter ──► coder_tools ──► Coder
                                            └─(完成/步数上限)─► Sandbox
                                                                 │
                                              ┌────(通过)────────┴───────(失败)──────┐
                                              ▼                                     ▼
                                             END                            Reviewer ──► Coder (修复循环)
                                                                                │
                                                                           retry ≥ max ──► 快照 + END
```

### 路由决策

| 路由 | 条件 | 下一节点 |
|------|------|----------|
| `route_after_planner` | 有 tool_calls | `planner_tools` |
| `route_after_planner` | 无 tool_calls | `coder` |
| `route_after_planner` | tool_calls ≥ MAX_PLANNER_STEPS | `coder` (强制结束探索) |
| `route_after_coder` | 有 tool_calls | `coder_step_counter` → `coder_tools` |
| `route_after_coder` | 无 tool_calls 或达到步数上限 | `sandbox` |
| `route_after_sandbox` | 无 error_trace | `END` |
| `route_after_sandbox` | 有 error 且 retry < max | `reviewer` → `coder` |
| `route_after_sandbox` | retry ≥ max | 保存快照 → `END` |

---

## 快速启动

### 环境要求

- Python 3.10+
- Docker Desktop (沙盒隔离)
- 任一 LLM 提供商：OpenAI / Anthropic / Ollama / DeepSeek

### 安装

```bash
git clone https://github.com/MagicalLiHua/nanoCursor.git
cd nanoCursor
pip install -r requirements.txt
```

### 配置

在 `src/core/` 下创建 `.env` 文件（参考 `.env.example`）：

```bash
# Ollama 本地模型
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder

# 或 OpenAI
# OPENAI_API_KEY=sk-xxx
# OPENAI_MODEL=gpt-4o

# 或 Anthropic
# ANTHROPIC_API_KEY=sk-ant-xxx
# ANTHROPIC_MODEL=claude-sonnet-4-6
```

### 运行

```bash
# 方式一：Streamlit Web UI (推荐)
streamlit run web_ui.py

# 方式二：CLI
python run.py
```

---

## 配置项

所有可配置参数集中在 `src/core/config.py`，部分支持环境变量覆盖：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SANDBOX_IMAGE` | `python:3.10-slim` | 沙盒容器镜像 |
| `SANDBOX_MEM_LIMIT` | `256m` | 沙盒容器内存限制 |
| `SANDBOX_TIMEOUT_SECONDS` | `60` | 沙盒执行超时（秒） |
| `LARGE_FILE_THRESHOLD` | `5000` | 大文件判定阈值（字符） |
| `FUZZY_MATCH_THRESHOLD` | `0.9` | edit_file 模糊匹配最低相似度 |
| `MAX_FUZZY_MATCH_LINES` | `2000` | 超过此行数跳过模糊匹配 |
| `MAX_CODER_STEPS` | `15` | Coder 单轮最大工具调用步数 |
| `MAX_PLANNER_STEPS` | `10` | Planner 最大探索步数 |

上下文管理器配置位于 `context_manager.py` 的 `DEFAULT_CONFIG`：

```python
DEFAULT_CONFIG = {
    "max_context_tokens": 8000,       # 上下文最大 Token 数
    "coder_keep_turns": 4,            # Coder 保留对话轮数
    "planner_keep_turns": 3,          # Planner 保留对话轮数
    "reviewer_keep_turns": 2,         # Reviewer 保留对话轮数
    "system_prompt_tokens": 800,      # 系统提示预留
    "error_trace_tokens": 500,        # 错误信息预留
}
```

---

## 项目结构

```
nanoCursor/
├── run.py                      # LangGraph 工作流编排 & CLI 入口
├── web_ui.py                   # Streamlit Web UI
├── requirements.txt            # Python 依赖
│
├── src/
│   ├── agents/
│   │   ├── Planner.py          # 规划师：需求理解 + 计划生成
│   │   ├── Coder.py            # 工程师：代码编写 + 文件修改
│   │   ├── Reviewer.py         # 审查员：报错分析 + 诊断报告
│   │   └── Sandbox.py          # 沙盒：Docker 隔离测试
│   │
│   ├── core/
│   │   ├── config.py           # 全局配置 & 路径解析
│   │   ├── context_manager.py  # 分层上下文管理 v2.0
│   │   ├── state.py            # AgentState 定义 + InMemorySaver
│   │   ├── llm_engine.py       # 多提供商 LLM 初始化 + 异步重试
│   │   ├── logger.py           # 结构化日志
│   │   ├── repo_map.py         # AST 仓库地图
│   │   ├── routing.py          # 路由决策逻辑
│   │   ├── recovery.py         # 熔断快照 & 错误恢复
│   │   └── metrics.py          # 可观测性指标收集器
│   │
│   └── tools/
│       └── file_tools.py       # 8 个文件工具 (read/edit/write/...)
│
├── tests/                      # 单元测试
└── workspace/                  # Agent 工作区
```

---

## 技术亮点

### AST 感知文件操作

```
read_file(filename)            → 小文件返回全文，大文件返回 AST 结构大纲
read_function(filename, name)  → AST 精确定位，提取函数完整源码
read_class(filename, name)     → AST 精确定位，提取类完整源码
read_file_range(filename, s, e)→ 按行号读取，带行号前缀
```

大纲包含所有函数/类的名称、参数、起止行号，Agent 可据此按需读取目标代码。

### edit_file 三级匹配

```
1. 精确匹配         search_block 原文一字不差
2. 去空匹配         首尾空白/换行 strip 后匹配
3. 模糊匹配 (90%)   difflib 滑动窗口，低于阈值拒绝并提示重新读取
```

### LLM 智能记忆压缩

旧对话经过 LLM（或基于规则的 fallback）压缩为四段式摘要：

```
1. 【用户需求】一句话概括原始需求
2. 【已完成的修改】列出所有文件改动
3. 【遇到的问题】列出遇到的错误和修复尝试
4. 【待解决】当前仍未解决的问题
```

---

## 未来规划

- [ ] **libcst / Tree-sitter 结构化修改** — 替代纯文本 Search/Replace，从根源消除匹配失败
- [ ] **Repo-level RAG** — 代码向量化 + 语义检索，增强大型仓库导航能力
- [ ] **Linter 前置检查** — 沙盒前置轻量 LSP/Linter，跳过明显语法错误，减少无效 Docker 调用
- [ ] **Circuit Breaker for LLM** — 连续失败 N 次后快速失败，而非重复重试
- [ ] **SWE-bench 评测** — 量化 Pass Rate、平均 Token 消耗等指标

---

## 依赖项

| 依赖 | 用途 |
|------|------|
| langgraph | 状态机 / Agent 编排 |
| langchain-core | LLM 消息 / 工具调用抽象 |
| langchain-openai / anthropic / ollama | LLM 提供商适配器 |
| pydantic v2 | 结构化输出解析 |
| tiktoken | 精确 Token 计数 |
| docker | 沙盒容器引擎 |
| streamlit | Web UI 框架 |
| python-dotenv | 环境变量管理 |

---

## 开源协议

MIT License

---

Made by [LiHua](https://github.com/MagicalLiHua)
