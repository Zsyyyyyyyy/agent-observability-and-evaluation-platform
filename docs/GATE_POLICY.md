# 版本晋级 Gate

Gate 只读取已有 `experiment.json`，不会执行 Agent 或读取模型密钥。默认策略见 `configs/default-gate.json`。

所有 Rate 必须是 `[0, 1]` 内的有限数，成本、耗时和计数必须是有限非负数；`NaN`、Infinity、负 Token 等不可能指标会被视为损坏的 Gate 输入并拒绝计算，不能借由比较运算绕过规则。

## 硬条件

- 完成率、评测通过率不得低于 Baseline。
- `model_failed_rate` 不得升高。
- `trace_incomplete_rate`、`infra_failed_rate` 必须为 0。
- 路径策略和 Diff 策略违规率必须为 0。
- 有足够重复 Trial 时，All-pass@3 一致性不得下降、Flaky Case Rate 不得升高。
- 平均耗时和 P95 尾延迟属于诊断指标，不单独阻断 Gate；P95 回归必须在发布结论中明确标记并人工复核慢 Trial。

## 带阈值的成本条件

- 平均工具调用相对 Baseline 最多增加 10%。当 Baseline 为 0 时，比例无定义，改用显式绝对增量阈值（默认 0）。
- 平均模型 Token 相对 Baseline 最多增加 10%。当 Baseline 为 0 时，比例无定义，改用显式绝对增量阈值（默认 0）。

这避免将微小波动误判为回归，同时确保明显成本上涨会阻断晋级。阈值应根据 Case 数量、Trial 数量和团队成本预算调整。

Console 同时展示 Raw Reliability 与 Agent Quality：后者会排除模型与基础设施失败，帮助定位问题；但 Gate 只使用前者，不能以诊断口径替代真实发布风险。

`all-pass@3` 表示同一 Case 的 3 次重复均有效通过；它不同于标准 Pass@3（至少一次成功），因此这里将其作为重复稳定性指标命名。

Gate 的优先级是正确性与可靠性高于效率：如果完成率、评测通过率、模型失败率、Trace、策略或 all-pass@3/Flaky 任一硬规则回归，即使 Token 下降，也会保持阻断，并在 `decision.message` 中说明“token savings cannot offset”。

## 使用方式

```bash
cd study/Regression
make gate RUNTIME=.runtime/repeated-experiment-v1-v2
```

输出 `gate-report.json`，每条规则包含实际值、阈值、结论和 Experiment Comparison 证据。退出码 `0` 表示通过，`1` 表示 Gate 阻断，`2` 表示输入或策略无效。
