# ⚡ nanoCursor

> **基于 LangGraph v1.0 与 Docker 的轻量级多智能体本地编程沙盒**

nanoCursor 是一个探索性的多智能体（Multi-Agent）自动编程协作框架。基于 LangGraph 状态机构建有向无环图 (DAG)，将大模型剥离为职责单一的 `Planner`（架构规划）、`Coder`（代码修改）与 `Reviewer`（报错诊断）三大智能体，实现了从"需求理解 → 架构规划 → 局部编码 → 沙盒测试 → 代码审查与自动修复"的全自动闭环流转。

---

## ✨ 核心特性

### 🧠 状态机驱动的 Agent 编排
基于 LangGraph v1.0 构建 DAG 工作流，每个节点职责单一、边界清晰：
- **Planner (规划师)**：理解需求，探索工作区，制定分步开发计划
- **Coder (工程师)**：作为"代码手术刀"精准执行文件修改
- **Sandbox (沙盒)**：Docker 隔离运行，自动捕获 `stdout/stderr`
- **Reviewer (审查员)**：分析错误栈，生成诊断报告并打回修复

### 🔄 上下文管理器 v2.0
针对多轮 Debug 导致 Token 爆炸和上下文丢失问题，设计了分层上下文管理策略：

| 层级 | 内容 | 管理策略 |
|------|------|----------|
| **核心记忆** | 用户需求、执行计划、报错信息 | 永久保留 |
| **工作记忆** | 最近 N 轮对话 | 滑动窗口裁剪 |
| **参考记忆** | 历史对话结构化摘要 | LLM 智能压缩 |

**关键技术实现：**
- **tiktoken 精确计数**：使用 `cl100k_base` encoding 精确计算 Token 消耗（不可用时回退估算模式）
- **LLM 智能摘要**：将旧对话压缩为结构化摘要（需求/已完成修改/遇到的问题/待解决），避免信息丢失
- **动态上下文窗口**：根据当前 Token 使用率自动调整保留轮数（>80% 缩减，<30% 扩展）
- **上下文槽位优先级队列**：`ContextSlot` 类实现优先级管理，Token 紧张时自动裁剪低优先级内容

### 📦 安全的 Docker 隔离沙盒
- 无网、限存（256MB）的临时容器中运行测试代码
- 自动捕获错误栈并反馈给 Agent 形成修复闭环
- 最大重试次数（`max_retries`）熔断保护机制

### 🔧 高可用文件修改策略
- 摒弃"全文覆写"，采用 `Search/Replace` 局部代码块替换
- 从"精确匹配"到基于 `difflib` 的"模糊匹配"降级容错
- 文件签名索引机制：提取函数/类签名，减少不必要的文件读取

### 🌐 可视化 Streamlit Web UI
- 基于 `stream_mode="updates"` 实时捕获节点执行状态
- 直观展示 Planner 规划、Coder 操作、Sandbox 运行结果、Reviewer 诊断报告
- 侧边栏实时监控 Agent 内部状态（目标文件、执行计划等）

---

## 🏗️ 架构设计

### 工作流程图

```
┌─────────────────────────────────────────────────────────────────┐
│                        START (用户需求)                          │
└──────────────────────┬──────────────────────────────────────────┘
                       ▼
              ┌────────────────┐
              │   Planner      │  探索工作区，制定计划
              └───────┬────────┘
                      │
         ┌────────────┴────────────┐
         ▼ (调用工具探索)           ▼ (计划完成)
┌─────────────┐           ┌─────────────┐
│planner_tools│──────────▶│   Planner    │  循环探索
└─────────────┘           └──────┬──────┘
                                 ▼
              ┌────────────────────────┐
              │       Coder            │  执行代码修改
              └────────┬───────────────┘
                       │
          ┌────────────┴────────────┐
          ▼ (调用工具修改)           ▼ ("DONE" 完成)
┌─────────────┐           ┌─────────────┐
│ coder_tools │──────────▶│   Coder     │  循环修改
└─────────────┘           └──────┬──────┘
                                 ▼
              ┌────────────────────────┐
              │      Sandbox           │  Docker 隔离测试
              └────────┬───────────────┘
                       │
          ┌────────────┴────────────┐
          ▼ (测试通过)               ▼ (测试失败)
┌─────────────┐           ┌─────────────┐
│    END ✅    │           │  Reviewer   │  分析报错
└─────────────┘           └──────┬──────┘
                                 │
                                 ▼
                        ┌─────────────┐
                        │   Coder     │  根据诊断修复
                        └─────────────┘
```

