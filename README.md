# nanoCursor

nanoCursor 是一个用 Python 实现的终端 Coding Agent。它能读取和修改仓库、执行命令、调用子 Agent，并在长任务中维护上下文和会话状态。项目同时包含 AgentEval：一套用于冻结真实代码任务、隔离运行环境、记录执行过程并自动验收补丁的评测工具。

这个仓库不是只展示一次成功 Demo。为了判断 Agent 到底完成了任务，项目把“运行 Agent”和“验收结果”拆开：Agent 在隔离容器里工作，外部 grader 再检查目标测试、回归测试和受保护文件。实验保留每次运行的通过状态、Turns、Token、工具调用和耗时，用来分析同一个模型为什么会走出不同的执行路径。

## 为什么做这个项目

Coding Agent 的进程结束，并不能说明代码已经修好；只看最终回答，也无法区分模型问题、Agent 编排问题、环境问题和判分问题。nanoCursor 因此围绕三个具体问题展开：

1. Agent 能不能在真实仓库 Issue 上完成代码修改，而不是只解独立代码题；
2. 模型和任务不变时，不同 harness 是否会明显改变功能结果与执行成本；
3. 失败发生后，能不能根据 grader、运行指标和轨迹把原因定位到任务理解、模型行为、Agent 编排或评测器。

## 项目由什么组成

| 模块 | 作用 | 主要实现 |
|---|---|---|
| nanoCursor Agent | 完成仓库检索、修改、执行与多轮推理 | Python、流式模型接口、工具循环、上下文压缩、Hook、权限与会话恢复 |
| 评测适配层 | 让 nanoCursor 和参考 harness 接受一致的任务与工具约束 | 统一 system prompt、预算、工具合同、事件字段和终止状态 |
| AgentEval | 组织任务并在 Agent 外部判定代码是否通过 | TypeScript、冻结 manifest、Docker 沙箱、目标/回归测试、轨迹与结果审计 |
| 分析工具 | 从运行产物生成可核验的统计结果 | 72 条脱敏记录、JSON 汇总、Bad Case 归因和标准库 SVG 绘图脚本 |

![受控评测流程](evaluation/results/figures/evaluation-pipeline.svg)

## 实验怎么做

从 SWE-bench 中选取 12 个 Python 真实仓库 Issue，覆盖 9 个开源项目。nanoCursor 与 Pi 参考 harness 使用同一 DeepSeek 模型、同一任务描述、96 turn 上限、20 分钟墙钟预算、相同容器资源和同一套确定性 grader。每题、每套 harness 各运行 3 次，共记录 72 次运行。

评测保留两个结果口径：

- `content_passed`：代码通过目标测试、回归测试及保护检查；
- `protocol_completed`：Agent 在预算内正常收尾并产出最终结果。

二者不能混为一谈：其中一次 nanoCursor 运行已经通过全部代码验收，但在第 96 turn 触顶，没有生成最终回答。如果只看运行状态，它会被误判成代码修复失败。

## 主要结果

| 指标 | nanoCursor | Pi 参考组 |
|---|---:|---:|
| 内容验收通过 | 32/36（88.9%） | 33/36（91.7%） |
| 正常完成协议 | 31/36（86.1%） | 33/36（91.7%） |
| 总 Token | 1,760,888 | 1,926,021 |
| 工具调用 | 1,946 | 1,982 |
| 运行总耗时 | 6,499.2 s | 7,341.7 s |

两套 harness 在名义 trial 上的功能结果一致 35/36（97.2%）。现有样本没有显示 nanoCursor 存在结构性的功能退化；主要 Bad Case 来自任务语义边界、模型验证范围和结束预算管理。该结果是受控实验的描述性结论，不是 SWE-bench 官方榜单成绩，也不用于声称两套 harness 等价。

![逐任务通过次数](evaluation/results/figures/pass-rate-by-task.svg)

## 从 72 次运行中看到了什么

