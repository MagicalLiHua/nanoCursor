# nanoCursor

nanoCursor 是一个面向真实代码仓库的终端 Coding Agent。它能够根据自然语言需求检索和阅读代码、修改文件、执行测试，并结合运行结果继续调整实现。项目使用 Python 开发，包含流式模型调用、工具执行、上下文管理、会话恢复、权限控制和多 Agent 协作等功能。

仓库同时提供独立的评测工具 AgentEval。AgentEval 使用 Docker 固定代码版本和运行环境，在 Agent 结束后执行目标测试、回归测试和受保护文件检查，并记录 Turns、Token、工具调用与耗时。Agent 负责完成代码任务，AgentEval 负责运行编排和结果验收，两者相互独立，Agent 无法读取隐藏验收结果。

![评测流程](evaluation/results/figures/evaluation-pipeline.svg)

## 项目组成

| 模块 | 实现 | 主要功能 |
|---|---|---|
| nanoCursor | Python | 代码检索、文件编辑、命令与测试执行、多轮工具调用 |
| AgentEval | TypeScript | 任务管理、Docker 沙箱、运行编排、轨迹记录和自动验收 |

nanoCursor 目前提供以下能力：

- Bash、代码搜索、文件读写和代码编辑工具；
- 流式多轮模型调用、上下文压缩和会话恢复；
- Hook、权限检查、MCP、Skills 和多 Agent 协作；
- 交互式终端与批量评测两种运行方式。

公开仓库还包含 72 次脱敏实验记录、Bad Case 分析和可重新生成的统计图表。

## 评测设计

评测任务选自 SWE-bench，包含 9 个 Python 开源仓库的 12 个真实 Issue，涉及 Astropy、Django、Matplotlib、pytest、Requests、scikit-learn、Sphinx、SymPy 和 Xarray。

nanoCursor 与 Pi 参考组使用相同的 DeepSeek 模型、任务描述、system prompt、工具权限和运行预算，并在相同的 Docker 环境中接受同一套验收。每个任务分别运行 3 次，共得到 72 次运行记录：

```text
12 个任务 × 2 套 Agent harness × 3 次 = 72 次运行
```

评测区分两类结果：

- **内容验收通过**：代码同时通过 Issue 目标测试和原仓库回归测试；
- **正常完成协议**：Agent 在限制内结束运行并返回最终结果。

两项指标分开记录，是为了区分代码结果与运行终止状态。例如，某次 nanoCursor 运行已经通过全部代码测试，但在第 96 turn 达到预算上限，未能输出最终总结。该次运行计为内容通过、协议未完成。

## 评测结果

| 指标 | nanoCursor | Pi 参考组 |
|---|---:|---:|
| 内容验收通过 | 32/36（88.9%） | 33/36（91.7%） |
| 正常完成协议 | 31/36（86.1%） | 33/36（91.7%） |
| 总 Token | 1,760,888 | 1,926,021 |
| 工具调用 | 1,946 | 1,982 |
| 运行总耗时 | 6,499.2 s | 7,341.7 s |

按相同任务和重复序号对齐后，两套 Agent 有 35/36 次得到相同的内容验收结果。12 个任务中，10 个任务在两套 Agent 上均为 3/3 通过；Django `11141` 均为 0/3，Sphinx `10449` 是唯一出现通过次数差异的任务，nanoCursor 为 2/3，Pi 为 3/3。

![逐任务通过次数](evaluation/results/figures/task-pass-counts.svg)

这些结果仅描述当前任务集合和实验配置，不构成两套 Agent 等价或具有普遍性能差异的证明。

## 结果分析

### 同一任务的运行波动

在模型、任务和预算保持一致的情况下，不同运行仍可能选择不同的搜索与验证路径。nanoCursor 在 sklearn `13328` 上三次运行的最大 Token 消耗是最小值的 `2.34` 倍；Pi 在 astropy `12907` 上的对应比例为 `2.22`。因此，项目保留每个任务的三次独立运行，没有使用单次结果代表整体表现。

![每次运行的 Token 变化](evaluation/results/figures/trial-token-lines.svg)

### 过程成本差异

按任务统计，nanoCursor 相对 Pi 的 Token 差异范围为 `-34.8%` 至 `+77.5%`。nanoCursor 的总 Token 和总耗时分别低 `8.6%` 和 `11.5%`，但不同任务上的差异方向并不一致，因此不据此推导普遍的效率优势。

![各任务的执行指标](evaluation/results/figures/task-metric-profiles.svg)

### 共同失败

Django `11141` 在 nanoCursor、Pi 及另一组模型实验中均遗漏相同的空 namespace 边界，9 次运行表现为同类失败。结合代码 Diff 和执行轨迹，该问题更接近模型对任务语义的理解偏差，而非某一套 harness 的单独工具故障。

完整统计、12 张图表和复算方法见[实验结果与复算说明](evaluation/results/README.md)，失败案例的逐项分析见 [Bad Case 分析](evaluation/results/CAUSAL_ANALYSIS.md)。

## 快速开始

项目要求 Python 3.11 或更高版本，推荐使用 [uv](https://docs.astral.sh/uv/) 管理环境。

```bash
git clone https://github.com/MagicalLiHua/nanoCursor.git
cd nanoCursor
uv sync
mkdir -p .nanocursor
cp .nanocursor/config.yaml.example .nanocursor/config.yaml
```

通过环境变量提供模型密钥：

```bash
export OPENAI_API_KEY="your-api-key"
uv run nanocursor
```

配置样例使用 OpenAI 兼容接口，可在 `.nanocursor/config.yaml` 中修改 `base_url`、模型名称和上下文参数。请勿将 API Key 写入配置文件或提交到仓库。

## 运行测试

```bash
uv run pytest -q
```

当前测试基线为 573 passed、1 skipped，覆盖核心 Agent 循环、工具执行、Hook、权限、团队协作、MCP、Skills、上下文管理和评测适配器。

## 使用 AgentEval

AgentEval 位于 `evaluation/agent-eval-lab/`：

```bash
cd evaluation/agent-eval-lab
npm install
npm run check
npm test
npm run cli -- issue-list
```

运行真实模型实验需要自行配置模型 API 和 Docker 环境。公开仓库不包含模型完整对话、原始轨迹或密钥，仅保留核验统计结果所需的脱敏字段。

## 仓库结构

```text
nanocursor/                         Python Coding Agent
tests/                              单元测试与集成测试
evaluation/agent-eval-lab/          TypeScript 评测工具
evaluation/results/data/            脱敏逐次结果与审计证据
evaluation/results/manifests/       冻结任务清单
evaluation/results/figures/         可复算 SVG 图表
evaluation/analysis/                数据导出与绘图脚本
```

## 结论边界

- 任务来自 SWE-bench，但本实验不是 SWE-bench 官方提交或榜单成绩；
- 每个任务重复 3 次，结果用于描述当前样本，不进行显著性推断；
- 两套 Agent 共享任务、提示词和工具合同，共同失败不能排除共同设计因素；
- Token 与耗时结果只适用于本次模型、任务和运行环境。

## 版本说明

`v3.0.0` 为当前 Agent + Evaluation 版本。旧版工程保留在 `legacy-v2.0.0` 标签中，主分支不再维护旧版架构。

## License

项目代码使用 [MIT License](LICENSE)。第三方组件及其许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
