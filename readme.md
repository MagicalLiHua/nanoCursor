# nanoCursor

**基于 LangGraph 和 Docker 的多智能体自动编程框架。**

nanoCursor 将自然语言需求转化为可运行的代码，通过四个专业智能体连接成状态机工作流——当测试失败时自动进入修复循环。

---

## 工作原理

```
用户需求
    │
    ▼
┌─────────┐  计划完成   ┌─────────┐  工具调用   ┌─────────────────┐
│ Planner │─────────────►│  Coder  │───────────►│ coder_tools     │
│ (架构师)│             │ (工程师)│            │ (编辑/读取/写入)│
└─────────┘              └─────────┘◄───────────┴─────────────────┘
                             │
                             │ 完成 / 达到最大步数
                             ▼
                       ┌─────────┐  错误      ┌──────────┐ 超过最大重试
                       │ Sandbox │────────────►│ Reviewer │
                       │(测试运行)│             │ (诊断师) │───────────► Coder
                       └─────────┘             └──────────┘
                             │ 正常
                             ▼
                            END
```

**循环机制：** Sandbox 运行测试 → 失败则 Reviewer 分析错误并让 Coder 修复 → 重复直到通过或触发断路器。

---

## 核心特性

### 四个专业智能体

| 智能体 | 角色 | 职责 |
|-------|------|------|
| **Planner** | 架构师 | 探索工作区，理解需求，生成执行计划 |
| **Coder** | 工程师 | 读取/编辑文件，执行代码修改 |
| **Sandbox** | 测试运行器 | Docker 隔离执行，自动发现并运行 `test_*.py` |
| **Reviewer** | 诊断师 | 分析错误栈和代码差异，生成修复建议 |

### 三层上下文管理 (v2.0)

| 层级 | 内容 | 策略 |
|-----|------|------|
| **核心层** | 用户需求、计划、错误栈 | 永久保留，从不淘汰 |
| **工作层** | 最近 N 轮对话 | 滑动窗口，动态可调 |
| **参考层** | LLM 生成的历史摘要 | 通过 tiktoken 智能压缩 |

使用 `cl100k_base` 编码进行精确分词，不可用时自动回退到估算模式。

### Docker 沙箱隔离

- 无网络连接、256MB 内存限制、60 秒超时
- 自动发现并运行 `test_*.py` / `*_test.py`
- 存在 `requirements.txt` 时自动安装
- `auto_remove=True` 防止容器泄漏

### AST 感知文件操作

- 大文件（>5000 字符）返回 AST 概要而非原始内容
- `read_function` / `read_class` / `read_file_range` 精确定位
- `edit_file` 三层匹配策略：
  1. 精确匹配（原文字符完全一致）
  2. 去空白匹配（去除空格后匹配）
  3. 模糊匹配 via difflib（90% 相似度阈值，低于阈值则拒绝）

### 可观测性

- **MetricsCollector** — 线程安全：LLM 调用次数/令牌数/延迟、工具成功率、修复循环次数
- **恢复快照** — 断路器触发时：将工作区 `.py` 文件 + active_files + 修改日志打包到 `workspace/.snapshots/`
- **节点级日志器** — 结构化日志便于调试

### 类型安全 API 层

所有接口均使用 Pydantic BaseModel 请求/响应模型：

```python
RunRequest(prompt: str, thread_id: str | None)
CancelResponse(cancelled: bool, thread_id: str)
AgentStateResponse(messages: list[Message], current_plan: str | None, ...)
FileListResponse(files: list[FileEntry])
MetricsResponse(current: MetricsCurrentResponse, historical: list[...])
ConfigResponse(llm_providers: dict, system: SystemConfig, env_vars: list[EnvVar])
```

### 持久化检查点

LangGraph 状态通过 `langgraph-checkpoint-sqlite` 持久化到 SQLite：

- **会话恢复** — 相同 `thread_id` 从上次中断处继续
- **并发控制** — 该线程已有运行中的任务时返回 429
- **速率限制** — 同一线程两次启动间隔至少 10 秒

包未安装时回退到 `InMemorySaver`。

---

## 快速开始

### 环境要求

