# NanoCursor N0.1 × Pi × DeepSeek Bad Case 归因分析

> 日期：2026-09-01
> 性质：冻结实验的事后观察性归因，不修改 Agent、任务、预算、grader 或正式结果
> 证据摘要：[`data/attribution-evidence.json`](data/attribution-evidence.json)

## 1. 结论

用户提出的判断成立：**现有结果不支持“Coding Agent harness 是主要问题”**。

在相同 DeepSeek 模型、任务、system prompt、预算、容器限制和 grader 下：

- NanoCursor 与 Pi 的功能结果在名义 trial index 上一致 `35/36`，即 `97.2%`；
- 12 题中有 11 题的三次功能通过数完全相同；
- NanoCursor 为 `32/36` 功能通过，Pi 为 `33/36`，唯一内容差异是 Sphinx 的一次失败；
- 两套系统都在 Django `11141` 上 `0/3`，而 GLM 在 Pi 上又 `0/3`；9 次都通过正向目标并破坏同一个空 namespace 回归；
- NanoCursor 额外出现一次 pytest 协议未完成，但它已经通过全部 grader，失败点是没有在 96 turns 内生成最终回答。

因此，当前证据支持以下分层判断：

1. **主要内容 Bad Case 是任务语义、局部代码结构与模型决策的交互问题。**
2. **没有确认由 NanoCursor harness 导致的代码功能失败。**
3. Sphinx 的单次差异只能说明 harness 或随机采样可能改变路径，不能证明 NanoCursor 存在系统性缺陷。
4. pytest 的额外失败属于 Agent 编排和预算终止问题，不是代码能力失败，也不是容器或 Provider 故障。
5. 当前能够确定的 evaluator 问题是状态分类：`turn-limit` 被标成 `INFRA_BLOCKED`，这个名字不准确。

更准确的项目结论是：

> NanoCursor N0.1 已经是可用的复杂代码任务评测载体。与 Pi 相比，它没有表现出结构性的功能退化；当前 Bad Case 主要暴露模型在语义边界、失败证据解释和验证范围控制上的问题。Harness 仍有少量测量与终止策略问题，但不是本轮内容失败的主因。

## 2. 分析问题

本报告回答四个不同问题，避免把它们混成一个“Agent 不行”：

| 问题 | 判定对象 |
|---|---|
| 代码为什么没有满足任务？ | 任务理解、模型假设、修改和验证行为 |
| 为什么同一模型在两套系统结果不同？ | Harness、Agent loop、上下文组织与随机采样 |
| 为什么程序结果与最终状态不一致？ | 预算、终止控制与状态分类 |
| Grader 是否造成假失败？ | Oracle、注入、回归命令与判分实现 |

归因链按以下顺序检查：

```text
任务/仓库/Prompt
      ↓
模型建立假设
      ↓
Harness 组织上下文、工具和循环
      ↓
模型修改与验证
      ↓
Grader 执行确定性验收
      ↓
Outcome 状态与最终报告
```

“首个可观察偏差”与“责任层”不是同一个概念。例如 pytest trial 2 的首个问题是验证范围扩张，内容结果其实通过；最终协议失败还需要预算终止策略共同作用。

## 3. 可比性与限制

[`data/full-audit.json`](data/full-audit.json) 已确认 36 组 nanoCursor/Pi 对照具有以下共同条件：

- 同一 `deepseek-v4-flash`；
- 同一 12 题、base commit、镜像、issue、gold patch 和 test patch；
- 同一 system prompt；
- 同一 96 turns、32,768 最大输出、20 分钟墙钟预算；
- 同一 2 CPU、4 GiB、512 PIDs、容器禁网；
- 同一工具与 grader 资产；
- 36 次均无 Provider retry、无命令 timeout；
- NanoCursor 审计无 artifact、manifest 或 trace 错误。

但它不是严格的随机配对实验：

