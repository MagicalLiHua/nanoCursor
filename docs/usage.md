# 使用、配置与故障排查

## 安装后日常使用

在目标目录执行 `nanocursor`。命令的安装位置与工作位置无关；不需要进入 nanoCursor 源码仓库。

也可以执行 `nanocursor PATH` 或 `nanocursor --cwd PATH`，二者不能同时使用。相对路径相对启动目录解释，支持中文、空格和软链接；不会自动创建不存在的目录。目录恰好名为 `setup` 或 `doctor` 时使用 `./setup`、`./doctor` 或 `--cwd`。

工作目录包含 Git 子目录时仍保留该目录。恢复旧 Worktree 需要在启动前选择，默认不恢复；拒绝恢复不删除旧记录。界面目录、文件工具和 Bash 使用实际工作目录，会话及记忆继续关联启动工作区。

支持目标是 macOS Apple Silicon 与 Linux x86_64。WSL2 走 Linux 安装路线，但仍需 WSL 实机验收；原生 Windows、macOS Intel 和 Linux ARM 目前没有完整验证声明。项目最低 Python 3.11，推荐安装命令选择 3.12。

## 首次连接

`nanocursor setup` 和首次交互启动共用设置向导。提供 DeepSeek、Anthropic、OpenAI Responses 与自定义接口选项，模型 ID 由用户按服务商填写，不依赖 Models API 可用。

## 直接使用环境变量

终端已导出完整的一组 Key、Base URL 和模型名时，没有连接配置也能直接运行 `nanocursor`，无需 setup，不会把这一组数据保存到配置或凭据文件。

| 服务 | 必需的三个环境变量 | 协议 |
| --- | --- | --- |
| DeepSeek | `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` | Chat Completions |
| Anthropic | `ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、`ANTHROPIC_MODEL` | Anthropic Messages |
| OpenAI | `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` | 默认 Responses；设置 `OPENAI_PROTOCOL=openai-compat` 可改用 Chat Completions |

三项必须属于同一组。单独一个 Key 不会触发完整连接的自动识别，也不会借用其他组的地址或模型。值为空或只有空白视为缺失。环境变量不完整时会列出缺失项；setup 会用已有的地址、模型名作为输入默认值，Key 已存在时默认选择环境变量来源。

已有文件定义的连接默认优先，保留之前的行为。需要本次明确使用环境变量时：

```bash
nanocursor --env deepseek
nanocursor doctor --env deepseek --json
nanocursor --env deepseek --cwd /path/to/project -p '解释这个项目'
```

`--env` 只选择本次模型连接，项目权限、Hooks/MCP 设置仍需要确认；它不会修改默认连接或覆盖用户配置。它与 `--provider` 互斥。环境连接在界面和诊断中显示为 `env:deepseek` 等名称；配置文件请使用其他名称。

没有文件连接且检测到多组完整环境变量时，交互界面会询问选择；`-p`、远程和 doctor 会提示用 `--env` 指定。缺失或非法的明确来源不会被其他组悄悄替代。损坏的配置文件仍需修复，`--env` 不会跳过配置校验。

程序读取启动进程继承的环境，不自动执行 `.bashrc`、`.zshrc` 或加载 `.env`。变量需要 `export`；若保存在 `.bashrc` 而当前使用 Zsh，可在目标项目目录执行 `bash -ic 'nanocursor --env deepseek'`。

## 协议与连接测试

三种协议：

| 配置值 | 接口 |
| --- | --- |
| `anthropic` | Anthropic Messages |
| `openai` | OpenAI Responses |
| `openai-compat` | Chat Completions 兼容接口，包括 DeepSeek |

Base URL 需按服务商填写，不能把所有服务都机械地加上 `/v1`。向导允许短模型连接测试，也允许离线保存；未测试不代表凭据已验证。测试请求没有工具，不读取项目文件，可能产生少量 API 费用。

取消设置不会保存配置。保存已有配置时创建 `config.backup-*.yaml` 私有备份，保留未知字段；YAML 注释不在重写后保留，可在备份里查看。并发修改时拒绝覆盖，重新运行 setup 即可。

## 凭据

默认目录是 `~/.nanocursor/`。保存 Key 时使用 `credentials.json`，配置中只保存引用。POSIX 新建目录为 `0700`，凭据、配置和备份为 `0600`。该文件仍是明文，同一用户运行的程序通常能读取它；当前 Bash 也继承进程环境。

可选择环境变量方式：

```yaml
schema_version: 2
default_provider: deepseek
providers:
  - name: deepseek
    protocol: openai-compat
    base_url: https://api.deepseek.com
    model: your-model-id
    api_key_env: DEEPSEEK_API_KEY