### 路由逻辑

| 路由位置 | 条件 | 下一节点 |
|----------|------|----------|
| `route_after_planner` | 有 tool_calls | `planner_tools` → `planner` |
| `route_after_planner` | 无 tool_calls | `coder` |
| `route_after_coder` | 有 tool_calls | `coder_tools` → `coder` |
| `route_after_coder` | 无 tool_calls | `sandbox` |
| `route_after_sandbox` | 无 error_trace | `END` ✅ |
| `route_after_sandbox` | 有 error 且 retry < max | `reviewer` → `coder` |
| `route_after_sandbox` | retry >= max | `END` (熔断) |

---

## 🚀 快速启动

### 环境要求
- **Python 3.10+**
- **Docker Desktop** (必须启动，用于沙盒隔离运行)
- 兼容 OpenAI 格式的 API 或 Ollama 本地模型

**推荐模型：**
- `Qwen2.5-Coder` (代码能力较强)
- `Claude-3.5-Sonnet`
- `GPT-4o`

### 安装步骤

```bash
# 克隆仓库
git clone https://github.com/MagicalLiHua/nanoCursor.git
cd nanoCursor

# 安装依赖
pip install -r requirements.txt
```

### 配置

在 `src/core` 目录下创建 `.env` 文件：

```bash
# Ollama 本地模型示例
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder

# 或 OpenAI 兼容 API 示例
# OPENAI_API_KEY=sk-xxx
# OPENAI_API_BASE=https://api.openai.com/v1
# OPENAI_MODEL=gpt-4o
```

### 运行

```bash
# 方式一：启动 Streamlit Web UI (推荐)
streamlit run web_ui.py

# 方式二：命令行直接运行
python run.py
```

启动 Web UI 后，在输入框中输入需求，例如：
> *"帮我在工作区写一个快速排序算法，并添加测试用例，确保沙盒测试通过。"*

系统将自动开始规划与执行。

---

## 📁 项目结构

```
nanoCursor/
├── run.py                  # LangGraph 工作流编排与入口
├── web_ui.py               # Streamlit Web UI
├── requirements.txt        # Python 依赖
├── readme.md               # 项目文档
├── .gitignore
│
├── src/
│   ├── agents/
│   │   ├── Planner.py      # 规划师节点 (需求理解 + 计划生成)
│   │   ├── Coder.py        # 工程师节点 (代码编写 + 文件修改)
│   │   ├── Reviewer.py     # 审查员节点 (报错分析 + 诊断报告)
│   │   └── Sandbox.py      # 沙盒节点 (Docker 隔离测试)
│   │
│   ├── core/
│   │   ├── config.py       # 全局配置 (路径、环境变量)
│   │   ├── context_manager.py  # 🔥 上下文管理器 v2.0 (核心)
│   │   ├── state.py        # Agent 状态定义 + Redis Checkpointer
│   │   ├── llm_engine.py   # LLM 引擎初始化
│   │   └── repo_map.py     # 仓库目录树 + 文件签名生成
│   │
│   └── tools/
│       └── file_tools.py   # 文件操作工具 (read/edit/write/list)
│
└── workspace/              # Agent 工作区 (文件读写 & 沙盒测试)
```

---

## ⚙️ 配置说明

### 上下文管理器配置

上下文管理器位于 `src/core/context_manager.py`，默认配置如下：

```python
DEFAULT_CONFIG = {
    "max_context_tokens": 8000,       # 上下文最大 Token 数
    "coder_keep_turns": 4,            # Coder 保留的对话轮数
    "planner_keep_turns": 3,          # Planner 保留的对话轮数
    "reviewer_keep_turns": 2,         # Reviewer 保留的对话轮数
    "system_prompt_tokens": 800,      # 系统提示预留 Token
    "error_trace_tokens": 500,        # 错误信息预留 Token
}
```

### Token 估算

项目优先使用 `tiktoken` 进行精确 Token 计数，支持的编码模型：
- `cl100k_base`：GPT-4, GPT-3.5-Turbo, Claude

若 `tiktoken` 不可用，自动回退到基于字符数的快速估算。

---

## 🔬 技术亮点

### 1. 分层上下文管理 (Layered Context Management)

