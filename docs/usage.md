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

## 终端界面与状态

输入区下方常驻两行状态。第一行显示模型、推理设置和上下文占用；第二行显示权限模式、审批、MCP、工具与累计 Token。窗口变窄时收起次要字段，完整信息仍可通过 **F2**、点击状态栏或 `/status` 查看。状态展示读取本地运行数据，不新增模型请求。

| 显示 | 含义 |
| --- | --- |
| 上下文 `~38.4k/200k · 19%` | 当前对话占用的近似值 / 解析出的上下文窗口；正占用不足 1% 时显示 `<1%` |
| Token `↓96.2k ↑8.1k` | 主 Agent 当前运行累计输入 / 输出；`/clear` 或重新启动后清零 |
| MCP `2/3` | 2 个已连接 / 3 个已配置；包含连接失败的配置 |
| 工具 `10` | 已启用工具数；详情区分内置、MCP，以及已提供给模型的工具 |
| `自动审批` / `审查中` / `待确认` | 自动审批已启用 / 正在调用审批模型 / 等待人工选择 |

上下文优先使用最近一次 API 返回的输入、缓存与输出用量作为锚点，新增消息用字符估算；初次调用之前或压缩之后暂按消息字符估算，尚未计入系统提示和工具定义。窗口上限按显式配置、服务商元数据、内置映射、通用回退的顺序解析，通用回退为 **200,000**，详情会标明来源。回退值是本工具的预算设置，不会改变服务商的模型容量；需要不同上限时，在对应连接配置里设置 `context_window`。

累计 Token 的输入不含缓存读取与缓存创建，累计也不包含审批、摘要、压缩、记忆整理或子 Agent 的独立模型调用；它不是完整账单。后台记忆整理、可选召回筛选和独立 Skill 的用量分别在 `/memory consolidate status`、`/memory recall status` 和 Skill 结果中显示。恢复历史对话不会恢复之前运行的累计用量。延迟加载的工具可以已启用，但尚未提供给模型，因此详情里的“当前可见”数量可能更少。

用量在每次模型响应结束后更新。若服务没有返回用量，显示 `Token —`；有部分请求缺失时，已有累计数末尾显示 `*`，F2 详情说明缺失次数，不以字符估算冒充 API 用量。兼容 DeepSeek 将用量附在最后一个正文数据包中的格式，也支持独立用量包。

推理只显示实际发送的配置：Anthropic 客户端启用 thinking 时显示“思考开启”，其余显示“默认”。目前没有统一的模型努力档位设置。

**Esc** 或 **F2** 关闭状态详情，不会取消后台任务。正常聊天时 **Esc** 仍取消当前任务，**Shift+Tab** 切换权限模式，**Ctrl+O** 展开或折叠工具输出。成功的连续只读工具会合并为可点击摘要，错误保持可见并默认展开；较长输出展示有上限的预览。

### 引用文件

在普通聊天输入中键入 `@`，会列出当前工作目录中的文件和子目录。继续输入路径前缀筛选，用上下键选择，按 **Tab**、**Enter** 或点击候选补全；选中目录会继续列出其子项。选择文件时只替换当前引用，保留前后文字，再按 **Enter** 才发送整条消息。**Esc** 可关闭候选菜单。

也可以直接输入完整路径，例如：

```text
解释 @README.md 的安装步骤
比较 @src/main.py 和 @tests/test_main.py
阅读 @"docs/设计说明 v2.md" 并总结
```

路径相对于当前工作目录解析；含空格或括号等字符的路径会自动加双引号。发送后会将所引用的 UTF-8 文本加入模型上下文，每个文件最多附加前 **10 KiB**，超出时明确标注截断。目录本身不会批量附加内容，候选列表按所输入的目录逐层查找，不做全仓库模糊搜索。Skills 展开内容和内部通知中的 `@` 不会自行触发文件附加。

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

Plan 模式只对本次分配的确切计划文件提供写入例外，其他同名文件或路径中包含 `plans` 的文件不因此获准。显式 deny/ask 规则和目录边界仍优先。

## 会话、取消与压缩

`/session resume` 显示最近的可恢复会话及完整 ID；随后输入 `/session resume 1`，编号对应最后一次显示的候选列表。没有先查看列表、编号超出范围或会话已删除时会明确提示，不会恢复其他会话。也可以始终使用 `/session resume <完整 ID>`。