```

`api_key_env`、`credential_ref`、旧式内联 `api_key` 三者只能选一个。明确指定来源但找不到时不会退到别的 Key。不要把 `${VAR}` 写在 `api_key` 中，使用 `api_key_env`。

已有配置未指定新来源时，继续按原行为：内联 Key 优先，否则 Anthropic 使用 `ANTHROPIC_API_KEY`，两种 OpenAI 协议使用 `OPENAI_API_KEY`。应用不执行 `.bashrc`，也不自动导入 Shell 中的密钥。需要摆脱 Shell 配置时运行 setup 选择保存凭据。

本地无认证服务可选 `auth: none`，不可同时提供密钥来源；此时模型请求不发送认证头。

保存的凭据与 Provider name、协议、完整 Base URL 绑定。改变其中任一项需要通过 setup 配置相应连接，不能在项目内改 URL 后借用原服务的 Key。

## 配置分层

优先级由低到高：内置默认、用户级 `config.yaml`、启动工作区 `.nanocursor/config.yaml`、`.nanocursor/config.local.yaml`、显式 CLI 参数。

项目配置只需提供覆盖字段。例如全局已有 `deepseek` 时：

```yaml
default_provider: deepseek
providers:
  - name: deepseek
    model: another-model-id
permission_mode: default
sandbox:
  enabled: false
approval:
  mode: manual
  provider: null
