# 阶段 11：Fail-Closed Promotion Gate

## 目标

将 Promotion Gate 从“Candidate 相对 Baseline 未退化”收紧为可用于发布决策的 fail-closed 判断：只有严格可比、覆盖完整、指标可用，并同时达到 Candidate 绝对质量下限和相对非退化条件的实验，才可以 `promote`。

## 决策状态

| 状态 | 含义 |
| --- | --- |
| `promote` | 所有证据完整，绝对门槛、相对门槛和成本规则均通过。 |
| `hold` | 证据完整，但 Candidate 未达到最低质量或出现回归。 |
| `inconclusive` | Protocol 非 strict、Case/Trial 覆盖不足，或关键指标不可用。 |

`inconclusive` 不是通过；命令仍以非零状态结束，避免调用方把“不知道”当成“可发布”。

## 新增硬规则

- Protocol 必须是 `strict`。
- 至少 8 个完整配对 Case，每个 Case 至少 3 个 Baseline、Candidate 和配对 Trial。
- Candidate completion rate 与 evaluation pass rate 默认必须为 `1.0`。
- Candidate model、trace、infrastructure、path-policy、diff-policy failure rate 默认必须为 `0.0`。
- 所有参与 Gate 的质量、可靠性和成本指标必须存在；缺失值不再默认成 `0`。
- 在满足绝对门槛后，Candidate 还必须不低于 Baseline 的正确性、可靠性和一致性，并遵守工具调用与 Token 增长预算。

## 已验证反例

- 0% completion vs 0% completion → `hold`。
- 100% infrastructure failure vs 100% infrastructure failure → `hold`。
- 缺少 Token 指标 → `inconclusive`。
- 少一个 Case 或少一个配对 Trial → `inconclusive`。
- 非 strict Protocol → `inconclusive`。

## 对历史 V4.1 证据的影响

使用 `configs/default-gate.json` 对 `.runtime/external-openai-v3-v4-1-benchmark/experiment.json` 重新评估，结论仍为 `promote`：8 个完整 Case、24 个有效 Baseline Trial、24 个有效 Candidate Trial，且所有新增绝对阈值通过。

这次重评估只更新可重建的 Evolution Catalog Gate 投影；不会改写任何不可变 Attempt、Trace、Trial Result、Experiment 或历史 Gate Artifact。
