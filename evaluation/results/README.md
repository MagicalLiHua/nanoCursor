# 实验结果与复算说明

## 实验设置

- 任务：12 个 SWE-bench Python 真实仓库 Issue，覆盖 9 个开源项目；
- 重复次数：每个任务、每套 harness 各 3 次；
- 总运行数：nanoCursor 36 次，Pi 参考组 36 次；
- 公平约束：相同模型、任务文本、system prompt、最大 96 turns、20 分钟墙钟预算、容器资源限制和 grader；
- 网络：任务容器内禁网；
- 结果层次：`content_passed` 表示确定性代码验收通过，`protocol_completed` 表示 Agent 正常结束并给出最终结果。

## 结果摘要

| 指标 | nanoCursor | Pi 参考组 |
|---|---:|---:|
| 内容验收通过 | 32/36（88.9%） | 33/36（91.7%） |
| 正常完成协议 | 31/36（86.1%） | 33/36（91.7%） |
| 总 Turns | 1,495 | 1,521 |
| 总 Token | 1,760,888 | 1,926,021 |
| 工具调用 | 1,946 | 1,982 |
| 运行总耗时 | 6,499.2 s | 7,341.7 s |

两套 harness 按相同任务和名义 trial 对齐后，功能结果一致 35/36（97.2%）。这说明在当前有限样本和受控设置下，两套执行路径的最终功能结果高度接近；它不是等价性证明，也不能外推为通用效率优势。

![结果概览](figures/overall-outcomes.svg)

![成本对比](figures/cost-comparison.svg)

## 文件说明

- `data/runs.csv`：72 次脱敏逐次记录；
- `data/summary.json`：README 表格的汇总来源；
- `data/attribution-evidence.json`：逐任务归因证据；
- `data/full-audit.json`：任务、配置与结果一致性审计；
- `manifests/`：12 个任务的冻结资产清单和哈希；
- `figures/`：由公开 CSV 重新生成的 SVG 图表；
- `CAUSAL_ANALYSIS.md`：Bad Case 逐项归因与结论边界。

`runs.csv` 不含 prompt、模型回答、源码片段和完整工具轨迹。字段含义：

| 字段 | 含义 |
|---|---|
| `harness` | nanoCursor 或 Pi 参考组 |
| `run_id` | 唯一运行标识 |
| `task_id` | 冻结任务标识 |
| `repository` | 上游仓库 |
| `trial` | 同任务的名义重复序号 |
| `status` | Agent 协议结果 |
| `protocol_completed` | 是否正常结束并生成最终回答 |
| `content_passed` | 确定性 grader 是否接受代码结果 |
| `termination` | 运行终止原因 |
| `turns` / `total_tokens` / `tool_calls` | 运行成本指标 |
| `wall_seconds` | 单次墙钟耗时 |

## 复算

图表脚本只使用 Python 标准库。基于已提交的 CSV 重建汇总和图表：

```bash
python evaluation/analysis/build_public_artifacts.py \
  --output-dir evaluation/results
```

脚本也支持从私有原始结果导出公开 CSV；这些原始文件不在仓库中，相关参数可通过 `--help` 查看。

## 结论边界

- 任务选自 SWE-bench，但该实验不是 SWE-bench 官方提交或榜单成绩；
- trial 没有共享随机种子，不是严格随机配对实验；
- 每题只有 3 次重复，不做显著性检验；
- 两套 harness 共享任务、prompt 与工具合同，共同失败不能单独排除共同设计因素；
- 总 Token 与耗时是本次任务集合上的描述性统计，不声称 nanoCursor 在其他模型或任务上始终更省。