模型生成、执行工具、等待审批、取消收尾或手动压缩期间，清空/切换会话、回退、再次压缩、修改工作目录等操作不会执行，命令保留在输入框中。先按 **Esc**，等待停止与保存完成，再重试。普通聊天新输入仍会先取消并等待旧任务，再开始新任务；重复按 Esc 不会打断取消清理。

`/status`、F2、列表与任务管理仍可使用。后台任务用 `/tasks` 查看、`/tasks cancel <任务 ID>` 请求停止；等待它结束后再切换。已完成的回复与后台结果属于创建它们的会话，不会注入后来打开的会话。正常退出会等待实际任务和 Hook 进程停止；若未能停止或保存，会报告原因。

`/compact` 使用当前上下文大小判断是否需要压缩，最低门槛为 5,000 tokens，与累计用量无关；压缩不会清零累计 Token。空白、截断、包含工具调用或结构异常的摘要不会替换原历史。会话文件有不完整尾部时，恢复会保留原始记录并写入恢复边界，后续新增对话仍可再次恢复。

模型返回错误或在正常终态前断流时，本轮显示失败，已完成工具操作不会自动重做。Responses 的 `max_output_tokens` 与其他协议一样使用连接配置中的输出上限；遇到纯文本输出达到上限时连续最多尝试三次续写，不会自动将单次上限提高到 64K。这个值限制每次请求，续写可能增加整轮累计用量。

## Hook 配置与迁移

命令 Hook 现在把 `command` 作为固定 shell 程序执行。工具参数通过数据通道传入，不再先替换进 shell 源码。三个环境变量分别为 `NANOCURSOR_HOOK_EVENT`、`NANOCURSOR_HOOK_TOOL_NAME` 和 `NANOCURSOR_HOOK_FILE_PATH`；像普通 shell 变量一样使用双引号：

```yaml
hooks:
  - id: show-edited-file
    event: post_tool_use
    if: 'tool == "WriteFile"'
    action:
      type: command
      command: 'printf "%s\n" "$NANOCURSOR_HOOK_FILE_PATH"'
      timeout: 30
```

旧命令中的 `$EVENT`、`$TOOL_NAME`、`$FILE_PATH`、`$MESSAGE`、`$ERROR`、`$TOOL_ARGS.*` 模板会在加载时报告迁移错误，包含配置来源和 Hook 标识。应用不会自动修改配置或静默跳过拦截 Hook。正常 `$PATH`、固定管道等 shell 语法继续可用；`type: agent` 尚未实现，明确报不支持。

长文本与完整参数使用 `input: context-json`：

```yaml
hooks:
  - id: inspect-tool
    event: pre_tool_use
    action:
      type: command
      command: 'python3 /absolute/path/to/inspect_hook.py'
      input: context-json
      timeout: 30
```

脚本从 stdin 读取一个 JSON 对象，字段为 `schema_version: 1`、`event`、`tool_name`、`tool_args`、`file_path`、`message`、`error`。省略 `input` 时不注入 JSON。Prompt/HTTP Hook 的原有文本展开保持不变。命令超时或任务取消会终止所属进程组；自行编写 `eval` 仍会把数据解释为程序，应按脚本本身的语义审查。

## 记忆与后台整理

`/memory list` 查看有效记忆，`/memory edit` 显示实际目录。默认项目记忆位于 `.nanocursor/memory/`，用户记忆位于 `~/.nanocursor/memory/`。自动写入校验文件名、目录和符号链接；合法旧文件名可继续读取，不会自动重命名。

### 首答前召回

默认 `local`：先在本地 Markdown 中检索，再发起主模型的第一次请求；纯文本回答也能使用记忆。英文和代码标识符按词匹配，中文使用二字片段，标题与描述优先，最多取 5 篇相关片段。问候或无关问题不强行填入记忆。当前管理的记忆内容合计不超过配置预算、模型窗口的 5% 和实际剩余输入空间三者中的最小值；中文采用更保守的估算，同时限制 UTF-8 字节数。索引和说明也计入预算。

```text
/memory recall status
/memory recall off
/memory recall local
/memory recall model
```

这些切换只作用于本次应用运行。`off` 关闭动态正文召回，仍保留有界的启动索引，不关闭记忆提取和后台整理。`model` 会将有限候选片段发送给当前连接的独立客户端筛选；默认 2 秒、一次请求、无工具，失败回退本地结果。合法空选择保持为空。它不会自动读取模型返回的任意路径。默认 `local` 和 `off` 没有额外的召回模型请求。

用户级配置示例：

