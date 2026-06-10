# 14. 启动与配置：别人 clone 后怎么跑起来

最后更新：2026-06-09

## 1. 本章目标

读完本章，你应该能回答：

- nanoCursor 的完整启动步骤是什么？（后端 + 前端 + 可选 Go sidecar）
- `.env` 文件中有哪些关键配置项？每个的含义是什么？
- LLM provider 的自动检测机制是怎么工作的？
- Go sidecar 的 feature flag 和 fallback 机制如何配置？
- 常见启动问题的排查方法。

## 2. 完整启动流程

### 2.1 最小启动（后端 + 前端）

```bash
# 1. 克隆项目
git clone <repo-url>
cd nanoCursor

# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 配置环境变量（选择 LLM provider）
cp .env.example .env
# 编辑 .env，填入至少一个 LLM provider 的 API key

# 4. 启动后端（终端 1）
python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8100

# 5. 启动前端（终端 2）
cd frontend
npm install
npm run dev

# 6. 打开浏览器
# 前端: http://127.0.0.1:5173
# 后端 API 文档: http://127.0.0.1:8100/docs
```

### 2.2 完整启动（含 Go sidecar）

Go sidecar 是可选的，不启动也能正常运行（自动 fallback 到 Python）。

推荐使用项目脚本启动，脚本会进入正确目录并启动对应的 `cmd/nanocursor-*` 程序：

```bash
# 终端 3：Go 项目索引服务，默认端口 50051
./scripts/start_indexer.sh

# 终端 4：Go 文件工具服务，默认端口 50054
./scripts/start_filetools.sh

# 终端 5：Go 命令执行服务，可选，默认端口 50055
./scripts/start_executor.sh

# 终端 6：Go MCP Gateway，可选，默认端口 50056
./scripts/start_mcp.sh
```

如果不用脚本，也要使用当前真实入口，而不是旧文档里的 `cmd/server`：

```bash
(cd go-services/indexer && go run ./cmd/nanocursor-indexer)
(cd go-services/filetools && go run ./cmd/nanocursor-filetools)
(cd go-services/executor && go run ./cmd/nanocursor-executor)
(cd go-services/mcp && go run ./cmd/nanocursor-mcp)
```

### 2.3 启动学习站

```bash
cd learning-site
npm install
npm run dev
# 按终端输出访问。通常主前端占用 5173 后，学习站会落到 http://127.0.0.1:5174
```

## 3. 环境变量配置详解

### 3.1 LLM Provider 配置

```bash
# provider 自动检测优先级: anthropic > deepseek > minimax > openai > ollama
# 也可以显式指定:
# LLM_PROVIDER=deepseek

# 通用参数
LLM_TEMPERATURE=0.2        # 模型温度（0=确定性，1=创造性）
LLM_MAX_TOKENS=4096        # 单次回复最大 token 数

# Anthropic (Claude)
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-3-5-sonnet-latest

# DeepSeek
DEEPSEEK_API_KEY=...
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com

# MiniMax (Anthropic 兼容协议)
MINIMAX_API_KEY=...
MINIMAX_MODEL=MiniMax-M2.7
MINIMAX_BASE_URL=https://api.minimaxi.com/anthropic

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o

# Ollama (本地模型)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder
```

LLM provider 的自动检测逻辑：

```text
if ANTHROPIC_API_KEY is set → use Anthropic
elif DEEPSEEK_API_KEY is set → use DeepSeek
elif MINIMAX_API_KEY is set → use MiniMax
elif OPENAI_API_KEY is set → use OpenAI
elif OLLAMA_BASE_URL is set → use Ollama
else → raise error: no LLM provider configured
```

### 3.2 工作区配置

```bash
# 默认用户工作区。留空时使用 .nanocursor/workspaces/default
# NANOCURSOR_WORKSPACE_DIR=/absolute/path/to/your/project

# 默认工作区集合根目录
# NANOCURSOR_WORKSPACE_ROOT=/absolute/path/to/nanocursor-workspaces
```

工作区决定了 Agent 在哪个目录下操作文件。**不要把 `NANOCURSOR_WORKSPACE_DIR` 设置为 nanoCursor 源码目录**——这会导致 Agent 修改自己的源代码。

### 3.3 Agent 行为配置