- Python 3.10+
- Docker Desktop（运行中）
- 至少一个 LLM 提供商：OpenAI / Anthropic / Ollama / DeepSeek

### 安装

```bash
git clone https://github.com/MagicalLiHua/nanoCursor.git
cd nanoCursor
pip install -r requirements.txt
```

### 配置

创建 `src/core/.env`（或从 `.env.example` 复制）：

```bash
# 选项 1: Ollama（本地，免费）
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder

# 选项 2: OpenAI
OPENAI_API_KEY=sk-xxx
OPENAI_MODEL=gpt-4o

# 选项 3: Anthropic
ANTHROPIC_API_KEY=sk-ant-xxx
ANTHROPIC_MODEL=claude-3-5-sonnet-latest

# 选项 4: DeepSeek
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_MODEL=deepseek-chat
```

### 运行

```bash
# Web UI（推荐）
streamlit run web_ui.py

# 或：API 服务 + React 前端
python api_server.py   # 启动于 http://localhost:8100
# 然后在另一个终端：
cd frontend && npm install && npm run dev

# 或：CLI（run.py 中硬编码的 prompt）
python run.py
```

### 沙箱要求

Docker Desktop 必须运行中。沙箱使用 `python:3.10-slim` 镜像，无网络连接，256MB 内存限制。

---

## 配置参考

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SANDBOX_IMAGE` | `python:3.10-slim` | 沙箱 Docker 镜像 |
| `SANDBOX_MEM_LIMIT` | `256m` | 容器内存限制 |
| `SANDBOX_TIMEOUT_SECONDS` | `60` | 最大执行时间 |
| `MAX_CODER_STEPS` | `15` | Coder 每轮最大工具调用次数 |
| `MAX_PLANNER_STEPS` | `10` | Planner 最大探索步数 |
| `LARGE_FILE_THRESHOLD` | `5000` | 触发 AST 概要的字符数阈值 |
| `FUZZY_MATCH_THRESHOLD` | `0.9` | 可接受的最小编辑相似度 |
| `MAX_FUZZY_MATCH_LINES` | `2000` | 超过此行数则跳过模糊匹配 |
| `LLM_TEMPERATURE` | `0.2` | LLM 采样温度 |
| `LLM_MAX_TOKENS` | `4096` | 最大输出令牌数 |

### 上下文管理器配置（位于 `context_manager.py`）

```python
DEFAULT_CONFIG = {
    "max_context_tokens": 8000,
    "coder_keep_turns": 4,
    "planner_keep_turns": 3,
    "reviewer_keep_turns": 2,
    "system_prompt_tokens": 800,
    "error_trace_tokens": 500,
}
```

---

## 项目结构

```
nanoCursor/
├── api_server.py              # FastAPI 后端（REST + SSE）
├── run.py                     # LangGraph StateGraph 构建器 + CLI 入口
├── web_ui.py                  # Streamlit 备选前端
├── requirements.txt           # Python 依赖
│
├── src/
│   ├── api/
│   │   ├── __init__.py
│   │   └── models.py          # 26 个 Pydantic 请求/响应模型
│   ├── agents/
│   │   ├── Planner.py          # 计划生成 + 工作区探索
│   │   ├── Coder.py           # 文件编辑 + 代码修改
│   │   ├── Sandbox.py         # Docker 容器生命周期 + 测试执行
│   │   └── Reviewer.py        # 错误分析 + 修复诊断
│   ├── core/
│   │   ├── config.py          # 路径解析 + 环境引导
│   │   ├── state.py           # AgentState TypedDict + SqliteSaver 检查点
│   │   ├── llm_engine.py      # 多提供商 LLM 初始化 + 重试包装器
│   │   ├── context_manager.py # 三层内存（核心/工作/参考）
│   │   ├── routing.py         # route_after_planner/coder/sandbox 决策
│   │   ├── metrics.py         # MetricsCollector 单例
│   │   ├── recovery.py        # 断路器快照系统
│   │   ├── logger.py          # 结构化日志工厂
│   │   └── repo_map.py        # 基于 AST 的函数/类签名提取
│   └── tools/
│       └── file_tools.py      # 8 个工具：read, write, edit, read_function,
│                              #   read_class, read_file_range, backup, rollback
├── tests/                     # 97 个 pytest 测试（51% 覆盖率）
├── frontend/                  # Vite + React + TypeScript
│   └── src/pages/
│       ├── ChatPage.tsx       # SSE 驱动的实时工作流更新
│       ├── MetricsPage.tsx    # LLM 调用/令牌/延迟/修复循环
│       ├── FileBrowserPage.tsx # 工作区文件树 + 语法高亮
│       └── ConfigPage.tsx     # LLM 提供商状态 + 环境变量
├── .github/workflows/ci.yml  # GitHub Actions：lint + test + frontend + audit
├── .pre-commit-config.yaml   # black, isort, ruff, mypy 钩子
└── pyproject.toml             # pytest, coverage（最低 50%）, ruff, mypy 配置
```

---

## API 参考

| 方法 | 端点 | 说明 |
|------|------|------|
| `POST` | `/api/run` | 启动工作流 `RunRequest(prompt, thread_id?)` |
| `GET` | `/api/run/{thread_id}/events` | SSE 节点事件流 |
| `POST` | `/api/run/{thread_id}/cancel` | 取消运行中的工作流 |
| `GET` | `/api/run/{thread_id}/state` | 获取最终 AgentState |
| `GET` | `/api/files` | 列出工作区文件 |
| `GET` | `/api/files/{path}` | 读取文件内容 |
| `GET` | `/api/metrics` | 当前 + 历史指标 |
| `GET` | `/api/config` | LLM 提供商、系统配置、环境变量 |
| `GET` | `/api/snapshots` | 列出恢复快照 |
| `GET` | `/api/snapshots/{id}` | 获取快照详情 |
| `GET` | `/api/backups` | 列出备份文件 |
| `GET` | `/api/backups/{name}` | 读取备份内容 |

---

## 开发指南

```bash
# 运行所有测试
pytest