```yaml
memory:
  recall:
    mode: local
    max_context_tokens: 4096
    model_timeout_ms: 2000
  consolidation:
    enabled: false
```

`max_context_tokens` 允许 128–16384 的整数，`model_timeout_ms` 允许 100–10000 的整数。项目可以使用 `local/off`，但不能在用户配置未开启时擅自启用 `model`。显式指定的独立配置文件也可以设置这些选项。

本地读取的前台等待目标为 300ms；超时跳过本轮未验证内容，不向已开始的回答补入迟到结果。每个作用域最多为 200 篇建立工作索引，每篇读取前 16 KiB，缓存文本最多 8 MiB；最终候选另做安全全文读取与版本验证。超过扫描范围时状态显示“部分索引”。这不是全库语义检索：无词面交集的跨语言表达，以及超长文档后半部分的独有关键词，可能漏召回；可选模型模式也只能筛选已覆盖的候选。

`/memory recall status` 和 F2 详情显示模式、命中、新增/替换、去重、估算占用、耗时、回退原因和独立筛选用量；不逐轮向聊天区添加技术面板。正文或索引变化后替换当前管理块，重新计算上下文用量；恢复会话保留来源信息，压缩掉的内容可以再次召回。`/memory clear` 同时清除当前记忆块并使旧召回失效。

旧会话中没有来源字段的文字继续作为普通历史读取，不猜测删除；因此旧历史里可能保留此前注入的未标记记忆。新预算约束宿主管理的记忆块，无法追溯约束模型在普通回答或摘要中引用过的内容。恢复到旧版程序会丢失新来源信息，不能保证相同的去重行为。

### 后台整理

后台整理默认关闭，仅在主 Agent 交互界面调度。普通子 Agent、Skill fork、`-p`、评测及 Remote 不会自动启动它。以下命令只改变本次运行，不改配置文件：

```text
/memory consolidate on
/memory consolidate status
/memory consolidate off
```

如需每次启动都开启，在用户级 `~/.nanocursor/config.yaml` 中添加：

```yaml
memory:
  consolidation:
    enabled: true
```

项目配置可以关闭整理；在用户配置尚未开启时，项目单方面设为 `true` 会报错。用户明确指定的独立配置文件也可设置此字段。

开启时及前台任务完成后的空闲检查会尝试触发整理，默认要求距上次成功检查至少 24 小时，并有至少 5 个新增或更新的会话；常规检查间隔至少 10 分钟。首次没有成功记录时按首次检查处理，仍需满足会话门槛和已有记忆条件。因此开启后显示“等待门控”是正常状态。未完成的候选保留到后续检查，不要求再等一整天或增加五个会话。

整理复用当前连接，每个作用域每次最多请求一轮提案，超时 60 秒，输出不超过连接上限且至多 4,096 tokens，输入也有大小限制。模型只能给出结构化合并建议；程序校验来源、作用域和版本，再保存新正文并发布索引，不给整理模型 Bash 或文件工具。项目整理可参考本项目有限会话片段；用户级整理只重组已有用户记忆，不把项目会话自动提升为跨项目事实。状态命令显示进度、冲突或失败原因，以及本进程内独立的后台用量。

关闭开关、清记忆、会话切换或退出会取消并等待整理。发布前失败、取消或内容冲突会保留旧有效集合；发布已经完成则保留提交结果并停止后续工作。原始正文不会被整理覆盖或删除。若显示“整理已发布，状态记录失败”，索引已经生效，程序会根据发布记录恢复状态，不应手工重新执行同一提案。

### 记忆索引与恢复旧内容

第一次实际整理前，会把现有安全的记忆文件登记到 `MEMORY.md`，保留人工说明，并加入 `<!-- nanocursor-memory-index: 1 -->` 标识。原索引备份位于相邻 `memory-history/index-before-migration-*.txt`，不参与召回。

迁移后，`MEMORY.md` 中的链接决定有效记忆集合。被合并的旧正文仍留在原目录，但不再参与召回、列表或自动去重；关闭整理也不会重新激活它们。手工新增文件必须同时向索引添加一条有效链接，例如 `- [项目约定](project-conventions.md) — 简短说明`。索引损坏或链接指向缺失文件时应先修复，程序不会扫描整个目录替你重新激活旧内容。

