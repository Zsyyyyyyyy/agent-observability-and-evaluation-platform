# Full Core Repeated Experiment：react-agent-v1 vs react-agent-v2

## 实验口径

- 8 个确定性 Python 修复 Case，每个版本、每个 Case 运行 3 次。
- 总计 48 条活跃 Trial（v1 24、v2 24）；Docker Sandbox、模型端点、工具策略、路径策略和 Evaluator 保持一致。
- v2 唯一变化是 `verify-once-v2` 控制策略。
- 汇总文件：`.runtime/repeated-experiment-v1-v2/experiment.json`，由 `--report-only` 重建，不再调用模型。
- 早期发生的 3 条并发 Trace 污染 Attempt 已归档到 `invalid-attempts/`，不属于 48 条活跃 Trial，也不参与统计。

## 全部尝试结果（版本 Gate 口径）

| 指标 | v1 | v2 | Delta（v2 - v1） |
|---|---:|---:|---:|
| completed / evaluation passed / test passed | 22/24（91.7%） | 23/24（95.8%） | +4.17 pp |
| `model_failed_rate` | 4.17% | 4.17% | 0 pp |
| `trace_incomplete_rate` | 0% | 0% | 0 pp |
| `infra_failed_rate` | 0% | 0% | 0 pp |
| 平均 Agent 耗时 | 17.83 s | 15.76 s | -11.6% |
| 平均工具调用 | 5.54 | 5.71 | +3.0% |
| 平均模型 Token | 6,105.9 | 5,811.9 | -4.8% |

v1 的额外一次失败发生在 `parse_port_blank_default`：Agent 达到工具调用预算，状态为 `budget_exceeded`。v2 在该 Case 为 3/3。两版本各有一次 `safe_slug_punctuation` 的模型请求 TimeoutError；两条 Trace 均完整，故将其视为 Provider Reliability 样本，而非 Agent 代码质量失败。

## 仅成功且 Trace 合法的 Trial

为避免模型超时的等待时间混入 Agent 策略成本，下表只统计 v1 的 22 条与 v2 的 23 条有效成功 Trial。

| 指标 | v1 均值 / 中位数 / 标准差 | v2 均值 / 中位数 / 标准差 | 解读 |
|---|---:|---:|---|
| 耗时 | 14.38 s / 12.58 s / 4.99 s | 13.66 s / 12.66 s / 3.64 s | v2 均值 -5.0%，中位数近似持平，波动更小 |
| 工具调用 | 5.23 / 4.5 / 2.14 | 5.78 / 5 / 1.13 | v2 工具成本略高，但更稳定 |
| 模型 Token | 5,670.2 / 4,852 / 2,345.4 | 5,991.3 / 5,447 / 1,082.2 | v2 成功样本 Token 更高，但波动明显更小 |

## 按 Case 通过率

| Case | v1 | v2 |
|---|---:|---:|
| bounded discount | 3/3 | 3/3 |
| cross-file greeting | 3/3 | 3/3 |
| deduplicate tags | 3/3 | 3/3 |
| merge settings | 3/3 | 3/3 |
| normalize none | 3/3 | 3/3 |
| parse port | 2/3（预算超限） | 3/3 |
| safe slug | 2/3（模型超时） | 2/3（模型超时） |
| calculator empty input | 3/3 | 3/3 |

## 结论

v2 在本次全量核心集上更适合作为**候选默认版本**：通过率从 91.7% 提升至 95.8%，没有引入新的 Trace 或基础设施失败，全部尝试平均耗时和 Token 更低。

但它不是无条件的成本优化：在成功样本中，v2 的工具调用和 Token 中位数略高。因此，晋级 Gate 应以“成功率不下降、可靠性不回归、平均耗时不升高”为硬条件，把 Token/工具成本作为带阈值的软条件，而不是将一次均值变化当成绝对胜负。

下一步应增加 Manifest 驱动的预期失败基准（路径违规、工具拒绝、超时），并把上述 Gate 规则实现为可执行 CLI；再将新的 Agent 改动纳入同一口径比较。