```bash
CONTEXT_MAX_TOKENS=8000     # 上下文最大 token 数
MAX_CODER_STEPS=15          # Coder 最大执行步数
MAX_PLANNER_STEPS=10        # Planner 最大步数
LLM_TIMEOUT_SECONDS=30      # LLM 调用超时
MAX_CONCURRENT_RUNS=5       # 最大并发 run 数
```

### 3.4 Go Sidecar Feature Flags

```bash
# 项目索引服务（默认启用）
NANOCURSOR_GO_INDEXER_ENABLED=true
NANOCURSOR_GO_INDEXER_FALLBACK=true
NANOCURSOR_GO_INDEXER_ADDR=localhost:50051
NANOCURSOR_GO_INDEXER_FAILURE_COOLDOWN_SECONDS=10

# 命令执行服务（默认关闭）
# NANOCURSOR_GO_EXECUTOR_ENABLED=false
# NANOCURSOR_GO_EXECUTOR_FALLBACK=true
# NANOCURSOR_GO_EXECUTOR_ADDR=localhost:50055

# 文件工具服务（默认启用）
NANOCURSOR_GO_FILETOOLS_ENABLED=true
NANOCURSOR_GO_FILETOOLS_FALLBACK=true
NANOCURSOR_GO_FILETOOLS_ADDR=localhost:50054

# MCP Gateway（默认关闭）
# NANOCURSOR_GO_MCP_GATEWAY_ENABLED=false
# NANOCURSOR_GO_MCP_GATEWAY_FALLBACK=true
# NANOCURSOR_GO_MCP_GATEWAY_ADDR=localhost:50056
```

每个 Go sidecar 的配置模式都一样：

```text
*_ENABLED:      是否启用 Go 后端
*_FALLBACK:     Go 不可用时是否自动回退 Python
*_ADDR:         Go 服务 gRPC 地址
*_FAILURE_COOLDOWN_SECONDS: 连接失败后的冷却时间
```

注意：`indexer` 和 `filetools` 的 Python feature flag 默认是启用的，但“启用”不等于“Go 进程一定存在”。如果 Go 进程没启动，系统会在第一次调用失败后记录 fallback，并回退到 Python 实现。

### 3.5 Executor 路由配置

```bash
NANOCURSOR_EXECUTOR_ROUTING_MODE=auto        # auto / always / never
NANOCURSOR_EXECUTOR_GO_MIN_TIMEOUT_SECONDS=2  # Go executor 最小超时阈值
NANOCURSOR_EXECUTOR_GO_COMMAND_PATTERNS=pytest,npm test,go test,ruff,mypy
NANOCURSOR_EXECUTOR_PYTHON_COMMAND_PATTERNS=pwd,ls,cat,echo,git status
```

路由模式：
- `auto`：根据命令模式自动选择。
- `always`：所有命令都用 Go executor。
- `never`：所有命令都用 Python subprocess。

### 3.6 沙盒与安全配置

```bash
SANDBOX_MEM_LIMIT=256m            # Docker 沙盒内存限制
SANDBOX_TIMEOUT_SECONDS=60        # 沙盒超时
SANDBOX_CPU_QUOTA_PERCENT=50      # CPU 配额

LOG_LEVEL=INFO                     # 日志级别
# LOG_FILE=logs/nanocursor.log     # 日志文件输出
```

## 4. 运行时数据目录

启动后，nanoCursor 在工作区创建以下目录结构：

```text
<workspace>/
  .nanocursor/
    runs/<thread_id>/           # 运行 session 和事件
      session.json
      events.jsonl
      ephemeral_agents.json
      parallel_proposals.json
      parallel_merge_plan.json
    conversations/<id>/         # 会话持久化
    memory/                     # 记忆存储
      records.json
      selections/               # 记忆选择审计
    thread_workspaces.json      # thread→workspace 索引
  .tasks/                       # 任务持久化
  .backups/                     # 文件备份
  .snapshots/                   # 状态快照
```

## 5. 常见启动问题排查

### 5.1 后端启动失败

```bash
# 问题：ModuleNotFoundError
# 排查：确认在虚拟环境中，确认 pip install -r requirements.txt 成功
pip list | grep fastapi

# 问题：端口 8100 被占用
# 排查：换一个端口或杀掉占用进程
lsof -i :8100
python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8101
```

### 5.2 LLM 调用失败