- **总体通过率接近，但运行路径并不相同。** nanoCursor 与 Pi 的功能结果在 35/36 个名义配对上相同，逐任务 Token 差异却从 `-34.8%` 到 `+77.5%`。只比较最终通过率会漏掉执行过程的差别。
- **同题重复运行仍有明显波动。** 例如 nanoCursor 在 sklearn `13328` 的三次 Token 最大值是最小值的 `2.34` 倍；Pi 在 astropy `12907` 上为 `2.22` 倍。单跑一次不足以代表稳定成本。
- **双方共同失败比单边失败更值得检查任务语义。** Django `11141` 在 nanoCursor、Pi 以及额外模型对照中都遗漏同一个空 namespace 边界，共 9 次出现相同模式；证据不支持把它简单归为某个 harness 的故障。
- **成本总量不能直接写成效率优势。** nanoCursor 总 Token 少 `8.6%`、总耗时少 `11.5%`，但逐题并非同方向，因此这里只报告当前样本的描述性统计。
- **功能结果和协议结果必须分开。** pytest 的一次运行完成了正确补丁却耗尽 turn budget，这暴露的是收尾与预算管理问题，不是代码能力失败。

![逐任务执行曲线](evaluation/results/figures/task-metric-profiles.svg)

![36 次运行 Token 折线](evaluation/results/figures/trial-token-lines.svg)

完整的 12 张图表、逐次数据和复算方法见 [实验结果与图表](evaluation/results/README.md)，具体失败链见 [Bad Case 归因报告](evaluation/results/CAUSAL_ANALYSIS.md)。

## Agent 能力

- 终端交互界面、流式模型响应和会话恢复；
- Bash、文件读写、搜索、编辑、网页内容读取等工具调用；
- 子 Agent、团队协作和共享任务状态；
- MCP 工具发现与调用、Skills 加载、Hook 与权限控制；
- 上下文压缩、缓存、工作树隔离和执行失败恢复；
- 面向外部 grader 的无侵入评测适配。

## 快速开始

要求 Python 3.11 或更高版本，推荐使用 [uv](https://docs.astral.sh/uv/)。

```bash
git clone git@github.com:MagicalLiHua/nanoCursor.git
cd nanoCursor
uv sync
mkdir -p .nanocursor
cp .nanocursor/config.yaml.example .nanocursor/config.yaml
```

通过环境变量提供模型密钥，不要把密钥写入仓库：

```bash
export OPENAI_API_KEY="your-api-key"
uv run nanocursor
```

配置样例使用 OpenAI 兼容接口，可在 `.nanocursor/config.yaml` 中调整 `base_url`、模型名称和上下文参数。

## 运行测试

```bash
uv run pytest -q
```

当前 Python 测试基线为 573 passed、1 skipped。测试覆盖核心 Agent 循环、工具执行、Hook、权限、团队协作、MCP、Skills、上下文和评测适配器。

## AgentEval

AgentEval 位于 `evaluation/agent-eval-lab/`，使用 TypeScript 实现任务清单冻结、Docker 沙箱、运行编排、轨迹记录和确定性验收。

```bash
cd evaluation/agent-eval-lab
npm install
npm run check
npm test
npm run cli -- issue-list
```

真实模型实验需要自行提供模型 API 和可用 Docker 环境。公开仓库不包含完整模型对话、原始轨迹或密钥，只提供核验统计结论所需的脱敏字段。

## 仓库结构

```text
nanocursor/                         Python Coding Agent
tests/                              Agent 单元与集成测试
evaluation/agent-eval-lab/          TypeScript 评测工具
evaluation/results/data/            脱敏逐次结果与审计证据
evaluation/results/manifests/       冻结任务清单
evaluation/results/figures/         可复算 SVG 图表
evaluation/analysis/                数据导出与绘图脚本
```

## 版本说明

`v3.0.0` 是当前 Agent + Evaluation 版本。旧版工程保留在 `legacy-v2.0.0` 标签中，主分支不再维护旧版架构。

## License

项目代码使用 [MIT License](LICENSE)。第三方组件及其许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
