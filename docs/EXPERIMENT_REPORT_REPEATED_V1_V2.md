# Repeated Core Experiment：react-agent-v1 vs react-agent-v2

## 实验设计

- Cases：4 个代表性确定性 Python 修复任务（空输入、名称标准化、跨文件默认值、标点 Slug）。
- Trials：每个 Case、每个版本 3 次，共 24 次计划 Trial。
- 固定条件：同一模型端点、Docker Sandbox、工具 Allowlist、路径策略、测试命令与 Evaluator。
- 唯一变量：v2 使用 `verify-once-v2` Agent 控制策略。
- 原始汇总：`.runtime/repeated-experiment-v1-v2/experiment.json`。该报告由 `--report-only` 从已有 Artifact 重建，不调用模型。

## 数据完整性处理

执行初期出现过一次并发写入同一输出目录，造成 3 条 Trace 污染。它们均已归档至 `.runtime/repeated-experiment-v1-v2/invalid-attempts/`，未进入统计；对应 Trial 使用新 Worktree 重跑。平台随后修复为：Trace 校验失败标记 `trace_incomplete`，恢复时不再将它当作成功 Trial。

最终有效实验集为 24 条当前 Trial Artifact：

| 指标 | v1 Baseline | v2 Candidate |
|---|---:|---:|
| 计划 Trial | 12 | 12 |
| completed + evaluation passed + valid trace | 11 (91.7%) | 11 (91.7%) |
| `model_failed` | 1 | 1 |
| 失败原因 | 模型请求 TimeoutError | 模型请求 TimeoutError |

两条失败 Trial 的 Trace 都完整，因此被保留为模型/网络可靠性样本，而非静默重跑或删除。

## 成功 Trial 的效率指标

下表仅统计 11 条“完成、评测通过且 Trace 合法”的 Trial，避免将模型网关超时的等待时间误解释为 Agent 策略成本。

| 指标 | v1 均值 / 中位数 / 标准差 | v2 均值 / 中位数 / 标准差 | 观察 |
|---|---:|---:|---|
| 耗时 | 16.47 s / 17.86 s / 5.99 s | 14.88 s / 12.66 s / 4.46 s | v2 均值 -9.6%，中位数 -29.1% |
| 工具调用 | 6.00 / 7 / 2.28 | 6.09 / 6 / 1.30 | 均值 +1.5%，中位数少 1 次，波动更小 |
| 模型 Token | 6,618.5 / 7,567 / 2,711.9 | 6,168.2 / 5,525 / 1,286.2 | v2 均值 -6.8%，中位数 -27.0% |

若将两条模型超时也纳入全部 12 次尝试，版本通过率仍相同；`experiment.json` 中 v2 的平均耗时、工具调用与 Token 也均更低。但该口径会受网关超时等待显著影响，因此不作为 Agent 策略效率的唯一依据。

## 结论与晋级建议

在本次 4 Case × 3 Trial 的样本中，v2 保持与 v1 相同的成功率，并在成功样本上呈现更低的耗时和 Token 中位数、较低的波动。**建议将 v2 作为候选默认策略进入下一轮扩大验证**，但不宣称已经证明全面优于 v1：

- 只有 4 个 Case，任务覆盖仍有限；
- 两版本各发生一次相同类型的模型请求超时，可靠性没有提升证据；
- 工具调用均值略高，仍需在更多复杂任务上观察是否带来额外成本。

下一轮建议扩展到全部 8 个 Case、每个版本至少 3 Trial，并将模型超时单列为 Provider Reliability 指标，而不是混入 Agent 代码质量结论。