传统方案直接将所有历史消息传入 LLM，导致：
- Token 浪费，上下文窗口被长文件内容挤占
- 注意力分散，关键信息被淹没
- 多轮迭代后触发上下文超长截断

nanoCursor v2.0 的三层架构：

```
┌─────────────────────────────────────┐
│  Layer 1: 核心记忆 (永久保留)         │  ← 用户需求、当前计划、报错信息
├─────────────────────────────────────┤
│  Layer 2: 工作记忆 (动态窗口)         │  ← 最近 N 轮对话 (可动态调整)
├─────────────────────────────────────┤
│  Layer 3: 参考记忆 (LLM 智能摘要)    │  ← 历史对话的结构化压缩摘要
└─────────────────────────────────────┘
```

### 2. 上下文槽位优先级队列 (Priority-Based Context Building)

```python
class ContextSlot:
    """上下文槽位 - 用于管理不同类型的上下文片段"""
    def __init__(self, name, messages, priority, is_fixed=False):
        self.name = name
        self.messages = messages
        self.priority = priority  # 数字越小越重要
        self.is_fixed = is_fixed  # 固定内容不可裁剪
        self.token_count = estimate_messages_tokens(messages)
```

当接近 Token 上限时，系统从低优先级开始自动裁剪，保证核心信息不丢失。

### 3. LLM 驱动的智能记忆压缩

```python
def summarize_conversation(old_messages, llm=None, max_summary_length=500):
    """使用 LLM 对旧对话进行智能摘要"""
    # 生成结构化摘要格式：
    # 1. 【用户需求】一句话概括原始需求
    # 2. 【已完成的修改】列出所有文件改动
    # 3. 【遇到的问题】列出遇到的错误和修复尝试
    # 4. 【待解决】当前仍未解决的问题
```

### 4. 动态上下文窗口

```python
def calculate_dynamic_window(total_tokens, max_tokens, current_turns):
    """根据 Token 使用率动态调整保留轮数"""
    token_ratio = total_tokens / max_tokens
    if token_ratio > 0.8:
        return max(min_turns, current_turns - 1)  # 缩减
    elif token_ratio < 0.3:
        return min(max_turns, current_turns + 1)  # 扩展
    return current_turns
```

---

## 🔮 未来规划

- [ ] **基于 AST 的代码修改 (Tree-sitter)**：当前基于纯文本查找替换的机制在面对 LLM 缩进/空行幻觉时仍显脆弱。计划引入 Tree-sitter 将代码解析为抽象语法树，实现函数/类级别的结构化精准修改。
- [ ] **仓库级语义检索 (Repo-level RAG)**：当前 Planner 严重依赖遍历目录寻找文件。对于大型仓库，计划引入代码向量化 (Code Embedding) 与语义检索工具，增强跨文件上下文理解能力。
- [ ] **静态检查前置 (Linter/LSP)**：目前的语法拼写错误也需要经过 Docker 沙盒运行后才能被 Reviewer 发现。计划在沙盒节点前置轻量级的 LSP 检查，失败直接打回，降低整体执行耗时与算力成本。
- [ ] **评测驱动开发 (Evaluation)**：构建轻量级的 SWE-bench 微型测试集，以量化的方式（Pass Rate, 平均 Token 消耗等）评估迭代架构的有效性。
- [ ] **多文件并行处理**：支持 Coder 同时处理多个不相关的文件修改任务，提升执行效率。
- [ ] **增量编译与热更新**：沙盒层支持增量测试，避免每次修改都重新执行完整测试流程。

---

## 📄 依赖项

| 依赖 | 版本 | 用途 |
|------|------|------|
| langgraph | ~1.0.10 | 状态机/Agent 编排框架 |
| langchain-core | ~1.2.18 | LLM 消息/工具调用抽象 |
| langchain-ollama | ~1.0.0 | Ollama 本地模型适配器 |
| pydantic | ~2.12.5 | 结构化输出解析 |
| tiktoken | ~0.9.0 | 精确 Token 计数 |
| docker | ~7.0.0 | 沙盒容器引擎 |
| streamlit | ~1.30.0 | Web UI 框架 |
| redis | ~7.1.0 | 状态持久化 Checkpointer |
| python-dotenv | ~1.0.0 | 环境变量管理 |

---

## 📝 开源协议 (License)

[MIT License](LICENSE)

---

<p align="center">Made with ❤️ by <a href="https://github.com/MagicalLiHua">LiHua</a></p>