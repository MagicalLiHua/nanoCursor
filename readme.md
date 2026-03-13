# ⚡ nanoCursor

> **基于 LangGraph 与 Docker 的轻量级多智能体本地编程沙盒**

nanoCursor 是一个探索性的多智能体（Multi-Agent）自动编程协作框架。区别于传统的单体大模型线性对话，本项目基于状态机架构构建了真实的工程化闭环，实现了从“需求理解 -> 架构规划 -> 局部编码 -> 沙盒测试 -> 代码审查与自动修复”的全自动流转。


## 核心特性

-  **状态机驱动的 Agent 编排**：基于 LangGraph 构建有向无环图 (DAG)，将大模型剥离为职责单一的 `Planner`（架构思考）、`Coder`（代码修改）与 `Reviewer`（报错诊断），提升执行稳定性。
-  **安全的 Docker 隔离沙盒**：内置隔离执行引擎，在无网、限存（256MB）的临时 Docker 容器中运行测试代码，自动捕获 `stdout/stderr` 并反馈给 Agent 形成修复闭环。
-  **高可用文件修改策略**：摒弃极易产生幻觉和 Token 消耗极大的“全文覆写”，采用基于 `Search/Replace` 的局部代码块替换。底层实现了从“精确匹配”到基于 `difflib` 的“模糊匹配”的降级容错机制。
-  **动态上下文管理 (Context Management)**：针对多轮 Debug 容易导致 Token 爆炸并引起大模型注意力丢失的问题，在 Coder 节点引入基于滑动窗口的上下文裁剪机制。
-  **可视化的流转追踪**：提供基于 Streamlit 的 Web UI，可实时观测 Agent 的内在思考过程（规划、工具调用、目标文件状态）。

## 架构设计

系统的工作流由以下几个核心节点循环驱动：

1.  **Planner (规划师)**：接收需求，通过 `list_directory` 和 `read_file` 探索本地工作区，制定包含边界检查的详细分步计划，并圈定 `active_files`。
2.  **Coder (工程师)**：剥离了宏观思考负担，仅作为“手术刀”执行 Planner 的计划。通过工具精准完成目标文件的 `edit_file` 操作。
3.  **Sandbox (沙盒)**：不调用大模型。将工作区挂载至临时 Docker 容器中执行 Python 脚本，判定成功或捕获错误栈。拥有最大重试（`max_retries`）熔断保护机制。
4.  **Reviewer (审查员)**：当沙盒报错时触发。读取原始执行计划与错误栈，生成人类可读的“诊断报告”，并打回给 Coder 重新修改。

## 快速启动

### 1. 环境依赖
- Python 3.10+
- **Docker Desktop** (必须启动，用于沙盒隔离运行)
- 兼容 OpenAI 格式的 API 或 Ollama 本地模型（建议使用 Qwen2.5-Coder / Claude-3.5-Sonnet 等代码能力较强的模型）

### 2. 安装与配置
```bash
# 1. 克隆仓库
git clone [https://github.com/yourusername/nanoCursor.git](https://github.com/yourusername/nanoCursor.git)
cd nanoCursor

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 配置环境变量
# 在src/core目录创建 .env 文件，参考如下配置：
# OLLAMA_BASE_URL=http://localhost:11434
# OLLAMA_MODEL=qwen2.5-coder

```

### 3. 运行项目

```bash
# 启动 Web 交互界面
streamlit run web_ui.py

```

> 此时你可以输入例如：*“帮我在工作区写一个快速排序算法，并添加测试用例，确保沙盒测试通过。”* 系统将自动开始规划与执行。

## 未来工作

作为一个概念验证 (PoC) 项目，当前架构仍存在一些瓶颈，也是未来持续优化的方向：

* [ ] **基于 AST 的代码修改 (Tree-sitter)**：当前基于纯文本查找替换的机制在面对 LLM 缩进/空行幻觉时仍显脆弱。未来计划引入 Tree-sitter 将代码解析为抽象语法树，实现函数/类级别的结构化精准修改。
* [ ] **仓库级检索 (Repo-level RAG)**：当前 Planner 严重依赖遍历目录寻找文件。对于大型仓库，计划引入代码向量化 (Code Embedding) 与语义检索工具，增强跨文件上下文理解能力。
* [ ] **静态检查前置 (Linter/LSP)**：目前的语法拼写错误也需要经过 Docker 沙盒运行后才能被 Reviewer 发现。计划在沙盒节点前置轻量级的 LSP 检查，失败直接打回，降低整体执行耗时与算力成本。
* [ ] **评测驱动开发 (Evaluation)**：构建轻量级的 SWE-bench 微型测试集，以量化的方式（Pass Rate, 平均 Token 消耗等）评估迭代架构的有效性。

## 开源协议 (License)

[MIT License](https://www.google.com/search?q=LICENSE)