恢复特定旧记忆时，先关闭整理并退出使用该目录的 nanoCursor，备份当前 `MEMORY.md`，再阅读保留的旧正文。向当前索引补回该文件的相对链接；若它替代某个合并版本，同时移除对应合并版本的链接。保留索引版本标识与其他条目，目标必须是目录内真实存在的普通 Markdown 文件，且同一文件只登记一次。重启后用 `/memory list` 核对。迁移前备份供对照使用，不要直接覆盖新版索引，否则可能重新激活所有已退役正文。`/memory clear` 会删除自动记忆正文，不能用来回退一次整理。

## MCP 管理与工具列表

在主 Agent 的交互界面，可以直接让模型使用 `ManageMCP` 配置并启动、重新启动或关闭服务。例如：

```text
请使用 ManageMCP 配置并启动 context7，地址是 https://mcp.context7.com/mcp。
请关闭 context7，并保持下次启动时关闭。
请重新启动已保存的 context7。
```

`ManageMCP` 的参数如下；它是模型调用的工具，不是终端命令：

```json
{"action":"start","name":"context7","config":{"url":"https://mcp.context7.com/mcp"}}
{"action":"start","name":"local-example","config":{"command":"/absolute/path/to/server","args":["--stdio"]}}
{"action":"stop","name":"context7"}
{"action":"start","name":"context7"}
{"action":"list"}
```

- `start`：首次添加或替换时提供完整 `config`；已保存的服务可只提供名称。启动后立即注册工具，无需重启 nanoCursor。模型通过 `ToolSearch` 发现工具后使用。
- `stop`：立即撤下主 Agent 的相关工具、断开连接，并将用户配置中的 `enabled` 保存为 `false`。旧工具引用不能自行重连。远程 HTTP 服务只断开本客户端，不关闭远端主机；其他已经运行的 nanoCursor 实例也不会自动断开。
- `list`：读取本地配置和连接状态，不发起网络探测。

设置保存在用户级 `~/.nanocursor/config.yaml`（受 `NANOCURSOR_HOME` 影响），保留其他 MCP 条目、模型连接与设置。启动前先保存为关闭，连接及工具发现成功后才保存为开启；失败或取消时不会留下自动启动的半成品配置。文件损坏、配置在审批期间被修改、项目存在同名覆盖时会报错，不覆盖这些内容。项目显式清空 MCP 列表时也不能通过此工具启动；先手动调整项目配置。

默认与 `acceptEdits` 模式下，启动/关闭走人工权限确认，并展示目标和配置；显式 allow/deny/ask 规则与用户主动选择的 `bypassPermissions` 仍有效。管理操作不进入 Bash 自动审批，不提供“以后不再询问”的快捷选项。Plan 模式只允许查看。工具仅注册到主交互入口，普通子 Agent、独立 Skill、Team、`-p` 和 Remote 不提供 MCP 管理能力。

本地服务的 `command` 是可执行文件，参数放在 `args`，不会额外套一层 shell。使用 `npx`/`uvx` 等程序时可能下载并执行第三方软件；其运行目录取本次启动时的工作目录，本地 MCP 进程不使用 Bash 工具的 OS 沙箱。远程服务使用 Streamable HTTP `url`。凭据用 `env` 或 `headers` 显式配置，推荐引用环境变量，例如 `Authorization: "Bearer ${CONTEXT7_API_KEY}"`。配置与启停不代替服务自身的依赖安装或 OAuth 登录。

在聊天输入框内查看状态：

```text
/mcp
/tools
/tools enabled
/tools disabled
```

`/tools` 显示当前注册工具的名称、来源、说明和状态：已禁用、已启用但待发现、已提供给模型。未连接的 MCP 工具会另行标注；已经关闭并撤下的工具可通过 `/mcp` 查看所属服务。已启用不代表免审批。命令只读取本地状态，不调用模型，运行期间也可以查看。

## 普通子 Agent、Skills 与实验 Team

普通子 Agent 可直接使用，无需启用 Team。Skill 的 fork 模式使用调用时当前会话的独立文本快照：`none` 不继承，`recent` 取最近五条符合条件的文本消息，`full` 汇总全部符合条件消息、每条最多 200 字符。`full` 不是完整工具协议历史的克隆；清空或恢复会话后调用会使用新的当前会话。

### Skill 配置与独立执行

项目 Skill 放在 `.nanocursor/skills/`，用户 Skill 放在 `~/.nanocursor/skills/`，项目同名定义优先。支持单文件 Markdown、目录 `SKILL.md`，以及 `skill.yaml + prompt.md`；三者使用相同校验。YAML 目录格式继续支持从目录名、正文推导名称和描述。`/skill list` 查看可用项和错误，`/skill info <name>` 查看声明及当前有效配置，`/skill reload` 重新加载。