- trial 1、2、3 只是名义索引，不共享随机种子；
- 每个任务每套 harness 只有 3 次；
- 模型输出具有随机性；
- NanoCursor 和 Pi 的 Agent loop、消息序列化及内部上下文可能不同；
- 两套系统共享 prompt 和工具合同，因此“双方都失败”不能单独排除共同设计因素。

所以本报告使用“确认、高、中、低”证据等级，不做显著性检验，也不声称行业通用通过率。

## 4. 总体对照

### 4.1 功能结果

| 名义配对结果 | 次数 |
|---|---:|
| 两者都通过 | 32 |
| 两者都失败 | 3 |
| NanoCursor 通过、Pi 失败 | 0 |
| NanoCursor 失败、Pi 通过 | 1 |

功能结果名义一致 `35/36`。唯一不一致是 `sphinx-10449` trial 1。

这并不证明两套 harness 等价，但足以否定“现有数据已经显示 NanoCursor harness 是主要瓶颈”。如果 harness 是主要瓶颈，通常应看到多个任务上稳定、方向一致的失败差异；当前没有这种模式。

### 4.2 协议结果

| 名义配对结果 | 次数 |
|---|---:|
| 两者都正常完成 | 31 |
| 两者都未完成 | 3 |
| 仅 NanoCursor 正常完成 | 0 |
| 仅 Pi 正常完成 | 2 |

协议结果一致 `34/36`。两项 NanoCursor 额外未完成分别是：

- Sphinx trial 1：代码内容失败；
- pytest trial 2：代码内容通过，但达到 turn limit，缺少最终回答。

协议差异不能直接等同为 coding 能力差异。

### 4.3 成本结果

NanoCursor 相对 Pi：

- turns `-1.7%`；
- total tokens `-8.6%`；
- tool calls `-1.8%`；
- 单次墙钟总和 `-11.5%`。

这些总体数字没有形成稳定的逐题优势：NanoCursor 在 7 题 token 更少、5 题更多，Requests 为 `+77.5%`，Sphinx 为 `-34.8%`。因此只能说两套执行路径不同，不能说某套 harness 全面更好。

## 5. Bad Case 逐项归因

### 5.1 Django `11141`：跨 Harness、跨模型的同型语义缩减

#### 观察

| 系统 | Trial 数 | 正向目标 | 空 namespace 回归 |
|---|---:|---:|---:|
| Pi + DeepSeek | 3 | 3/3 PASS | 3/3 FAIL |
| NanoCursor + DeepSeek | 3 | 3/3 PASS | 3/3 FAIL |
| Pi + GLM | 3 | 3/3 PASS | 3/3 FAIL |

共 9 次运行都删除了 namespace package 的 `__file__` 拒绝，使“含 migration 文件”的 namespace 能加载；同时继续无条件执行 `self.migrated_apps.add(app_label)`，导致空 namespace 被错误归为 migrated app。

维护者实现不是简单删除检查，而是在扫描 `migration_names` 后分支：存在 migration 或启用 `ignore_no_migrations` 才加入 `migrated_apps`，否则加入 `unmigrated_apps`。

#### 失效链

```text
Issue 强调“__file__ 检查不再需要”
  → 模型把任务压缩为“删除 __file__ 分支”
  → 自建测试主要覆盖含 migration 的正例
  → 没有建立“目录存在但为空”的第二状态
  → 相关回归明确失败
  → 部分 trial 把失败解释为应被更新的旧行为
  → 报告完成，但 grader 判定 PARTIAL
```

#### 归因

- 直接失效机制：`HYPOTHESIS → VALIDATION → REPORTING`。
- 主要责任层：`TASK_SPEC × MODEL_BEHAVIOR` 的交互。
- Harness 责任：未发现。
- Grader 责任：trial 2 曾有隐藏 patch 路径冲突，但无模型重放后仍是相同内容 Partial；它只影响旧状态标签，不影响内容结论。
- 置信度：**高**。

