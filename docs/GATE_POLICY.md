# 版本晋级 Gate

Gate 只读取已有 `experiment.json`，不会执行 Agent 或读取模型密钥。默认策略见 `configs/default-gate.json`。

## 硬条件

- 完成率、评测通过率不得低于 Baseline。
- `model_failed_rate` 不得升高。
- `trace_incomplete_rate`、`infra_failed_rate` 必须为 0。
- 平均 Agent 耗时不得升高。

## 带阈值的成本条件

- 平均工具调用相对 Baseline 最多增加 10%。
- 平均模型 Token 相对 Baseline 最多增加 10%。

这避免将微小波动误判为回归，同时确保明显成本上涨会阻断晋级。阈值应根据 Case 数量、Trial 数量和团队成本预算调整。

## 使用方式

```bash
cd study/Regression
make gate RUNTIME=.runtime/repeated-experiment-v1-v2
```

输出 `gate-report.json`，每条规则包含实际值、阈值、结论和 Experiment Comparison 证据。退出码 `0` 表示通过，`1` 表示 Gate 阻断，`2` 表示输入或策略无效。
