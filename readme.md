# nanoCursor

**LLM 驱动的多智能体自动编程框架。** 用自然语言描述需求，Agent 自主调用工具完成编码任务。

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 LLM（创建 .env，填入任意一个提供商的 API Key）
#    支持 Anthropic / DeepSeek / MiniMax / OpenAI / Ollama
#    系统自动检测已配置的 Key，无需手动指定提供商

# 3. 启动
python api_server.py          # FastAPI 后端 + React 前端
# 浏览器打开 http://localhost:8100
```

前端开发模式：
```bash
cd frontend && npm install && npm run dev   # Vite :3000 → API :8100
```

---

## 架构

```
用户输入 → Agent Loop → LLM (tool_use?) → 执行工具 → 结果回传 → 循环
                                              ↓
                               bash / read_file / write_file / edit_file
                               TodoWrite / task_create / spawn_teammate ...
```

### Agent Loop

核心是一个简单的 `while` 循环，非复杂状态机：

1. 将消息 + 工具定义发送给 LLM
2. 如果 `stop_reason == "tool_use"`，执行工具调用，结果追加到对话历史，继续循环
3. 否则返回最终文本结果

### 工具系统

| 类别 | 工具 | 说明 |
|------|------|------|
| **文件** | `bash`, `read_file`, `write_file`, `edit_file`, `list_directory` | 文件操作与命令执行 |
| **Todo** | `TodoWrite`, `TodoList` | 任务清单管理 |
| **任务** | `task_create`, `task_update`, `task_list` | DAG 任务依赖管理 |
| **子 Agent** | `task` | 派发独立子代理执行隔离任务 |
| **团队** | `spawn_teammate`, `send_message`, `broadcast`, `plan_approval` 等 | 多智能体协作 |

### 团队协作

多个自主 Agent 通过 JSONL inbox 异步通信。每个 Teammate 是独立线程，自动轮询未认领任务。支持计划审批工作流和优雅关闭协议。

### 上下文管理

- **自动压缩**：消息历史超过阈值时，保留最近工具结果，其余压缩为长度标记
- **System Prompt 缓存**：静态部分缓存，仅重建动态上下文
- **参数安全**：工具调用缺少必填参数时返回明确错误信息，Agent 可自纠正

---

## Web 界面

React 前端提供四个页面，侧边栏布局，SSE 实时更新：

| 页面 | 功能 |
|------|------|
| **工作台** | CLI 风格聊天界面，Markdown 渲染 + 代码高亮，支持 `/` 斜杠命令 |
| **指标面板** | LLM 调用次数、Token 消耗、延迟、工具成功率，历史趋势 |
| **文件浏览器** | 工作区文件树 + 语法高亮查看器 |
| **配置面板** | LLM 提供商状态、系统配置、环境变量 |

### 斜杠命令

在聊天框输入 `/` 开头的命令：

| 命令 | 说明 |
|------|------|
| `/help` | 显示所有命令 |
| `/clear` | 清空对话，开启新线程 |
| `/files` | 列出工作区文件 |
| `/config` | 显示 LLM 配置状态 |
| `/metrics` | 显示当前指标 |
| `/bash <cmd>` | 在工作区执行 shell 命令 |
| `/cancel` | 取消正在运行的任务 |
| `/workspace` | 显示当前工作区路径 |

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/run` | 启动工作流 `{thread_id, status}` |
| `GET` | `/api/run/{id}/events` | SSE 事件流（实时推送） |
| `POST` | `/api/run/{id}/cancel` | 取消运行 |
| `GET` | `/api/files` | 工作区文件树 |
| `GET` | `/api/files/{path}` | 读取文件内容 |
| `POST` | `/api/bash` | 直接执行 bash 命令 |
| `GET` | `/api/metrics` | 指标数据 + 历史 |
| `GET` | `/api/config` | LLM 配置 + 系统信息 |
| `GET/POST` | `/api/todos` | Todo CRUD |
| `GET/POST` | `/api/memories` | 记忆存储与搜索 |

---

## 项目结构

```
nanoCursor/
├── api_server.py              # FastAPI 后端入口
├── run.py                     # CLI 入口
├── requirements.txt
├── .env.example               # 配置模板
├── src/
│   ├── agent/
│   │   ├── engine.py          # Agent loop + 工具处理函数
│   │   ├── state.py           # AgentState + 取消异常
│   │   ├── prompt.py          # 管道式系统提示构建
│   │   └── error_recovery.py  # 错误恢复 + 指数退避
│   ├── team/
│   │   └── team.py            # MessageBus + TeammateManager
│   ├── memory/
│   │   ├── manager.py         # 跨会话记忆（Markdown）
│   │   └── compactor.py       # 三层上下文压缩
│   ├── tasks/
│   │   ├── manager.py         # TaskPool DAG 管理
│   │   └── skill.py           # 技能按需加载
│   ├── infra/
│   │   ├── config.py          # 全局配置
│   │   ├── llm_config.py      # LLM 提供商自动检测
│   │   ├── metrics.py         # MetricsCollector + 持久化
│   │   ├── db.py              # SQLite 持久化
│   │   ├── permission.py      # 权限管道 + Bash 安全验证
│   │   ├── hooks.py           # 事件钩子系统
│   │   ├── background.py      # 后台任务管理
│   │   ├── cron.py            # 定时任务调度
│   │   ├── worktree.py        # Git worktree 隔离
│   │   ├── messages.py        # 消息类型
│   │   └── schemas.py         # Pydantic 模型
│   ├── api/
│   │   └── models.py          # API 响应模型
│   └── tools/
│       ├── file_tools.py      # 文件操作工具
│       ├── bash_tools.py      # Docker 沙盒执行
│       ├── todo_tools.py      # Todo 工具
│       └── memory_tools.py    # 记忆工具
├── frontend/                  # React 18 + TypeScript + Vite
│   └── src/
│       ├── pages/             # ChatPage, MetricsPage, FileBrowserPage, ConfigPage
│       ├── context/           # AppContext 全局状态
│       ├── api/               # API 客户端
│       └── index.css          # 完整 Design System
└── tests/                     # pytest 测试
```

---

## LLM 提供商

在 `.env` 中配置任一提供商的 API Key，系统自动检测（无需手动设置 `LLM_PROVIDER`）：

```bash
# 自动检测优先级：Anthropic > DeepSeek > MiniMax > OpenAI > Ollama
ANTHROPIC_API_KEY=sk-ant-...      # Claude
DEEPSEEK_API_KEY=sk-...           # DeepSeek
MINIMAX_API_KEY=...               # MiniMax
OPENAI_API_KEY=sk-...             # OpenAI / 兼容 API
# OLLAMA_BASE_URL=http://localhost:11434  # 本地模型（无需 Key）
```

也可显式指定：`LLM_PROVIDER=deepseek`

---

## 测试

```bash
pytest                              # 全部 46 个测试
pytest tests/test_file_tools.py     # 单文件
```

---

## License

MIT · [LiHua](https://github.com/MagicalLiHua)