```

字段出现就覆盖，`false` 和 `default` 有效；嵌套对象逐字段合并。只有明确允许的字段接受 `null`，例如 `approval.provider`。配置文件仅从所选工作区读取，不向父目录搜索；项目指令文件仍保留原来的目录链发现规则。

schema 规则：

- 无 `schema_version` 的旧文件为 v1。setup 更新既有 v1 文件会保留其语义，不自动升到 v2。
- v1 的 Provider 列表整体替换，Hooks 累加；MCP 按 name 合并。
- 新配置使用 v2：连接档案定义在全局，项目只能选择已有档案、修改模型与上下文/输出参数，不能改地址或凭据来源；Hooks 显式列表替换。
- 显式 `mcp_servers: []` 清空；某个 MCP 可以用 `name` 加 `enabled: false` 禁用。
- 不认识的新 schema 拒绝读取，防止旧版本覆盖新格式。升级前仍应保留备份，尤其从本次改造前的版本回退时。

项目配置可影响权限、进程启动或模型连接，首次使用和设置变更后需要确认。信任记录按工作区和配置内容保存到用户目录，不写回业务仓库。非交互运行要提前交互确认；自动化调用者也可显式传 `--trust-project` 批准当次配置。

## 命令与自动化

| 命令 | 行为 |
| --- | --- |
| `nanocursor setup` | 用户级连接设置；需要交互终端 |
| `nanocursor doctor` | 本地只读诊断，不执行 Hooks/MCP 或调用模型 |
| `nanocursor doctor --json` | 稳定 JSON 报告，成功 0、阻塞问题 1、参数错误 2 |
| `nanocursor doctor --network` | 显式短模型请求，有超时、不重试、不跟随重定向 |
| `nanocursor --provider NAME` | 本次选择全局/有效配置中的 Provider |
| `nanocursor --env deepseek` | 本次使用完整 DeepSeek 环境变量连接，也支持 anthropic、openai |
| `nanocursor --mode MODE` | `default`、`acceptEdits`、`plan`、`bypassPermissions` |
| `nanocursor -p 'PROMPT'` | 非交互执行，默认不会自动恢复旧 Worktree |
| `nanocursor -p 'PROMPT' --output-format stream-json` | NDJSON 事件，启动错误也使用结构化事件 |
| `nanocursor --remote` | 原有浏览器入口，监听 `0.0.0.0:18888`，不弹向导 |
| `nanocursor --help` / `--version` | 不需要配置、网络或工作区写权限 |

非交互、远程入口不增加模型自动审批。交互主 Agent 的 `/approval` 保留既有行为，默认 manual。

默认模式下，普通 `WriteFile` / `EditFile` 审批中可选“允许本次，并开启自动编辑（本次运行）”。该选项会批准当前操作，并切换到已有的 `acceptEdits` 模式：后续普通读写按该模式放行，Bash 仍按原有命令审批规则处理。显式 ask/deny 规则和受限路径检查继续有效；因这些限制或计划模式触发的审批不提供此快捷选项。

这个选择只修改当前运行的模式，不保存配置或新增白名单。状态栏显示 `accept-edits`；可用 `/permission mode default` 恢复逐次写入确认，也可以用 Shift+Tab 切换模式。启动时明确选择该模式可执行 `nanocursor --mode acceptEdits`。

配置 `sandbox.enabled: true` 时，后端缺失会停止启动而不会降级为无沙箱执行。doctor 报告对应平台后端；安装好后端或明确关闭配置后再启动。

### 非交互任务的授权和退出状态

`-p` 表示执行一次任务后退出，不进入聊天界面。例如：

```bash
nanocursor -p '阅读 README，概括项目结构'
nanocursor --mode acceptEdits -p '在当前目录创建 hello.py，打印 hello'
```

第二条命令主动授权普通文件编辑，Bash 命令及显式 ask/deny 规则仍使用原有检查。`-p` 遇到需要人工审批的操作时立即停止，提示需要授权并以退出码 1 结束；不会自动代替用户同意，也不会让模型继续寻找替代执行方式。此前已经完成的已授权操作不会自动撤销。

成功完成返回 0；模型请求失败、达到迭代上限、连续无效工具调用等终止错误返回非零状态。可恢复的压缩警告本身不算任务失败，单个工具报错后模型仍可纠正并完成任务。

`--output-format stream-json` 的运行事件以一个最终 `result` 结束，其中包含 `is_error`、`exit_code` 和 `stop_reason`；待授权时为 `permission_required`。工具汇总按 `tool_id` 关联，未执行完的工具状态为 `null`。脚本应检查进程退出码及最终结果，不能只看是否产生了回答文字。

### 权限规则文件

用户级 `~/.nanocursor/permissions.yaml`、项目级 `.nanocursor/permissions.yaml` 和本地 `.nanocursor/permissions.local.yaml` 使用相同格式：

```yaml
- rule: "Bash(git push*)"
  effect: ask
- rule: "ReadFile(*.pem)"
  effect: deny
