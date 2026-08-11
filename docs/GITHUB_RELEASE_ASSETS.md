# GitHub 首页与求职素材

## 仓库名与一句话定位

**建议仓库名：** `regression-lab`

**一句话：** A local observability, evaluation, and promotion-gate platform for Coding Agents.

**中文简介：** 面向 Coding Agent 的本地可观测、回归评测与版本晋级平台：隔离执行、Trace、确定性评分、真实实验对比和可执行 Gate。

## README 首屏文案

```text
Regression Lab turns a Coding Agent run into an auditable experiment:
isolated worktree → Docker sandbox → JSONL trace → deterministic evaluators → promotion gate.
```

紧接着给出真实数据：`8 Cases × 3 Trials × 2 Versions`，v1 `22/24`、v2 `23/24`，并明确 v2 通过率更高、全尝试平均耗时更低，但成功样本 Token/工具成本仍存在权衡。

## 已准备的截图

| 文件 | 用途 | 建议配文 |
|---|---|---|
| `assets/console-release-overview.jpg` | README 首屏 / 项目总览 | Gate 直接给出候选版本晋级结论，并汇总 48 条真实 Trial。 |
| `assets/console-paired-case.jpg` | README 实验结果部分 / 演示视频封面 | 同一 Case 的 v1/v2 三次 Trial 并列，预算超限与模型失败可直接定位。 |

## GitHub Topics

```text
ai-agent, coding-agent, llm-evals, observability, regression-testing,
docker-sandbox, agent-evaluation, python, llmops
```

## 演示视频标题与描述

**标题：** Regression Lab：如何用真实实验决定 Coding Agent v2 是否晋级

**描述：** 使用 8 个确定性修复任务、每版本 3 次 Trial，对比 `react-agent-v1/v2`。演示 Docker Sandbox、Trace、六类 Evaluator、Case 并列矩阵、Failure Suite 和 Promotion Gate。所有模型失败与成本权衡均保留在 Artifact 中，不使用单一分数掩盖问题。

## 简历链接旁的短描述

```text
Coding Agent observability & evaluation platform · 48 real trials · Docker sandbox · promotion gate
```