不能把它简单称为“DeepSeek 能力差”：GLM 也出现 3/3 同型失败。更合理的解释是，Issue 表述和局部代码把多种状态压缩成一个显眼改动，而模型没有主动构造状态矩阵，也没有尊重已有回归证据。两套系统共用的 prompt 没有强制做边界状态枚举，这也是可能的共同促成因素。

反事实：如果 NanoCursor harness 是主因，那么 Pi 或 GLM 至少应较稳定地保留空 namespace 行为；实际 9/9 都没有。

### 5.2 Sphinx `10449` trial 1：一次模型路径偏差，Harness 差异未证实

#### 观察

- Pi：3/3 功能通过；
- NanoCursor：2/3 功能通过；
- NanoCursor trial 1 在 32 turns 正常结束，没有 timeout、Provider 或工具基础设施故障；
- 该 trial 把任务关联到旧问题 `#9575`，对 class 且 `autoclass_content` 非 `init/both` 时完全跳过 type-hint merge；
- 实际要求只是不显示构造函数的 `Return type: None`，class 参数仍应保留；
- Agent 已看到相关测试失败，却判断 evaluator 会更新旧预期，最后仍声明完成。

#### 归因

- 直接失效机制：`MODEL_BEHAVIOR`，包括错误类比、修改过宽和否定失败证据。
- NanoCursor 是否提高该错误概率：**未知**。
- Harness 基础设施故障：可排除。
- 置信度：直接机制为**高**；差异是否由 harness 引起为**低**。

这里只有 1 个不一致样本，另外两次 NanoCursor 走了更窄的修复并通过。没有观察到上下文截断、工具返回损坏或执行器误导等连接到 harness 的具体机制。因此不能从 `2/3` 对 `3/3` 推导 NanoCursor 系统性较差。

反事实验证需要在未暴露的同类 development 任务上随机交错运行更多次，并检查两套 loop 是否改变“读取失败测试后继续修正”的概率；重跑本题只能作为回归，不能再当盲测。

### 5.3 pytest `8399` trial 2：功能成功，协议预算删失

#### 观察

- 96/96 turns；
- 产品修改、Agent 新测试、隐藏目标、相关回归和保护检查全部 PASS；
- `grade.passed = true`；
- 最终回答为空；
- 42 次搜索、39 次读取、24 次命令、28 次写/改/删；
- 大量动作围绕自建 `--fixtures` 输出断言与临时 dump 调试；
- 同题另外两次 NanoCursor 为 51、42 turns，Pi 为 50、42、45 turns。

#### 归因

- coding 内容：成功，不应记为模型修复失败。
- 首个偏差：`VALIDATION` 范围扩张，模型持续调试非必要自建测试细节。
- 促成因素：Agent loop 没有结束预算保留或“grader 已满足后收尾”机制。
- 状态问题：`turn-limit` 被归为 `INFRA_BLOCKED`，属于 evaluator taxonomy 缺陷。
- 责任层：`MODEL_BEHAVIOR × AGENT_ORCHESTRATION`；状态名另归 `EVALUATOR`。
- 置信度：**中高**。

这是当前最接近 NanoCursor 可优化点的案例，但优化目标应表述为“减少协议预算删失”，而不是“提高代码修复能力”。Pi 没有在该题达到上限，说明 NanoCursor 路径可能更容易扩张；只有一次样本，尚不足以证明稳定的 harness 效应。

### 5.4 工具合同拒绝：双方共有，NanoCursor 略高但不是内容主因

为避免适配器 `isError` 语义差异，只比较四类能够在两套轨迹中同样识别的策略拒绝：inline Python、修改既有测试、pytest 命令入口、其他项目命令入口。

| 系统 | 可比策略拒绝 | 工具调用 | 拒绝率 |
|---|---:|---:|---:|
| NanoCursor | 82 | 1,946 | 4.21% |
| Pi | 72 | 1,982 | 3.63% |

NanoCursor 高 `0.58` 个百分点，主要来自 inline Python `48` 对 `36`、修改既有测试 `24` 对 `20`。与此同时，Pi 的命令入口拒绝更多：`16` 对 `10`。

