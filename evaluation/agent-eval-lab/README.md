# AgentEval

AgentEval 是 nanoCursor 的真实代码任务评测工具。它负责冻结任务资产、在隔离容器中准备仓库、运行 Coding Agent、记录执行指标，并使用测试补丁与回归命令做确定性验收。

正式对照实验使用 12 个来自 SWE-bench 的 Python 仓库任务。工具也保留了早期的离线任务、HTTP 沙箱、缺陷发现和协作任务模式，便于回归基础设施，但这些历史模式不计入公开的 72 次对照结果。

## 安装与检查

要求 Node.js 22 或更高版本，并准备可用的 Docker 环境。

```bash
npm install
npm run check
npm test
npm run build
```

## 常用命令

```bash
# 检查任务目录
npm run cli -- validate

# 查看正式 Issue 任务
npm run cli -- issue-list

# 验证单题资产和沙箱
npm run cli -- issue-preflight issue-eval-astropy-12907

# 运行参考 harness
npm run cli -- issue-run issue-eval-astropy-12907 --model deepseek --trials 1 --env .env

# 运行 nanoCursor 候选 harness
npm run cli -- issue-nanocursor-validate --nanocursor-root ../..
npm run cli -- issue-nanocursor-run issue-eval-astropy-12907 --nanocursor-root ../.. --model deepseek --trials 1 --env .env
```

环境变量样例见 `.env.example`。API 密钥只在运行时读取，不应进入结果、报告或版本控制。

## 验收原则

每个任务固定上游仓库、base commit、容器镜像、Issue 描述、gold patch、test patch、资源预算和 grader。Agent 修改完成后，grader 独立检查目标测试、相关回归和受保护文件，并把“代码内容是否通过”与“Agent 是否正常结束并给出最终回答”分开记录。

完整模型消息和工具轨迹可能包含大段源码，不随公开仓库分发。公开结果只保留任务、trial、状态、通过情况、turn、Token、工具调用和耗时等复算指标。

## 历史模式

```bash
npm run cli -- real-list
npm run cli -- real-discovery-list
npm run cli -- agent-task-list
```

这些命令用于基础设施回归和方法探索，不应与正式 SWE-bench Issue Evaluation 的结果混合统计。