# 带覆盖率运行
pytest --cov=src --cov=api_server --cov=run

# 代码检查
ruff check src/ api_server.py

# 类型检查
mypy src/

# Pre-commit（提交前运行 lint + 类型检查 + 测试）
pre-commit run --all-files
```

---

## 架构亮点

### 工作流取消

每个节点在入口处检查 `state.cancelled`。通过 `graph_app.update_state()` 将其设为 `True` 会导致所有进行中的节点抛出 `WorkflowCancelledError`，干净地终止工作流。

### 三层编辑匹配

当 `edit_file` 无法找到精确搜索块时，会尝试逐步放宽的策略：

```
1. 精确匹配   → search_block 完全一致
2. 去空白匹配 → 去除空格后匹配
3. 模糊匹配   → difflib 滑动窗口，需 90% 相似度
               低于阈值：拒绝 + 提示用户重新读取
```

### 断路器

当 Sandbox 报告错误且 `retry_count >= max_retries` 时：
1. 恢复系统将所有 `.py` 文件 + active_files + 修改日志打包为快照
2. 工作流终止并附带诊断信息
3. 用户可检查快照内容并手动继续

---

## 路线图

- [ ] **libcst / Tree-sitter** — 用 AST 级编辑替换文本搜索/替换
- [ ] **代码库级 RAG** — 大仓库的向量化代码搜索
- [ ] **Linter 预检查** — 在沙箱前运行 LSP/Linter 跳过明显错误
- [ ] **LLM 断路器** — N 次连续 LLM 失败后快速失败
- [ ] **SWE-bench** — 量化通过率、平均令牌成本指标

---

## 依赖

| 包 | 用途 |
|---|------|
| langgraph ≥1.0.0 | 状态机 / 智能体编排 |
| langchain-core ≥1.2.0 | LLM 消息 / 工具抽象 |
| langchain-openai / anthropic / ollama | 提供商适配器 |
| pydantic ≥2.0.0 | 结构化 I/O 验证 |
| tiktoken ≥0.9.0 | 精确分词（cl100k_base） |
| docker ≥7.0.0 | 沙箱容器管理 |
| fastapi ≥0.115.0 | REST API + SSE |
| langgraph-checkpoint-sqlite ≥2.0.0 | 持久化工作流状态 |

---

## 开源协议

MIT

---

由 [LiHua](https://github.com/MagicalLiHua) 构建