```bash
# 问题：No LLM provider configured
# 排查：确认 .env 文件存在且至少设置了一个 API key
cat .env | grep API_KEY

# 问题：401 Unauthorized / API key 无效
# 排查：确认 API key 正确，确认有可用额度
curl -H "Authorization: Bearer $DEEPSEEK_API_KEY" https://api.deepseek.com/v1/models

# 问题：超时
# 排查：增大 LLM_TIMEOUT_SECONDS 或检查网络
```

### 5.3 前端访问 404

```bash
# 问题：前端请求后端 404
# 排查：确认后端在 8100 端口运行，确认前端 vite config 的 proxy 配置
curl http://127.0.0.1:8100/api/health
```

### 5.4 Go Sidecar 连接失败

```bash
# 现象：日志中出现 "go_filetools_fallback" 或类似事件
# 这不影响使用，系统自动回退到 Python
# 如果想确认 Go sidecar 是否已启动：
lsof -i :50051  # indexer
lsof -i :50054  # filetools
lsof -i :50055  # executor
lsof -i :50056  # mcp gateway

# 或直接调用后端状态接口：
curl http://127.0.0.1:8100/api/runtime/indexer/status
curl http://127.0.0.1:8100/api/runtime/filetools/status
```

## 6. 开发模式 vs 生产模式

nanoCursor 目前只支持开发模式。生产部署需要注意：

- **安全**：当前没有认证机制，不应暴露在公网。
- **并发**：`MAX_CONCURRENT_RUNS` 应根据服务器资源调整。
- **数据持久化**：确保 `.nanocursor/` 目录有足够的磁盘空间。
- **Go sidecar**：生产环境应确保 Go 服务进程监控和自动重启。

## 7. 面试预备问题

### Q1：clone 项目后需要做哪些配置才能跑起来？

最少只需三步：(1) `pip install -r requirements.txt`，(2) 在 `.env` 中设置一个 LLM provider 的 API key，(3) 分别启动后端和前端。Go sidecar 是可选的，不启动也能正常运行。

### Q2：LLM provider 是怎么被选择的？

自动检测优先级：Anthropic > DeepSeek > MiniMax > OpenAI > Ollama。系统检查哪个 `*_API_KEY` 环境变量已设置，选择第一个可用的。也可以通过 `LLM_PROVIDER` 显式指定。

### Q3：Go sidecar 的 *_FALLBACK 选项有什么作用？

当 Go 服务未启动或调用失败时，`*_FALLBACK=true` 会让系统自动回退到 Python 实现，不会报错。`*_FALLBACK=false` 则会把错误向上抛出。开发时建议保持 true（启动简单），生产环境可以设为 false（强制使用 Go 以获得更好的隔离和性能）。

### Q4：为什么默认工作区不能设置为 nanoCursor 源码目录？

因为 Agent 有文件写入权限。如果把工作区设为源码目录，Agent 可能修改自己的源代码、覆盖配置文件、或删除关键文件。这是安全设计的重要一环：Agent 的操作范围应该由工作区限定。

## 8. 自测题

1. nanoCursor 的最小启动需要哪几步？Go sidecar 不启动会影响使用吗？
2. LLM provider 的自动检测优先级是什么？如果想强制使用 DeepSeek，应该怎么配置？
3. Go sidecar 的 `*_ENABLED`、`*_FALLBACK`、`*_ADDR` 分别控制什么？
4. `NANOCURSOR_EXECUTOR_ROUTING_MODE` 的三种模式（auto/always/never）有什么区别？
5. 运行时数据目录 `.nanocursor/` 下有哪些子目录和关键文件？
6. 后端启动后，如何验证它是否正常运行？
7. 如果 Go filetools 连接失败，日志中会出现什么事件？系统会怎么处理？

## 9. 动手练习

1. **从零启动验证**：clone 项目到一个新目录，按本章步骤从零配置 `.env`、安装依赖、启动后端和前端。记录每一步遇到的问题和解决方法。
2. **切换 LLM provider**：尝试至少两种 LLM provider（如 DeepSeek + Ollama 或 Anthropic + DeepSeek），对比同一任务在不同模型下的响应差异。
3. **修改 feature flag 观察行为变化**：将 `NANOCURSOR_GO_FILETOOLS_ENABLED` 从 true 改为 false，重启后端。观察文件工具的行为变化——前端 RunInspector 中的 filetools status 是否有变化。
4. **检查运行时数据目录**：启动项目并执行一次任务后，用 `tree .nanocursor/` 或 `find .nanocursor/` 查看完整的数据目录结构，对照本章第 4 节的表格确认每个文件都已生成。