归因结论：

- 明确工具边界在两套系统中都被模型反复试探，属于共享的“模型—工具合同”问题；
- NanoCursor 的略高拒绝率是弱差异信号，但没有与四个功能失败形成稳定因果链；
- 不同适配器对普通测试 exit 1 是否标成 `isError` 的编码不同，不能比较原始 error 总数；
- 当前不能据此认定 NanoCursor tool bridge 有缺陷。

置信度：共享问题为**高**；NanoCursor 特有问题为**低**。

### 5.5 历史 Pi UTF-8 与 grader 路径冲突：确认的 evaluator 问题，不属于当前 NanoCursor 内容失败

Pi 的 Django `11133` trial 3 曾因 Python/ASCII stdio 在读取非 ASCII 文件时抛出 `UnicodeEncodeError`，模型重复读取 41 次。该链路包含明确的 evaluator 编码缺陷和模型恢复策略缺陷。v1.0.2 已用 `PYTHONIOENCODING=utf-8` 修复，并通过 12/12 smoke；NanoCursor 三次为 30–36 turns，没有复现。

Pi 的 Django `11141` trial 2 曾因 Agent 新测试与隐藏 test patch 同路径，被错误标为 Infra。确定性重放证明内容仍是“目标通过、回归失败”。

这两项说明评测系统确实曾有可优化点，但它们已经有直接机制、复现与无模型验证，不能拿来解释当前 NanoCursor 的 Sphinx 或 pytest 差异。

## 6. 归因矩阵

| 现象 | 直接原因 | 最终责任层 | Harness 是否主因 | 置信度 |
|---|---|---|---|---|
| Django 3 个 NanoCursor 内容失败 | 漏掉空 namespace 状态 | 任务表述 × 模型假设/验证 | 否 | 高 |
| Django 跨系统 9/9 同型失败 | 语义缩减与正例偏置 | 共同任务/Prompt + 模型行为 | 否 | 高 |
| Sphinx NanoCursor trial 1 | 错误类比并否定测试失败 | 模型轨迹行为 | 未证实 | 中 |
| pytest NanoCursor trial 2 | 验证扩张后耗尽轮次 | 模型行为 × Agent 编排 | 部分可能 | 中高 |
| pytest 被标 `INFRA_BLOCKED` | Outcome taxonomy 过粗 | Evaluator | 是，但仅影响标签 | 高 |
| 工具策略拒绝 | 未稳定吸收工具合同 | 共享模型—工具交互 | 未证实 | 中 |
| Pi UTF-8 重复读取 | stdio 编码 + 无熔断 | Evaluator × 恢复策略 | 是，已修复 | 高 |
| Pi 隐藏 patch 路径冲突 | grader 注入覆盖 Agent 文件 | Evaluator | 是，已修复 | 高 |

按 NanoCursor 本轮 4 个功能失败计数：

- 3 个是跨 harness、跨模型稳定复现的 Django 语义 Bad Case；
- 1 个是 Sphinx 单次模型路径偏差；
- **0 个有证据确认由 NanoCursor harness 基础设施直接造成。**

另外 1 个协议失败 pytest 已经功能通过，不能混入上述 4 个内容失败。

## 7. 可以和不可以得出的结论

### 可以得出

1. NanoCursor N0.1 能稳定执行这套真实代码任务，基本功能与 Pi 接近。
2. 当前最稳定的 Bad Case 是边界状态遗漏，不是某个 harness 的工具故障。
3. 同一模型在不同 harness 上会产生不同轨迹、成本和偶发结果。
4. Outcome 必须同时保留协议口径和内容口径，否则会误判 pytest trial 2。
5. 归因必须使用 trace、grader 和反事实对照，不能仅看最终状态。

### 不可以得出