普通 inline Skill 只需名称、描述和正文：

```markdown
---
name: explain-code
description: 解释指定代码
---
阅读相关代码，解释执行流程和关键取舍。
任务：$ARGUMENTS
```

调用 `/explain-code 文件路径`，或由模型使用 `LoadSkill(name="explain-code", args="文件路径")`。inline 沿用主模型、工具和权限；其 SOP 保持激活直到现有会话清理，不建立临时授权栈。`context` 省略或使用旧值 `full` 均可；inline 不支持 `provider/tools`、其他 `model` 值或 `context: none/recent`。

独立检查的示例（需先配置名为 `review` 的连接）：

```markdown
---
name: review-changes
description: 独立检查代码改动
mode: fork
provider: review
context: recent
tools: [ReadFile, Grep, Glob]
---
阅读相关代码，只报告有源码证据的问题。
任务：$ARGUMENTS
```

fork 的规则如下：

- 不填 `provider/model`，或 `model: inherit`，使用主会话当前连接的独立客户端。`provider` 必须精确匹配已配置连接名；`model` 必须是实际模型 ID，优先匹配当前连接，否则要求唯一匹配。歧义、未配置、不受信任或缺少凭据都会报错，不回退主模型。目标连接的协议、窗口、输出限制和 thinking 设置一起生效。
- 不填 `tools` 时，只继承父级启用的 ReadFile、WriteFile、EditFile、Bash、Grep、Glob、已连接 MCP 工具和 ToolSearch。`tools: []` 完全不给工具；显式列表只能缩小范围，使用实际注册名，如 `mcp_server_tool`。Agent、Team、LoadSkill、InstallSkill、工作树/Plan/任务控制工具不支持进入 fork。无法识别或已禁用的名字报错。
- 工具声明不授予权限。明确 deny/ask、路径约束、Plan 模式和 OS 沙箱继续生效；需要审批的操作返回“请在主会话授权”，不会调用主会话的自动审批。父级模式、规则、目录或已捕获 MCP 连接变化后，旧执行范围不能继续获得新权限。`tools: [Bash]` 仍能执行 Bash 原本允许的操作；要做只读审查应选择文件读取/搜索工具。Hook 自身行为由用户 Hook 配置决定。
- `none/recent/full` 的上下文投影最终受目标窗口和 8192 估算 Token 上限约束；长投影会标记截断，不复制孤立工具协议块。完整 SOP 和参数放不下时明确失败，不暗中删掉任务或先调用摘要模型。
- `/<skill>` 托管独立后台运行，结果显示成功/错误、实际连接与独立用量；下一安全边界会把有界结果写入原会话，便于继续提问。这是任务结果，不是新的用户授权。`LoadSkill` 使用同一执行器并等待 fork 结果。主状态栏保持主模型。Esc 停止独立 Skill；清空、切会话、rewind 和退出会取消并等待，迟到结果不能进入新会话。

配置修改对下一次调用生效，已开始的调用保留捕获的定义。源文件损坏、删除或配置无效时停止执行旧缓存，并给出来源诊断；项目同名定义损坏时不偷偷执行用户层的另一份内容。`license/compatibility/metadata` 仅作描述性信息，其他未知字段报错；自定义信息放入 `metadata`。

迁移旧 Skill 时，删除 inline 中过去被忽略的模型/工具覆盖，或改成 fork。`allowed-tools` 的预批准语义不受支持；若只想限制能力，使用 fork 的 `tools`，免审批规则仍须由用户的权限设置提供。不实现嵌套 Skill 授权栈。

### 实验 Team

Team 需要显式配置：

```yaml
enable_teams: true
```

默认关闭时不注册创建工具，`Agent(team_name=...)` 也会在产生工作树之前拒绝；`enable_fork` 或协调模式不会隐式打开 Team。启用后它仍是实验功能，负责人通信、成员持续上下文和续聊尚未完整闭合。

`TeamDelete` 的含义是停止并关闭 Team，保留所有队友工作树、分支和成果，包括干净工作树与仅本地存在的提交。返回信息列出保留路径，恢复记录在 `~/.nanocursor/teams/<名称>/config.json`。退出采用同样的保留策略；取消超时或历史进程无法确认停止时显示 `closing`，不会宣称已经关闭。关闭中的 Team 不接受新成员。现阶段不自动回收这些目录，确认不再需要成果后再自行管理。

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
