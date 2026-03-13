# ⚡ nanoCursor

> **基于 LangGraph 的多智能体协作编程本地沙盒**
> 
> *状态：🚧 核心架构开发中 (WIP)*

## 📖 项目愿景 (Vision)

传统的大模型 Supervisor 分发模式在复杂的软件工程中往往力不从心。**nanoCursor** 旨在构建一个精简但五脏俱全的本地多智能体协作编程闭环（类似极简版 Cursor / Devin）。

本项目放弃了简单的线性对话，转而采用**基于黑板模式 (Blackboard) 的状态机架构**，通过 LangGraph 编排不同的 Agent 节点，实现“需求理解 -> 架构规划 -> 局部编码 -> 沙盒测试 -> 自我反思 -> 人类兜底”的真实工程流转。

## 🏗️ 核心架构设计 (Architecture)

系统底层依赖 LangGraph 构建有向无环图 (DAG) 与循环状态机，核心流转节点包括：

* **🧠 Planner (架构师 Agent):** * 职责：接收自然语言需求，读取项目目录树，制定分步执行计划 (`current_plan`)。
    * 特点：不直接写代码，专注于拆解任务和圈定需要修改的文件作用域 (`active_files`)。
* **💻 Coder (工程师 Agent):**
    * 职责：根据 Planner 的计划，调用文件操作 Tools 精准修改本地代码。
    * 特点：剥离了宏观思考负担，仅作为“手术刀”执行代码变更。
* **🛠️ Sandbox (沙盒工具节点):**
    * 职责：在本地或隔离环境中执行 `npm test` / `python script.py` 等命令，捕获终端 `stdout/stderr` 输出。
* **🔍 Reviewer (反思节点):**
    * 职责：条件路由 (Conditional Edge) 触发。当沙盒报错时，读取错误栈并生成修复建议，控制权回滚给 Coder。
* **🛡️ Human-in-the-loop (HITL 人类兜底):**
    * 职责：通过 `interrupt_before` 机制拦截高危操作（如删除文件）或在超过最大重试次数 (`max_retries`) 时挂起节点，交由人类接管。

## ✨ 工程化亮点 (Engineering Highlights)

* **结构化全局状态 (State Management):** 严格区分“对话上下文 (Messages)”与“工程上下文 (Plan, Active Files, Test Output)”，避免上下文污染。
* **基于 Search/Replace 的精准文件修改 (攻坚中):** 摒弃极易产生幻觉的“纯行号替换”和“全量覆盖”，采用基于锚点的局部块替换逻辑，大幅降低 Token 消耗并提升修改精准度。
* **Token 裁剪策略:** 针对多轮 Bug 修复导致的 Token 爆炸问题，设计针对终端 Error 信息首尾截断算法与历史 ToolMessage 的动态滑动窗口。

## 🚀 快速启动 (Quick Start)

*(MVP 版本代码即将合入主分支，敬请期待)*

```bash
# 克隆仓库
git clone [https://github.com/yourusername/nanoCursor.git](https://github.com/yourusername/nanoCursor.git)
cd nanoCursor

# 安装依赖
pip install -r requirements.txt

# 启动交互式终端
python main.py

```

## 🗺️ Roadmap (未来规划)

* [x] LangGraph 核心状态机与流转逻辑设计
* [x] Planner 与 Coder 职责解耦
* [x] HITL (人在闭环) 安全拦截机制
* [ ] 完善 Search/Replace 局部代码修改工具
* [ ] 接入 Docker 作为安全隔离沙盒环境