```

文件不存在表示没有该层规则；已有文件必须是有效的规则列表，空列表写作 `[]`。损坏的 YAML、无效规则或不可读文件会报错并阻止相关执行，不会被当作空规则忽略。启动时校验全部规则层，执行时重新读取以响应变更。子 Agent 和 Team 成员继承父级显式规则，后台无法完成人工确认的 ask 操作不会放行。

## 检查点与回退

`/rewind` 列出本次运行的检查点，每个检查点对应一轮任务完成后的文件和对话状态。`/rewind 1 1` 恢复第一个检查点的代码和对话；末尾选项 `2` 只恢复对话，`3` 只恢复代码。

文件回退覆盖主 Agent 的 `WriteFile` / `EditFile` 跟踪的文件，包括撤销检查点之后新建的文件，或恢复之后才首次编辑的已有文件。Bash、外部编辑器和子 Agent 的任意文件改动不在这项能力的完整覆盖范围内。遇到已跟踪文件的外部修改、备份丢失或损坏时会报错，避免覆盖或误删用户文件。多文件恢复不保证跨文件事务，磁盘写入中途失败会明确提示可能部分恢复。

检查点最多保留 100 个，仅支持当前运行；新建、切换、恢复会话会重置代码检查点。回退后的对话会持久化，之后恢复会话不会重新播放已撤回的对话。正在运行的后台任务需要先完成，才能回退、清空或切换会话。回退对话后，模型自动审批退回人工确认，直到新建会话，避免沿用已撤回分支的授权上下文。

## 大量输出与文件大小

Bash 持续排空输出管道，最多保留 1 MiB 的首尾内容，超出部分丢弃并明确提示；这不会提前终止正在执行的命令。界面与模型收到处理后的预览，较长工具返回内容按现有规则保存到 `.nanocursor/session/tool-results/`，文件不包含此前已被 Bash 丢弃的中间输出。

`ReadFile` 对单文件设置 8 MiB 上限，超过上限会报错，即使只请求部分行也不会整体读入。`Grep` 跳过超过这一上限或无法读取的文件，并限制搜索结果约为 10,000 字符，返回截断或跳过提示。大文件可使用获准的 Bash 命令读取需要的片段。

对话压缩不再删除工具输出目录，保留当前和历史会话中的文件引用。暂未实现磁盘配额或自动回收；确认相关会话不再需要这些内容后，可自行清理对应目录，清理后旧引用将失效。

## 常见问题

| 现象 | 排查 |
| --- | --- |
| 找不到 `nanocursor` | `uv tool update-shell`，重开终端；已能运行时用 doctor 查看命令路径 |
| 修改源码却没有变化 | 普通安装固定代码；开发者需 editable 安装，或重新安装新版本 |
| Bash 可以，Zsh 没密钥 | 运行 setup 保存到应用文件，或为当前 Shell 配置所选环境变量 |
| 选择环境变量后仍缺密钥 | doctor 查看变量名；明确来源不会自动回退到其他服务的 Key |
| 配置无法解析 | 按错误文件/行号修复；损坏配置不会被 setup 悄悄覆盖 |
| Key 存在但调用失败 | `doctor --network` 区分鉴权、模型/端点、限流、DNS/TLS/代理和超时 |
| Models API 不可用 | 直接填写模型 ID，通过真实短请求检查 |
| 只读项目无法启动会话 | 当前会话仍需写项目状态目录；help/version/setup/本地 doctor 不需写项目 |
| 配置文件是软链接 | 读取旧配置可以；安全写入会拒绝重定向文件，手动管理目标或选择普通应用目录 |

诊断日志位于 `~/.nanocursor/logs/debug.log`，按大小轮换，避免每次启动截断上一份日志。不要把日志、会话或凭据放进公开仓库。

`NANOCURSOR_HOME` 可指定独立的绝对用户数据目录，用于测试或多个独立配置；它也覆盖用户权限规则、Skills、Agent 定义和用户记忆位置。业务项目自己的状态仍保留在项目 `.nanocursor/` 下。

## 升级、回退与卸载

仓库安装用户获取选定的新版本源码，在该目录重新执行 README 的安装命令，使用同版本约束文件。Release 用户用选定 wheel 和随附约束安装。固定版本安装不应期待普通 `uv tool upgrade` 越过原版本约束。

回退时安装目标旧版产物及其约束；配置格式也需相容。本次改造以前的旧版没有新凭据解析能力，不能直接读取 v2 凭据引用；应保留原配置备份，回退前恢复对应配置。不要删除当前凭据仓库来修复安装。

`uv tool uninstall nanocursor` 仅卸载程序；用户配置、凭据和项目历史仍保留。彻底清理数据时先检查并备份具体目录，再自行删除。