1. 不能说 NanoCursor 与 Pi 能力完全等价。
2. 不能说 NanoCursor 平均节省 `8.6%` token 会推广到其他任务。
3. 不能根据 Sphinx `2/3` 对 `3/3` 断言 NanoCursor 更差。
4. 不能把 Django 失败只归因于 DeepSeek，因为 GLM 也同型失败。
5. 不能把所有工具拒绝归为 harness bug，策略本来就在正确拦截越界动作。
6. 不能用这 12 题估计 DeepSeek、Pi、NanoCursor 或 SWE-bench 的一般通过率。

## 8. 是否需要做 N1

当前不建议直接开发一个“大而全的 N1”。若目标是测试工程师评估 Agent，最有价值的动作是冻结 SUT 并完善归因方法，而不是看到 Bad Case 就替被测系统修题。

可以做的改动分两类：

### 8.1 测量层修正，可直接做

- 把 `turn-limit` 从 `INFRA_BLOCKED` 拆为 `BUDGET_CENSORED`；
- 报告同时展示协议结果和 grader 内容结果；
- 统一两套 adapter 对工具错误、命令 exit 1 和策略拒绝的事件语义；
- 为每个失败自动生成首个偏差、最后有效证据和反事实字段。

这些改动提高测量准确性，不宣称提高 Agent 能力。

### 8.2 SUT 行为干预，只能作为新实验

- 最后若干 turns 保留给收尾和最终答复；
- 对重复同签名工具拒绝加入提醒或熔断；
- 相关回归失败时禁止无证据地声明“旧测试应更新”；
- 在修改前要求列出正例、空值、缺失值和边界状态。

这些会改变被测 Agent，必须形成 N1。当前 12 题已经暴露，只能作为 regression；N1 的最终效果需要新的 holdout 任务验证，不能在这 12 题上调完再称盲测提升。

## 9. 推荐的下一步实验

如果继续研究因果而不是追求更高成绩，优先级如下：

1. 先只修状态 taxonomy 和工具事件口径，不调用模型；
2. 从新 development 任务做一个单变量实验，例如“是否预留最终答复预算”；
3. 两个版本随机交错运行，至少保证每个任务多次重复；
4. 预先定义主指标：功能通过、协议完成、策略拒绝率和无效重复动作；
5. 使用全新 holdout 判断是否推广；
6. 本轮 12 题只报告回归，不再作为盲测 KPI。

不建议同时修改 system prompt、工具 schema、turn 策略和上下文压缩。多项一起改变后，即使结果提高，也无法知道是哪一项产生作用。

## 10. 三分钟讲述版本

> 我把同一个 DeepSeek 模型放进 NanoCursor 和 Pi 两套 Coding Agent harness，在冻结的 12 个真实 Python Issue 上各跑 3 次。两套系统的功能结果名义一致 35/36，12 题中 11 题的三次通过数相同，所以数据不支持 harness 是主要瓶颈。最稳定的 Django Bad Case 在两套 harness、两个模型上 9/9 同型复现：模型只删除了显眼的 `__file__` 检查，却漏掉空 namespace 状态，并把真实回归失败解释成旧测试。这是任务语义与模型假设、验证行为的交互。NanoCursor 唯一额外内容失败是一次 Sphinx 随机路径，样本不足以归因给 harness；另一次 pytest 虽然耗尽轮次，但所有 grader 已通过，因此是协议预算删失，不是修复失败。这个实验的价值不只是报通过率，而是建立了模型、Agent 编排、Evaluator、Oracle 和环境分层归因的方法。

## 11. 证据位置

- 72 次脱敏运行记录：[`data/runs.csv`](data/runs.csv)
- 汇总统计：[`data/summary.json`](data/summary.json)
- 机器审计：[`data/full-audit.json`](data/full-audit.json)
- 本报告机器证据：[`data/attribution-evidence.json`](data/attribution-evidence.json)
- 冻结任务清单：[`manifests/`](manifests/)
- 复算脚本：[`../analysis/build_public_artifacts.py`](../analysis/build_public_artifacts.py)

完整模型消息、源码片段和逐工具调用轨迹未公开；公开数据保留核验本文数字所需的任务级与运行级字段。
